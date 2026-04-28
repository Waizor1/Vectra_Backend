from datetime import date, datetime, timedelta, timezone
from time import monotonic
from typing import Any

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from bloobcat.bot.notifications.admin import notify_frozen_base_auto_resumed_admin
from bloobcat.bot.notifications.subscription.renewal import (
    notify_frozen_base_auto_resumed_success,
)
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.notifications import NotificationMarks
from bloobcat.db.subscription_freezes import SubscriptionFreezes
from bloobcat.db.users import Users, normalize_date
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings
from bloobcat.services.subscription_limits import family_devices_threshold, tariff_kind_for_device_count


logger = get_logger("subscription_overlay")


VALID_TARIFF_KINDS = frozenset({"base", "family"})
FREEZE_REASON_FAMILY_OVERLAY = "family_overlay"
FREEZE_REASON_BASE_OVERLAY = "base_overlay"
STALE_WARNING_THROTTLE_SECONDS = 60.0
STALE_WARNING_CACHE_MAX_SIZE = 512
REVERSE_MIGRATION_COOLDOWN_SECONDS = 60 * 60
SUBSCRIPTION_RESUME_NOTIFY_MARK_TYPE = "subscription_resume_notify"
SUBSCRIPTION_RESUME_NOTIFY_PENDING_SUFFIX = ":pending"
SUBSCRIPTION_RESUME_NOTIFY_PENDING_TTL_SECONDS = 300
_stale_warning_cache: dict[tuple[int, int, str], float] = {}


class FrozenBaseActivationError(RuntimeError):
    def __init__(self, *, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class FrozenFamilyActivationError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retry_after_seconds: int | None = None,
        reverse_migration_available_at: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.reverse_migration_available_at = reverse_migration_available_at


def _now_monotonic() -> float:
    return monotonic()


def _resume_notification_sent_meta(*, freeze_id: int) -> str:
    return f"freeze:{int(freeze_id)}"


def _resume_notification_pending_meta(*, freeze_id: int) -> str:
    return f"{_resume_notification_sent_meta(freeze_id=freeze_id)}{SUBSCRIPTION_RESUME_NOTIFY_PENDING_SUFFIX}"


def _is_resume_pending_mark_stale(
    mark: NotificationMarks, *, now: datetime | None = None
) -> bool:
    created_at = _normalize_utc_datetime(getattr(mark, "sent_at", None))
    current = _normalize_utc_datetime(now) or datetime.now(timezone.utc)
    if created_at is None:
        return True
    return (
        current - created_at
    ).total_seconds() >= SUBSCRIPTION_RESUME_NOTIFY_PENDING_TTL_SECONDS


async def _claim_resume_notification_pending_mark(
    *, user_id: int, freeze_id: int, channel: str
) -> bool:
    sent_meta = _resume_notification_sent_meta(freeze_id=freeze_id)
    pending_meta = _resume_notification_pending_meta(freeze_id=freeze_id)

    if await NotificationMarks.get_or_none(
        user_id=int(user_id),
        type=SUBSCRIPTION_RESUME_NOTIFY_MARK_TYPE,
        key=channel,
        meta=sent_meta,
    ):
        return False

    pending_mark = await NotificationMarks.get_or_none(
        user_id=int(user_id),
        type=SUBSCRIPTION_RESUME_NOTIFY_MARK_TYPE,
        key=channel,
        meta=pending_meta,
    )
    if pending_mark is not None:
        if not _is_resume_pending_mark_stale(pending_mark):
            return False
        await NotificationMarks.filter(id=pending_mark.id).delete()

    try:
        await NotificationMarks.create(
            user_id=user_id,
            type=SUBSCRIPTION_RESUME_NOTIFY_MARK_TYPE,
            key=channel,
            meta=pending_meta,
        )
        return True
    except IntegrityError:
        return False


async def _release_resume_notification_pending_mark(
    *, user_id: int, freeze_id: int, channel: str
) -> None:
    await NotificationMarks.filter(
        user_id=int(user_id),
        type=SUBSCRIPTION_RESUME_NOTIFY_MARK_TYPE,
        key=channel,
        meta=_resume_notification_pending_meta(freeze_id=freeze_id),
    ).delete()


async def _finalize_resume_notification_mark(
    *, user_id: int, freeze_id: int, channel: str
) -> None:
    try:
        await NotificationMarks.create(
            user_id=int(user_id),
            type=SUBSCRIPTION_RESUME_NOTIFY_MARK_TYPE,
            key=channel,
            meta=_resume_notification_sent_meta(freeze_id=freeze_id),
        )
    except IntegrityError:
        pass
    await _release_resume_notification_pending_mark(
        user_id=int(user_id), freeze_id=freeze_id, channel=channel
    )


async def _get_latest_resumed_freeze(
    *, user_id: int, freeze_reason: str = FREEZE_REASON_FAMILY_OVERLAY
) -> SubscriptionFreezes | None:
    return (
        await SubscriptionFreezes.filter(
            user_id=int(user_id),
            freeze_reason=freeze_reason,
            is_active=False,
            resume_applied=True,
        )
        .order_by("-resumed_at", "-id")
        .first()
    )


async def _notify_frozen_base_auto_resumed_once(
    *,
    user_id: int,
    freeze_id: int,
    restored_days: int,
    restored_until,
) -> None:
    user = await Users.get_or_none(id=user_id)
    if user is None:
        return

    user_mark_claimed = await _claim_resume_notification_pending_mark(
        user_id=int(user.id), freeze_id=freeze_id, channel="user"
    )
    if user_mark_claimed:
        try:
            await notify_frozen_base_auto_resumed_success(
                user,
                restored_days=restored_days,
                restored_until=restored_until,
            )
            await _finalize_resume_notification_mark(
                user_id=int(user.id), freeze_id=freeze_id, channel="user"
            )
        except Exception as exc:
            await _release_resume_notification_pending_mark(
                user_id=int(user.id), freeze_id=freeze_id, channel="user"
            )
            logger.warning(
                "Failed to send frozen base auto-resume user notification user=%s freeze_id=%s err=%s",
                user.id,
                freeze_id,
                exc,
            )
    else:
        logger.info(
            "Frozen base auto-resume user notification already pending/sent user=%s freeze_id=%s",
            user.id,
            freeze_id,
        )

    admin_mark_claimed = await _claim_resume_notification_pending_mark(
        user_id=int(user.id), freeze_id=freeze_id, channel="admin"
    )
    if admin_mark_claimed:
        try:
            await notify_frozen_base_auto_resumed_admin(
                user,
                freeze_id=freeze_id,
                restored_days=restored_days,
                restored_until=restored_until,
            )
            await _finalize_resume_notification_mark(
                user_id=int(user.id), freeze_id=freeze_id, channel="admin"
            )
        except Exception as exc:
            await _release_resume_notification_pending_mark(
                user_id=int(user.id), freeze_id=freeze_id, channel="admin"
            )
            logger.warning(
                "Failed to send frozen base auto-resume admin notification user=%s freeze_id=%s err=%s",
                user.id,
                freeze_id,
                exc,
            )
    else:
        logger.info(
            "Frozen base auto-resume admin notification already pending/sent user=%s freeze_id=%s",
            user.id,
            freeze_id,
        )


async def _replay_frozen_base_auto_resumed_notifications_if_needed(user: Users) -> None:
    freeze = await _get_latest_resumed_freeze(user_id=int(user.id))
    if freeze is None:
        return
    await _notify_frozen_base_auto_resumed_once(
        user_id=int(user.id),
        freeze_id=int(freeze.id),
        restored_days=max(0, int(freeze.base_remaining_days or 0)),
        restored_until=normalize_date(user.expired_at)
        or (
            date.today() + timedelta(days=max(0, int(freeze.base_remaining_days or 0)))
        ),
    )


def _normalize_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_base_tariff_snapshot(
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    if not snapshot:
        return {}

    normalized: dict[str, Any] = {}
    if "base_tariff_name" in snapshot:
        normalized["base_tariff_name"] = snapshot.get("base_tariff_name") or None
    if snapshot.get("base_tariff_months") is not None:
        normalized["base_tariff_months"] = int(snapshot["base_tariff_months"])
    if snapshot.get("base_tariff_price") is not None:
        normalized["base_tariff_price"] = int(snapshot["base_tariff_price"])
    if snapshot.get("base_hwid_limit") is not None:
        normalized["base_hwid_limit"] = max(1, int(snapshot["base_hwid_limit"]))
    if snapshot.get("base_lte_gb_total") is not None:
        normalized["base_lte_gb_total"] = max(0, int(snapshot["base_lte_gb_total"]))
    if snapshot.get("base_lte_gb_used") is not None:
        normalized["base_lte_gb_used"] = max(0.0, float(snapshot["base_lte_gb_used"]))
    if snapshot.get("base_lte_price_per_gb") is not None:
        normalized["base_lte_price_per_gb"] = float(snapshot["base_lte_price_per_gb"])
    if snapshot.get("base_progressive_multiplier") is not None:
        normalized["base_progressive_multiplier"] = float(
            snapshot["base_progressive_multiplier"]
        )
    if snapshot.get("base_residual_day_fraction") is not None:
        normalized["base_residual_day_fraction"] = float(
            snapshot["base_residual_day_fraction"]
        )
    return normalized


def _reverse_migration_available_at(freeze: SubscriptionFreezes) -> datetime:
    created_at = _normalize_utc_datetime(getattr(freeze, "created_at", None))
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    return created_at + timedelta(seconds=REVERSE_MIGRATION_COOLDOWN_SECONDS)


def _compute_reverse_migration_retry_after_seconds(
    available_at: datetime, *, now: datetime | None = None
) -> int:
    current = _normalize_utc_datetime(now) or datetime.now(timezone.utc)
    return max(0, int((available_at - current).total_seconds()))


def _prune_stale_warning_cache(*, now: float) -> None:
    min_allowed_ts = now - STALE_WARNING_THROTTLE_SECONDS
    stale_keys = [
        key for key, ts in _stale_warning_cache.items() if ts < min_allowed_ts
    ]
    for key in stale_keys:
        _stale_warning_cache.pop(key, None)

    overflow = len(_stale_warning_cache) - int(STALE_WARNING_CACHE_MAX_SIZE)
    if overflow <= 0:
        return

    oldest_keys = sorted(_stale_warning_cache.items(), key=lambda item: item[1])[
        :overflow
    ]
    for key, _ in oldest_keys:
        _stale_warning_cache.pop(key, None)


def _should_emit_stale_overlay_warning(
    *, user_id: int, freeze_id: int, reason: str
) -> bool:
    now = _now_monotonic()
    _prune_stale_warning_cache(now=now)

    key = (int(user_id), int(freeze_id), str(reason))
    last_seen = _stale_warning_cache.get(key)
    if last_seen is not None and (now - last_seen) < STALE_WARNING_THROTTLE_SECONDS:
        return False

    _stale_warning_cache[key] = now
    _prune_stale_warning_cache(now=now)
    return True


def family_devices_limit() -> int:
    # Compatibility name: this is now the family threshold, not capacity.
    return family_devices_threshold()


def is_family_purchase(months: int, device_count: int) -> bool:
    return int(device_count or 1) >= family_devices_threshold()


def normalize_tariff_kind(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in VALID_TARIFF_KINDS:
        return normalized
    return None


def resolve_tariff_kind_by_limits(
    *,
    months: int,
    device_count: int,
    default_devices_limit: int | None = None,
    family_devices: int | None = None,
    family_plan_enabled: bool = True,
) -> str:
    # New tariff model: product kind is driven only by selected device count.
    # Duration and legacy family-card flags are kept in the signature for old callers.
    return tariff_kind_for_device_count(max(1, int(device_count or 1)))


def _active_freezes_queryset(
    *, user_id: int, conn: Any | None = None, freeze_reason: str | None = None
):
    qs = SubscriptionFreezes.filter(
        user_id=int(user_id),
        is_active=True,
        resume_applied=False,
    ).order_by("-id")
    if freeze_reason is not None:
        qs = qs.filter(freeze_reason=str(freeze_reason))
    if conn is not None:
        qs = qs.using_db(conn)
    return qs


async def _deactivate_duplicate_active_freezes(
    *, user_id: int, keep_id: int, conn: Any | None = None
) -> None:
    dup_qs = SubscriptionFreezes.filter(
        user_id=int(user_id),
        is_active=True,
        resume_applied=False,
    ).exclude(id=keep_id)
    if conn is not None:
        dup_qs = dup_qs.using_db(conn)
    deactivated = await dup_qs.update(
        is_active=False,
        updated_at=datetime.now(timezone.utc),
    )
    if deactivated:
        logger.warning(
            "Deactivated duplicate active subscription_freezes rows user={} count={} keep_id={}",
            user_id,
            deactivated,
            keep_id,
        )


async def _get_latest_active_freeze(
    *,
    user_id: int,
    for_update: bool = False,
    conn: Any | None = None,
    freeze_reason: str | None = None,
) -> SubscriptionFreezes | None:
    qs = _active_freezes_queryset(
        user_id=int(user_id), conn=conn, freeze_reason=freeze_reason
    )
    if for_update:
        qs = qs.select_for_update()
    rows = await qs.limit(2)
    if not rows:
        return None

    latest = rows[0]
    if len(rows) > 1:
        await _deactivate_duplicate_active_freezes(
            user_id=int(user_id), keep_id=int(latest.id), conn=conn
        )
    return latest


async def get_active_base_overlay(user: Users) -> SubscriptionFreezes | None:
    freeze = await _get_latest_active_freeze(
        user_id=int(user.id),
        for_update=False,
        freeze_reason=FREEZE_REASON_BASE_OVERLAY,
    )
    if not freeze:
        return None

    base_overlay_exp = normalize_date(freeze.family_expires_at)
    if not base_overlay_exp or base_overlay_exp < date.today():
        return None
    return freeze


async def freeze_base_subscription_if_needed(
    user: Users, *, family_expires_at: date
) -> None:
    today = date.today()
    current_exp = normalize_date(user.expired_at)
    remaining_days = max(0, (current_exp - today).days) if current_exp else 0
    if remaining_days <= 0:
        return

    now = datetime.now(timezone.utc)
    async with in_transaction() as conn:
        existing = await _get_latest_active_freeze(
            user_id=int(user.id),
            for_update=True,
            conn=conn,
        )
        if existing:
            existing_reason = str(existing.freeze_reason or "").strip().lower()
            existing_family_exp = normalize_date(existing.family_expires_at)
            if existing_reason == FREEZE_REASON_FAMILY_OVERLAY:
                if (
                    existing_family_exp is None
                    or family_expires_at > existing_family_exp
                ):
                    await (
                        SubscriptionFreezes.filter(id=existing.id)
                        .using_db(conn)
                        .update(
                            family_expires_at=family_expires_at,
                            updated_at=now,
                        )
                    )
                return

            await (
                SubscriptionFreezes.filter(id=existing.id)
                .using_db(conn)
                .update(
                    is_active=False,
                    resume_applied=False,
                    last_resume_error="superseded_by_family_purchase",
                    updated_at=now,
                )
            )

        active_tariff = None
        if user.active_tariff_id:
            active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)

        payload = {
            "user_id": int(user.id),
            "freeze_reason": FREEZE_REASON_FAMILY_OVERLAY,
            "base_remaining_days": int(remaining_days),
            "base_expires_at_snapshot": current_exp,
            "family_expires_at": family_expires_at,
            "base_tariff_name": (active_tariff.name if active_tariff else None),
            "base_tariff_months": (
                int(active_tariff.months) if active_tariff else None
            ),
            "base_tariff_price": (int(active_tariff.price) if active_tariff else None),
            "base_hwid_limit": (
                int(active_tariff.hwid_limit)
                if active_tariff
                else int(user.hwid_limit or 1)
            ),
            "base_lte_gb_total": (
                int(active_tariff.lte_gb_total or 0)
                if active_tariff
                else int(user.lte_gb_total or 0)
            ),
            "base_lte_gb_used": (
                float(active_tariff.lte_gb_used or 0.0) if active_tariff else 0.0
            ),
            "base_lte_price_per_gb": (
                float(active_tariff.lte_price_per_gb or 0) if active_tariff else 0.0
            ),
            "base_progressive_multiplier": (
                float(active_tariff.progressive_multiplier or 0)
                if active_tariff
                else 0.0
            ),
            "base_residual_day_fraction": (
                float(active_tariff.residual_day_fraction or 0)
                if active_tariff
                else 0.0
            ),
        }

        try:
            await SubscriptionFreezes.create(using_db=conn, **payload)
        except IntegrityError:
            # Unique partial index can race under concurrent purchases.
            existing = await _get_latest_active_freeze(
                user_id=int(user.id),
                for_update=True,
                conn=conn,
                freeze_reason=FREEZE_REASON_FAMILY_OVERLAY,
            )
            if existing:
                existing_family_exp = normalize_date(existing.family_expires_at)
                if (
                    existing_family_exp is None
                    or family_expires_at > existing_family_exp
                ):
                    await (
                        SubscriptionFreezes.filter(id=existing.id)
                        .using_db(conn)
                        .update(
                            family_expires_at=family_expires_at,
                            updated_at=now,
                        )
                    )


async def apply_base_purchase_to_frozen_base_if_active(
    user: Users,
    *,
    purchased_days: int,
    base_tariff_snapshot: dict[str, Any] | None = None,
) -> bool:
    added_days = max(0, int(purchased_days or 0))
    if added_days <= 0:
        return False

    today = date.today()
    now = datetime.now(timezone.utc)
    async with in_transaction() as conn:
        freeze = await _get_latest_active_freeze(
            user_id=int(user.id),
            for_update=True,
            conn=conn,
            freeze_reason=FREEZE_REASON_FAMILY_OVERLAY,
        )
        if not freeze:
            return False

        family_exp = normalize_date(freeze.family_expires_at)
        if not family_exp or family_exp < today:
            return False

        new_remaining_days = max(0, int(freeze.base_remaining_days or 0)) + added_days
        snapshot_updates = _normalize_base_tariff_snapshot(base_tariff_snapshot)
        await (
            SubscriptionFreezes.filter(id=freeze.id)
            .using_db(conn)
            .update(
                base_remaining_days=new_remaining_days,
                base_expires_at_snapshot=(today + timedelta(days=new_remaining_days)),
                **snapshot_updates,
                updated_at=now,
            )
        )
        return True


async def has_active_family_overlay(user: Users) -> bool:
    freeze = await _get_latest_active_freeze(
        user_id=int(user.id),
        for_update=False,
        freeze_reason=FREEZE_REASON_FAMILY_OVERLAY,
    )
    if not freeze:
        return False
    family_exp = normalize_date(freeze.family_expires_at)
    return bool(family_exp and family_exp >= date.today())


async def activate_frozen_base_with_current_freeze(user: Users) -> dict[str, int | str]:
    today = date.today()
    now = datetime.now(timezone.utc)

    async with in_transaction() as conn:
        freeze = await _get_latest_active_freeze(
            user_id=int(user.id),
            for_update=True,
            conn=conn,
            freeze_reason=FREEZE_REASON_FAMILY_OVERLAY,
        )
        if not freeze:
            raise FrozenBaseActivationError(
                code="FROZEN_BASE_NOT_FOUND",
                message="Замороженный базовый тариф не найден",
            )

        family_exp = normalize_date(freeze.family_expires_at)
        if not family_exp or family_exp < today:
            raise FrozenBaseActivationError(
                code="FROZEN_BASE_EXPIRED",
                message="Замороженный базовый тариф недоступен",
            )

        frozen_base_days = max(0, int(freeze.base_remaining_days or 0))
        if frozen_base_days <= 0:
            raise FrozenBaseActivationError(
                code="FROZEN_BASE_EMPTY",
                message="Замороженный базовый тариф не содержит оставшихся дней",
            )

        current_exp = normalize_date(user.expired_at)
        current_days = max(0, (current_exp - today).days) if current_exp else 0
        if current_days <= 0:
            raise FrozenBaseActivationError(
                code="CURRENT_SUBSCRIPTION_NOT_ACTIVE",
                message="Текущая подписка уже не активна",
            )

        current_active_tariff = None
        if user.active_tariff_id:
            current_active_tariff = await ActiveTariffs.get_or_none(
                id=user.active_tariff_id
            )

        current_snapshot = {
            "base_tariff_name": (
                current_active_tariff.name if current_active_tariff else None
            ),
            "base_tariff_months": (
                int(current_active_tariff.months) if current_active_tariff else None
            ),
            "base_tariff_price": (
                int(current_active_tariff.price) if current_active_tariff else None
            ),
            "base_hwid_limit": (
                int(current_active_tariff.hwid_limit)
                if current_active_tariff
                else int(user.hwid_limit or 1)
            ),
            "base_lte_gb_total": (
                int(current_active_tariff.lte_gb_total or 0)
                if current_active_tariff
                else int(user.lte_gb_total or 0)
            ),
            "base_lte_gb_used": (
                float(current_active_tariff.lte_gb_used or 0.0)
                if current_active_tariff
                else 0.0
            ),
            "base_lte_price_per_gb": (
                float(current_active_tariff.lte_price_per_gb or 0.0)
                if current_active_tariff
                else 0.0
            ),
            "base_progressive_multiplier": (
                float(current_active_tariff.progressive_multiplier or 0.0)
                if current_active_tariff
                else 0.0
            ),
            "base_residual_day_fraction": (
                float(current_active_tariff.residual_day_fraction or 0.0)
                if current_active_tariff
                else 0.0
            ),
        }

        if user.active_tariff_id:
            old_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
            if old_tariff:
                await old_tariff.delete(using_db=conn)

        new_active_id: str | None = None
        if (
            freeze.base_tariff_name
            and freeze.base_tariff_months
            and freeze.base_tariff_price is not None
        ):
            restored_tariff = await ActiveTariffs.create(
                user=user,
                name=str(freeze.base_tariff_name),
                months=int(freeze.base_tariff_months),
                price=int(freeze.base_tariff_price),
                hwid_limit=max(1, int(freeze.base_hwid_limit or 1)),
                lte_gb_total=max(0, int(freeze.base_lte_gb_total or 0)),
                lte_gb_used=max(0.0, float(freeze.base_lte_gb_used or 0.0)),
                lte_price_per_gb=float(freeze.base_lte_price_per_gb or 0.0),
                progressive_multiplier=float(freeze.base_progressive_multiplier or 0.0),
                residual_day_fraction=float(freeze.base_residual_day_fraction or 0.0),
                using_db=conn,
            )
            new_active_id = str(restored_tariff.id)

        switched_until = today + timedelta(days=frozen_base_days)
        await (
            Users.filter(id=user.id)
            .using_db(conn)
            .update(
                expired_at=switched_until,
                active_tariff_id=new_active_id,
                hwid_limit=max(1, int(freeze.base_hwid_limit or user.hwid_limit or 1)),
                lte_gb_total=max(0, int(freeze.base_lte_gb_total or 0)),
            )
        )

        await (
            SubscriptionFreezes.filter(id=freeze.id)
            .using_db(conn)
            .update(
                is_active=False,
                resume_applied=True,
                resumed_at=now,
                updated_at=now,
                last_resume_error=None,
            )
        )

        await SubscriptionFreezes.create(
            using_db=conn,
            user_id=int(user.id),
            freeze_reason=FREEZE_REASON_BASE_OVERLAY,
            is_active=True,
            resume_applied=False,
            base_remaining_days=int(current_days),
            base_expires_at_snapshot=current_exp,
            family_expires_at=switched_until,
            base_tariff_name=current_snapshot["base_tariff_name"],
            base_tariff_months=current_snapshot["base_tariff_months"],
            base_tariff_price=current_snapshot["base_tariff_price"],
            base_hwid_limit=current_snapshot["base_hwid_limit"],
            base_lte_gb_total=current_snapshot["base_lte_gb_total"],
            base_lte_gb_used=current_snapshot["base_lte_gb_used"],
            base_lte_price_per_gb=current_snapshot["base_lte_price_per_gb"],
            base_progressive_multiplier=current_snapshot["base_progressive_multiplier"],
            base_residual_day_fraction=current_snapshot["base_residual_day_fraction"],
        )

    return {
        "switched_until": switched_until.isoformat(),
        "frozen_current_days": int(current_days),
        "activated_frozen_base_days": int(frozen_base_days),
    }


async def activate_frozen_family_with_current_freeze(
    user: Users,
) -> dict[str, int | str]:
    today = date.today()
    now = datetime.now(timezone.utc)

    async with in_transaction() as conn:
        freeze = await _get_latest_active_freeze(
            user_id=int(user.id),
            for_update=True,
            conn=conn,
            freeze_reason=FREEZE_REASON_BASE_OVERLAY,
        )
        if not freeze:
            raise FrozenFamilyActivationError(
                code="FROZEN_FAMILY_NOT_FOUND",
                message="Замороженный семейный тариф не найден",
            )

        reverse_available_at = _reverse_migration_available_at(freeze)
        retry_after_seconds = _compute_reverse_migration_retry_after_seconds(
            reverse_available_at, now=now
        )
        if retry_after_seconds > 0:
            raise FrozenFamilyActivationError(
                code="FAMILY_RESTORE_COOLDOWN_ACTIVE",
                message="Возврат на семейный тариф временно недоступен",
                retry_after_seconds=retry_after_seconds,
                reverse_migration_available_at=reverse_available_at.isoformat(),
            )

        base_exp = normalize_date(freeze.family_expires_at)
        if not base_exp or base_exp < today:
            raise FrozenFamilyActivationError(
                code="FROZEN_FAMILY_EXPIRED",
                message="Замороженный семейный тариф недоступен",
            )

        frozen_family_days = max(0, int(freeze.base_remaining_days or 0))
        if frozen_family_days <= 0:
            raise FrozenFamilyActivationError(
                code="FROZEN_FAMILY_EMPTY",
                message="Замороженный семейный тариф не содержит оставшихся дней",
            )

        current_exp = normalize_date(user.expired_at)
        current_days = max(0, (current_exp - today).days) if current_exp else 0
        if current_days <= 0:
            raise FrozenFamilyActivationError(
                code="CURRENT_SUBSCRIPTION_NOT_ACTIVE",
                message="Текущая подписка уже не активна",
            )

        current_active_tariff = None
        if user.active_tariff_id:
            current_active_tariff = await ActiveTariffs.get_or_none(
                id=user.active_tariff_id
            )

        current_snapshot = {
            "base_tariff_name": (
                current_active_tariff.name if current_active_tariff else None
            ),
            "base_tariff_months": (
                int(current_active_tariff.months) if current_active_tariff else None
            ),
            "base_tariff_price": (
                int(current_active_tariff.price) if current_active_tariff else None
            ),
            "base_hwid_limit": (
                int(current_active_tariff.hwid_limit)
                if current_active_tariff
                else int(user.hwid_limit or 1)
            ),
            "base_lte_gb_total": (
                int(current_active_tariff.lte_gb_total or 0)
                if current_active_tariff
                else int(user.lte_gb_total or 0)
            ),
            "base_lte_gb_used": (
                float(current_active_tariff.lte_gb_used or 0.0)
                if current_active_tariff
                else 0.0
            ),
            "base_lte_price_per_gb": (
                float(current_active_tariff.lte_price_per_gb or 0.0)
                if current_active_tariff
                else 0.0
            ),
            "base_progressive_multiplier": (
                float(current_active_tariff.progressive_multiplier or 0.0)
                if current_active_tariff
                else 0.0
            ),
            "base_residual_day_fraction": (
                float(current_active_tariff.residual_day_fraction or 0.0)
                if current_active_tariff
                else 0.0
            ),
        }

        if user.active_tariff_id:
            old_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
            if old_tariff:
                await old_tariff.delete(using_db=conn)

        new_active_id: str | None = None
        if (
            freeze.base_tariff_name
            and freeze.base_tariff_months
            and freeze.base_tariff_price is not None
        ):
            restored_tariff = await ActiveTariffs.create(
                user=user,
                name=str(freeze.base_tariff_name),
                months=int(freeze.base_tariff_months),
                price=int(freeze.base_tariff_price),
                hwid_limit=max(1, int(freeze.base_hwid_limit or 1)),
                lte_gb_total=max(0, int(freeze.base_lte_gb_total or 0)),
                lte_gb_used=max(0.0, float(freeze.base_lte_gb_used or 0.0)),
                lte_price_per_gb=float(freeze.base_lte_price_per_gb or 0.0),
                progressive_multiplier=float(freeze.base_progressive_multiplier or 0.0),
                residual_day_fraction=float(freeze.base_residual_day_fraction or 0.0),
                using_db=conn,
            )
            new_active_id = str(restored_tariff.id)

        switched_until = today + timedelta(days=frozen_family_days)
        await (
            Users.filter(id=user.id)
            .using_db(conn)
            .update(
                expired_at=switched_until,
                active_tariff_id=new_active_id,
                hwid_limit=max(1, int(freeze.base_hwid_limit or user.hwid_limit or 1)),
                lte_gb_total=max(0, int(freeze.base_lte_gb_total or 0)),
            )
        )

        await (
            SubscriptionFreezes.filter(id=freeze.id)
            .using_db(conn)
            .update(
                is_active=False,
                resume_applied=True,
                resumed_at=now,
                updated_at=now,
                last_resume_error=None,
            )
        )

        await SubscriptionFreezes.create(
            using_db=conn,
            user_id=int(user.id),
            freeze_reason=FREEZE_REASON_FAMILY_OVERLAY,
            is_active=True,
            resume_applied=False,
            base_remaining_days=int(current_days),
            base_expires_at_snapshot=current_exp,
            family_expires_at=switched_until,
            base_tariff_name=current_snapshot["base_tariff_name"],
            base_tariff_months=current_snapshot["base_tariff_months"],
            base_tariff_price=current_snapshot["base_tariff_price"],
            base_hwid_limit=current_snapshot["base_hwid_limit"],
            base_lte_gb_total=current_snapshot["base_lte_gb_total"],
            base_lte_gb_used=current_snapshot["base_lte_gb_used"],
            base_lte_price_per_gb=current_snapshot["base_lte_price_per_gb"],
            base_progressive_multiplier=current_snapshot["base_progressive_multiplier"],
            base_residual_day_fraction=current_snapshot["base_residual_day_fraction"],
        )

    return {
        "switched_until": switched_until.isoformat(),
        "frozen_current_days": int(current_days),
        "activated_frozen_family_days": int(frozen_family_days),
    }


async def resume_frozen_base_if_due(user: Users, *, force: bool = False) -> bool:
    today = date.today()
    freeze_id: int | None = None
    restored_days = 0
    restored_expired_at = None
    try:
        async with in_transaction() as conn:
            now = datetime.now(timezone.utc)
            freeze = await _get_latest_active_freeze(
                user_id=int(user.id), for_update=True, conn=conn
            )
            if not freeze:
                should_replay_after_commit = True
                restored_days = 0
                restored_expired_at = None
            else:
                should_replay_after_commit = False
            if not freeze:
                pass
            else:
                family_expired = normalize_date(freeze.family_expires_at)
                user_expired_at = normalize_date(user.expired_at)

                # Drift guard: if current user expiry is ahead of stored family freeze expiry,
                # do not resume base yet; first align freeze expiry to the active paid period.
                if user_expired_at and (
                    family_expired is None or user_expired_at > family_expired
                ):
                    await (
                        SubscriptionFreezes.filter(id=freeze.id)
                        .using_db(conn)
                        .update(
                            family_expires_at=user_expired_at,
                            updated_at=now,
                            last_resume_error="family_expiry_resynced_from_user_expired_at",
                        )
                    )
                    logger.warning(
                        "Resynced stale family freeze expiry user={} freeze_id={} old_family_exp={} new_family_exp={}",
                        user.id,
                        freeze.id,
                        family_expired,
                        user_expired_at,
                    )
                    return False

                due = bool(family_expired and family_expired < today)
                if not force and not due:
                    return False

                freeze_id = int(freeze.id)
                restored_days = max(0, int(freeze.base_remaining_days or 0))
                await (
                    SubscriptionFreezes.filter(id=freeze_id)
                    .using_db(conn)
                    .update(
                        resume_attempt_count=int(freeze.resume_attempt_count or 0) + 1,
                        last_resume_error=None,
                    )
                )

                restored_expired_at = today + timedelta(days=restored_days)

                if user.active_tariff_id:
                    old_tariff = await ActiveTariffs.get_or_none(
                        id=user.active_tariff_id
                    )
                    if old_tariff:
                        await old_tariff.delete(using_db=conn)

                new_active_id: str | None = None
                if (
                    freeze.base_tariff_name
                    and freeze.base_tariff_months
                    and freeze.base_tariff_price is not None
                ):
                    restored_tariff = await ActiveTariffs.create(
                        user=user,
                        name=str(freeze.base_tariff_name),
                        months=int(freeze.base_tariff_months),
                        price=int(freeze.base_tariff_price),
                        hwid_limit=max(1, int(freeze.base_hwid_limit or 1)),
                        lte_gb_total=max(0, int(freeze.base_lte_gb_total or 0)),
                        lte_gb_used=max(0.0, float(freeze.base_lte_gb_used or 0.0)),
                        lte_price_per_gb=float(freeze.base_lte_price_per_gb or 0.0),
                        progressive_multiplier=float(
                            freeze.base_progressive_multiplier or 0.0
                        ),
                        residual_day_fraction=float(
                            freeze.base_residual_day_fraction or 0.0
                        ),
                        using_db=conn,
                    )
                    new_active_id = str(restored_tariff.id)

                await (
                    Users.filter(id=user.id)
                    .using_db(conn)
                    .update(
                        expired_at=restored_expired_at,
                        active_tariff_id=new_active_id,
                        hwid_limit=max(
                            1, int(freeze.base_hwid_limit or user.hwid_limit or 1)
                        ),
                        lte_gb_total=max(0, int(freeze.base_lte_gb_total or 0)),
                    )
                )
                await (
                    SubscriptionFreezes.filter(id=freeze_id)
                    .using_db(conn)
                    .update(
                        is_active=False,
                        resume_applied=True,
                        resumed_at=now,
                        updated_at=now,
                        last_resume_error=None,
                    )
                )
        if should_replay_after_commit:
            await _replay_frozen_base_auto_resumed_notifications_if_needed(user)
            return False
        if freeze_id is not None:
            await _notify_frozen_base_auto_resumed_once(
                user_id=int(user.id),
                freeze_id=freeze_id,
                restored_days=restored_days,
                restored_until=restored_expired_at,
            )
        return True
    except Exception as exc:
        now = datetime.now(timezone.utc)
        if freeze_id is not None:
            freeze_after_rollback = await SubscriptionFreezes.get_or_none(id=freeze_id)
            if freeze_after_rollback:
                await SubscriptionFreezes.filter(id=freeze_id).update(
                    resume_attempt_count=int(
                        freeze_after_rollback.resume_attempt_count or 0
                    )
                    + 1,
                    last_resume_error=str(exc)[:2000],
                    updated_at=now,
                )
        logger.error("Failed to resume frozen base for user={}: {}", user.id, exc)
        return False


async def get_overlay_payload(user: Users) -> dict:
    freeze = await _get_latest_active_freeze(user_id=int(user.id), for_update=False)
    if not freeze:
        return {
            "has_frozen_base": False,
            "active_kind": "base",
        }

    freeze_reason = str(freeze.freeze_reason or "").strip().lower()
    if freeze_reason == FREEZE_REASON_BASE_OVERLAY:
        base_overlay_exp = normalize_date(freeze.family_expires_at)
        frozen_family_days = max(0, int(freeze.base_remaining_days or 0))
        reverse_available_at = _reverse_migration_available_at(freeze)
        reverse_retry_after_seconds = _compute_reverse_migration_retry_after_seconds(
            reverse_available_at
        )
        return {
            "has_frozen_base": False,
            "has_frozen_family": frozen_family_days > 0,
            "frozen_family_remaining_days": frozen_family_days,
            "frozen_family_hwid_limit": int(
                freeze.base_hwid_limit or family_devices_limit()
            ),
            "frozen_family_resume_at": base_overlay_exp.isoformat()
            if base_overlay_exp
            else None,
            "reverse_migration_available_at": reverse_available_at.isoformat(),
            "reverse_migration_retry_after_seconds": int(reverse_retry_after_seconds),
            "active_kind": "base",
        }

    if freeze_reason != FREEZE_REASON_FAMILY_OVERLAY:
        return {
            "has_frozen_base": False,
            "active_kind": "base",
        }

    today = date.today()
    family_exp = normalize_date(freeze.family_expires_at)
    user_exp = normalize_date(user.expired_at)
    base_remaining_days = int(freeze.base_remaining_days or 0)

    active_kind = (
        "family" if (family_exp and user_exp and user_exp >= today) else "base"
    )

    if base_remaining_days <= 0:
        stale_reason = "stale_freeze_non_positive_base_remaining_days"
        if _should_emit_stale_overlay_warning(
            user_id=int(user.id), freeze_id=int(freeze.id), reason=stale_reason
        ):
            logger.warning(
                "Suppressed stale frozen overlay in payload only user={} freeze_id={} reason={}",
                user.id,
                freeze.id,
                stale_reason,
            )
        return {
            "has_frozen_base": False,
            "active_kind": active_kind,
        }

    stale_reason: str | None = None
    if not family_exp or family_exp < today:
        stale_reason = "stale_freeze_family_expired"
    elif not user_exp or user_exp < today:
        stale_reason = "stale_freeze_user_not_active"
    if stale_reason:
        if _should_emit_stale_overlay_warning(
            user_id=int(user.id), freeze_id=int(freeze.id), reason=stale_reason
        ):
            logger.warning(
                "Suppressed stale frozen overlay in payload only user={} freeze_id={} reason={}",
                user.id,
                freeze.id,
                stale_reason,
            )
        return {
            "has_frozen_base": False,
            "active_kind": "base",
        }

    return {
        "has_frozen_base": True,
        "base_remaining_days": base_remaining_days,
        "base_hwid_limit": int(freeze.base_hwid_limit or 1),
        "base_resume_at": family_exp.isoformat() if family_exp else None,
        "will_restore_base_after_family": bool(family_exp and family_exp >= today),
        "active_kind": active_kind,
    }
