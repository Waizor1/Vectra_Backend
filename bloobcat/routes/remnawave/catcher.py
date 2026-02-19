from fastapi import APIRouter, BackgroundTasks
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import asyncio
import uuid
from typing import Dict, Any, Optional

from bloobcat.bot.notifications.admin import on_activated_key, send_admin_message, write_to
from bloobcat.db.users import Users, normalize_date
from bloobcat.db.connections import Connections
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.logger import get_logger
from bloobcat.db.hwid_local import HwidDeviceLocal
from .client import RemnaWaveClient
from .activation_logic import should_trigger_registration
from .hwid_utils import extract_hwid_from_device, has_duplicate_hwid, parse_remnawave_devices
from bloobcat.settings import remnawave_settings, test_mode
from bloobcat.bot.notifications.general.referral import on_referral_registration
from bloobcat.bot.notifications.trial.revoked_hwid import notify_trial_revoked_hwid
from tortoise.expressions import F
from tortoise.exceptions import IntegrityError

router = APIRouter(prefix="/remnawave", tags=["remnawave"])
logger = get_logger("remnawave_catcher")

remnawave = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())

last_activated_info = {}
last_notification_expired = {}
user_state_cache: Dict[str, Dict[str, Any]] = {}
update_lock = asyncio.Lock()

@router.get("/webhook")
async def webhook(background_tasks: BackgroundTasks):
    """Обработчик вебхука для запуска обновления RemnaWave.

    Запускает remnawave_updater в фоне, чтобы не блокировать HTTP-ответ.
    update_lock в updater предотвращает гонки при параллельных вызовах.
    """
    logger.info("Получен запрос на вебхук, планирование remnawave_updater")
    background_tasks.add_task(remnawave_updater)
    return {"status": "ok"}


async def remnawave_updater():
    """Основной процесс синхронизации дат истечения из БД в RemnaWave
    
    ОПТИМИЗАЦИЯ: Использует батчевое обновление для минимизации нагрузки на БД.
    Вместо сохранения каждого пользователя отдельно, собирает изменения в списки
    и выполняет bulk_update в конце. Перепланирование задач происходит только
    для пользователей, которым это действительно необходимо (первая регистрация).
    """
    lock_acquired = False
    try:
        await asyncio.wait_for(update_lock.acquire(), timeout=0)
        lock_acquired = True
    except asyncio.TimeoutError:
        logger.info("Процесс обновления уже запущен, пропускаем")
        return

    start_time = datetime.now(ZoneInfo("Europe/Moscow"))
    logger.info(f"Запуск remnawave_updater в {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    updated = 0
    errors = 0
    
    # Списки для батчевого обновления
    users_to_bulk_update = []
    users_need_task_reschedule = []
    users_with_sanctions = []
    users_hwid_limit_updates = []
    users_hwid_limit_update_ids = set()
    
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

                    devices_payload = parse_remnawave_devices(raw_devices)

                    for item in devices_payload:
                        hwid_str = extract_hwid_from_device(item)
                        if not hwid_str:
                            continue
                        hwid_index.setdefault(hwid_str, set()).add(user_uuid_str)

                        try:
                            try:
                                obj, created = await HwidDeviceLocal.get_or_create(
                                    hwid=hwid_str,
                                    user_uuid=user_uuid_str,
                                    defaults={"telegram_user_id": user.id},
                                )
                            except IntegrityError:
                                obj = await HwidDeviceLocal.get_or_none(
                                    hwid=hwid_str, user_uuid=user_uuid_str
                                )
                                if not obj:
                                    raise
                                created = False
                            if obj.telegram_user_id != user.id:
                                obj.telegram_user_id = user.id
                            obj.last_seen_at = datetime.now(ZoneInfo("UTC"))
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

        # Загружаем актуальные данные expired_at, hwid_limit, active_tariff_id и флагов триала из БД для избежания race condition
        # Важно: делаем это ПОСЛЕ получения данных из RemnaWave, чтобы минимизировать окно race condition
        # (если промокод был применён во время загрузки данных из RemnaWave)
        user_ids = [u.id for u in users_with_uuid]
        fresh_users_data = await Users.filter(id__in=user_ids).values(
            'id', 'expired_at', 'hwid_limit', 'active_tariff_id', 'is_trial', 'used_trial'
        )
        fresh_data_map = {item['id']: item for item in fresh_users_data}
        logger.debug(f"Загружены актуальные данные для {len(fresh_data_map)} пользователей")

        # Выполним проверку пользователей
        not_found_users = []
        for user in users_with_uuid:
            # Получаем UUID пользователя как строку
            user_uuid_str = str(user.remnawave_uuid)
            msk_today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            
            if user_uuid_str in remnawave_users_dict:
                remnawave_user = remnawave_users_dict[user_uuid_str]
                
                try:
                    fresh_data = fresh_data_map.get(user.id) or {}
                    db_expired_at = fresh_data.get('expired_at', user.expired_at)
                    db_hwid_limit = fresh_data.get('hwid_limit', user.hwid_limit)
                    db_active_tariff_id = fresh_data.get('active_tariff_id', user.active_tariff_id)
                    db_is_trial = fresh_data.get('is_trial', user.is_trial)
                    db_used_trial = fresh_data.get('used_trial', user.used_trial)

                    # Словарь для сбора обновлений для RemnaWave
                    remnawave_updates = {}

                    # ----------------- Логика обработки подключений (onlineAt) -----------------
                    # В новой панели onlineAt может быть перенесён в userTraffic.onlineAt
                    online_at = remnawave_user.get("onlineAt") or (remnawave_user.get("userTraffic") or {}).get("onlineAt")
                    logger.debug(
                        f"Пользователь {user.id}: onlineAt={online_at}, текущий connected_at={user.connected_at}"
                    )
                    
                    if online_at:
                        new_connected_at = datetime.fromisoformat(online_at.replace('Z', '+00:00'))
                        old_connected_at = user.connected_at

                        registration_changed = False
                        connection_changed = False

                        # Флаги регистрации и санкций
                        block_registration = False
                        sanction_changed = False
                        has_paid_subscription = bool(
                            db_active_tariff_id
                            and db_expired_at
                            and normalize_date(db_expired_at) >= msk_today
                            and not db_is_trial
                        )

                        # Уже санкционирован ранее за дубль HWID — сохраняем блок, чтобы не прошло повторно
                        persisted_expired_at = db_expired_at
                        persisted_expired_date = normalize_date(persisted_expired_at)
                        is_antitwink_sanction = bool(
                            not db_is_trial
                            and db_used_trial
                            and persisted_expired_date
                            and persisted_expired_date <= msk_today
                            and not has_paid_subscription
                        )
                        if is_antitwink_sanction:
                            block_registration = True
                        if (
                            anti_twink_enabled
                            and not old_connected_at
                            and not user.is_registered
                        ):
                            current_uuid = str(user.remnawave_uuid) if user.remnawave_uuid else None
                            user_devices_hwid = (
                                [hwid for hwid, owners in hwid_index.items() if current_uuid in owners]
                                if current_uuid
                                else []
                            )
                            duplicate_hwid = (
                                bool(current_uuid) and has_duplicate_hwid(current_uuid, hwid_index)
                            )

                            if duplicate_hwid and not has_paid_subscription:
                                block_registration = True
                                user.is_trial = False
                                user.used_trial = True
                                user.expired_at = msk_today
                                sanction_changed = True
                                users_with_sanctions.append(user)
                                try:
                                    await notify_trial_revoked_hwid(user)
                                except Exception as notify_err:
                                    logger.warning(
                                        "Не удалось отправить уведомление об отзыве триала пользователю %s: %s",
                                        user.id,
                                        notify_err,
                                    )
                                try:
                                    referrer = await user.referrer()
                                    ref_text = (
                                        f"{referrer.full_name} (ID: <code>{referrer.id}</code>)"
                                        if referrer
                                        else "Отсутствует"
                                    )
                                    hwid_preview = ", ".join(user_devices_hwid[:5]) if user_devices_hwid else "—"
                                    text = f"""🚫 Отозван триал (дублирующий HWID)

👤 Пользователь: {user.full_name} ({'@'+user.username if user.username else 'нет юзернейма'})
🆔 ID: <code>{user.id}</code>
👨‍👩‍👧‍👦 Реферер: {ref_text}
🖥 HWID: {hwid_preview}
📅 expireAt → {msk_today}

#trial #hwid #antitwink"""
                                    await send_admin_message(
                                        text=text,
                                        reply_markup=await write_to(
                                            user.id, referrer.id if referrer else 0
                                        ),
                                    )
                                except Exception as admin_log_err:
                                    logger.warning(
                                        "Не удалось отправить лог в админ-чат об отзыве триала %s: %s",
                                        user.id,
                                        admin_log_err,
                                    )
                                logger.info(
                                    "Анти-твинк: HWID повтор у пользователя %s, триал отозван",
                                    user.id,
                                )

                        # Разрешаем регистрацию, только если нет блокировки (включая сохранённую анти-твинк санкцию)
                        if should_trigger_registration(
                            online_at=online_at,
                            old_connected_at=old_connected_at,
                            is_registered=user.is_registered,
                            block_registration=block_registration,
                            is_antitwink_sanction=is_antitwink_sanction,
                        ):
                            referrer = await user.referrer()
                            await on_activated_key(user.id, user.full_name, referrer_id=referrer.id if referrer else None, referrer_name=referrer.full_name if referrer else None, utm=user.utm)
                            user.is_registered = True
                            registration_changed = True
                            logger.info(f"Первое подключение пользователя {user.id}, отправлено уведомление")

                            # Partner QR "activation": when a user connects for the first time,
                            # and their utm/start param points to a QR token, increment QR activations counter.
                            try:
                                utm = (user.utm or "").strip() if hasattr(user, "utm") else ""
                                if utm.startswith("qr_"):
                                    token = utm[3:]
                                    qr = None
                                    try:
                                        qr_uuid = uuid.UUID(token) if len(token) != 32 else uuid.UUID(hex=token)
                                        qr = await PartnerQr.get_or_none(id=qr_uuid)
                                    except Exception:
                                        qr = None
                                    if not qr:
                                        qr = await PartnerQr.get_or_none(slug=token)
                                    if qr:
                                        await PartnerQr.filter(id=qr.id).update(activations_count=F("activations_count") + 1)
                            except Exception as e_qr_act:
                                logger.warning("Failed to update partner QR activations for user %s: %s", user.id, e_qr_act)

                            # TVPN referral program is days-based (not money-based).
                            # We do NOT credit any RUB bonus on registration.
                            # Optional: keep a lightweight "friend registered" notification (no balance changes).
                            if referrer:
                                try:
                                    await on_referral_registration(referrer, user)
                                    logger.info(f"Реферал зарегистрировался: уведомили реферера {referrer.id} о регистрации {user.id}")
                                except Exception as e_ref_reg:
                                    logger.warning("Не удалось отправить уведомление о регистрации реферала %s -> %s: %s", referrer.id, user.id, e_ref_reg)
                        
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
                    # Используем актуальное значение expired_at из fresh_data_map (загруженного в начале)
                    # чтобы избежать race condition (например, если промокод был применён во время работы цикла)
                    db_expire_at_date = normalize_date(db_expired_at)

                    remnawave_expire_at = datetime.fromisoformat(remnawave_user['expireAt'].replace('Z', '+00:00'))
                    today = msk_today
                    remnawave_expire_date = remnawave_expire_at.astimezone(ZoneInfo("Europe/Moscow")).date()

                    if db_expire_at_date and db_expire_at_date >= today and db_expire_at_date != remnawave_expire_date:
                        logger.debug(f"Обновление даты истечения в RemnaWave для {user.id}: {remnawave_expire_date} -> {db_expire_at_date}")
                        remnawave_updates['expireAt'] = db_expire_at_date

                    # ----------------- Логика синхронизации `hwid_limit` (двусторонняя) -----------------
                    remnawave_hwid_limit = remnawave_user.get('hwidDeviceLimit')
                    # Используем актуальное значение hwid_limit и active_tariff_id из fresh_data_map

                    hwid_changed = False
                    if remnawave_hwid_limit is not None and isinstance(remnawave_hwid_limit, int):
                        # Сценарий 1: в БД пусто, берем из RemnaWave
                        if db_hwid_limit is None:
                            # ВАЖНО: если у пользователя есть активный тариф, НЕ переносим hwid_limit из панели в БД.
                            # Источник истины для лимита в этом случае — тариф/покупка (иначе возможен race condition с webhook оплаты).
                            if db_active_tariff_id:
                                logger.debug(
                                    f"Пропуск синхронизации (RemnaWave -> БД) hwid_limit для {user.id}: active_tariff_id={db_active_tariff_id} присутствует"
                                )
                            else:
                                user.hwid_limit = remnawave_hwid_limit
                                hwid_changed = True
                                logger.info(f"Синхронизация (RemnaWave -> БД): hwid_limit для {user.id} установлен в {remnawave_hwid_limit}")
                        # Сценарий 2: в БД есть значение и оно отличается, отправляем в RemnaWave
                        elif db_hwid_limit != remnawave_hwid_limit:
                            logger.debug(f"Обновление лимита устройств в RemnaWave для {user.id}: {remnawave_hwid_limit} -> {db_hwid_limit}")
                            remnawave_updates['hwidDeviceLimit'] = db_hwid_limit
                    
                    # Добавляем пользователя в список для батчевого обновления, если hwid изменился
                    if hwid_changed:
                        if user.id not in users_hwid_limit_update_ids:
                            users_hwid_limit_updates.append(user)
                            users_hwid_limit_update_ids.add(user.id)

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

                except Exception:
                    logger.exception("Ошибка при обработке пользователя %s", user.id)
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
                # Используем bulk_update для обновления connected_at и is_registered
                await Users.bulk_update(
                    users_to_bulk_update, 
                    fields=['connected_at', 'is_registered']
                )
                logger.info(f"Батчевое обновление выполнено для {len(users_to_bulk_update)} пользователей")
            except Exception:
                logger.exception("Ошибка при батчевом обновлении пользователей")
                # Fallback: сохраняем пользователей по одному
                for user in users_to_bulk_update:
                    try:
                        await user.save(update_fields=['connected_at', 'is_registered'])
                    except Exception:
                        logger.exception("Ошибка при сохранении пользователя %s", user.id)
                        errors += 1

        if users_hwid_limit_updates:
            try:
                await Users.bulk_update(
                    users_hwid_limit_updates,
                    fields=['hwid_limit']
                )
                logger.info(f"Батчевое обновление hwid_limit выполнено для {len(users_hwid_limit_updates)} пользователей")
            except Exception:
                logger.exception("Ошибка при батчевом обновлении hwid_limit")
                for user in users_hwid_limit_updates:
                    try:
                        await user.save(update_fields=['hwid_limit'])
                    except Exception:
                        logger.exception("Ошибка при сохранении hwid_limit пользователя %s", user.id)
                        errors += 1

        # Санкционные поля (is_trial, used_trial, expired_at) сохраняются отдельно от bulk_update
        if users_with_sanctions:
            for user in users_with_sanctions:
                try:
                    await user.save(update_fields=['is_trial', 'used_trial', 'expired_at'])
                except Exception:
                    logger.exception("Ошибка при сохранении санкционных изменений пользователя %s", user.id)
                    errors += 1
        
        # Перепланируем задачи только для пользователей, которым это действительно нужно
        if users_need_task_reschedule:
            try:
                from bloobcat.scheduler import schedule_user_tasks
                for user in users_need_task_reschedule:
                    try:
                        await schedule_user_tasks(user)
                        logger.debug(f"Задачи перепланированы для пользователя {user.id}")
                    except Exception:
                        logger.exception("Ошибка при перепланировании задач для пользователя %s", user.id)
                        errors += 1
                logger.info(f"Задачи перепланированы для {len(users_need_task_reschedule)} пользователей")
            except Exception:
                logger.exception("Ошибка при импорте или выполнении schedule_user_tasks")
        
        # Вычисляем время выполнения с учётом одинаковой tz-aware метки
        elapsed = (datetime.now(ZoneInfo("Europe/Moscow")) - start_time).total_seconds()
        logger.info(f"Синхронизация завершена за {elapsed:.2f} секунд. Обновлено: {updated}, ошибок: {errors}, батчевых обновлений: {len(users_to_bulk_update)}, перепланировано задач: {len(users_need_task_reschedule)}")
        
    except Exception:
        logger.exception("Критическая ошибка в remnawave_updater")
    finally:
        if lock_acquired:
            update_lock.release()
            end_time = datetime.now(ZoneInfo("Europe/Moscow"))
            total_time = (end_time - start_time).total_seconds()
            logger.info(f"Завершение работы remnawave_updater, время выполнения: {total_time:.2f} секунд")
