from fastapi import APIRouter
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import asyncio
from typing import Dict, Any, Optional

from bloobcat.bot.notifications.admin import on_activated_key
from bloobcat.db.users import Users
from bloobcat.db.connections import Connections
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger
from bloobcat.db.hwid_local import HwidDeviceLocal
from .client import RemnaWaveClient
from bloobcat.settings import remnawave_settings, test_mode
from bloobcat.bot.notifications.general.referral import on_referral_registration
from bloobcat.bot.notifications.trial.revoked_hwid import notify_trial_revoked_hwid

router = APIRouter(prefix="/remnawave", tags=["remnawave"])
logger = get_logger("remnawave_catcher")

remnawave = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())

last_activated_info = {}
last_notification_expired = {}
user_state_cache: Dict[str, Dict[str, Any]] = {}
update_in_progress = False

@router.get("/webhook")
async def webhook():
    """Обработчик вебхука для запуска обновления RemnaWave"""
    try:
        logger.info("Получен запрос на вебхук, запуск remnawave_updater")
        await remnawave_updater()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка в обработчике вебхука: {str(e)}")
        return {"status": "error", "detail": str(e)}


async def remnawave_updater():
    """Основной процесс синхронизации дат истечения из БД в RemnaWave
    
    ОПТИМИЗАЦИЯ: Использует батчевое обновление для минимизации нагрузки на БД.
    Вместо сохранения каждого пользователя отдельно, собирает изменения в списки
    и выполняет bulk_update в конце. Перепланирование задач происходит только
    для пользователей, которым это действительно необходимо (первая регистрация).
    """
    global update_in_progress, logger
    
    if update_in_progress:
        logger.info("Процесс обновления уже запущен, пропускаем")
        return
    
    update_in_progress = True
    start_time = datetime.now(ZoneInfo("Europe/Moscow"))
    logger.info(f"Запуск remnawave_updater в {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    updated = 0
    errors = 0
    
    # Списки для батчевого обновления
    users_to_bulk_update = []
    users_need_task_reschedule = []
    
    # Константы для повторных попыток
    max_retry_time = 30  # максимальное время для повторных попыток в секундах
    retry_interval = 3   # интервал между попытками в секундах
    
    try:
        # Проверка соединения с API с повторными попытками
        remnawave_nodes = None
        start_retry_time = datetime.now()
        retry_attempt = 0
        
        while remnawave_nodes is None:
            try:
                retry_attempt += 1
                remnawave_nodes = await remnawave.nodes.get_nodes()
            except Exception as e:
                elapsed_retry_time = (datetime.now() - start_retry_time).total_seconds()
                
                if elapsed_retry_time > max_retry_time:
                    logger.error(f"Превышено максимальное время повторных попыток ({max_retry_time} сек) для API. Последняя ошибка: {str(e)}")
                    return
                
                logger.warning(f"Ошибка при проверке соединения с API (попытка {retry_attempt}): {str(e)}. Повторная попытка через {retry_interval} сек.")
                await asyncio.sleep(retry_interval)
        
        # Проверка соединения с БД с повторными попытками
        users = None
        start_retry_time = datetime.now()
        retry_attempt = 0
        
        while users is None:
            try:
                retry_attempt += 1
                users = await Users.all()
                if users is None:
                    raise Exception("Результат запроса users is None")
                logger.debug(f"Получено {len(users)} пользователей из БД")
            except Exception as e:
                elapsed_retry_time = (datetime.now() - start_retry_time).total_seconds()
                
                if elapsed_retry_time > max_retry_time:
                    logger.error(f"Превышено максимальное время повторных попыток ({max_retry_time} сек) для БД. Последняя ошибка: {str(e)}")
                    return
                
                logger.warning(f"Ошибка при получении пользователей из БД (попытка {retry_attempt}): {str(e)}. Повторная попытка через {retry_interval} сек.")
                await asyncio.sleep(retry_interval)
            
        # Проверка пользователей    
        users_with_uuid = [u for u in users if u.remnawave_uuid and hasattr(u, 'expired_at') and u.expired_at]
        if not users_with_uuid:
            logger.warning("Не найдено пользователей с UUID и датой истечения")
            return
            
        logger.debug(f"Найдено {len(users_with_uuid)} пользователей с UUID и датой истечения")
        
        # Получаем данные из RemnaWave с повторными попытками
        remnawave_users = []
        start_retry_time = datetime.now()
        retry_attempt = 0
        
        # Размер страницы и начальное смещение
        page_size = 100
        start_index = 0
        total_users = None
        
        while total_users is None or start_index < total_users:
            try:
                retry_attempt += 1
                logger.debug(f"Получение списка пользователей из RemnaWave (страница {start_index//page_size + 1})")
                
                remnawave_users_response = await remnawave.users.get_users(size=page_size, start=start_index)
                
                if not remnawave_users_response:
                    raise Exception("Пустой ответ от RemnaWave API при получении пользователей")
                    
                if "response" not in remnawave_users_response:
                    raise Exception(f"Некорректный ответ от RemnaWave API: {remnawave_users_response}")
                    
                if "users" not in remnawave_users_response["response"]:
                    raise Exception(f"В ответе от RemnaWave API отсутствует поле users: {remnawave_users_response}")
                
                # Обновляем общее количество пользователей если не было известно
                if total_users is None and "total" in remnawave_users_response["response"]:
                    total_users = remnawave_users_response["response"]["total"]
                    logger.debug(f"Всего пользователей в RemnaWave: {total_users}")
                    
                page_users = remnawave_users_response["response"]["users"]
                if page_users is None:
                    raise Exception("Поле users в ответе RemnaWave API равно None")
                
                # Добавляем пользователей текущей страницы в общий список
                remnawave_users.extend(page_users)
                logger.debug(f"Получено {len(page_users)} пользователей на странице {start_index//page_size + 1}")
                
                # Если страница пустая или мы получили меньше, чем размер страницы - прерываем
                if not page_users or len(page_users) < page_size:
                    break
                    
                # Увеличиваем смещение для следующей страницы
                start_index += page_size
                
                # Если мы получили все, прерываем цикл
                if len(remnawave_users) >= total_users:
                    break
                    
            except Exception as e:
                elapsed_retry_time = (datetime.now() - start_retry_time).total_seconds()
                
                if elapsed_retry_time > max_retry_time:
                    logger.error(f"Превышено максимальное время повторных попыток ({max_retry_time} сек) для получения пользователей RemnaWave. Последняя ошибка: {str(e)}")
                    if not remnawave_users:  # Если не получили ни одного пользователя
                        return
                    break  # Если получили хотя бы часть пользователей, продолжаем с тем, что есть
                
                logger.warning(f"Ошибка при получении списка пользователей из RemnaWave (попытка {retry_attempt}): {str(e)}. Повторная попытка через {retry_interval} сек.")
                await asyncio.sleep(retry_interval)
        
        logger.debug(f"Всего получено {len(remnawave_users)} пользователей из RemnaWave")

        # Анти-твинк: выключаем только в тестовом режиме
        anti_twink_enabled = not test_mode

        hwid_index: dict[str, set[str]] = {}
        if anti_twink_enabled:
            try:
                # 0) Берем локальный кеш, чтобы учитывать старые HWID (даже если удалены на панели)
                cached = await HwidDeviceLocal.all().values("hwid", "user_uuid")
                for item in cached:
                    hwid_value = item.get("hwid")
                    user_uuid_value = item.get("user_uuid")
                    if hwid_value and user_uuid_value:
                        hwid_index.setdefault(str(hwid_value), set()).add(str(user_uuid_value))

                # 1) Для каждого нашего пользователя тянем его устройства и сохраняем локально
                for user in users_with_uuid:
                    user_uuid_str = str(user.remnawave_uuid)
                    try:
                        raw_devices = await remnawave.users.get_user_hwid_devices(user_uuid_str)
                    except Exception as e:
                        logger.warning("Не удалось получить HWID для %s: %s", user_uuid_str, e)
                        continue

                    devices_payload = []
                    if isinstance(raw_devices, list):
                        devices_payload = [item for item in raw_devices if isinstance(item, dict)]
                    elif isinstance(raw_devices, dict):
                        data = raw_devices.get("response", raw_devices) or {}
                        maybe_devices = data.get("devices") or data.get("response") or []
                        if isinstance(maybe_devices, list):
                            devices_payload = [item for item in maybe_devices if isinstance(item, dict)]

                    for item in devices_payload:
                        hwid = item.get("hwid")
                        if not hwid:
                            continue
                        hwid_str = str(hwid)
                        hwid_index.setdefault(hwid_str, set()).add(user_uuid_str)

                        try:
                            obj, created = await HwidDeviceLocal.get_or_create(
                                hwid=hwid_str,
                                user_uuid=user_uuid_str,
                                defaults={"telegram_user_id": user.id},
                            )
                            need_update = False
                            if obj.telegram_user_id != user.id:
                                obj.telegram_user_id = user.id
                                need_update = True
                            obj.last_seen_at = datetime.now(ZoneInfo("UTC"))
                            need_update = True
                            if need_update:
                                await obj.save(update_fields=["telegram_user_id", "last_seen_at"])
                        except Exception as persist_exc:
                            logger.warning(
                                "Не удалось сохранить HWID %s/%s локально: %s",
                                hwid_str,
                                user_uuid_str,
                                persist_exc,
                            )

                logger.debug("Индекс HWID собран (лок+панель): %s записей", len(hwid_index))
            except Exception as e:
                anti_twink_enabled = False
                logger.warning(
                    "Не удалось собрать индекс HWID, анти-твинк отключен для цикла: %s",
                    e,
                )
        
        # Создаем словарь с UUID в качестве ключей
        remnawave_users_dict = {}
        for user in remnawave_users:
            uuid_key = user.get('uuid')
            if uuid_key:
                remnawave_users_dict[uuid_key] = user
                
        # Выполним проверку пользователей
        not_found_users = []
        for user in users_with_uuid:
            # Получаем UUID пользователя как строку
            user_uuid_str = str(user.remnawave_uuid)
            msk_today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            
            if user_uuid_str in remnawave_users_dict:
                remnawave_user = remnawave_users_dict[user_uuid_str]
                
                try:
                    # Словарь для сбора обновлений для RemnaWave
                    remnawave_updates = {}

                    # ----------------- Логика обработки подключений (onlineAt) -----------------
                    online_at = remnawave_user.get('onlineAt')
                    logger.debug(f"Пользователь {user.id}: onlineAt={online_at}, текущий connected_at={user.connected_at}")
                    
                    if online_at:
                        new_connected_at = datetime.fromisoformat(online_at.replace('Z', '+00:00'))
                        old_connected_at = user.connected_at

                        registration_changed = False
                        connection_changed = False

                        # Анти-твинк: проверяем только для новых подключений
                        block_registration = False
                        sanction_changed = False
                        has_paid_subscription = bool(
                            user.active_tariff_id
                            and user.expired_at
                            and user.expired_at >= msk_today
                            and not user.is_trial
                        )
                        if (
                            anti_twink_enabled
                            and not old_connected_at
                            and not user.is_registered
                        ):
                            current_uuid = str(user.remnawave_uuid) if user.remnawave_uuid else None
                            user_devices_hwid = []
                            if current_uuid:
                                for hwid_value, owners in hwid_index.items():
                                    if current_uuid in owners:
                                        user_devices_hwid.append(hwid_value)

                            duplicate_hwid = False
                            for hwid_value in user_devices_hwid:
                                owners = hwid_index.get(hwid_value, set())
                                if any(owner != current_uuid for owner in owners):
                                    duplicate_hwid = True
                                    break

                            if duplicate_hwid and not has_paid_subscription:
                                block_registration = True
                                user.is_trial = False
                                user.used_trial = True
                                user.expired_at = msk_today
                                sanction_changed = True
                                try:
                                    await notify_trial_revoked_hwid(user)
                                except Exception as notify_err:
                                    logger.warning(
                                        "Не удалось отправить уведомление об отзыве триала пользователю %s: %s",
                                        user.id,
                                        notify_err,
                                    )
                                logger.info(
                                    "Анти-твинк: HWID повтор у пользователя %s, триал отозван",
                                    user.id,
                                )

                        # Разрешаем регистрацию, если не заблокирован или если уже есть оплаченная подписка
                        if not user.is_registered and (not block_registration or has_paid_subscription):
                            referrer = await user.referrer()
                            await on_activated_key(user.id, user.full_name, referrer_id=referrer.id if referrer else None, referrer_name=referrer.full_name if referrer else None, utm=user.utm)
                            user.is_registered = True
                            registration_changed = True
                            logger.info(f"Первое подключение пользователя {user.id}, отправлено уведомление")

                            if referrer and not referrer.is_partner:
                                referrer.balance += 50
                                await referrer.save()
                                await on_referral_registration(referrer, user)
                                logger.info(f"Начислено 50₽ рефереру {referrer.id} за регистрацию {user.id}")
                            elif referrer and referrer.is_partner:
                                logger.info(f"Реферер {referrer.id} партнер, бонус 50₽ не начисляется за регистрацию {user.id}")
                        
                        if not old_connected_at or new_connected_at > old_connected_at:
                            user.connected_at = new_connected_at
                            connection_changed = True
                            await Connections.process(user.id, new_connected_at.date())
                            logger.debug(f"Обновлен статус подключения для {user.id}: {new_connected_at}")
                            
                        # Добавляем пользователя в список для батчевого обновления, если были изменения
                        if connection_changed or registration_changed:
                            users_to_bulk_update.append(user)
                            # Только при первой регистрации нужно перепланировать задачи
                            if registration_changed:
                                users_need_task_reschedule.append(user)
                        elif sanction_changed:
                            # Сохраняем пользователя, чтобы зафиксировать отзыв триала
                            users_to_bulk_update.append(user)

                    # ----------------- Логика синхронизации `expired_at` (БД -> RemnaWave) -----------------
                    remnawave_expire_at = datetime.fromisoformat(remnawave_user['expireAt'].replace('Z', '+00:00'))
                    db_expire_at_date = user.expired_at
                    today = msk_today
                    remnawave_expire_date = remnawave_expire_at.astimezone(ZoneInfo("Europe/Moscow")).date()
                    
                    if db_expire_at_date >= today and db_expire_at_date != remnawave_expire_date:
                        logger.debug(f"Обновление даты истечения в RemnaWave для {user.id}: {remnawave_expire_date} -> {db_expire_at_date}")
                        remnawave_updates['expireAt'] = db_expire_at_date

                    # ----------------- Логика синхронизации `hwid_limit` (двусторонняя) -----------------
                    remnawave_hwid_limit = remnawave_user.get('hwidDeviceLimit')
                    db_hwid_limit = user.hwid_limit

                    hwid_changed = False
                    if remnawave_hwid_limit is not None and isinstance(remnawave_hwid_limit, int):
                        # Сценарий 1: в БД пусто, берем из RemnaWave
                        if db_hwid_limit is None:
                            user.hwid_limit = remnawave_hwid_limit
                            hwid_changed = True
                            logger.info(f"Синхронизация (RemnaWave -> БД): hwid_limit для {user.id} установлен в {remnawave_hwid_limit}")
                        # Сценарий 2: в БД есть значение и оно отличается, отправляем в RemnaWave
                        elif db_hwid_limit != remnawave_hwid_limit:
                            logger.debug(f"Обновление лимита устройств в RemnaWave для {user.id}: {remnawave_hwid_limit} -> {db_hwid_limit}")
                            remnawave_updates['hwidDeviceLimit'] = db_hwid_limit
                    
                    # Добавляем пользователя в список для батчевого обновления, если hwid изменился
                    if hwid_changed:
                        # Проверяем, не добавлен ли уже пользователь в список
                        if user not in users_to_bulk_update:
                            users_to_bulk_update.append(user)

                    # ----------------- Отправка всех собранных обновлений в RemnaWave -----------------
                    if remnawave_updates:
                        logger.info(f"Отправка обновлений в RemnaWave для {user.id}: {remnawave_updates}")
                        try:
                            await remnawave.users.update_user(user_uuid_str, **remnawave_updates)
                            updated += 1
                        except Exception as update_err:
                            # Если пользователь отсутствует в RemnaWave – пересоздаём и повторяем
                            if any(token in str(update_err) for token in ["User not found", "A039", "Update user error"]):
                                recreated = await user.recreate_remnawave_user()
                                if recreated:
                                    # Обновляем локальную переменную UUID
                                    user_uuid_str = str(user.remnawave_uuid)
                                    await remnawave.users.update_user(user_uuid_str, **remnawave_updates)
                                    updated += 1
                            else:
                                raise

                except Exception as e:
                    logger.error(f"Ошибка при обработке пользователя {user.id}: {str(e)}")
                    errors += 1
            else:
                logger.warning(f"Пользователь {user.id} с UUID {user_uuid_str} не найден в RemnaWave")
                not_found_users.append(user)
                
        if not_found_users:
            logger.warning(f"Найдено {len(not_found_users)} пользователей, которые есть в БД, но отсутствуют в RemnaWave")
            
            # Попробуем получить каждого пользователя напрямую по UUID
            for user in not_found_users:
                try:
                    user_response = await remnawave.users.get_user_by_uuid(user.remnawave_uuid)
                    if user_response and "response" in user_response:
                        remnawave_users_dict[user.remnawave_uuid] = user_response["response"]
                        logger.info(f"Пользователь {user.id} успешно получен по прямому запросу")
                except Exception as e:
                    logger.warning(f"Не удалось получить пользователя {user.id} по UUID {user.remnawave_uuid}: {str(e)}. Пересоздаем и сохраним новый UUID.")
                    try:
                        recreated = await user.recreate_remnawave_user()
                        if recreated:
                            logger.info(f"Пользователь {user.id} успешно пересоздан после отсутствия в списке RemnaWave")
                    except Exception as e2:
                        logger.error(f"Ошибка при пересоздании пользователя {user.id}: {e2}")
        
        # Выполняем батчевое обновление пользователей
        if users_to_bulk_update:
            try:
                # Используем bulk_update для обновления connected_at, is_registered и hwid_limit
                await Users.bulk_update(
                    users_to_bulk_update, 
                    fields=['connected_at', 'is_registered', 'hwid_limit', 'is_trial', 'used_trial', 'expired_at']
                )
                logger.info(f"Батчевое обновление выполнено для {len(users_to_bulk_update)} пользователей")
            except Exception as e:
                logger.error(f"Ошибка при батчевом обновлении пользователей: {e}")
                # Fallback: сохраняем пользователей по одному
                for user in users_to_bulk_update:
                    try:
                        await user.save()
                    except Exception as save_error:
                        logger.error(f"Ошибка при сохранении пользователя {user.id}: {save_error}")
                        errors += 1
        
        # Перепланируем задачи только для пользователей, которым это действительно нужно
        if users_need_task_reschedule:
            try:
                from bloobcat.scheduler import schedule_user_tasks
                for user in users_need_task_reschedule:
                    try:
                        await schedule_user_tasks(user)
                        logger.debug(f"Задачи перепланированы для пользователя {user.id}")
                    except Exception as e:
                        logger.error(f"Ошибка при перепланировании задач для пользователя {user.id}: {e}")
                        errors += 1
                logger.info(f"Задачи перепланированы для {len(users_need_task_reschedule)} пользователей")
            except Exception as e:
                logger.error(f"Ошибка при импорте или выполнении schedule_user_tasks: {e}")
        
        # Вычисляем время выполнения с учётом одинаковой tz-aware метки
        elapsed = (datetime.now(ZoneInfo("Europe/Moscow")) - start_time).total_seconds()
        logger.info(f"Синхронизация завершена за {elapsed:.2f} секунд. Обновлено: {updated}, ошибок: {errors}, батчевых обновлений: {len(users_to_bulk_update)}, перепланировано задач: {len(users_need_task_reschedule)}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка в remnawave_updater: {str(e)}")
    finally:
        update_in_progress = False
        # Лог завершения с использованием той же tz-aware даты
        end_time = datetime.now(ZoneInfo("Europe/Moscow"))
        total_time = (end_time - start_time).total_seconds()
        logger.info(f"Завершение работы remnawave_updater, время выполнения: {total_time:.2f} секунд")
