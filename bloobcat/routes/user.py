from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Dict, Any
from datetime import datetime, timedelta, timezone
import uuid  # For generating new familyurl

from bloobcat.db.users import User_Pydantic, Users
from bloobcat.funcs.validate import validate
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings
from bloobcat.logger import get_logger
from fastapi import FastAPI
from starlette.background import BackgroundTask
from bloobcat.routes.remnawave.hwid_utils import cleanup_user_hwid_devices
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.bot.notifications.admin import cancel_subscription

logger = get_logger("routes.user")

router = APIRouter(
    prefix="/user",
    tags=["user"],
)


class UserUpdate(BaseModel):
    email: EmailStr


# --- Инициализируем клиент RemnaWave один раз ---
remnawave_client = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())

# Функция для закрытия клиента RemnaWave при завершении работы приложения
async def close_remnawave_client():
    logger.info("Закрытие клиента RemnaWave при завершении работы приложения")
    await remnawave_client.close()

def register_shutdown_event(app: FastAPI):
    """Регистрирует обработчик события завершения работы приложения"""
    @app.on_event("shutdown")
    async def shutdown_event():
        logger.info("Приложение завершает работу, закрываем RemnaWave клиент")
        await close_remnawave_client()

@router.get("")
async def check(user: Users = Depends(validate)) -> Dict[str, Any]:
    """
    Возвращает данные пользователя и URL подписки RemnaWave.
    Создает пользователя в RemnaWave при первом обращении.
    """
    subscription_url = None
    error_getting_url = None
    
    # Убеждаемся, что пользователь есть в RemnaWave (вызов уже был в Users.get_user)
    if not user.remnawave_uuid:
        logger.error(f"Пользователь {user.id} прошел валидацию, но не был создан в RemnaWave (_ensure_remnawave_user не сработал?).")
        # Не прерываем, вернем пользователя без URL
        error_getting_url = "Failed to initialize user account."
    else:
        try:
            # Получаем URL подписки
            logger.debug(f"Получаем URL подписки для {user.id} внутри эндпоинта /user")
            subscription_url = await remnawave_client.users.get_subscription_url(user)
            logger.debug(f"Пользователь {user.id} получил URL подписки RemnaWave: {subscription_url[:20]}...")
        except Exception as e:
            error_getting_url = f"Failed to get subscription URL: {str(e)}"
            logger.error(f"Ошибка при получении URL подписки для пользователя {user.id} в эндпоинте /user: {error_getting_url}")
            # Не прерываем запрос, вернем данные пользователя с ошибкой URL

    # Получаем стандартные данные пользователя
    user_data = await User_Pydantic.from_tortoise_orm(user)
    user_dict = user_data.model_dump()

    # Добавляем URL и возможную ошибку в ответ
    user_dict["subscription_url"] = subscription_url
    user_dict["subscription_url_error"] = error_getting_url

    # Добавляем количество HWID устройств для пользователя
    devices_count = 0
    if user.remnawave_uuid:
        try:
            raw_resp = await remnawave_client.users.get_user_hwid_devices(str(user.remnawave_uuid))
            devices_list = []
            if isinstance(raw_resp, list):
                devices_list = raw_resp
            elif isinstance(raw_resp, dict):
                resp = raw_resp.get("response")
                if isinstance(resp, list):
                    devices_list = resp
                elif isinstance(resp, dict) and isinstance(resp.get("devices"), list):
                    devices_list = resp.get("devices")
            devices_count = len(devices_list)
        except Exception as e:
            logger.error(f"Ошибка получения списка устройств для пользователя {user.id}: {e}")
    user_dict["devices_count"] = devices_count

    # --- Определение лимита устройств (только из БД) ---
    devices_limit = 1  # 1. Значение по умолчанию
    source = "дефолту"

    # 2. Пытаемся получить из тарифа
    if user.active_tariff_id:
        tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if tariff:
            devices_limit = tariff.hwid_limit
            source = f"тарифу ({tariff.name})"
    
    # 3. Личное значение из БД имеет наивысший приоритет
    if user.hwid_limit is not None:
        devices_limit = user.hwid_limit
        source = "личной настройке в БД"

    logger.debug(f"Итоговый лимит устройств для пользователя {user.id} установлен по {source}: {devices_limit}")
    user_dict["devices_limit"] = devices_limit

    return user_dict


@router.patch("")
async def update_user_profile(
    update_data: UserUpdate,
    user: Users = Depends(validate)
):
    """
    Обновляет email пользователя.
    """
    user.email = update_data.email
    await user.save()
    # Можно вернуть новый формат ответа, как в check, или оставить старый
    user_data = await User_Pydantic.from_tortoise_orm(user)
    user_dict = user_data.model_dump()
    # Опционально: добавить сюда получение URL, если нужно
    # user_dict["subscription_url"] = await remnawave_client.users.get_subscription_url(user)
    return user_dict


@router.post("/unsubscribe")
async def unsubscribe(user: Users = Depends(validate)):
    """
    Отписывает пользователя от рассылки (но не влияет на подписку VPN).
    """
    if not user.is_subscribed:
        return {"status": "ok", "message": "already unsubscribed"}
    user.is_subscribed = False
    await user.save()
    await cancel_subscription(user)
    return {"status": "ok"}

@router.post("/reset_devices")
async def reset_devices(user: Users = Depends(validate)):
    """
    Ручной сброс HWID устройств пользователя. Доступен не чаще, чем раз в 24 часа.
    """
    now = datetime.now(timezone.utc)
    if user.last_hwid_reset and (now - user.last_hwid_reset) < timedelta(hours=24):
        raise HTTPException(status_code=400, detail="Сброс устройств можно выполнять не чаще, чем раз в 24 часа")
    if not user.remnawave_uuid:
        raise HTTPException(status_code=400, detail="У пользователя нет RemnaWave UUID")
    await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)
    user.last_hwid_reset = now
    await user.save()
    return {"status": "ok"}

@router.get("/family/{familyurl}")
async def get_family_subscription(familyurl: str):
    """
    Возвращает данные подписки и информацию об устройствах пользователя, приглашённого по семейному URL.
    """
    user = await Users.get_or_none(familyurl=familyurl)
    if not user:
        raise HTTPException(status_code=404, detail="Family link not found")

    # Подготовка URL подписки
    subscription_url = None
    error_getting_url = None
    if not user.remnawave_uuid:
        error_getting_url = "Failed to initialize user account."
    else:
        try:
            subscription_url = await remnawave_client.users.get_subscription_url(user)
        except Exception as e:
            error_getting_url = f"Failed to get subscription URL: {str(e)}"

    # Сериализация данных пользователя
    user_data = await User_Pydantic.from_tortoise_orm(user)
    user_dict = user_data.model_dump()
    user_dict["subscription_url"] = subscription_url
    user_dict["subscription_url_error"] = error_getting_url

    # Подсчёт устройств
    devices_count = 0
    if user.remnawave_uuid:
        try:
            raw_resp = await remnawave_client.users.get_user_hwid_devices(str(user.remnawave_uuid))
            devices_list = []
            if isinstance(raw_resp, list):
                devices_list = raw_resp
            elif isinstance(raw_resp, dict):
                resp = raw_resp.get("response")
                if isinstance(resp, list):
                    devices_list = resp
                elif isinstance(resp, dict) and isinstance(resp.get("devices"), list):
                    devices_list = resp.get("devices")
            devices_count = len(devices_list)
        except Exception:
            pass
    user_dict["devices_count"] = devices_count

    # --- Определение лимита устройств (только из БД) ---
    devices_limit = 1  # 1. Значение по умолчанию
    source = "дефолту"

    # 2. Пытаемся получить из тарифа
    if user.active_tariff_id:
        tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if tariff:
            devices_limit = tariff.hwid_limit
            source = f"тарифу ({tariff.name})"
    
    # 3. Личное значение из БД имеет наивысший приоритет
    if user.hwid_limit is not None:
        devices_limit = user.hwid_limit
        source = "личной настройке в БД"

    logger.debug(f"Итоговый лимит устройств для пользователя {user.id} по семейной ссылке установлен по {source}: {devices_limit}")
    user_dict["devices_limit"] = devices_limit

    return user_dict

@router.post("/family/revoke")
async def revoke_family(user: Users = Depends(validate)) -> Dict[str, Any]:
    """
    Отзываем семейную подписку: делаем revoke в RemnaWave и регенерируем familyurl
    """
    if not user.remnawave_uuid:
        raise HTTPException(status_code=400, detail="No RemnaWave UUID for user")
    # Отзываем подписку в RemnaWave
    try:
        await remnawave_client.users.revoke_user(str(user.remnawave_uuid))
    except Exception as e:
        logger.error(f"Error revoking subscription in RemnaWave for user {user.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to revoke subscription in RemnaWave")

    # Очищаем зарегистрированные устройства в RemnaWave
    try:
        await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)
    except Exception as e:
        logger.error(f"Error cleaning up HWID devices for user {user.id}: {e}")
        # Не прерываем основной поток, продолжаем регенерацию ссылки

    # Регенерируем семейную ссылку случайным UUID
    new_url = uuid.uuid4().hex
    user.familyurl = new_url
    await user.save()
    return {"new_familyurl": new_url}
