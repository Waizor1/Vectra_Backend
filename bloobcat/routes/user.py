from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Dict, Any
from datetime import datetime, timedelta, timezone

from bloobcat.db.users import User_Pydantic, Users
from bloobcat.funcs.validate import validate
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings
from bloobcat.logger import get_logger
from fastapi import FastAPI
from starlette.background import BackgroundTask
from bloobcat.routes.remnawave.hwid_utils import cleanup_user_hwid_devices
from bloobcat.db.active_tariff import ActiveTariffs

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
            logger.info(f"Получаем URL подписки для {user.id} внутри эндпоинта /user")
            subscription_url = await remnawave_client.users.get_subscription_url(user)
            logger.info(f"Пользователь {user.id} получил URL подписки RemnaWave: {subscription_url[:20]}...")
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

    # Добавляем лимит устройств из активного тарифа или 1 по умолчанию
    devices_limit = 1
    if user.active_tariff_id:
        tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if tariff:
            devices_limit = tariff.hwid_limit
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
    user.is_subscribed = False
    await user.save()
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
