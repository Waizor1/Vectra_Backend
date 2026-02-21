from __future__ import annotations

from datetime import datetime, timezone, timedelta, time as dt_time, date
from typing import Optional, Dict, Any

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.notifications import NotificationMarks
from bloobcat.db.remnawave_retry_jobs import RemnaWaveRetryJobs
from bloobcat.db.tariff import Tariffs
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings
from tortoise.exceptions import IntegrityError

logger = get_logger("admin_integration")

DELETE_RETRY_JOB_TYPE = "remnawave_user_delete"
DELETE_RETRY_MAX_ATTEMPTS = 8
DELETE_RETRY_BASE_DELAY_SECONDS = 60
DELETE_RETRY_MAX_DELAY_SECONDS = 3600


def normalize_hwid_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, parsed)


def is_remnawave_not_found_error(error_text: str) -> bool:
    lowered = (error_text or "").lower()
    return (
        "user not found" in lowered
        or "a063" in lowered
        or "404" in lowered
        or "not found" in lowered
    )


def is_remnawave_transient_error(error_text: str) -> bool:
    lowered = (error_text or "").lower()
    transient_tokens = (
        "timeout",
        "timed out",
        "temporarily",
        "temporary",
        "try again",
        "too many requests",
        "rate limit",
        "network error",
        "connection",
        "connection reset",
        "connection refused",
        "broken pipe",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "api error [429]",
        "api error [500]",
        "api error [502]",
        "api error [503]",
        "api error [504]",
    )
    if any(token in lowered for token in transient_tokens):
        return True
    return any(f"[{code}]" in lowered for code in ("429", "500", "502", "503", "504"))


def _retry_backoff_seconds(attempt_number: int) -> int:
    safe_attempt = max(1, int(attempt_number))
    delay = DELETE_RETRY_BASE_DELAY_SECONDS * (2 ** (safe_attempt - 1))
    return min(DELETE_RETRY_MAX_DELAY_SECONDS, delay)


async def enqueue_remnawave_delete_retry(
    *,
    user_id: int,
    remnawave_uuid: str,
    last_error: str | None = None,
) -> bool:
    uuid_value = str(remnawave_uuid or "").strip()
    if not uuid_value:
        return False

    existing = await RemnaWaveRetryJobs.get_or_none(
        job_type=DELETE_RETRY_JOB_TYPE,
        user_id=int(user_id),
        status="pending",
    )
    if existing:
        fields_to_update: list[str] = []
        if existing.remnawave_uuid != uuid_value:
            existing.remnawave_uuid = uuid_value
            fields_to_update.append("remnawave_uuid")
        if last_error:
            existing.last_error = str(last_error)[:1024]
            fields_to_update.append("last_error")
        if fields_to_update:
            await existing.save(update_fields=fields_to_update)
        return False

    try:
        await RemnaWaveRetryJobs.create(
            job_type=DELETE_RETRY_JOB_TYPE,
            user_id=int(user_id),
            remnawave_uuid=uuid_value,
            status="pending",
            attempts=0,
            next_retry_at=datetime.now(timezone.utc),
            last_error=(str(last_error)[:1024] if last_error else None),
        )
    except IntegrityError:
        return False

    logger.warning(
        "Queued RemnaWave delete retry job: user_id=%s uuid=%s error=%s",
        user_id,
        uuid_value,
        (str(last_error)[:256] if last_error else None),
    )
    return True


async def _delete_remnawave_user_with_retry_policy(
    *,
    user_id: int,
    remnawave_uuid: str | None,
    source: str,
    enqueue_on_transient: bool,
) -> Dict[str, Any]:
    uuid_value = str(remnawave_uuid or "").strip()
    if not uuid_value:
        return {"ok": True, "deleted": False, "not_found": False, "queued_retry": False, "reason": "uuid_missing"}

    client = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
    try:
        await client.users.delete_user(uuid_value)
        logger.info("RemnaWave user deleted (%s): user_id=%s uuid=%s", source, user_id, uuid_value)
        return {"ok": True, "deleted": True, "not_found": False, "queued_retry": False}
    except Exception as exc:
        error_text = str(exc)
        if is_remnawave_not_found_error(error_text):
            logger.info(
                "RemnaWave user already missing (%s): user_id=%s uuid=%s",
                source,
                user_id,
                uuid_value,
            )
            return {"ok": True, "deleted": False, "not_found": True, "queued_retry": False}

        queued_retry = False
        if enqueue_on_transient and is_remnawave_transient_error(error_text):
            queued_retry = await enqueue_remnawave_delete_retry(
                user_id=user_id,
                remnawave_uuid=uuid_value,
                last_error=error_text,
            )

        logger.warning(
            "RemnaWave delete failed (%s): user_id=%s uuid=%s queued_retry=%s error=%s",
            source,
            user_id,
            uuid_value,
            queued_retry,
            error_text,
        )
        return {
            "ok": False,
            "deleted": False,
            "not_found": False,
            "queued_retry": queued_retry,
            "error": error_text,
        }
    finally:
        await client.close()


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
                except Exception as exc:
                    if "api/users/stats/usage" in str(exc) and "404" in str(exc):
                        logger.warning(
                            "RemnaWave usage endpoint missing, fallback LTE enable: user=%s total=%s",
                            user.id,
                            lte_gb_total_int,
                        )
                        should_enable = lte_gb_total_int > 0
                    else:
                        raise
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
            updates["hwidDeviceLimit"] = normalize_hwid_limit(hwid_limit)
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


async def prepare_user_delete_via_admin(user_id: int) -> Dict[str, Any]:
    user_obj = await Users.get_or_none(id=user_id)
    if not user_obj:
        logger.info("Admin pre-delete skipped: user=%s not found", user_id)
        return {"ok": True, "deleted": False, "not_found": True, "queued_retry": False, "noop": True}

    return await _delete_remnawave_user_with_retry_policy(
        user_id=int(user_obj.id),
        remnawave_uuid=str(user_obj.remnawave_uuid) if user_obj.remnawave_uuid else None,
        source="admin_pre_delete",
        enqueue_on_transient=True,
    )


async def delete_user_via_admin(user_id: int) -> bool:
    user_obj = await Users.get_or_none(id=user_id)
    if not user_obj:
        logger.info("Admin delete user noop: user=%s not found", user_id)
        return True
    await user_obj.delete()
    return True


async def process_remnawave_delete_retry_jobs(batch_limit: int = 50) -> Dict[str, int]:
    now = datetime.now(timezone.utc)
    jobs = (
        await RemnaWaveRetryJobs.filter(
            job_type=DELETE_RETRY_JOB_TYPE,
            status="pending",
            next_retry_at__lte=now,
        )
        .order_by("next_retry_at", "id")
        .limit(int(batch_limit))
    )
    if not jobs:
        return {"processed": 0, "completed": 0, "rescheduled": 0, "dead_letter": 0}

    stats = {"processed": 0, "completed": 0, "rescheduled": 0, "dead_letter": 0}
    for job in jobs:
        stats["processed"] += 1
        job.attempts = int(job.attempts or 0) + 1
        try:
            result = await _delete_remnawave_user_with_retry_policy(
                user_id=int(job.user_id),
                remnawave_uuid=job.remnawave_uuid,
                source="retry_job",
                enqueue_on_transient=False,
            )
            if result.get("ok"):
                job.status = "done"
                job.last_error = None
                await job.save(update_fields=["attempts", "status", "last_error"])
                stats["completed"] += 1
                continue

            error_text = str(result.get("error") or "Unknown error")
            if not is_remnawave_transient_error(error_text) or job.attempts >= DELETE_RETRY_MAX_ATTEMPTS:
                job.status = "dead_letter"
                job.last_error = error_text[:1024]
                await job.save(update_fields=["attempts", "status", "last_error"])
                stats["dead_letter"] += 1
                logger.error(
                    "RemnaWave delete retry moved to dead-letter: job=%s user=%s attempts=%s error=%s",
                    job.id,
                    job.user_id,
                    job.attempts,
                    error_text,
                )
                continue

            delay_seconds = _retry_backoff_seconds(job.attempts)
            job.next_retry_at = now + timedelta(seconds=delay_seconds)
            job.last_error = error_text[:1024]
            await job.save(update_fields=["attempts", "next_retry_at", "last_error"])
            stats["rescheduled"] += 1
        except Exception as exc:
            error_text = str(exc)
            if job.attempts >= DELETE_RETRY_MAX_ATTEMPTS:
                job.status = "dead_letter"
                job.last_error = error_text[:1024]
                await job.save(update_fields=["attempts", "status", "last_error"])
                stats["dead_letter"] += 1
                logger.exception(
                    "RemnaWave delete retry hard-failed to dead-letter: job=%s user=%s",
                    job.id,
                    job.user_id,
                )
                continue

            delay_seconds = _retry_backoff_seconds(job.attempts)
            job.next_retry_at = now + timedelta(seconds=delay_seconds)
            job.last_error = error_text[:1024]
            await job.save(update_fields=["attempts", "next_retry_at", "last_error"])
            stats["rescheduled"] += 1
            logger.exception(
                "RemnaWave delete retry transient failure: job=%s user=%s attempt=%s",
                job.id,
                job.user_id,
                job.attempts,
            )

    return stats


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


async def compute_tariff_effective_pricing(
    *,
    tariff_id: Optional[int],
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    existing = await Tariffs.get_or_none(id=tariff_id) if tariff_id is not None else None

    base = _to_int(
        patch.get("base_price"),
        existing.base_price if existing else 1,
    )
    progressive_multiplier = _to_float(
        patch.get("progressive_multiplier"),
        existing.progressive_multiplier if existing else 0.9,
    )
    devices_limit_default = max(
        1,
        _to_int(
            patch.get("devices_limit_default"),
            existing.devices_limit_default if existing else 3,
        ),
    )
    devices_limit_family = max(
        devices_limit_default,
        _to_int(
            patch.get("devices_limit_family"),
            existing.devices_limit_family if existing else 10,
        ),
    )
    final_price_default = patch.get("final_price_default")
    final_price_family = patch.get("final_price_family")
    family_plan_enabled_raw = patch.get("family_plan_enabled")
    if family_plan_enabled_raw is None:
        family_plan_enabled = bool(existing.family_plan_enabled) if existing else True
    else:
        family_plan_enabled = bool(family_plan_enabled_raw)

    model = Tariffs()
    model.base_price = max(1, base)
    model.progressive_multiplier = progressive_multiplier
    model.devices_limit_default = devices_limit_default
    model.devices_limit_family = devices_limit_family
    model.family_plan_enabled = family_plan_enabled
    model.final_price_default = _to_int(final_price_default, 0) if final_price_default is not None else (existing.final_price_default if existing else None)
    model.final_price_family = _to_int(final_price_family, 0) if final_price_family is not None else (existing.final_price_family if existing else None)

    effective_base, effective_multiplier = model.get_effective_pricing()
    return {
        "base_price": int(effective_base),
        "progressive_multiplier": float(effective_multiplier),
    }
