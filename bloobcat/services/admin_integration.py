from __future__ import annotations

from datetime import datetime, timezone, timedelta, time as dt_time, date
from typing import Optional, Dict, Any
from uuid import UUID

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.hwid_local import HwidDeviceLocal
from bloobcat.db.notifications import NotificationMarks
from bloobcat.db.remnawave_retry_jobs import RemnaWaveRetryJobs
from bloobcat.db.tariff import Tariffs
from bloobcat.services.subscription_limits import (
    family_devices_threshold,
    lte_default_price_per_gb,
    subscription_devices_max,
)
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.settings import remnawave_settings
from tortoise.exceptions import IntegrityError

logger = get_logger("admin_integration")

# Keep this symbol patchable in tests, while resolving the real class lazily.
RemnaWaveClient: Any = None


class HwidPurgePreconditionError(RuntimeError):
    """Raised when a destructive HWID purge cannot safely enumerate live owners."""


DELETE_RETRY_JOB_TYPE = "remnawave_user_delete"
DELETE_RETRY_MAX_ATTEMPTS = 8
DELETE_RETRY_BASE_DELAY_SECONDS = 60
DELETE_RETRY_MAX_DELAY_SECONDS = 3600
DELETE_RETRY_PROCESSING_LEASE_SECONDS = 300


def _build_remnawave_client():
    client_factory = RemnaWaveClient
    if client_factory is None:
        from bloobcat.routes.remnawave.client import RemnaWaveClient as client_factory

    return client_factory(remnawave_settings.url, remnawave_settings.token.get_secret_value())


def normalize_hwid_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, parsed)


def is_remnawave_not_found_error(error_text: str) -> bool:
    normalized = "".join(ch for ch in (error_text or "").lower() if ch.isalnum())
    explicit_signals = (
        "a025",
        "a063",
        "userwithspecifiedparamsnotfound",
        "usernotfound",
    )
    return any(signal in normalized for signal in explicit_signals)


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

    async def _update_active_job_if_needed(job: RemnaWaveRetryJobs) -> None:
        fields_to_update: list[str] = []
        if job.remnawave_uuid != uuid_value:
            job.remnawave_uuid = uuid_value
            fields_to_update.append("remnawave_uuid")
        if last_error:
            normalized_error = str(last_error)[:1024]
            if job.last_error != normalized_error:
                job.last_error = normalized_error
                fields_to_update.append("last_error")
        if fields_to_update:
            await job.save(update_fields=fields_to_update)

    async def _get_active_job() -> RemnaWaveRetryJobs | None:
        return (
            await RemnaWaveRetryJobs.filter(
                job_type=DELETE_RETRY_JOB_TYPE,
                user_id=int(user_id),
                status__in=["pending", "processing"],
            )
            .order_by("id")
            .first()
        )

    existing = await _get_active_job()
    if existing:
        await _update_active_job_if_needed(existing)
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
        existing_after_race = await _get_active_job()
        if existing_after_race:
            await _update_active_job_if_needed(existing_after_race)
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

    client = _build_remnawave_client()
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

    # Stamp the admin-grant moment so the limiter can anchor the quota window
    # to "now" rather than the user's `created_at` (months/years ago for
    # legacy accounts). Only stamp on first grant — re-runs of the same
    # sync must not slide the anchor forward.
    if lte_gb_total_int > 0 and getattr(user, "admin_lte_granted_at", None) is None:
        user.admin_lte_granted_at = datetime.now(timezone.utc)
        try:
            await user.save(update_fields=["admin_lte_granted_at"])
        except Exception as exc:
            logger.warning(
                "Failed to stamp admin_lte_granted_at for user=%s: %s",
                user_id,
                exc,
            )

    active_tariff = None
    if user.active_tariff_id:
        active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)

    if active_tariff:
        active_tariff.lte_gb_total = lte_gb_total_int
        await active_tariff.save(update_fields=["lte_gb_total"])
        try:
            should_enable = lte_gb_total_int > float(active_tariff.lte_gb_used or 0)
            if user.remnawave_uuid:
                from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status

                await set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable)
            await NotificationMarks.filter(user_id=user.id, type="lte_usage").delete()
        except Exception as exc:
            logger.error("LTE sync failed for user=%s: %s", user_id, exc)
        return

    # Users without active_tariff_id (trial/partners) — check actual usage
    try:
        should_enable = lte_gb_total_int > 0
        if user.remnawave_uuid:
            from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status

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

            client = _build_remnawave_client()
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
            from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status

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

    client = _build_remnawave_client()
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


def _normalize_hwid(raw_hwid: Any) -> str:
    hwid = str(raw_hwid or "").strip()
    if not hwid:
        raise ValueError("HWID is required")
    if len(hwid) > 255:
        raise ValueError("HWID is too long")
    return hwid


def _serialize_dateish(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _extract_device_user_uuid(device: Dict[str, Any]) -> Optional[str]:
    if not isinstance(device, dict):
        return None
    owner_uuid = device.get("userUuid") or device.get("user_uuid")
    if owner_uuid is None and isinstance(device.get("user"), dict):
        owner_uuid = device["user"].get("uuid")
    if owner_uuid is None:
        return None
    normalized = str(owner_uuid).strip()
    return normalized or None


def _normalize_hwid_actor(actor: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(actor, dict):
        return {}

    normalized: Dict[str, Any] = {}
    for source_key, target_key in (
        ("directus_user_id", "directus_user_id"),
        ("user_id", "directus_user_id"),
        ("directus_role_id", "directus_role_id"),
        ("role_id", "directus_role_id"),
        ("email", "email"),
        ("name", "name"),
    ):
        value = actor.get(source_key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[target_key] = text[:255]

    for source_key, target_key in (
        ("is_admin", "is_admin"),
        ("admin", "is_admin"),
    ):
        value = actor.get(source_key)
        if isinstance(value, bool):
            normalized[target_key] = value
            break

    return normalized


async def _load_local_hwid_history(hwid: str) -> list[Dict[str, Any]]:
    rows = (
        await HwidDeviceLocal.filter(hwid=hwid)
        .order_by("-last_seen_at", "-id")
        .values(
            "id",
            "hwid",
            "user_uuid",
            "telegram_user_id",
            "first_seen_at",
            "last_seen_at",
        )
    )
    serialized: list[Dict[str, Any]] = []
    for row in rows:
        serialized.append(
            {
                "id": int(row["id"]),
                "hwid": str(row["hwid"]),
                "user_uuid": (
                    str(row["user_uuid"]).strip() if row.get("user_uuid") is not None else None
                ),
                "telegram_user_id": (
                    int(row["telegram_user_id"]) if row.get("telegram_user_id") is not None else None
                ),
                "first_seen_at": _serialize_dateish(row.get("first_seen_at")),
                "last_seen_at": _serialize_dateish(row.get("last_seen_at")),
            }
        )
    return serialized


async def _scan_live_hwid_matches(hwid: str) -> Dict[str, Any]:
    from bloobcat.routes.remnawave.hwid_utils import (
        extract_hwid_from_device,
        parse_remnawave_devices,
    )

    matches: list[Dict[str, Any]] = []
    page_size = 500
    start = 0
    pages_fetched = 0
    total_devices: Optional[int] = None

    client = _build_remnawave_client()
    try:
        while pages_fetched < 100:
            raw = await client.users.get_hwid_devices(start=start, size=page_size)
            pages_fetched += 1
            response = raw.get("response") if isinstance(raw, dict) else None
            if isinstance(response, dict):
                raw_total = response.get("total")
                try:
                    total_devices = int(raw_total) if raw_total is not None else total_devices
                except (TypeError, ValueError):
                    total_devices = total_devices

            devices = parse_remnawave_devices(raw)
            if not devices:
                break

            for device in devices:
                if extract_hwid_from_device(device) != hwid:
                    continue
                matches.append(
                    {
                        "hwid": hwid,
                        "user_uuid": _extract_device_user_uuid(device),
                        "platform": device.get("platform") or device.get("os"),
                        "os_version": device.get("osVersion") or device.get("os_version"),
                        "device_model": device.get("deviceModel") or device.get("model") or device.get("device"),
                        "user_agent": device.get("userAgent") or device.get("user_agent"),
                        "created_at": _serialize_dateish(device.get("createdAt")),
                        "updated_at": _serialize_dateish(
                            device.get("updatedAt") or device.get("lastSeenAt")
                        ),
                    }
                )

            if total_devices is not None and start + page_size >= total_devices:
                break
            if len(devices) < page_size and total_devices is None:
                break
            start += page_size

        return {
            "ok": True,
            "matches": matches,
            "pages_fetched": pages_fetched,
            "total_devices": total_devices,
            "error": None,
        }
    except Exception as exc:
        error_text = str(exc)
        logger.warning(
            "Admin HWID preview: live RemnaWave scan failed for hwid=%s: %s",
            hwid,
            error_text,
        )
        return {
            "ok": False,
            "matches": matches,
            "pages_fetched": pages_fetched,
            "total_devices": total_devices,
            "error": error_text,
        }
    finally:
        await client.close()


async def _load_related_hwid_users(
    *,
    telegram_user_ids: list[int],
    owner_uuids: list[str],
) -> list[Dict[str, Any]]:
    seen_by_user_id: dict[int, Dict[str, Any]] = {}

    async def _merge_rows(rows: list[Dict[str, Any]]) -> None:
        for row in rows:
            user_id = int(row["id"])
            seen_by_user_id[user_id] = {
                "id": user_id,
                "username": row.get("username"),
                "full_name": row.get("full_name"),
                "remnawave_uuid": (
                    str(row["remnawave_uuid"]).strip() if row.get("remnawave_uuid") is not None else None
                ),
                "is_trial": bool(row.get("is_trial")),
                "used_trial": bool(row.get("used_trial")),
                "expired_at": _serialize_dateish(row.get("expired_at")),
                "active_tariff_id": row.get("active_tariff_id"),
                "is_registered": bool(row.get("is_registered")),
            }

    if telegram_user_ids:
        rows = await Users.filter(id__in=telegram_user_ids).values(
            "id",
            "username",
            "full_name",
            "remnawave_uuid",
            "is_trial",
            "used_trial",
            "expired_at",
            "active_tariff_id",
            "is_registered",
        )
        await _merge_rows(rows)

    uuid_values: list[UUID] = []
    for raw_uuid in owner_uuids:
        try:
            uuid_values.append(UUID(str(raw_uuid)))
        except (TypeError, ValueError):
            continue

    if uuid_values:
        rows = await Users.filter(remnawave_uuid__in=uuid_values).values(
            "id",
            "username",
            "full_name",
            "remnawave_uuid",
            "is_trial",
            "used_trial",
            "expired_at",
            "active_tariff_id",
            "is_registered",
        )
        await _merge_rows(rows)

    return list(seen_by_user_id.values())


def _build_hwid_owner_rows(
    *,
    local_history: list[Dict[str, Any]],
    live_matches: list[Dict[str, Any]],
    users: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    users_by_id = {
        int(item["id"]): item for item in users if item.get("id") is not None
    }
    users_by_uuid = {
        str(item["remnawave_uuid"]): item
        for item in users
        if item.get("remnawave_uuid")
    }

    owners: dict[str, Dict[str, Any]] = {}
    owner_order: list[str] = []

    def _ensure_owner(user_uuid: str) -> Dict[str, Any]:
        if user_uuid not in owners:
            owners[user_uuid] = {
                "user_uuid": user_uuid,
                "telegram_user_id": None,
                "local_user_id": None,
                "username": None,
                "full_name": None,
                "is_trial": None,
                "used_trial": None,
                "expired_at": None,
                "active_tariff_id": None,
                "is_registered": None,
                "source_local_history": False,
                "source_remnawave_live": False,
                "local_history_rows": 0,
                "live_matches": 0,
                "local_last_seen_at": None,
                "live_last_seen_at": None,
                "live_platforms": [],
                "live_device_models": [],
            }
            owner_order.append(user_uuid)
        return owners[user_uuid]

    for row in local_history:
        user_uuid = row.get("user_uuid")
        if not user_uuid:
            continue
        owner = _ensure_owner(str(user_uuid))
        owner["source_local_history"] = True
        owner["local_history_rows"] += 1
        if row.get("telegram_user_id") is not None:
            owner["telegram_user_id"] = int(row["telegram_user_id"])
        if row.get("last_seen_at"):
            owner["local_last_seen_at"] = row["last_seen_at"]

    for row in live_matches:
        user_uuid = row.get("user_uuid")
        if not user_uuid:
            continue
        owner = _ensure_owner(str(user_uuid))
        owner["source_remnawave_live"] = True
        owner["live_matches"] += 1
        if row.get("updated_at"):
            owner["live_last_seen_at"] = row["updated_at"]
        platform = row.get("platform")
        if platform and platform not in owner["live_platforms"]:
            owner["live_platforms"].append(platform)
        device_model = row.get("device_model")
        if device_model and device_model not in owner["live_device_models"]:
            owner["live_device_models"].append(device_model)

    for owner in owners.values():
        linked_user = None
        if owner.get("user_uuid"):
            linked_user = users_by_uuid.get(owner["user_uuid"])
        if linked_user is None and owner.get("telegram_user_id") is not None:
            linked_user = users_by_id.get(int(owner["telegram_user_id"]))
        if linked_user is None:
            continue

        owner["local_user_id"] = int(linked_user["id"])
        owner["telegram_user_id"] = int(linked_user["id"])
        owner["username"] = linked_user.get("username")
        owner["full_name"] = linked_user.get("full_name")
        owner["is_trial"] = linked_user.get("is_trial")
        owner["used_trial"] = linked_user.get("used_trial")
        owner["expired_at"] = linked_user.get("expired_at")
        owner["active_tariff_id"] = linked_user.get("active_tariff_id")
        owner["is_registered"] = linked_user.get("is_registered")

    return [owners[user_uuid] for user_uuid in owner_order]


async def _collect_hwid_context(hwid: str) -> Dict[str, Any]:
    local_history = await _load_local_hwid_history(hwid)
    live_scan = await _scan_live_hwid_matches(hwid)
    live_matches = live_scan.get("matches") or []

    owner_uuid_order: list[str] = []
    owner_uuid_seen: set[str] = set()
    telegram_user_ids: list[int] = []
    telegram_user_seen: set[int] = set()

    for row in local_history:
        user_uuid = row.get("user_uuid")
        if user_uuid:
            normalized_uuid = str(user_uuid)
            if normalized_uuid not in owner_uuid_seen:
                owner_uuid_seen.add(normalized_uuid)
                owner_uuid_order.append(normalized_uuid)
        telegram_user_id = row.get("telegram_user_id")
        if telegram_user_id is not None:
            normalized_user_id = int(telegram_user_id)
            if normalized_user_id not in telegram_user_seen:
                telegram_user_seen.add(normalized_user_id)
                telegram_user_ids.append(normalized_user_id)

    for row in live_matches:
        user_uuid = row.get("user_uuid")
        if user_uuid:
            normalized_uuid = str(user_uuid)
            if normalized_uuid not in owner_uuid_seen:
                owner_uuid_seen.add(normalized_uuid)
                owner_uuid_order.append(normalized_uuid)

    users = await _load_related_hwid_users(
        telegram_user_ids=telegram_user_ids,
        owner_uuids=owner_uuid_order,
    )
    owners = _build_hwid_owner_rows(
        local_history=local_history,
        live_matches=live_matches,
        users=users,
    )

    return {
        "hwid": hwid,
        "summary": {
            "local_history_rows": len(local_history),
            "remnawave_live_matches": len(live_matches),
            "owners": len(owners),
            "local_users": len(users),
            "has_matches": bool(local_history or live_matches),
        },
        "owner_uuids": owner_uuid_order,
        "local_history": local_history,
        "live_matches": live_matches,
        "owners": owners,
        "users": users,
        "remnawave_scan": {
            "ok": bool(live_scan.get("ok")),
            "pages_fetched": int(live_scan.get("pages_fetched") or 0),
            "total_devices": live_scan.get("total_devices"),
            "error": live_scan.get("error"),
        },
    }


async def _delete_hwid_from_remnawave_owners(
    hwid: str,
    owner_uuids: list[str],
) -> list[Dict[str, Any]]:
    if not owner_uuids:
        return []

    client = _build_remnawave_client()
    try:
        results: list[Dict[str, Any]] = []
        for owner_uuid in owner_uuids:
            result: Dict[str, Any] = {
                "user_uuid": owner_uuid,
                "status": "deleted",
                "error": None,
            }
            try:
                await client.users.delete_user_hwid_device(str(owner_uuid), hwid)
            except Exception as exc:
                error_text = str(exc)
                if is_remnawave_not_found_error(error_text):
                    result["status"] = "user_missing"
                elif "A101" in error_text or "Delete hwid user device error" in error_text:
                    result["status"] = "already_absent"
                else:
                    result["status"] = "error"
                    result["error"] = error_text[:1024]
            results.append(result)
        return results
    finally:
        await client.close()


async def _delete_local_hwid_history(hwid: str) -> int:
    deleted_rows = await HwidDeviceLocal.filter(hwid=hwid).delete()
    return int(deleted_rows or 0)


async def preview_hwid_purge(raw_hwid: Any) -> Dict[str, Any]:
    hwid = _normalize_hwid(raw_hwid)
    return await _collect_hwid_context(hwid)


async def purge_hwid_everywhere(
    raw_hwid: Any,
    *,
    reason: Optional[str] = None,
    actor: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    hwid = _normalize_hwid(raw_hwid)
    normalized_reason = str(reason or "").strip()[:500] or None
    normalized_actor = _normalize_hwid_actor(actor)

    context = await _collect_hwid_context(hwid)
    remnawave_scan = context.get("remnawave_scan")
    if isinstance(remnawave_scan, dict) and remnawave_scan.get("ok") is False:
        error_text = str(remnawave_scan.get("error") or "unknown error")
        logger.warning(
            "Admin HWID purge aborted before deletion: hwid=%s live_scan_error=%s",
            hwid,
            error_text,
        )
        raise HwidPurgePreconditionError(
            "Live RemnaWave HWID scan failed; purge aborted before deleting local history: "
            f"{error_text}"
        )

    remnawave_results = await _delete_hwid_from_remnawave_owners(
        hwid,
        context.get("owner_uuids") or [],
    )
    local_history_deleted = await _delete_local_hwid_history(hwid)

    remnawave_deleted = sum(1 for item in remnawave_results if item.get("status") == "deleted")
    remnawave_already_absent = sum(
        1 for item in remnawave_results if item.get("status") == "already_absent"
    )
    remnawave_user_missing = sum(
        1 for item in remnawave_results if item.get("status") == "user_missing"
    )
    remnawave_errors = [item for item in remnawave_results if item.get("status") == "error"]
    partial = bool(remnawave_errors)

    logger.info(
        "Admin HWID purge executed: hwid=%s actor=%s reason=%s local_deleted=%s remnawave_deleted=%s already_absent=%s user_missing=%s errors=%s",
        hwid,
        normalized_actor or None,
        normalized_reason,
        local_history_deleted,
        remnawave_deleted,
        remnawave_already_absent,
        remnawave_user_missing,
        len(remnawave_errors),
    )

    return {
        "ok": not partial,
        "partial": partial,
        "hwid": hwid,
        "reason": normalized_reason,
        "actor": normalized_actor,
        "context": context,
        "summary": {
            "local_history_deleted": local_history_deleted,
            "remnawave_attempts": len(remnawave_results),
            "remnawave_deleted": remnawave_deleted,
            "remnawave_already_absent": remnawave_already_absent,
            "remnawave_user_missing": remnawave_user_missing,
            "remnawave_errors": len(remnawave_errors),
        },
        "remnawave_results": remnawave_results,
    }


async def process_remnawave_delete_retry_jobs(batch_limit: int = 50) -> Dict[str, int]:
    stats = {"processed": 0, "completed": 0, "rescheduled": 0, "dead_letter": 0}

    async def _claim_next_ready_job(now_utc: datetime):
        lease_until = now_utc + timedelta(seconds=DELETE_RETRY_PROCESSING_LEASE_SECONDS)

        while True:
            candidate = (
                await RemnaWaveRetryJobs.filter(
                    job_type=DELETE_RETRY_JOB_TYPE,
                    status__in=["pending", "processing"],
                    next_retry_at__lte=now_utc,
                )
                .order_by("next_retry_at", "id")
                .first()
            )
            if not candidate:
                return None

            claimed = await RemnaWaveRetryJobs.filter(
                id=candidate.id,
                job_type=DELETE_RETRY_JOB_TYPE,
                status=candidate.status,
                next_retry_at__lte=now_utc,
            ).update(
                status="processing",
                next_retry_at=lease_until,
            )
            if claimed:
                candidate.status = "processing"
                candidate.next_retry_at = lease_until
                return candidate, lease_until

    async def _finalize_owned_job(job_id: int, lease_until: datetime, **updates: Any) -> bool:
        updated = await RemnaWaveRetryJobs.filter(
            id=job_id,
            status="processing",
            next_retry_at=lease_until,
        ).update(**updates)
        if updated:
            return True
        logger.warning(
            "RemnaWave delete retry ownership lost, skipping finalize: job=%s",
            job_id,
        )
        return False

    while stats["processed"] < int(batch_limit):
        now = datetime.now(timezone.utc)
        claimed = await _claim_next_ready_job(now)
        if not claimed:
            break
        job, lease_until = claimed

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
                finalized = await _finalize_owned_job(
                    job.id,
                    lease_until,
                    attempts=job.attempts,
                    status="done",
                    last_error=None,
                )
                if finalized:
                    stats["completed"] += 1
                continue

            error_text = str(result.get("error") or "Unknown error")
            if not is_remnawave_transient_error(error_text) or job.attempts >= DELETE_RETRY_MAX_ATTEMPTS:
                finalized = await _finalize_owned_job(
                    job.id,
                    lease_until,
                    attempts=job.attempts,
                    status="dead_letter",
                    last_error=error_text[:1024],
                )
                if finalized:
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
            finalize_now = datetime.now(timezone.utc)
            finalized = await _finalize_owned_job(
                job.id,
                lease_until,
                attempts=job.attempts,
                status="pending",
                next_retry_at=finalize_now + timedelta(seconds=delay_seconds),
                last_error=error_text[:1024],
            )
            if finalized:
                stats["rescheduled"] += 1
        except Exception as exc:
            error_text = str(exc)
            if job.attempts >= DELETE_RETRY_MAX_ATTEMPTS:
                finalized = await _finalize_owned_job(
                    job.id,
                    lease_until,
                    attempts=job.attempts,
                    status="dead_letter",
                    last_error=error_text[:1024],
                )
                if finalized:
                    stats["dead_letter"] += 1
                    logger.exception(
                        "RemnaWave delete retry hard-failed to dead-letter: job=%s user=%s",
                        job.id,
                        job.user_id,
                    )
                continue

            delay_seconds = _retry_backoff_seconds(job.attempts)
            finalize_now = datetime.now(timezone.utc)
            finalized = await _finalize_owned_job(
                job.id,
                lease_until,
                attempts=job.attempts,
                status="pending",
                next_retry_at=finalize_now + timedelta(seconds=delay_seconds),
                last_error=error_text[:1024],
            )
            if finalized:
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


def _geometric_sum(multiplier: float, device_count: int) -> float:
    return Tariffs._geometric_sum(multiplier, max(1, int(device_count or 1)))


def _calculate_price(base_price: int, multiplier: float, device_count: int) -> int:
    return Tariffs._calculate_with_params(max(1, int(base_price or 1)), multiplier, max(1, int(device_count or 1)))


def _solve_multiplier_for_anchor(*, base_price: int, anchor_device_count: int, anchor_total_price: int) -> float:
    devices = max(1, int(anchor_device_count or 1))
    target = max(1, int(anchor_total_price or base_price))
    if devices <= 1 or target <= base_price:
        return 0.9
    lo = 0.1
    hi = 0.9999
    for _ in range(80):
        mid = (lo + hi) / 2.0
        current = _calculate_price(base_price, mid, devices)
        if current > target:
            hi = mid
        else:
            lo = mid
    return Tariffs._sanitize_multiplier((lo + hi) / 2.0)


def _first_present(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return None


def _preview_rows(*, base_price: int, multiplier: float, devices_max: int, lte_price_per_gb: float, lte_examples: tuple[int, ...] = (0, 10)) -> list[Dict[str, Any]]:
    candidates = [1, 2, 5, 10, 30]
    rows: list[Dict[str, Any]] = []
    for devices in candidates:
        if devices > devices_max:
            continue
        subscription_price = _calculate_price(base_price, multiplier, devices)
        lte_rows = []
        for gb in lte_examples:
            if gb <= 0:
                continue
            lte_price = int(round(gb * lte_price_per_gb))
            lte_rows.append({"gb": gb, "priceRub": lte_price, "totalRub": subscription_price + lte_price})
        rows.append(
            {
                "deviceCount": devices,
                "tariffKind": "family" if devices >= family_devices_threshold() else "base",
                "totalRub": subscription_price,
                "avgPerDeviceRub": int(round(subscription_price / devices)),
                "lteExamples": lte_rows,
            }
        )
    return rows


async def compute_tariff_effective_pricing(
    *,
    tariff_id: Optional[int],
    patch: Dict[str, Any],
) -> Dict[str, Any]:
    existing = await Tariffs.get_or_none(id=tariff_id) if tariff_id is not None else None
    patch = patch or {}

    raw_one_device = _first_present(
        patch,
        "price_per_device",
        "price_for_one_device",
        "price_1_device",
        "one_device_price",
        "base_price",
        "final_price_default",
    )
    fallback_base = existing.base_price if existing else 1
    raw_base_value = _to_int(raw_one_device, fallback_base)
    base = max(1, raw_base_value)

    raw_devices_max = _first_present(patch, "devices_max", "devices_limit_family", "max_devices")
    devices_max = max(1, _to_int(raw_devices_max, existing.devices_limit_family if existing else subscription_devices_max()))
    configured_devices_max = subscription_devices_max()

    anchor_device_count = max(1, _to_int(_first_present(patch, "anchor_device_count", "anchorDevices", "max_devices"), devices_max))
    anchor_device_count = min(anchor_device_count, devices_max)
    anchor_total_raw = _first_present(patch, "anchor_total_price", "anchorTotalPrice", "target_final_price", "final_price_family")

    progressive_multiplier = _to_float(
        patch.get("progressive_multiplier"),
        existing.progressive_multiplier if existing else 0.9,
    )
    if anchor_total_raw is not None:
        progressive_multiplier = _solve_multiplier_for_anchor(
            base_price=base,
            anchor_device_count=anchor_device_count,
            anchor_total_price=max(base, _to_int(anchor_total_raw, base)),
        )
    progressive_multiplier = Tariffs._sanitize_multiplier(progressive_multiplier)

    lte_enabled = bool(_first_present(patch, "lte_enabled") if "lte_enabled" in patch else (existing.lte_enabled if existing else True))
    lte_price_per_gb = max(0.0, _to_float(patch.get("lte_price_per_gb"), existing.lte_price_per_gb if existing else lte_default_price_per_gb()))
    lte_min_gb = max(0, _to_int(patch.get("lte_min_gb"), getattr(existing, "lte_min_gb", 0) if existing else 0))
    lte_max_gb = max(0, _to_int(patch.get("lte_max_gb"), getattr(existing, "lte_max_gb", 500) if existing else 500))
    raw_lte_step_gb = _to_int(patch.get("lte_step_gb"), getattr(existing, "lte_step_gb", 1) if existing else 1)
    lte_step_gb = max(1, raw_lte_step_gb)

    warnings: list[Dict[str, str]] = []
    blocking_errors: list[Dict[str, str]] = []
    if devices_max > configured_devices_max:
        blocking_errors.append({"field": "devices_limit_family", "message": f"Максимум устройств не должен превышать {configured_devices_max}"})
    if raw_base_value <= 0:
        blocking_errors.append({"field": "price_per_device", "message": "Цена за 1 устройство должна быть больше 0"})
    if anchor_total_raw is not None and _to_int(anchor_total_raw, 0) < base:
        blocking_errors.append({"field": "anchor_total_price", "message": "Anchor price не может быть ниже цены одного устройства"})
    if progressive_multiplier >= 0.98:
        warnings.append({"field": "progressive_multiplier", "message": "Скидочная кривая почти линейная: каждое следующее устройство почти по полной цене"})
    if progressive_multiplier <= 0.2:
        warnings.append({"field": "progressive_multiplier", "message": "Слишком агрессивная скидочная кривая, проверьте preview"})
    if lte_enabled and lte_price_per_gb <= 0:
        warnings.append({"field": "lte_price_per_gb", "message": "LTE включён, но цена за 1 ГБ равна 0"})
    if lte_max_gb < lte_min_gb:
        blocking_errors.append({"field": "lte_max_gb", "message": "Максимум LTE не может быть меньше минимума"})
    if raw_lte_step_gb <= 0:
        blocking_errors.append({"field": "lte_step_gb", "message": "Шаг LTE должен быть больше 0"})

    computed = {
        "base_price": int(base),
        "progressive_multiplier": float(progressive_multiplier),
        "devices_limit_default": 1,
        "devices_limit_family": int(devices_max),
        "family_plan_enabled": False,
        "final_price_default": int(base),
        "final_price_family": _calculate_price(base, progressive_multiplier, devices_max),
        "lte_enabled": bool(lte_enabled),
        "lte_price_per_gb": float(lte_price_per_gb),
        "lte_min_gb": int(lte_min_gb),
        "lte_max_gb": int(lte_max_gb),
        "lte_step_gb": int(lte_step_gb),
    }
    preview = _preview_rows(
        base_price=int(base),
        multiplier=float(progressive_multiplier),
        devices_max=int(devices_max),
        lte_price_per_gb=float(lte_price_per_gb if lte_enabled else 0.0),
    )
    return {
        "ok": len(blocking_errors) == 0,
        "computed": computed,
        "preview": preview,
        "warnings": warnings,
        "blockingErrors": blocking_errors,
        "blocking_errors": blocking_errors,
    }


async def preview_tariff_quote_rows(*, tariff_id: Optional[int], patch: Dict[str, Any]) -> Dict[str, Any]:
    return await compute_tariff_effective_pricing(tariff_id=tariff_id, patch=patch)
