from __future__ import annotations

from datetime import datetime, timezone, timedelta, time as dt_time, date
from typing import Optional, Dict, Any

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.notifications import NotificationMarks
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings

logger = get_logger("admin_integration")


async def sync_user_lte(user_id: int, lte_gb_total: Optional[int]) -> None:
    if lte_gb_total is None:
        return
    try:
        lte_gb_total_int = int(lte_gb_total)
    except (TypeError, ValueError):
        logger.warning("Invalid lte_gb_total for user=%s: %s", user_id, lte_gb_total)
        return
    if lte_gb_total_int < 0:
        lte_gb_total_int = 0

    user = await Users.get_or_none(id=user_id)
    if not user:
        logger.warning("User not found for LTE sync: %s", user_id)
        return

    active_tariff = None
    if user.active_tariff_id:
        active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)

    if active_tariff:
        active_tariff.lte_gb_total = lte_gb_total_int
        await active_tariff.save(update_fields=["lte_gb_total"])
        try:
            should_enable = lte_gb_total_int > float(active_tariff.lte_gb_used or 0)
            if user.remnawave_uuid:
                await set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable)
            await NotificationMarks.filter(user_id=user.id, type="lte_usage").delete()
        except Exception as exc:
            logger.error("LTE sync failed for user=%s: %s", user_id, exc)
        return

    # Users without active_tariff_id (trial/partners) — check actual usage
    try:
        should_enable = lte_gb_total_int > 0
        if user.remnawave_uuid:
            BYTES_IN_GB = 1024 ** 3
            MSK_TZ = timezone(timedelta(hours=3))
            marker_upper = (remnawave_settings.lte_node_marker or "").upper()

            created_at = user.created_at
            if created_at:
                if getattr(created_at, "tzinfo", None):
                    start_date = created_at.astimezone(MSK_TZ).date()
                else:
                    created_at_utc = created_at.replace(tzinfo=timezone.utc)
                    start_date = created_at_utc.astimezone(MSK_TZ).date()
            else:
                start_date = datetime.now(MSK_TZ).date()

            start_dt = datetime.combine(start_date, dt_time.min, tzinfo=MSK_TZ)
            start_str = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            end_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            client = RemnaWaveClient(
                remnawave_settings.url,
                remnawave_settings.token.get_secret_value()
            )
            try:
                resp = await client.users.get_user_usage_by_range(
                    str(user.remnawave_uuid),
                    start_str,
                    end_str,
                )
                items = resp.get("response") or []
                used_gb = 0.0
                for item in items:
                    node_name = str(item.get("nodeName") or "").upper()
                    if marker_upper and marker_upper not in node_name:
                        continue
                    total_bytes = float(item.get("total") or 0)
                    used_gb += total_bytes / BYTES_IN_GB
                should_enable = float(lte_gb_total_int) > used_gb
            finally:
                await client.close()

            await set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable)

        await NotificationMarks.filter(user_id=user.id, type="lte_usage").delete()
        logger.info(
            "Admin LTE update without active_tariff: user=%s total=%s enable=%s",
            user.id,
            lte_gb_total_int,
            should_enable,
        )
    except Exception as exc:
        logger.error("LTE sync failed (no active_tariff) user=%s: %s", user_id, exc)


async def sync_active_tariff_lte(
    active_tariff_id: str,
    lte_gb_total: Optional[int] = None,
    lte_gb_used: Optional[float] = None,
) -> None:
    active_tariff = await ActiveTariffs.get_or_none(id=active_tariff_id)
    if not active_tariff or not active_tariff.user_id:
        logger.warning("ActiveTariff not found for LTE sync: %s", active_tariff_id)
        return

    user = await active_tariff.user
    if not user:
        return

    if lte_gb_total is None:
        lte_gb_total = active_tariff.lte_gb_total
    if lte_gb_used is None:
        lte_gb_used = active_tariff.lte_gb_used

    try:
        should_enable = float(lte_gb_total or 0) > float(lte_gb_used or 0)
        if user.remnawave_uuid:
            await set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable)
        await NotificationMarks.filter(user_id=active_tariff.user_id, type="lte_usage").delete()
        logger.info(
            "Admin active_tariff LTE sync: tariff=%s user=%s total=%s used=%s",
            active_tariff.id,
            active_tariff.user_id,
            lte_gb_total,
            lte_gb_used,
        )
    except Exception as exc:
        logger.error(
            "Admin active_tariff LTE sync failed: tariff=%s user=%s error=%s",
            active_tariff.id,
            active_tariff.user_id,
            exc,
        )


async def sync_user_remnawave_fields(
    user_id: int,
    expired_at: Optional[date],
    hwid_limit: Optional[int],
) -> None:
    user = await Users.get_or_none(id=user_id)
    if not user or not user.remnawave_uuid:
        return

    updates: Dict[str, Any] = {}
    if expired_at is not None:
        # avoid accidental past date
        today = date.today()
        if expired_at >= today:
            updates["expireAt"] = expired_at
        else:
            logger.warning("Expired_at in past for user=%s: %s", user_id, expired_at)
    if hwid_limit is not None:
        try:
            updates["hwidDeviceLimit"] = int(hwid_limit)
        except (TypeError, ValueError):
            logger.warning("Invalid hwid_limit for user=%s: %s", user_id, hwid_limit)

    if not updates:
        return

    client = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
    try:
        await client.users.update_user(user.remnawave_uuid, **updates)
        logger.info("RemnaWave user update: user=%s payload=%s", user_id, updates)
    finally:
        await client.close()


async def delete_user_via_admin(user_id: int) -> bool:
    user_obj = await Users.get_or_none(id=user_id)
    if not user_obj:
        return False
    await user_obj.delete()
    return True
