import asyncio
from datetime import date, timedelta
from typing import Dict, Any, Optional
import uuid

from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.bot.notifications.admin import on_activated_key
from bloobcat.logger import get_logger
from bloobcat.settings import remnawave_settings

logger = get_logger("processing.remnawave")

remnawave_client_instance: Optional[RemnaWaveClient] = None

def get_remnawave_client_proc() -> RemnaWaveClient:
    """Возвращает синглтон экземпляр RemnaWaveClient для процессора."""
    global remnawave_client_instance
    if remnawave_client_instance is None:
        remnawave_client_instance = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
        logger.debug("Создан экземпляр RemnaWaveClient")
    return remnawave_client_instance

async def process_user_safe(user: Users, remnawave_users_dict: Dict = None) -> bool:
    """Безопасная обработка пользователя с возвратом статуса выполнения."""
    try:
        await process_user(user, remnawave_users_dict)
        return True
    except Exception as e:
        logger.error(f"Ошибка при обработке пользователя {user.id}: {str(e)}", exc_info=True)
        return False

async def process_user(user: Users, remnawave_users_dict: Optional[Dict] = None):
    """Обрабатывает состояние одного пользователя: регистрация, триал, синхронизация с RemnaWave."""
    if not user:
        logger.warning("Попытка обработки пустого пользователя (None)")
        return

    logger.debug(f"Обработка пользователя {user.id} ({user.name()}): зарегистрирован={user.is_registered}, срок={user.expires()}")
    
    user_updated = False

    # 1. Обработка регистрации и триала для новых пользователей
    if user.connected_at and not user.is_registered:
        registered_now = await _handle_new_user_registration(user)
        if registered_now:
             user_updated = True

    # 2. Синхронизация статуса с RemnaWave
    if user.is_registered:
        status_updated = await _synchronize_remnawave_status(user, remnawave_users_dict)
        if status_updated:
             user_updated = True
    else:
        logger.debug(f"Пользователь {user.id} еще не зарегистрирован, синхронизация статуса RemnaWave пропущена.")

    if user_updated:
        logger.info(f"Обновлено состояние пользователя {user.id}")

async def _handle_new_user_registration(user: Users) -> bool:
    """Обрабатывает регистрацию нового пользователя, назначает триал и создает в RemnaWave."""
    logger.debug(f"Активация ключа для нового пользователя {user.id}")
    
    # Проверяем платежи
    has_payments = await ProcessedPayments.filter(
        user_id=user.id,
        status="succeeded",
        processed_at__gte=user.created_at
    ).exists()
    logger.info(f"Проверка платежей для нового пользователя {user.id}: has_payments={has_payments}")

    # Назначаем триал, если нет платежей и подписка не активна
    if (not user.expired_at or user.expired_at < date.today()) and not has_payments:
        if not user.used_trial:
            user.expired_at = date.today() + timedelta(3)
            user.is_trial = True
            user.used_trial = True
            logger.info(f"Установлен пробный период для пользователя {user.id} до {user.expired_at}")
        else:
            logger.info(f"Пользователь {user.id} уже использовал пробный период.")
    elif has_payments:
        logger.info(f"У пользователя {user.id} есть платежи, пробный период не устанавливается.")

    # Создаем пользователя в RemnaWave, если его там еще нет
    if not user.remnawave_uuid:
        try:
            remnawave = get_remnawave_client_proc()
            
            # Базовый лимит устройств
            hwid_limit = 1

            # Если есть платежи, получаем лимит из тарифа
            if has_payments:
                payment = await ProcessedPayments.filter(
                    user_id=user.id,
                    status="succeeded"
                ).first()
                if payment and payment.tariff:
                    # Используем hwid_limit из тарифа, если он задан
                    hwid_limit = getattr(payment.tariff, 'hwid_limit', 1)
                    logger.info(f"Установлен лимит устройств {hwid_limit} из тарифа {payment.tariff.name}")
            
            response = await remnawave.users.create_user(
                username=str(user.id),
                expire_at=user.expired_at,
                telegram_id=user.id,
                email=user.email,
                description=f"Telegram: {user.name()}",
                hwid_device_limit=hwid_limit
            )
            user.remnawave_uuid = response["response"]["uuid"]
            logger.info(f"Создан пользователь в RemnaWave {user.id} с UUID: {user.remnawave_uuid}")
        except Exception as e:
            logger.error(f"Ошибка при создании пользователя {user.id} в RemnaWave: {str(e)}")
            raise

    # Регистрируем пользователя
    user.is_registered = True
    await user.save()
    logger.info(f"Пользователь {user.id} успешно зарегистрирован (is_registered=True).")

    # Отправляем уведомление админу
    try:
        referrer = await user.referrer()
        await on_activated_key(
            user.id,
            user.name(),
            referrer_id=referrer.id if referrer else None,
            referrer_name=referrer.name() if referrer else None,
        )
        logger.info(f"Уведомление об активации отправлено для пользователя {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об активации для {user.id}: {str(e)}")

    return True

async def _synchronize_remnawave_status(user: Users, remnawave_users_dict: Optional[Dict] = None) -> bool:
    """Проверяет статус пользователя в RemnaWave и применяет ограничения при необходимости."""
    if not user.remnawave_uuid:
        logger.warning(f"У пользователя {user.id} нет UUID RemnaWave")
        return False

    remnawave = get_remnawave_client_proc()
    user_updated = False
    current_status = "unknown"

    try:
        if remnawave_users_dict is not None and str(user.remnawave_uuid) in remnawave_users_dict:
            current_user = remnawave_users_dict[str(user.remnawave_uuid)]
            current_status = current_user.get("status", "unknown")
            logger.debug(f"Найден статус '{current_status}' для пользователя {user.id} в кеше RemnaWave")
        else:
            is_full_users_list_provided = remnawave_users_dict is not None

            if is_full_users_list_provided:
                logger.debug(f"Пользователь {user.id} отсутствует в RemnaWave (проверено по предоставленному словарю)")
                current_status = "not_found"
            else:
                try:
                    current_user = await remnawave.users.get_user_by_uuid(user.remnawave_uuid)
                    current_status = current_user["response"].get("status", "unknown")
                    logger.debug(f"Получен статус '{current_status}' для пользователя {user.id} из API RemnaWave")
                except Exception as e:
                    if "404" in str(e):
                        logger.debug(f"Пользователь {user.id} не найден в RemnaWave (ответ API)")
                        current_status = "not_found"
                    else:
                        logger.error(f"Ошибка при получении статуса пользователя {user.id} из RemnaWave: {str(e)}")
                        current_status = "unknown"

    except Exception as e:
        logger.error(f"Ошибка при проверке статуса пользователя {user.id} в RemnaWave: {str(e)}")
        current_status = "unknown"

    # Логика применения ограничений
    subscription_days_left = user.expires()
    should_be_limited = subscription_days_left == 0

    if current_status == "not_found":
        # Если пользователь не найден в RemnaWave, пытаемся создать его
        try:
            response = await remnawave.users.create_user(
                username=str(user.id),
                expire_at=user.expired_at,
                telegram_id=user.id,
                email=user.email,
                description=f"Telegram: {user.name()}"
            )
            user.remnawave_uuid = response["response"]["uuid"]
            user.last_action = "limit" if should_be_limited else "unlimit"
            await user.save()
            user_updated = True
            logger.info(f"Создан новый пользователь в RemnaWave с UUID: {user.remnawave_uuid}")
        except Exception as e:
            logger.error(f"Ошибка при создании пользователя в RemnaWave: {str(e)}")

    elif current_status != "unknown":
        is_limited = current_status in ["DISABLED", "LIMITED", "EXPIRED"]

        if should_be_limited and not is_limited:
            try:
                await remnawave.users.update_user(
                    uuid=str(user.remnawave_uuid),
                    status="DISABLED"
                )
                user.last_action = "limit"
                await user.save()
                user_updated = True
                logger.info(f"Установлено ограничение для пользователя {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при установке ограничения для {user.id}: {str(e)}")

        elif not should_be_limited and is_limited:
            try:
                await remnawave.users.update_user(
                    uuid=str(user.remnawave_uuid),
                    status="ACTIVE"
                )
                user.last_action = "unlimit"
                await user.save()
                user_updated = True
                logger.info(f"Снято ограничение для пользователя {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при снятии ограничения для {user.id}: {str(e)}")

    return user_updated 