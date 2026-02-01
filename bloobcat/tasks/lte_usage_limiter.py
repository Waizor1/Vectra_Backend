import asyncio
from collections import defaultdict
from datetime import date, datetime, time, timezone, timedelta

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.notifications import NotificationMarks
from bloobcat.db.users import Users
from bloobcat.bot.notifications.lte import notify_lte_full_limit, notify_lte_half_limit
from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status
from bloobcat.settings import remnawave_settings

logger = get_logger("tasks.lte_usage_limiter")

BYTES_IN_GB = 1024 ** 3
TRIAL_LTE_LIMIT_GB = 1.0
MSK_TZ = timezone(timedelta(hours=3))


def _format_range_start(start_date: date) -> str:
    start_dt = datetime.combine(start_date, time.min, tzinfo=MSK_TZ)
    return start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _format_range_end(end_dt: datetime) -> str:
    return end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _safe_parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except Exception:
        return None


async def _fetch_lte_nodes(client: RemnaWaveClient) -> list[dict]:
    marker = (remnawave_settings.lte_node_marker or "").upper()
    if not marker:
        return []
    raw_resp = await client.nodes.get_nodes()
    nodes = raw_resp.get("response") or []
    lte_nodes = []
    for node in nodes:
        name = str(node.get("name") or "").upper()
        if marker in name:
            lte_nodes.append(node)
    return lte_nodes


async def _notify_lte_thresholds(
    *,
    user: Users,
    used_gb: float,
    total_gb: float,
    is_trial: bool,
) -> None:
    if total_gb <= 0:
        return
    half_key = "trial_half" if is_trial else "half"
    full_key = "trial_full" if is_trial else "full"

    if used_gb >= total_gb:
        already_full = await NotificationMarks.filter(
            user_id=user.id, type="lte_usage", key=full_key
        ).exists()
        if not already_full:
            await notify_lte_full_limit(user, used_gb=used_gb, total_gb=total_gb, is_trial=is_trial)
            await NotificationMarks.create(user_id=user.id, type="lte_usage", key=full_key)
        return

    if used_gb >= total_gb * 0.5:
        already_half = await NotificationMarks.filter(
            user_id=user.id, type="lte_usage", key=half_key
        ).exists()
        if not already_half:
            await notify_lte_half_limit(user, used_gb=used_gb, total_gb=total_gb, is_trial=is_trial)
            await NotificationMarks.create(user_id=user.id, type="lte_usage", key=half_key)


async def lte_usage_limiter_once() -> int:
    if not remnawave_settings.lte_internal_squad_uuid:
        logger.debug("LTE squad UUID not set, skipping LTE limiter")
        return 0

    client = RemnaWaveClient(
        remnawave_settings.url, remnawave_settings.token.get_secret_value()
    )
    updated = 0
    try:
        lte_nodes = await _fetch_lte_nodes(client)
        if not lte_nodes:
            logger.info("LTE nodes not found by marker, skipping limiter")
            return 0

        lte_node_uuids = [node.get("uuid") for node in lte_nodes if node.get("uuid")]
        if not lte_node_uuids:
            return 0

        msk_today = datetime.now(MSK_TZ).date()

        active_tariffs = await ActiveTariffs.filter(lte_gb_total__gt=0).prefetch_related("user")
        active_by_uuid: dict[str, ActiveTariffs] = {}
        min_start = msk_today
        for tariff in active_tariffs:
            user = tariff.user
            if not user or not user.remnawave_uuid:
                continue
            user_uuid = str(user.remnawave_uuid)
            active_by_uuid[user_uuid] = tariff
            if tariff.lte_usage_last_date and tariff.lte_usage_last_date < min_start:
                min_start = tariff.lte_usage_last_date

        usage_by_user: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
        if active_by_uuid:
            start_str = _format_range_start(min_start)
            end_str = _format_range_end(datetime.now(timezone.utc))
            for node_uuid in lte_node_uuids:
                resp = await client.nodes.get_node_user_usage_by_range(
                    node_uuid, start_str, end_str
                )
                items = resp.get("response") or []
                for item in items:
                    user_uuid = str(item.get("userUuid") or "")
                    if user_uuid not in active_by_uuid:
                        continue
                    day = _safe_parse_date(item.get("date"))
                    if not day:
                        continue
                    total_bytes = float(item.get("total") or 0)
                    usage_by_user[user_uuid][day] += total_bytes / BYTES_IN_GB

        for user_uuid, tariff in active_by_uuid.items():
            user = tariff.user
            if not user:
                continue
            usage_days = usage_by_user.get(user_uuid, {})
            last_date = tariff.lte_usage_last_date
            last_total = float(tariff.lte_usage_last_total_gb or 0)
            effective_start = last_date or msk_today

            delta_gb = 0.0
            new_last_date = last_date
            new_last_total = last_total

            for day in sorted(usage_days.keys()):
                if day < effective_start:
                    continue
                total_gb = usage_days.get(day, 0.0)
                if last_date and day == last_date:
                    delta_gb += max(0.0, total_gb - last_total)
                else:
                    delta_gb += total_gb
                if new_last_date is None or day > new_last_date:
                    new_last_date = day
                    new_last_total = total_gb
                elif day == new_last_date:
                    new_last_total = total_gb

            prev_used = float(tariff.lte_gb_used or 0)
            new_used = prev_used + delta_gb
            if delta_gb > 0:
                tariff.lte_gb_used = new_used

            update_fields = []
            if delta_gb > 0:
                update_fields.append("lte_gb_used")
            if new_last_date != last_date:
                tariff.lte_usage_last_date = new_last_date
                update_fields.append("lte_usage_last_date")
            if new_last_total != last_total:
                tariff.lte_usage_last_total_gb = new_last_total
                update_fields.append("lte_usage_last_total_gb")

            if update_fields:
                await tariff.save(update_fields=update_fields)

            total_limit = float(tariff.lte_gb_total or 0)
            if total_limit > 0:
                await _notify_lte_thresholds(
                    user=user,
                    used_gb=new_used,
                    total_gb=total_limit,
                    is_trial=False,
                )

                if new_used >= total_limit:
                    try:
                        await set_lte_squad_status(user_uuid, enable=False, client=client)
                    except Exception as e:
                        logger.error(f"LTE limiter: ошибка отключения LTE для {user.id}: {e}")
                else:
                    full_mark = await NotificationMarks.filter(
                        user_id=user.id, type="lte_usage", key="full"
                    ).exists()
                    if full_mark:
                        await NotificationMarks.filter(
                            user_id=user.id, type="lte_usage"
                        ).delete()
                        try:
                            await set_lte_squad_status(user_uuid, enable=True, client=client)
                        except Exception as e:
                            logger.error(f"LTE limiter: ошибка включения LTE для {user.id}: {e}")

            updated += 1

        trial_users = await Users.filter(is_trial=True, remnawave_uuid__not_isnull=True)
        if trial_users:
            end_str = _format_range_end(datetime.now(timezone.utc))
            marker_upper = (remnawave_settings.lte_node_marker or "").upper()
            for trial_user in trial_users:
                user_uuid = str(trial_user.remnawave_uuid)
                if trial_user.created_at:
                    created_at = trial_user.created_at
                    if getattr(created_at, "tzinfo", None):
                        start_date = created_at.astimezone(MSK_TZ).date()
                    else:
                        created_at_utc = created_at.replace(tzinfo=timezone.utc)
                        start_date = created_at_utc.astimezone(MSK_TZ).date()
                else:
                    start_date = msk_today
                start_str = _format_range_start(start_date)
                resp = await client.users.get_user_usage_by_range(user_uuid, start_str, end_str)
                items = resp.get("response") or []
                total_gb = 0.0
                for item in items:
                    node_name = str(item.get("nodeName") or "").upper()
                    if marker_upper and marker_upper not in node_name:
                        continue
                    total_bytes = float(item.get("total") or 0)
                    total_gb += total_bytes / BYTES_IN_GB

                await _notify_lte_thresholds(
                    user=trial_user,
                    used_gb=total_gb,
                    total_gb=TRIAL_LTE_LIMIT_GB,
                    is_trial=True,
                )

                if total_gb >= TRIAL_LTE_LIMIT_GB:
                    try:
                        await set_lte_squad_status(user_uuid, enable=False, client=client)
                    except Exception as e:
                        logger.error(f"LTE limiter: ошибка отключения LTE для trial {trial_user.id}: {e}")
                else:
                    full_mark = await NotificationMarks.filter(
                        user_id=trial_user.id, type="lte_usage", key="trial_full"
                    ).exists()
                    if full_mark:
                        await NotificationMarks.filter(
                            user_id=trial_user.id, type="lte_usage"
                        ).delete()
                        try:
                            await set_lte_squad_status(user_uuid, enable=True, client=client)
                        except Exception as e:
                            logger.error(f"LTE limiter: ошибка включения LTE для trial {trial_user.id}: {e}")

        return updated
    finally:
        await client.close()


async def _fetch_user_today_lte_gb(
    client: RemnaWaveClient,
    *,
    user_uuid: str,
    marker_upper: str,
    msk_today: date,
) -> float:
    start_str = _format_range_start(msk_today)
    end_str = _format_range_end(datetime.now(timezone.utc))
    resp = await client.users.get_user_usage_by_range(user_uuid, start_str, end_str)
    items = resp.get("response") or []
    total_gb = 0.0
    for item in items:
        node_name = str(item.get("nodeName") or "").upper()
        if marker_upper and marker_upper not in node_name:
            continue
        total_bytes = float(item.get("total") or 0)
        total_gb += total_bytes / BYTES_IN_GB
    return total_gb


async def lte_usage_limiter_quick_once(recent_minutes: int = 30) -> int:
    if not remnawave_settings.lte_internal_squad_uuid:
        logger.debug("LTE squad UUID not set, skipping LTE limiter (quick)")
        return 0

    marker_upper = (remnawave_settings.lte_node_marker or "").upper()
    if not marker_upper:
        logger.info("LTE node marker not set, skipping limiter (quick)")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=recent_minutes)
    active_tariffs = await ActiveTariffs.filter(lte_gb_total__gt=0).prefetch_related("user")
    targets: list[ActiveTariffs] = []
    for tariff in active_tariffs:
        user = tariff.user
        if not user or not user.remnawave_uuid or not user.connected_at:
            continue
        connected_at = user.connected_at
        if connected_at.tzinfo is None:
            connected_at = connected_at.replace(tzinfo=timezone.utc)
        if connected_at >= cutoff:
            targets.append(tariff)

    if not targets:
        return 0

    client = RemnaWaveClient(
        remnawave_settings.url, remnawave_settings.token.get_secret_value()
    )
    updated = 0
    sem = asyncio.Semaphore(10)
    msk_today = datetime.now(MSK_TZ).date()

    async def process_one(tariff: ActiveTariffs):
        nonlocal updated
        async with sem:
            user = tariff.user
            if not user or not user.remnawave_uuid:
                return
            total_gb = await _fetch_user_today_lte_gb(
                client,
                user_uuid=str(user.remnawave_uuid),
                marker_upper=marker_upper,
                msk_today=msk_today,
            )
            last_date = tariff.lte_usage_last_date
            last_total = float(tariff.lte_usage_last_total_gb or 0)
            if last_date == msk_today:
                delta_gb = max(0.0, total_gb - last_total)
            else:
                delta_gb = total_gb
            new_used = float(tariff.lte_gb_used or 0) + delta_gb

            update_fields = []
            if delta_gb > 0:
                tariff.lte_gb_used = new_used
                update_fields.append("lte_gb_used")
            if last_date != msk_today:
                tariff.lte_usage_last_date = msk_today
                update_fields.append("lte_usage_last_date")
            if total_gb != last_total:
                tariff.lte_usage_last_total_gb = total_gb
                update_fields.append("lte_usage_last_total_gb")

            if update_fields:
                await tariff.save(update_fields=update_fields)

            total_limit = float(tariff.lte_gb_total or 0)
            if total_limit > 0:
                await _notify_lte_thresholds(
                    user=user,
                    used_gb=new_used,
                    total_gb=total_limit,
                    is_trial=False,
                )
                try:
                    should_enable = new_used < total_limit
                    await set_lte_squad_status(
                        str(user.remnawave_uuid), enable=should_enable, client=client
                    )
                except Exception as e:
                    logger.error(f"LTE limiter quick: ошибка обновления LTE для {user.id}: {e}")

            updated += 1

    try:
        await asyncio.gather(*(process_one(t) for t in targets))
    finally:
        await client.close()

    return updated


async def run_lte_usage_limiter_scheduler(interval_seconds: int = 600):
    logger.info(f"Starting LTE usage limiter scheduler (interval: {interval_seconds}s)")
    while True:
        try:
            await lte_usage_limiter_once()
        except Exception as e:
            logger.error(f"Error in LTE usage limiter scheduler: {e}")
        await asyncio.sleep(interval_seconds)


async def run_lte_usage_limiter_quick_scheduler(
    interval_seconds: int = 60, recent_minutes: int = 30
):
    logger.info(
        "Starting LTE usage limiter QUICK scheduler (interval: %ss, recent: %smin)",
        interval_seconds,
        recent_minutes,
    )
    while True:
        try:
            await lte_usage_limiter_quick_once(recent_minutes=recent_minutes)
        except Exception as e:
            logger.error(f"Error in LTE usage limiter QUICK scheduler: {e}")
        await asyncio.sleep(interval_seconds)
