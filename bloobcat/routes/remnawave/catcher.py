from fastapi import APIRouter, BackgroundTasks
from datetime import datetime, timedelta, date, timezone
from zoneinfo import ZoneInfo
import asyncio
import uuid
from typing import Dict, Any, Optional

from bloobcat.bot.notifications.admin import on_activated_key, send_admin_message, write_to, _safe_html
from bloobcat.db.users import Users, normalize_date
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.connections import Connections
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.funcs.referral_attribution import is_partner_source_utm
from bloobcat.logger import get_logger
from bloobcat.db.hwid_local import HwidDeviceLocal
from .client import RemnaWaveClient
from .activation_logic import should_trigger_registration
from .hwid_utils import (
    extract_hwid_from_device,
    is_paid_subscription_active,
    is_user_already_antitwink_sanctioned,
    parse_remnawave_devices,
)
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
update_state_lock = asyncio.Lock()
_update_in_progress = False

# Diagnostic: last run status (readable via /remnawave/status endpoint)
_last_run_status: Dict[str, Any] = {
    "last_run_at": None,
    "last_success_at": None,
    "last_error": None,
    "total_runs": 0,
    "total_errors": 0,
    "last_summary": {},
}


async def _try_start_updater_run() -> bool:
    """Atomically mark updater as running and reject concurrent starts."""
    global _update_in_progress
    async with update_state_lock:
        if _update_in_progress:
            return False
        _update_in_progress = True
        return True


async def _finish_updater_run() -> None:
    global _update_in_progress
    async with update_state_lock:
        _update_in_progress = False


def _eligible_users_for_checker(users: list[Any]) -> list[Any]:
    """Возвращает пользователей, которых нужно обрабатывать в checker-проходе.

    Важно: наличие remnawave_uuid достаточно для проверки activation/connections/HWID.
    expired_at может быть пустым у части сценариев и не должен исключать пользователя из цикла.
    """
    return [u for u in users if getattr(u, "remnawave_uuid", None)]


def _extract_online_at(remnawave_user: Dict[str, Any] | None) -> Optional[str]:
    data = remnawave_user or {}
    user_traffic = data.get("userTraffic") or {}
    return (
        data.get("onlineAt")
        or user_traffic.get("onlineAt")
        or user_traffic.get("firstConnectedAt")
    )


def _safe_parse_online_at(raw_online_at: Any) -> Optional[datetime]:
    """Parse connection timestamp without breaking the updater on bad input."""
    if not raw_online_at:
        return None
    try:
        value = str(raw_online_at).strip()
        if not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _normalize_hwid_limit(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, parsed)


def _seed_orphan_owners_from_hwid_cache(
    uuid_owner_map: Dict[str, int],
    cached_rows: list[dict],
) -> None:
    """Backfill UUID ownership from the local HWID cache.

    Old RemnaWave UUIDs can remain in `hwid_devices_local` after a local user is
    rebound or recreated. Treating those old UUIDs as a different owner creates
    false duplicate-HWID sanctions; the cache stores telegram_user_id exactly to
    keep same-owner historical UUIDs safe.
    """
    for item in cached_rows:
        user_uuid_value = item.get("user_uuid")
        cached_owner_id = item.get("telegram_user_id")
        if not user_uuid_value or not cached_owner_id:
            continue
        uuid_owner_map.setdefault(str(user_uuid_value), int(cached_owner_id))


def _user_has_hwid_device(user_uuid: str, hwid_index: Dict[str, set[str]]) -> bool:
    return any(user_uuid in owners for owners in hwid_index.values())


def _has_duplicate_hwid_for_other_owner(
    user_uuid: str,
    owner_id: int | None,
    uuid_owner_map: Dict[str, int],
    hwid_index: Dict[str, set[str]],
) -> bool:
    for owners in hwid_index.values():
        if user_uuid not in owners:
            continue
        for other_uuid in owners:
            if other_uuid == user_uuid:
                continue
            if uuid_owner_map.get(other_uuid) != owner_id:
                return True
    return False

@router.get("/status")
async def remnawave_status():
    """Diagnostic endpoint: last run status of remnawave_updater."""
    from bloobcat.bot.notifications.admin import get_admin_msg_stats
    return {
        **_last_run_status,
        "admin_notifications": get_admin_msg_stats(),
    }


@router.get("/webhook")
async def webhook(background_tasks: BackgroundTasks):
    """Обработчик вебхука для запуска обновления RemnaWave.

    Запускает remnawave_updater в фоне, чтобы не блокировать HTTP-ответ.
    update_state_lock в updater предотвращает гонки при параллельных вызовах.
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
    if not await _try_start_updater_run():
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
        users_with_uuid = _eligible_users_for_checker(users)
        logger.info(
            "Checker input: total_users=%s users_with_uuid=%s",
            len(users),
            len(users_with_uuid),
        )
        if not users_with_uuid:
            logger.warning("Не найдено пользователей с UUID")
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
        if remnawave_users:
            first_user = remnawave_users[0] or {}
            sample_online_at = _extract_online_at(first_user)
            logger.info(
                "RemnaWave API sample: has_userTraffic=%s, sample_onlineAt=%s",
                "userTraffic" in first_user,
                sample_online_at,
            )

        # Анти-твинк: выключаем только в тестовом режиме
        anti_twink_enabled = not test_mode
        logger.info("Anti-twink checker enabled=%s (test_mode=%s)", anti_twink_enabled, test_mode)

        uuid_owner_map: dict[str, int] = {}
        tracked_uuid_owner_pairs: list[tuple[str, int]] = []
        for item in users_with_uuid:
            uuid_str = str(item.remnawave_uuid)
            uuid_owner_map[uuid_str] = item.id
            tracked_uuid_owner_pairs.append((uuid_str, item.id))

        hwid_index: dict[str, set[str]] = {}
        hwid_check_available = anti_twink_enabled
        total_hwid_payload_items = 0
        users_with_any_hwid_payload = 0
        if anti_twink_enabled:
            try:
                # 0) Берем локальный кеш, чтобы учитывать старые HWID (даже если удалены на панели)
                cached = await HwidDeviceLocal.all().values(
                    "hwid",
                    "user_uuid",
                    "telegram_user_id",
                )
                for item in cached:
                    hwid_value = item.get("hwid")
                    user_uuid_value = item.get("user_uuid")
                    if hwid_value and user_uuid_value:
                        hwid_index.setdefault(str(hwid_value), set()).add(str(user_uuid_value))
                _seed_orphan_owners_from_hwid_cache(uuid_owner_map, cached)

                # 1) Для каждого tracked UUID тянем его устройства и сохраняем локально
                for user_uuid_str, owner_id in tracked_uuid_owner_pairs:
                    try:
                        raw_devices = await remnawave.users.get_user_hwid_devices(user_uuid_str)
                    except Exception as e:
                        logger.warning("Не удалось получить HWID для %s: %s", user_uuid_str, e)
                        continue

                    devices_payload = parse_remnawave_devices(raw_devices)
                    total_hwid_payload_items += len(devices_payload)
                    if devices_payload:
                        users_with_any_hwid_payload += 1

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
                                    defaults={"telegram_user_id": owner_id},
                                )
                            except IntegrityError:
                                obj = await HwidDeviceLocal.get_or_none(
                                    hwid=hwid_str, user_uuid=user_uuid_str
                                )
                                if not obj:
                                    raise
                                created = False
                            if obj.telegram_user_id != owner_id:
                                obj.telegram_user_id = owner_id
                            obj.last_seen_at = datetime.now(ZoneInfo("UTC"))
                            await obj.save(update_fields=["telegram_user_id", "last_seen_at"])
                        except Exception as persist_exc:
                            logger.warning(
                                "Не удалось сохранить HWID %s/%s локально: %s",
                                hwid_str,
                                user_uuid_str,
                                persist_exc,
                            )

                logger.info(
                    "HWID index built: unique_hwid=%s users_with_payload=%s payload_items=%s",
                    len(hwid_index),
                    users_with_any_hwid_payload,
                    total_hwid_payload_items,
                )
            except Exception as e:
                anti_twink_enabled = False
                hwid_check_available = False
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
            'id', 'expired_at', 'hwid_limit', 'active_tariff_id', 'is_trial', 'used_trial', 'key_activated'
        )
        fresh_data_map = {item['id']: item for item in fresh_users_data}
        logger.debug(f"Загружены актуальные данные для {len(fresh_data_map)} пользователей")

        # Подгружаем флаг is_promo_synthetic у активных тарифов, чтобы анти-твинк
        # не считал promo-активированные синтетические подписки за «оплаченные».
        active_tariff_ids = [
            item['active_tariff_id'] for item in fresh_users_data if item.get('active_tariff_id')
        ]
        active_tariff_synthetic_map: dict[str, bool] = {}
        if active_tariff_ids:
            rows = await ActiveTariffs.filter(id__in=active_tariff_ids).values(
                'id', 'is_promo_synthetic'
            )
            active_tariff_synthetic_map = {
                row['id']: bool(row.get('is_promo_synthetic')) for row in rows
            }

        # Выполним проверку пользователей
        not_found_users = []
        online_at_available_count = 0
        online_at_recovered_count = 0
        activation_path_entered_count = 0
        activation_notify_sent_count = 0
        activation_notify_failed_count = 0
        activation_skipped_flags_count = 0
        duplicate_hwid_detected_count = 0
        duplicate_hwid_blocked_count = 0
        duplicate_hwid_paid_skip_count = 0

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
                    db_key_activated = fresh_data.get('key_activated', user.key_activated)

                    # Словарь для сбора обновлений для RemnaWave
                    remnawave_updates = {}

                    # ----------------- Логика обработки подключений (onlineAt) -----------------
                    # В новой панели onlineAt может быть перенесён в userTraffic.onlineAt / firstConnectedAt.
                    online_at = _extract_online_at(remnawave_user)
                    logger.debug(
                        f"Пользователь {user.id}: onlineAt={online_at}, текущий connected_at={user.connected_at}"
                    )

                    # Fallback: для еще не зарегистрированных пользователей без connected_at
                    # пробуем точечный запрос по UUID, если list endpoint не вернул onlineAt.
                    if not online_at and not user.is_registered and not user.connected_at:
                        try:
                            user_detail_response = await remnawave.users.get_user_by_uuid(user_uuid_str)
                            user_detail = (
                                user_detail_response.get("response")
                                if isinstance(user_detail_response, dict)
                                else {}
                            ) or {}
                            online_at = _extract_online_at(user_detail)
                            if online_at:
                                online_at_recovered_count += 1
                                logger.info(
                                    "Recovered onlineAt via individual fetch for user %s: %s",
                                    user.id,
                                    online_at,
                                )
                        except Exception as detail_err:
                            logger.debug(
                                "Could not fetch individual onlineAt for user %s: %s",
                                user.id,
                                detail_err,
                            )

                    registration_changed = False
                    connection_changed = False
                    sanction_changed = False

                    if online_at:
                        online_at_available_count += 1
                        new_connected_at = _safe_parse_online_at(online_at)
                        if not new_connected_at:
                            logger.warning(
                                "Invalid onlineAt for user %s: %r (type=%s) — skipping activation/connection, proceeding to sync",
                                user.id,
                                online_at,
                                type(online_at).__name__,
                            )
                        else:
                            old_connected_at = user.connected_at

                            # Флаги регистрации и санкций
                            block_registration = False
                            is_promo_synthetic_active_tariff = bool(
                                db_active_tariff_id
                                and active_tariff_synthetic_map.get(db_active_tariff_id)
                            )

                            # Уже санкционирован ранее за дубль HWID — сохраняем блок, чтобы не прошло повторно
                            persisted_expired_at = db_expired_at
                            persisted_expired_date = normalize_date(persisted_expired_at)
                            has_paid_subscription = is_paid_subscription_active(
                                active_tariff_id=db_active_tariff_id,
                                expired_date=persisted_expired_date,
                                is_trial=db_is_trial,
                                today=msk_today,
                                is_promo_synthetic=is_promo_synthetic_active_tariff,
                            )
                            is_antitwink_sanction = is_user_already_antitwink_sanctioned(
                                is_trial=db_is_trial,
                                used_trial=db_used_trial,
                                expired_date=persisted_expired_date,
                                today=msk_today,
                                has_paid_subscription=has_paid_subscription,
                            )
                            if is_antitwink_sanction:
                                block_registration = True
                            if (
                                anti_twink_enabled
                                and not db_key_activated
                                and not is_antitwink_sanction
                            ):
                                current_uuid = str(user.remnawave_uuid) if user.remnawave_uuid else None
                                current_owner_id = (
                                    uuid_owner_map.get(current_uuid) if current_uuid else None
                                )
                                user_devices_hwid = (
                                    [hwid for hwid, owners in hwid_index.items() if current_uuid in owners]
                                    if current_uuid
                                    else []
                                )
                                duplicate_hwid = (
                                    bool(current_uuid)
                                    and _has_duplicate_hwid_for_other_owner(
                                        current_uuid,
                                        current_owner_id,
                                        uuid_owner_map,
                                        hwid_index,
                                    )
                                )

                                if duplicate_hwid and not has_paid_subscription:
                                    duplicate_hwid_detected_count += 1
                                    duplicate_hwid_blocked_count += 1
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
                                            f"{_safe_html(referrer.full_name)} (ID: <code>{referrer.id}</code>)"
                                            if referrer
                                            else "Отсутствует"
                                        )
                                        hwid_preview = ", ".join(user_devices_hwid[:5]) if user_devices_hwid else "—"
                                        user_name_safe = _safe_html(user.full_name)
                                        user_username_safe = _safe_html(f"@{user.username}") if user.username else "нет юзернейма"
                                        hwid_preview_safe = _safe_html(hwid_preview)
                                        text = f"""🚫 Отозван триал (дублирующий HWID)

👤 Пользователь: {user_name_safe} ({user_username_safe})
🆔 ID: <code>{user.id}</code>
👨‍👩‍👧‍👦 Реферер: {ref_text}
🖥 HWID: {hwid_preview_safe}
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
                                elif duplicate_hwid and has_paid_subscription:
                                    duplicate_hwid_detected_count += 1
                                    duplicate_hwid_paid_skip_count += 1
                                    logger.info(
                                        "HWID duplicate detected for paid user %s: sanction skipped",
                                        user.id,
                                    )

                            # Эталонная логика: activation/admin log должен срабатывать
                            # только после появления первого HWID, а не по одному onlineAt.
                            if test_mode:
                                has_hwid_device = True
                            elif hwid_check_available:
                                has_hwid_device = _user_has_hwid_device(
                                    user_uuid_str,
                                    hwid_index,
                                )
                            else:
                                has_hwid_device = False

                            # Разрешаем регистрацию, только если нет блокировки (включая сохранённую анти-твинк санкцию)
                            should_register = should_trigger_registration(
                                online_at=online_at,
                                old_connected_at=old_connected_at,
                                is_registered=user.is_registered,
                                block_registration=block_registration,
                                is_antitwink_sanction=is_antitwink_sanction,
                                key_activated=db_key_activated,
                                has_hwid_device=has_hwid_device,
                            )
                            if should_register:
                                activation_path_entered_count += 1
                                referrer = await user.referrer()
                                delivered = await on_activated_key(
                                    user.id,
                                    user.full_name,
                                    referrer_id=referrer.id if referrer else None,
                                    referrer_name=referrer.full_name if referrer else None,
                                    utm=user.utm,
                                )
                                user.key_activated = True
                                user.is_registered = True
                                registration_changed = True
                                if delivered:
                                    activation_notify_sent_count += 1
                                    logger.info(
                                        f"Первое зарегистрированное HWID-устройство пользователя {user.id}, admin log доставлен"
                                    )
                                else:
                                    activation_notify_failed_count += 1
                                    logger.warning(
                                        "Первое зарегистрированное HWID-устройство пользователя %s зафиксировано, "
                                        "но admin log не доставлен",
                                        user.id,
                                    )

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

                                # Vectra Connect referral program is days-based (not money-based).
                                # We do NOT credit any RUB bonus on registration.
                                # Optional: keep a lightweight "friend registered" notification (no balance changes).
                                if referrer and not is_partner_source_utm(getattr(user, "utm", None)):
                                    try:
                                        await on_referral_registration(referrer, user)
                                        logger.info(f"Реферал зарегистрировался: уведомили реферера {referrer.id} о регистрации {user.id}")
                                    except Exception as e_ref_reg:
                                        logger.warning("Не удалось отправить уведомление о регистрации реферала %s -> %s: %s", referrer.id, user.id, e_ref_reg)
                            else:
                                activation_skipped_flags_count += 1
                                logger.debug(
                                    "Activation skipped user=%s reason_flags: key_activated=%s has_hwid_device=%s is_registered=%s block_registration=%s antitwink_sanction=%s has_paid_subscription=%s old_connected_at=%s",
                                    user.id,
                                    db_key_activated,
                                    has_hwid_device,
                                    user.is_registered,
                                    block_registration,
                                    is_antitwink_sanction,
                                    has_paid_subscription,
                                    bool(old_connected_at),
                                )

                            if not old_connected_at or new_connected_at > old_connected_at:
                                user.connected_at = new_connected_at
                                connection_changed = True
                                await Connections.process(user.id, new_connected_at.date())
                                logger.debug(f"Обновлен статус подключения для {user.id}: {new_connected_at}")

                    # Добавляем пользователя в список для батчевого обновления, если были изменения
                    if connection_changed or registration_changed:
                        users_to_bulk_update.append(user)
                        if registration_changed:
                            users_need_task_reschedule.append(user)
                    elif sanction_changed:
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
                    # For device-per-user users the legacy RemnaWave user's HWID
                    # limit is derived from live inventory, not copied from the
                    # subscription total. Do not let the batch checker overwrite it.
                    if user.is_device_per_user_enabled():
                        logger.debug(
                            'Skip legacy hwid_limit checker sync for device-per-user user=%s',
                            user.id,
                        )
                    else:
                        remnawave_hwid_limit_raw = remnawave_user.get('hwidDeviceLimit')
                        remnawave_hwid_limit = _normalize_hwid_limit(remnawave_hwid_limit_raw)
                        normalized_db_hwid_limit = _normalize_hwid_limit(db_hwid_limit)
                        remnawave_limit_invalid = (
                            remnawave_hwid_limit_raw is not None
                            and remnawave_hwid_limit is not None
                            and remnawave_hwid_limit_raw != remnawave_hwid_limit
                        )

                        hwid_changed = False
                        if db_hwid_limit is not None and normalized_db_hwid_limit != db_hwid_limit:
                            user.hwid_limit = normalized_db_hwid_limit
                            db_hwid_limit = normalized_db_hwid_limit
                            hwid_changed = True
                            logger.warning(
                                'Invalid local hwid_limit normalized: user=%s old=%s new=%s',
                                user.id,
                                fresh_data.get('hwid_limit', user.hwid_limit),
                                normalized_db_hwid_limit,
                            )

                        if remnawave_hwid_limit is not None:
                            if db_hwid_limit is None:
                                if db_active_tariff_id:
                                    logger.debug(
                                        'Skip RemnaWave->DB hwid sync for user=%s because active_tariff_id=%s',
                                        user.id,
                                        db_active_tariff_id,
                                    )
                                else:
                                    user.hwid_limit = remnawave_hwid_limit
                                    db_hwid_limit = remnawave_hwid_limit
                                    hwid_changed = True
                                    logger.info(
                                        'Synced hwid_limit RemnaWave->DB for user=%s to %s',
                                        user.id,
                                        remnawave_hwid_limit,
                                    )
                                    if remnawave_limit_invalid:
                                        remnawave_updates['hwidDeviceLimit'] = remnawave_hwid_limit
                            elif normalized_db_hwid_limit != remnawave_hwid_limit or remnawave_limit_invalid:
                                logger.debug(
                                    'Syncing hwid limit to RemnaWave for user=%s: remote=%s local=%s',
                                    user.id,
                                    remnawave_hwid_limit_raw,
                                    normalized_db_hwid_limit,
                                )
                                remnawave_updates['hwidDeviceLimit'] = normalized_db_hwid_limit
                        elif normalized_db_hwid_limit is not None:
                            remnawave_updates['hwidDeviceLimit'] = normalized_db_hwid_limit
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
        
        summary = {
            "total_users_db": len(users),
            "users_with_uuid": len(users_with_uuid),
            "remnawave_users_fetched": len(remnawave_users),
            "onlineAt_available": online_at_available_count,
            "onlineAt_recovered_individual": online_at_recovered_count,
            "activation_candidates": activation_path_entered_count,
            "activation_notify_sent": activation_notify_sent_count,
            "activation_notify_failed": activation_notify_failed_count,
            "activation_skipped": activation_skipped_flags_count,
            "duplicate_hwid_detected": duplicate_hwid_detected_count,
            "duplicate_hwid_blocked": duplicate_hwid_blocked_count,
            "duplicate_hwid_paid_skip": duplicate_hwid_paid_skip_count,
            "not_found_in_remnawave": len(not_found_users),
            "anti_twink_enabled": anti_twink_enabled,
            "hwid_unique_count": len(hwid_index),
        }
        logger.info("Checker summary: %s", summary)

        # Выполняем батчевое обновление пользователей
        if users_to_bulk_update:
            try:
                # key_activated фиксирует факт первой HWID-активации; без него
                # admin/referral уведомления повторятся на следующих циклах.
                await Users.bulk_update(
                    users_to_bulk_update, 
                    fields=['connected_at', 'is_registered', 'key_activated']
                )
                logger.info(f"Батчевое обновление выполнено для {len(users_to_bulk_update)} пользователей")
            except Exception:
                logger.exception("Ошибка при батчевом обновлении пользователей")
                # Fallback: сохраняем пользователей по одному
                for user in users_to_bulk_update:
                    try:
                        await user.save(update_fields=['connected_at', 'is_registered', 'key_activated'])
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
        summary["elapsed_seconds"] = round(elapsed, 2)
        summary["remnawave_updated"] = updated
        summary["errors"] = errors
        summary["bulk_updates"] = len(users_to_bulk_update)
        summary["tasks_rescheduled"] = len(users_need_task_reschedule)
        logger.info(f"Синхронизация завершена за {elapsed:.2f} секунд. Обновлено: {updated}, ошибок: {errors}, батчевых обновлений: {len(users_to_bulk_update)}, перепланировано задач: {len(users_need_task_reschedule)}")

        _last_run_status["last_success_at"] = start_time.isoformat()
        _last_run_status["last_summary"] = summary

    except Exception as critical_err:
        logger.exception("Критическая ошибка в remnawave_updater")
        _last_run_status["last_error"] = f"{type(critical_err).__name__}: {critical_err}"
        _last_run_status["total_errors"] = _last_run_status.get("total_errors", 0) + 1
    finally:
        _last_run_status["total_runs"] = _last_run_status.get("total_runs", 0) + 1
        _last_run_status["last_run_at"] = start_time.isoformat()
        await _finish_updater_run()
        end_time = datetime.now(ZoneInfo("Europe/Moscow"))
        total_time = (end_time - start_time).total_seconds()
        logger.info(f"Завершение работы remnawave_updater, время выполнения: {total_time:.2f} секунд")
