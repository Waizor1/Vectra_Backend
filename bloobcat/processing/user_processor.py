import asyncio
from datetime import date, timedelta
from typing import Dict, Any, Optional

from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments
from bloobcat.routes.marzban.client import MarzbanClient
from bloobcat.bot.notifications.admin import on_activated_key
from bloobcat.logger import get_logger

logger = get_logger("processing.user")

# Создаем экземпляр клиента Marzban здесь, чтобы не передавать его постоянно
# Используем ленивую инициализацию, как в users.py
marzban_client_instance: Optional[MarzbanClient] = None

def get_marzban_client_proc() -> MarzbanClient:
    """Возвращает синглтон экземпляр MarzbanClient для процессора."""
    global marzban_client_instance
    if marzban_client_instance is None:
        from bloobcat.routes.marzban.client import MarzbanClient # Локальный импорт
        marzban_client_instance = MarzbanClient()
        logger.info("Экземпляр MarzbanClient создан для user_processor")
    return marzban_client_instance

async def process_user_safe(user: Users, marzban_users_dict: Dict = None) -> bool:
    """Безопасная обработка пользователя с возвратом статуса выполнения."""
    try:
        await process_user(user, marzban_users_dict)
        return True
    except Exception as e:
        logger.error(f"Ошибка при безопасной обработке пользователя {user.id}: {str(e)}", exc_info=True)
        return False

async def process_user(user: Users, marzban_users_dict: Optional[Dict] = None):
    """Обрабатывает состояние одного пользователя: регистрация, триал, синхронизация с Marzban."""
    if not user:
        logger.warning("Попытка обработки пустого пользователя (None)")
        return

    logger.info(f"Обработка пользователя {user.id} ({user.name()}): connected_at={user.connected_at}, is_registered={user.is_registered}, last_action={user.last_action}, expires()={user.expires()}")
    
    user_updated = False # Флаг, что пользователь был изменен

    # 1. Обработка регистрации и триала для новых пользователей
    if user.connected_at and not user.is_registered:
        registered_now = await _handle_new_user_registration(user)
        if registered_now:
             user_updated = True # Пользователь был обновлен в _handle_new_user_registration

    # 2. Синхронизация статуса с Marzban (limit/unlimit)
    # Выполняется только если пользователь зарегистрирован
    if user.is_registered:
        status_updated = await _synchronize_marzban_status(user, marzban_users_dict)
        if status_updated:
             user_updated = True
    else:
        logger.debug(f"Пользователь {user.id} еще не зарегистрирован, синхронизация статуса Marzban пропущена.")

    # Сохраняем пользователя один раз в конце, если были изменения
    # Этот save() не нужен, так как save() вызывается внутри _handle_new_user_registration и _synchronize_marzban_status
    # if user_updated:
    #     await user.save()
    #     logger.info(f"Пользователь {user.id} сохранен после обработки.")
    # else:
    #     logger.debug(f"Изменений для пользователя {user.id} не было, сохранение не требуется.")
    logger.info(f"Обработка пользователя {user.id} ({user.name()}) завершена.")

async def _handle_new_user_registration(user: Users) -> bool:
    """Обрабатывает регистрацию нового пользователя, назначает триал и отправляет уведомления."""
    logger.info(f"Активация ключа для нового пользователя {user.id}")
    
    # Проверяем платежи
    has_payments = await ProcessedPayments.filter(
        user_id=user.id,
        status="succeeded",
        processed_at__gte=user.created_at
    ).exists()
    logger.info(f"Проверка платежей для нового пользователя {user.id}: has_payments={has_payments}")

    trial_assigned = False
    # Назначаем триал, если нет платежей и подписка не активна
    if (not user.expired_at or user.expired_at < date.today()) and not has_payments:
        if not user.used_trial:
            user.expired_at = date.today() + timedelta(3)
            user.is_trial = True
            user.used_trial = True
            trial_assigned = True
            logger.info(f"Установлен пробный период для пользователя {user.id} до {user.expired_at}")
        else:
            logger.info(f"Пользователь {user.id} уже использовал пробный период.")
    elif has_payments:
        logger.info(f"У пользователя {user.id} есть платежи, пробный период не устанавливается.")
    
    # Регистрируем пользователя
    user.is_registered = True
    await user.save() # Сохраняем сразу после регистрации и установки триала
    logger.info(f"Пользователь {user.id} успешно зарегистрирован (is_registered=True).")

    # Логируем состояние после регистрации
    logger.info(f"Состояние пользователя {user.id} после регистрации: "
                f"expired_at={user.expired_at}, expires()={user.expires()}, "
                f"is_trial={user.is_trial}, used_trial={user.used_trial}, "
                f"last_action={user.last_action}")

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
        # Не перевыбрасываем, чтобы не прервать процесс

    return True # Возвращаем True, так как пользователь был обновлен (как минимум is_registered)

async def _synchronize_marzban_status(user: Users, marzban_users_dict: Optional[Dict] = None) -> bool:
    """Проверяет статус пользователя в Marzban и применяет limit/unlimit при необходимости."""
    marzban = get_marzban_client_proc()
    user_updated = False # Флаг, что статус пользователя в БД был изменен
    user_id_str = str(user.id)
    current_status = "unknown"

    # Получаем текущий статус из Marzban (сначала из кеша, потом по API)
    try:
        if marzban_users_dict is not None and user_id_str in marzban_users_dict:
            current_user_in_marzban = marzban_users_dict[user_id_str]
            current_status = current_user_in_marzban.get("status", "unknown")
            logger.debug(f"Найден статус '{current_status}' для пользователя {user_id_str} в кеше Marzban")
        else:
            # Определяем, был ли предоставлен полный словарь пользователей Marzban
            is_full_users_list_provided = marzban_users_dict is not None 
            
            if is_full_users_list_provided:
                # Если словарь был, но юзера там нет - значит его нет в Marzban
                logger.debug(f"Пользователь {user_id_str} отсутствует в Marzban (проверено по предоставленному словарю)")
                current_status = "not_found"
            else:
                 # Если словаря не было, делаем запрос к API
                logger.debug(f"Словарь пользователей Marzban не предоставлен или пуст, запрашиваем статус для {user_id_str} отдельно")
                try:
                    current_user_in_marzban = await marzban.users.get_user(user)
                    current_status = current_user_in_marzban.get("status", "unknown")
                    logger.debug(f"Получен статус '{current_status}' для пользователя {user_id_str} из API Marzban")
                except ValueError as e:
                    if "User not found" in str(e):
                        logger.debug(f"Пользователь {user_id_str} не найден в Marzban (ответ API)")
                        current_status = "not_found"
                    else:
                        # Другая ошибка ValueError при получении пользователя
                        logger.error(f"Ошибка ValueError при получении пользователя {user_id_str} из Marzban: {str(e)}")
                        current_status = "unknown"
                except Exception as e:
                    logger.warning(f"Не удалось получить статус пользователя {user_id_str} из Marzban: {str(e)}")
                    current_status = "unknown"

    except Exception as e:
        logger.warning(f"Ошибка при определении статуса пользователя {user_id_str} в Marzban: {str(e)}")
        current_status = "unknown"

    # Логика применения лимитов
    subscription_days_left = user.expires()
    # Ограничивать нужно, если подписка истекла (0 дней)
    # Если дата не установлена (None), то ограничивать не нужно.
    should_be_limited = subscription_days_left == 0 

    if current_status == "not_found":
        # Если пользователь не найден в Marzban, обновляем только last_action в БД
        if should_be_limited and user.last_action != "limit":
            logger.debug(f"Пользователь {user.id} не найден в Marzban, но подписка истекла. Обновляем last_action='limit' в БД")
            user.last_action = "limit"
            await user.save()
            user_updated = True
        elif not should_be_limited and user.last_action != "unlimit":
            logger.debug(f"Пользователь {user.id} не найден в Marzban, но подписка активна. Обновляем last_action='unlimit' в БД")
            user.last_action = "unlimit"
            await user.save()
            user_updated = True
        else:
            logger.debug(f"Пользователь {user.id} не найден в Marzban, last_action ('{user.last_action}') соответствует статусу подписки.")
    
    elif current_status != "unknown":
        # Если статус в Marzban известен
        is_limited_in_marzban = current_status == "disabled"

        if should_be_limited and not is_limited_in_marzban:
            # Нужно ограничить: подписка истекла, но в Marzban активен
            logger.info(f"Установка лимита для пользователя {user.id} (подписка истекла, статус Marzban: {current_status})")
            try:
                await marzban.users.limit_user(user)
                user.last_action = "limit"
                await user.save()
                user_updated = True
                logger.info(f"Лимит успешно установлен для {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при установке лимита для {user.id}: {str(e)}")
        
        elif not should_be_limited and is_limited_in_marzban:
             # Нужно снять лимит: подписка активна, но в Marzban ограничен
            logger.info(f"Снятие лимита для пользователя {user.id} (подписка активна, статус Marzban: {current_status})")
            try:
                await marzban.users.unlimit_user(user)
                user.last_action = "unlimit"
                await user.save()
                user_updated = True
                logger.info(f"Лимит успешно снят для {user.id}")
            except Exception as e:
                 logger.error(f"Ошибка при снятии лимита для {user.id}: {str(e)}")
        
        elif user.last_action == "limit" and not should_be_limited and not is_limited_in_marzban:
             # Случай: подписка продлена, но last_action еще 'limit', а в Marzban уже 'active'
             # (например, если unlimit_user сработал, но user.save() не прошел)
             logger.info(f"Статус Marzban для {user.id} уже 'active', обновляем last_action='unlimit' в БД.")
             user.last_action = "unlimit"
             await user.save()
             user_updated = True
        
        elif user.last_action == "unlimit" and should_be_limited and is_limited_in_marzban:
             # Случай: подписка истекла, но last_action еще 'unlimit', а в Marzban уже 'disabled'
             # (например, если limit_user сработал, но user.save() не прошел)
             logger.info(f"Статус Marzban для {user.id} уже 'disabled', обновляем last_action='limit' в БД.")
             user.last_action = "limit"
             await user.save()
             user_updated = True
             
        else:
            logger.debug(f"Статус пользователя {user.id} ('{current_status}') и last_action ('{user.last_action}') соответствуют статусу подписки (истекла: {should_be_limited}). Действий не требуется.")

    else: # current_status == "unknown"
        logger.warning(f"Не удалось определить статус пользователя {user.id} в Marzban. Синхронизация статуса пропускается.")

    return user_updated 

async def reset_expired_users():
    """Сброс подписок и VPN для пользователей с истекшей подпиской."""
    logger.info("Запуск сброса подписок для истекших пользователей")
    try:
        # Получаем только зарегистрированных пользователей для проверки
        users_to_check = await Users.filter(is_registered=True).all()
        logger.info(f"Найдено {len(users_to_check)} зарегистрированных пользователей для проверки на истечение")
        
        reset_tasks = []
        for user_db in users_to_check:
            # Проверяем, что подписка действительно истекла
            if user_db.expires() == 0:
                reset_tasks.append(reset_expired_user(user_db))
        
        if reset_tasks:
            # Запускаем задачи с ограничением конкурентности (например, 10 одновременных)
            sem = asyncio.Semaphore(10)
            async def reset_with_sem(user):
                async with sem:
                    await reset_expired_user(user)
            
            await asyncio.gather(*[reset_with_sem(user) for user in reset_tasks])
            logger.info(f"Завершена параллельная обработка {len(reset_tasks)} истекших пользователей")
        else:
            logger.info("Нет истекших пользователей для сброса")
                
        logger.info("Сброс подписок для истекших пользователей завершен")
    except Exception as e:
        logger.error(f"Критическая ошибка в reset_expired_users: {str(e)}", exc_info=True)
        # Не перевыбрасываем, т.к. это фоновая задача


async def reset_expired_user(user_db: Users):
    """Сбрасывает подписку и статус в Marzban для конкретного пользователя."""
    try:
        logger.info(f"Сброс VPN и подписки для пользователя {user_db.id} (истекла)")
        marzban = get_marzban_client_proc()
        
        # Пытаемся сбросить пользователя в Marzban (если он там есть)
        try:
            # Сначала можно попытаться установить статус 'disabled', если reset не работает
            # await marzban.users.limit_user(user_db) 
            # Или использовать API reset, если он есть и делает то, что нужно
            await marzban.users.reset_user(user_db) 
            logger.info(f"Запрос на сброс/ограничение пользователя {user_db.id} в Marzban отправлен.")
        except ValueError as e:
             if "User not found" in str(e):
                 logger.warning(f"Пользователь {user_db.id} не найден в Marzban при сбросе.")
             else:
                 logger.error(f"Ошибка ValueError при сбросе/ограничении пользователя {user_db.id} в Marzban: {str(e)}")
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при сбросе/ограничении пользователя {user_db.id} в Marzban: {str(e)}")

        # Обновляем статус в локальной БД
        user_db.is_subscribed = False
        user_db.renew_id = None
        user_db.last_action = "limit" # Устанавливаем согласованный статус
        
        if user_db.is_trial:
            user_db.is_trial = False # Сбрасываем триал, если он был активен
            logger.info(f"Сброс флага пробного периода для пользователя {user_db.id}")
        
        await user_db.save()
        logger.info(f"Статус пользователя {user_db.id} в БД обновлен (подписка истекла)")
    except Exception as e:
        logger.error(f"Ошибка при обработке сброса для пользователя {user_db.id}: {str(e)}", exc_info=True) 