from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from typing import Dict, Any
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, ROUND_HALF_UP, getcontext
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
from bloobcat.db.tariff import Tariffs
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
    try:
        logger.info(f"Начало обработки запроса /user для пользователя {user.id}")
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
        logger.debug(
            f"[/user check] sub_url_present={bool(subscription_url)}, url_error={error_getting_url}"
        )

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
        logger.debug(f"[/user check] devices_count={devices_count}")

        # --- Определение лимита устройств (только из БД) ---
        devices_limit = 1  # 1. Значение по умолчанию
        source = "дефолту"
        active_tariff_data = None

        # 2. Пытаемся получить из тарифа
        if user.active_tariff_id:
            tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
            if tariff:
                devices_limit = tariff.hwid_limit
                source = f"тарифу ({tariff.name})"
                # Добавляем информацию об активном тарифе в ответ
                active_tariff_data = {
                    "id": tariff.id,
                    "name": tariff.name,
                    "months": tariff.months,
                    "price": tariff.price,
                    "hwid_limit": tariff.hwid_limit
                }
    
        # 3. Личное значение из БД имеет наивысший приоритет
        if user.hwid_limit is not None:
            devices_limit = user.hwid_limit
            source = "личной настройке в БД"

        logger.debug(f"Итоговый лимит устройств для пользователя {user.id} установлен по {source}: {devices_limit}")
        user_dict["devices_limit"] = devices_limit
        user_dict["active_tariff"] = active_tariff_data

        logger.info(f"Успешно обработан запрос /user для пользователя {user.id}")
        return user_dict
    
    except Exception as e:
        logger.error(f"Ошибка в эндпоинте /user для пользователя {getattr(user, 'id', 'unknown')}: {str(e)}", exc_info=True)
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="Internal server error")


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
    Ручное отключение HWID устройств пользователя. Доступно не чаще, чем раз в 24 часа.
    """
    now = datetime.now(timezone.utc)
    if user.last_hwid_reset and (now - user.last_hwid_reset) < timedelta(hours=24):
        raise HTTPException(status_code=400, detail="Отключение устройств можно выполнять не чаще, чем раз в 24 часа")
    if not user.remnawave_uuid:
        raise HTTPException(status_code=400, detail="У пользователя нет RemnaWave UUID")
    await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)
    user.last_hwid_reset = now
    await user.save()
    return {"status": "ok"}


class ChangeDevicesRequest(BaseModel):
    device_count: int


@router.patch("/active_tariff")
async def change_active_tariff_devices(payload: ChangeDevicesRequest, user: Users = Depends(validate)) -> Dict[str, Any]:
    """
    Меняет количество устройств (hwid_limit) в текущем активном тарифе пользователя
    и пропорционально пересчитывает дату окончания подписки без новой покупки.
    Алгоритм: считаем стоимость неиспользованной части текущего тарифа и
    конвертируем её в дни по новой цене (с учётом device_count).
    """
    logger.debug(f"[change_active_tariff_devices] user_id={user.id}, payload={payload.model_dump()}")
    if payload.device_count is None or payload.device_count < 1:
        raise HTTPException(status_code=400, detail="Некорректное количество устройств")

    if not user.active_tariff_id:
        raise HTTPException(status_code=400, detail="У пользователя нет активного тарифа")

    logger.debug(f"[change_active_tariff_devices] active_tariff_id={user.active_tariff_id}")
    active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
    if not active_tariff:
        raise HTTPException(status_code=404, detail="Активный тариф не найден")

    # Находим соответствующий базовый тариф по имени и количеству месяцев
    # Находим базовый тариф (если есть), иначе используем снапшот из ActiveTariffs
    original = await Tariffs.filter(name=active_tariff.name, months=active_tariff.months).first()

    current_date = date.today()
    # Остаток дней по текущей подписке
    if not user.expired_at or user.expired_at <= current_date:
        days_remaining = 0
    else:
        days_remaining = (user.expired_at - current_date).days
    logger.debug(f"[change_active_tariff_devices] days_remaining={days_remaining}, expired_at={user.expired_at}, current_date={current_date}")

    # Если количество устройств не изменилось, нет смысла пересчитывать срок подписки.
    new_device_count = int(payload.device_count)
    if new_device_count < 1:
        new_device_count = 1

    stored_limits = [value for value in (user.hwid_limit, active_tariff.hwid_limit) if value]
    current_hwid_limit = stored_limits[0] if stored_limits else 1
    if new_device_count == current_hwid_limit and all(value == new_device_count for value in stored_limits):
        logger.debug(
            "[change_active_tariff_devices] device count unchanged (%s), skipping recalculation",
            new_device_count,
        )
        return {
            "status": "ok",
            "new_expired_at": user.expired_at,
            "hwid_limit": current_hwid_limit,
            "price": active_tariff.price,
        }

    # Полный период текущего тарифа в днях
    target_date_old = current_date.replace(
        year=current_date.year + ((current_date.month + active_tariff.months - 1) // 12),
        month=((current_date.month + active_tariff.months - 1) % 12) + 1
    )
    total_days_old = (target_date_old - current_date).days
    logger.debug(f"[change_active_tariff_devices] total_days_old={total_days_old}, target_date_old={target_date_old}")

    # Денежная стоимость неиспользованной части текущего тарифа
    # Точная математика: избегаем накопления ошибки за счет Decimal и округления к ближайшему дню
    getcontext().prec = 28
    unused_percent = (Decimal(days_remaining) / Decimal(total_days_old)) if total_days_old > 0 else Decimal(0)
    active_price_dec = Decimal(active_tariff.price)
    unused_value = (unused_percent * active_price_dec)
    logger.debug(f"[change_active_tariff_devices] unused_percent={float(unused_percent):.6f}, unused_value={float(unused_value):.6f}, active_price={active_tariff.price}")

    # Рассчитываем цену тарифа для нового количества устройств
    if original:
        # Если нашли базовый тариф — используем его актуальный multiplier
        base_price = original.base_price
        multiplier = active_tariff.progressive_multiplier or original.progressive_multiplier
    else:
        # Нет в магазине — используем снапшот цены и множитель (если есть) для восстановления base_price
        multiplier = (active_tariff.progressive_multiplier or 0.9)
        # Если текущая цена была рассчитана с прогрессивным множителем, восстановим base через сумму геометрической прогрессии
        # S_n = base * (1 - m^n) / (1 - m) при n>=1
        n = max(1, active_tariff.hwid_limit)
        if n == 1:
            base_price = active_tariff.price
        else:
            denom = (1 - multiplier)
            geom_sum = (1 - (multiplier ** n)) / denom if denom != 0 else n
            base_price = active_tariff.price / geom_sum if geom_sum > 0 else active_tariff.price

    def calc_price_dec(base: Decimal, mult: Decimal, devices: int) -> Decimal:
        if devices <= 1:
            return base
        total = base
        for device_num in range(2, devices + 1):
            total += base * (mult ** (device_num - 1))
        return total

    mult_dec = Decimal(str(multiplier))
    base_dec = Decimal(str(base_price))
    new_calculated_price_dec = calc_price_dec(base_dec, mult_dec, new_device_count)
    new_calculated_price = int(new_calculated_price_dec.to_integral_value(rounding=ROUND_HALF_UP))
    logger.debug(f"[change_active_tariff_devices] new_device_count={new_device_count}, new_calculated_price={new_calculated_price}")

    # Полный период тарифа (в днях) для перерасчёта по новой цене
    months_length = active_tariff.months  # сохраняем длительность из снапшота, даже если её нет в магазине
    target_date_new = current_date.replace(
        year=current_date.year + ((current_date.month + months_length - 1) // 12),
        month=((current_date.month + months_length - 1) % 12) + 1
    )
    total_days_new = (target_date_new - current_date).days
    logger.debug(f"[change_active_tariff_devices] total_days_new={total_days_new}, target_date_new={target_date_new}")

    # Конвертируем неиспользованную стоимость в дни по новой цене
    # Пропорция: x = (unused_value * total_days_new) / new_calculated_price
    new_days = 0
    residual = Decimal(str(active_tariff.residual_day_fraction or 0))
    if new_calculated_price > 0 and total_days_new > 0 and unused_value > 0:
        new_days_dec = (unused_value * Decimal(total_days_new)) / Decimal(new_calculated_price)
        # Добавляем накопленную дробную часть
        new_days_dec_total = new_days_dec + residual
        # Берем целую часть к добавлению, остаток сохраняем
        integer_part = int(new_days_dec_total.to_integral_value(rounding=ROUND_HALF_UP))
        # Чтобы не перепрыгивать, возьмем floor через int(new_days_dec_total)
        integer_part = int(new_days_dec_total)
        fractional_part = new_days_dec_total - Decimal(integer_part)
        # Гарантируем минимум 1 день, если что-то осталось
        new_days = max(1, integer_part)
        residual = fractional_part
    logger.debug(f"[change_active_tariff_devices] computed new_days={new_days}, residual={float(residual):.6f}")

    # Обновляем пользователя и активный тариф
    user.expired_at = current_date + timedelta(days=new_days)
    user.hwid_limit = new_device_count
    await user.save()
    logger.debug(f"[change_active_tariff_devices] user updated: expired_at={user.expired_at}, hwid_limit={user.hwid_limit}")

    # Обновляем snapshot активного тарифа
    active_tariff.hwid_limit = new_device_count
    active_tariff.price = new_calculated_price
    active_tariff.progressive_multiplier = multiplier
    active_tariff.residual_day_fraction = float(residual)
    await active_tariff.save()
    logger.debug(f"[change_active_tariff_devices] active_tariff updated: id={active_tariff.id}, hwid_limit={active_tariff.hwid_limit}, price={active_tariff.price}, residual={active_tariff.residual_day_fraction}")

    # Синхронизируем с RemnaWave
    if user.remnawave_uuid:
        try:
            await remnawave_client.users.update_user(
                uuid=user.remnawave_uuid,
                expireAt=user.expired_at,
                hwidDeviceLimit=new_device_count
            )
            logger.debug(f"[change_active_tariff_devices] RemnaWave updated: uuid={user.remnawave_uuid}, expireAt={user.expired_at}, hwidDeviceLimit={new_device_count}")
        except Exception as e:
            logger.error(f"Ошибка обновления RemnaWave при смене устройств: {e}")
            # Не прерываем из-за ошибки внешнего сервиса

    return {
        "status": "ok",
        "new_expired_at": user.expired_at,
        "hwid_limit": new_device_count,
        "price": new_calculated_price
    }

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
    active_tariff_data = None

    # 2. Пытаемся получить из тарифа
    if user.active_tariff_id:
        tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if tariff:
            devices_limit = tariff.hwid_limit
            source = f"тарифу ({tariff.name})"
            # Добавляем информацию об активном тарифе в ответ
            active_tariff_data = {
                "id": tariff.id,
                "name": tariff.name,
                "months": tariff.months,
                "price": tariff.price,
                "hwid_limit": tariff.hwid_limit
            }
    
    # 3. Личное значение из БД имеет наивысший приоритет
    if user.hwid_limit is not None:
        devices_limit = user.hwid_limit
        source = "личной настройке в БД"

    logger.debug(f"Итоговый лимит устройств для пользователя {user.id} по семейной ссылке установлен по {source}: {devices_limit}")
    user_dict["devices_limit"] = devices_limit
    user_dict["active_tariff"] = active_tariff_data

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
