from random import randint
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import asyncio
import random
import hashlib
import hmac
from functools import partial
from types import SimpleNamespace
from urllib.parse import urlparse
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Header, Request  # type: ignore
from pydantic import BaseModel
from yookassa import Configuration, Payment, Webhook
from yookassa.domain.notification import (
    WebhookNotification,
    WebhookNotificationEventType,
)
from urllib3.exceptions import ConnectTimeoutError, ReadTimeoutError
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    Timeout as RequestsTimeout,
)
from tortoise.expressions import F
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from bloobcat.bot.bot import get_bot_username
from bloobcat.bot.notifications.admin import (
    on_payment,
    cancel_subscription,
    notify_active_tariff_change,
    notify_lte_topup,
    notify_manual_payment_canceled,
)

# User-facing LTE top-up confirmation notification.
# Imported lazily-compatible: stub in tests that don't load the bot stack.
try:
    from bloobcat.bot.notifications.lte import notify_lte_topup_user
except ImportError:  # pragma: no cover
    async def notify_lte_topup_user(*args, **kwargs):  # type: ignore[misc]
        return False

# Notifications module can be stubbed in tests. Keep imports resilient.
try:
    from bloobcat.bot.notifications.general.referral import (
        on_referral_friend_bonus,
        on_referral_payment,
    )
except ImportError:  # pragma: no cover
    from bloobcat.bot.notifications.general.referral import on_referral_payment

    on_referral_friend_bonus = None  # type: ignore[assignment]

# Partner cashback notifications are optional (safe to stub in tests).
try:
    from bloobcat.bot.notifications.partner.earning import notify_partner_earning
except ImportError:  # pragma: no cover
    notify_partner_earning = None  # type: ignore[assignment]
from bloobcat.bot.notifications.subscription.renewal import (
    notify_auto_renewal_success_balance,
    notify_auto_renewal_failure,
    notify_family_purchase_success_yookassa,
    notify_renewal_success_yookassa,
    notify_payment_canceled_yookassa,
)
from bloobcat.bot.notifications.prize_wheel import notify_spin_awarded
from bloobcat.db.tariff import Tariffs
from bloobcat.db.users import Users, normalize_date
from bloobcat.funcs.referral_attribution import is_partner_source_utm
from bloobcat.funcs.validate import validate
from bloobcat.settings import (
    app_settings,
    yookassa_settings,
    payment_settings,
    platega_settings,
    remnawave_settings,
    telegram_settings,
)
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.partner_earnings import PartnerEarnings
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.logger import get_payment_logger
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.family_members import FamilyMembers
from bloobcat.db.notifications import NotificationMarks
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.routes.remnawave.hwid_utils import cleanup_user_hwid_devices
from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status
from bloobcat.services.discounts import (
    apply_personal_discount,
    consume_discount_if_needed,
    is_discount_available_if_needed,
)
from bloobcat.utils.dates import add_months_safe
from bloobcat.db.referral_rewards import ReferralRewards
from bloobcat.services.tariff_quote import build_subscription_quote, validate_device_count_for_tariff, validate_lte_gb_for_tariff
from bloobcat.services.segment_campaigns import select_active_campaign
from bloobcat.services.referral_gamification import award_referral_cashback
from bloobcat.services.platega import (
    PLATEGA_PROVIDER,
    PLATEGA_STATUS_CANCELED,
    PLATEGA_STATUS_CHARGEBACK,
    PLATEGA_STATUS_CHARGEBACKED,
    PLATEGA_STATUS_CONFIRMED,
    PLATEGA_STATUS_PENDING,
    PlategaAPIError,
    PlategaClient,
    PlategaConfigError,
    map_platega_status_to_internal,
    normalize_platega_status,
    parse_platega_payload,
)
from bloobcat.services.subscription_overlay import (
    apply_base_purchase_to_frozen_base_if_active,
    family_devices_limit,
    freeze_base_subscription_if_needed,
    get_active_base_overlay,
    get_overlay_payload,
    has_active_family_overlay,
    is_family_purchase,
    normalize_tariff_kind,
    resolve_tariff_kind_by_limits,
)

def _configure_yookassa_if_available() -> bool:
    shop_id = str(getattr(yookassa_settings, "shop_id", "") or "").strip()
    secret_key = (
        yookassa_settings.secret_key.get_secret_value()
        if getattr(yookassa_settings, "secret_key", None)
        else ""
    ).strip()
    if not shop_id or not secret_key:
        return False
    Configuration.account_id = shop_id
    Configuration.secret_key = secret_key
    return True


# YooKassa stays in the codebase as an operator-controlled rollback path.
# It is configured only when credentials exist, so Platega-first deployments can
# boot without YooKassa env vars and without accidental YooKassa API traffic.
_configure_yookassa_if_available()

router = APIRouter(prefix="/pay", tags=["pay"])
# NOTE: YooKassa webhook URL is sometimes configured without "/pay" prefix.
# We expose the same handler on both paths:
# - /pay/webhook/yookassa/{secret}   (default, under /pay)
# - /webhook/yookassa/{secret}       (alias, no /pay)
webhook_router = APIRouter(tags=["pay"])
logger = get_payment_logger()

BYTES_IN_GB = 1024**3
MSK_TZ = timezone(timedelta(hours=3))
PAYMENT_PROCESSING_STALE_SECONDS = 420
PAYMENT_EXTERNAL_CALL_TIMEOUT_SECONDS = 120
PAYMENT_FAST_STATUS_REMNAWAVE_TIMEOUT_SECONDS = 2.0
PAYMENT_NOTIFICATION_MARK_TYPE = "payment_notify"
PAYMENT_NOTIFICATION_MARK_KEY_LIMIT = 64
CLIENT_REQUEST_ID_MAX_LENGTH = 64
CLIENT_REQUEST_ID_ALLOWED_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:"
)
PAYMENT_PROVIDER_YOOKASSA = "yookassa"
PAYMENT_PROVIDER_PLATEGA = PLATEGA_PROVIDER
PAYMENT_CURRENCY_RUB = "RUB"


async def _sync_device_per_user_after_payment(user: Users, *, source: str) -> bool:
    if not user.is_device_per_user_enabled():
        return False
    try:
        from bloobcat.services.device_service import sync_device_entitlements

        await sync_device_entitlements(user)
        logger.info(
            "Device-per-user entitlements synced after payment source=%s user=%s",
            source,
            user.id,
        )
    except Exception as exc:
        logger.error(
            "Device-per-user entitlement sync failed source=%s user=%s err=%s",
            source,
            user.id,
            exc,
            exc_info=True,
        )
    return True


def _active_payment_provider() -> str:
    provider = str(getattr(payment_settings, "provider", "") or "").strip().lower()
    if provider == PAYMENT_PROVIDER_PLATEGA:
        return PAYMENT_PROVIDER_PLATEGA
    return PAYMENT_PROVIDER_YOOKASSA


def _auto_renewal_uses_yookassa() -> bool:
    mode = str(
        getattr(payment_settings, "auto_renewal_mode", "") or ""
    ).strip().lower()
    return mode == PAYMENT_PROVIDER_YOOKASSA


def _provider_payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# Допустимые значения колонки ProcessedPayments.payment_purpose.
# NULL означает «subscription» (legacy-default до миграции 116).
PAYMENT_PURPOSE_SUBSCRIPTION = "subscription"
PAYMENT_PURPOSE_LTE_TOPUP = "lte_topup"
PAYMENT_PURPOSE_DEVICES_TOPUP = "devices_topup"
PAYMENT_PURPOSE_UPGRADE_BUNDLE = "upgrade_bundle"


def _derive_payment_purpose(
    provider_payload: str | None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Определяет назначение платежа по metadata-флагам.

    Используется маркетинговой логикой сегментов (resolve_user_segments),
    чтобы топапы трафика и устройств не считались «первой покупкой
    подписки». Возвращает None, если флаги не выставлены — резолвер
    интерпретирует None как `subscription`.
    """

    if metadata is None:
        if not provider_payload:
            return None
        try:
            parsed = json.loads(str(provider_payload))
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        raw_meta = parsed.get("metadata")
        metadata = raw_meta if isinstance(raw_meta, dict) else {}
    def _is_truthy_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes"}
        return False

    if _is_truthy_flag(metadata.get("upgrade_bundle")) or (
        str(metadata.get("payment_purpose") or "").strip().lower()
        == PAYMENT_PURPOSE_UPGRADE_BUNDLE
    ):
        return PAYMENT_PURPOSE_UPGRADE_BUNDLE
    if _is_truthy_flag(metadata.get("lte_topup")):
        return PAYMENT_PURPOSE_LTE_TOPUP
    if _is_truthy_flag(metadata.get("devices_topup")):
        return PAYMENT_PURPOSE_DEVICES_TOPUP
    return None


def _load_provider_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _metadata_from_processed_payment(row: ProcessedPayments | None) -> dict[str, Any]:
    if not row:
        return {}
    provider_payload = _load_provider_payload(getattr(row, "provider_payload", None))
    metadata = provider_payload.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _platega_payment_stub(
    *,
    payment_id: str,
    amount: float,
    metadata: dict[str, Any],
) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(payment_id),
        amount=SimpleNamespace(value=str(amount)),
        status="succeeded",
        metadata=metadata,
        payment_method=None,
    )


class CreatePaymentRequest(BaseModel):
    email: str
    device_count: int = 1
    lte_gb: int = 0
    client_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentNotificationContext:
    days: int
    amount_external: float
    amount_from_balance: float
    device_count: int
    months: int
    is_auto_payment: bool
    discount_percent: int | None
    old_expired_at: date | None
    new_expired_at: date | None
    lte_gb_total: int
    method: str
    migration_direction: str | None = None


@dataclass(frozen=True, slots=True)
class AutoPaymentPreview:
    months: int
    device_count: int
    total_amount: float
    amount_external: float
    amount_from_balance: float
    discount_percent: int | None
    lte_gb_total: int
    lte_cost: int
    discount_id: int | None
    base_full_price: int
    discounted_price: float
    lte_price_per_gb: float


async def _await_payment_external_call(
    awaitable, *, operation: str, timeout: float | None = None
):
    """Bound external await duration to avoid stale-lease reclaim races."""
    effective_timeout = (
        PAYMENT_EXTERNAL_CALL_TIMEOUT_SECONDS if timeout is None else timeout
    )
    try:
        return await asyncio.wait_for(
            awaitable,
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Payment external call timed out: %s (timeout=%ss)",
            operation,
            effective_timeout,
        )
        raise


def _normalize_client_request_id(client_request_id: str | None) -> str | None:
    if client_request_id is None:
        return None

    normalized = str(client_request_id).strip()
    if not normalized:
        return None

    if len(normalized) > CLIENT_REQUEST_ID_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="Некорректный client_request_id")

    if any(ch not in CLIENT_REQUEST_ID_ALLOWED_CHARS for ch in normalized):
        raise HTTPException(status_code=400, detail="Некорректный client_request_id")

    return normalized


def _build_balance_payment_id(*, user_id: int, client_request_id: str) -> str:
    digest = hashlib.sha1(str(client_request_id).encode("utf-8")).hexdigest()[:20]
    return f"balance_req_{int(user_id)}_{digest}"


def _calc_referrer_bonus_days(months: int | None, device_count: int | None) -> int:
    """Legacy helper retained for imports/tests.

    The ordinary referral program no longer grants referrer subscription days;
    referrers now receive internal-balance cashback via ReferralCashbackRewards.
    """
    _ = months, device_count
    return 0


def _family_devices_limit() -> int:
    return family_devices_limit()


def _is_active_subscription(user: Users) -> bool:
    exp = normalize_date(user.expired_at)
    return bool(exp and exp >= date.today())


async def _resolve_base_devices_limit(user: Users) -> int:
    limit = 1
    tariff_id = getattr(user, "active_tariff_id", None)
    if tariff_id:
        tariff = await ActiveTariffs.get_or_none(id=tariff_id)
        if tariff:
            limit = int(getattr(tariff, "hwid_limit", 0) or limit)
    if getattr(user, "hwid_limit", None) is not None:
        limit = int(getattr(user, "hwid_limit", 0) or limit)
    return max(1, int(limit))


def _resolve_auto_payment_device_count(
    *, user: Users, active_tariff: ActiveTariffs
) -> int:
    return max(
        1,
        int(
            getattr(active_tariff, "hwid_limit", 0)
            or getattr(user, "hwid_limit", 0)
            or 1
        ),
    )


async def build_auto_payment_preview(
    user: Users,
    *,
    active_tariff: ActiveTariffs | None = None,
) -> AutoPaymentPreview | None:
    resolved_active_tariff = active_tariff
    if resolved_active_tariff is None:
        tariff_id = getattr(user, "active_tariff_id", None)
        if not tariff_id:
            return None
        resolved_active_tariff = await ActiveTariffs.get_or_none(id=tariff_id)
        if resolved_active_tariff is None:
            return None

    months = int(getattr(resolved_active_tariff, "months", 0) or 0)
    base_full_price = int(getattr(resolved_active_tariff, "price", 0) or 0)
    lte_gb_total = int(getattr(resolved_active_tariff, "lte_gb_total", 0) or 0)
    lte_price_per_gb = float(
        getattr(resolved_active_tariff, "lte_price_per_gb", 0) or 0
    )
    lte_cost = 0
    if not bool(getattr(resolved_active_tariff, "lte_autopay_free", False)):
        lte_cost = _round_rub(lte_gb_total * lte_price_per_gb)

    discounted_price, discount_id, discount_percent = await apply_personal_discount(
        user.id, base_full_price, months
    )
    total_amount = int(discounted_price) + lte_cost
    user_balance = float(getattr(user, "balance", 0) or 0)
    amount_external = 0.0
    amount_from_balance = float(total_amount)
    if user_balance < total_amount:
        amount_external = float(max(1.0, total_amount - user_balance))
        amount_from_balance = float(total_amount - amount_external)

    return AutoPaymentPreview(
        months=months,
        device_count=_resolve_auto_payment_device_count(
            user=user, active_tariff=resolved_active_tariff
        ),
        total_amount=float(total_amount),
        amount_external=float(amount_external),
        amount_from_balance=float(amount_from_balance),
        discount_percent=discount_percent,
        lte_gb_total=lte_gb_total,
        lte_cost=lte_cost,
        discount_id=discount_id,
        base_full_price=base_full_price,
        discounted_price=float(discounted_price),
        lte_price_per_gb=lte_price_per_gb,
    )


async def _has_active_family_entitlement(user: Users) -> bool:
    """Family entitlement for referral rules (no hardcoded device limits)."""
    try:
        member = await FamilyMembers.get_or_none(
            member_id=user.id,
            status="active",
            allocated_devices__gt=0,
        ).prefetch_related("owner")
        if member:
            owner_exp = normalize_date(member.owner.expired_at)
            is_member_entitled = bool(owner_exp and owner_exp >= date.today())
            logger.debug(
                "referral_family_entitlement user=%s result=%s reason=family_member owner=%s",
                user.id,
                is_member_entitled,
                getattr(member.owner, "id", None),
            )
            return is_member_entitled

        if not _is_active_subscription(user):
            logger.debug(
                "referral_family_entitlement user=%s result=false reason=inactive_subscription_non_member",
                user.id,
            )
            return False

        family_limit = _family_devices_limit()
        base_limit = await _resolve_base_devices_limit(user)
        is_owner_entitled = base_limit >= family_limit
        logger.debug(
            "referral_family_entitlement user=%s result=%s reason=base_limit_check base_limit=%s family_limit=%s",
            user.id,
            is_owner_entitled,
            base_limit,
            family_limit,
        )
        return is_owner_entitled
    except Exception as exc:
        logger.warning(
            "referral_family_entitlement user=%s result=false reason=error error=%s",
            getattr(user, "id", None),
            exc,
        )
        return False


async def _notify_successful_purchase(
    *,
    user: Users,
    days: int,
    amount_paid_via_yookassa: float,
    amount_from_balance: float,
    device_count: int,
    migration_direction: str | None = None,
) -> None:
    """
    Sends purchase success notification with family-specific copy for capacity-driven family plans.
    """
    normalized_device_count = int(device_count or 1)
    if normalized_device_count >= _family_devices_limit():
        await notify_family_purchase_success_yookassa(
            user=user,
            days=days,
            amount_paid_via_yookassa=amount_paid_via_yookassa,
            amount_from_balance=amount_from_balance,
            device_count=normalized_device_count,
            migration_direction=migration_direction,
        )
        return
    await notify_renewal_success_yookassa(
        user=user,
        days=days,
        amount_paid_via_yookassa=amount_paid_via_yookassa,
        amount_from_balance=amount_from_balance,
        migration_direction=migration_direction,
    )


async def _resolve_payment_migration_direction(
    *, user: Users, purchase_kind: str
) -> str | None:
    has_family_overlay = await has_active_family_overlay(user)
    if purchase_kind == "family" and has_family_overlay:
        return "base_to_family"
    if purchase_kind == "base" and has_family_overlay:
        return "family_to_base"
    return None


async def _build_payment_notification_context(
    *,
    user: Users,
    days: int,
    amount_external: float,
    amount_from_balance: float,
    device_count: int,
    months: int,
    is_auto_payment: bool,
    discount_percent: int | None,
    old_expired_at,
    new_expired_at,
    lte_gb_total: int,
    method: str,
    tariff_kind: str | None = None,
) -> PaymentNotificationContext:
    notification_metadata: dict[str, object] = {
        "month": months,
        "device_count": device_count,
    }
    normalized_tariff_kind = normalize_tariff_kind(tariff_kind)
    if normalized_tariff_kind:
        notification_metadata["tariff_kind"] = normalized_tariff_kind
    purchase_kind = await resolve_purchase_kind(
        metadata=notification_metadata,
        months=months,
        device_count=device_count,
    )
    migration_direction = await _resolve_payment_migration_direction(
        user=user,
        purchase_kind=purchase_kind,
    )
    return PaymentNotificationContext(
        days=int(days),
        amount_external=float(amount_external),
        amount_from_balance=float(amount_from_balance),
        device_count=int(device_count or 1),
        months=int(months),
        is_auto_payment=bool(is_auto_payment),
        discount_percent=discount_percent,
        old_expired_at=old_expired_at,
        new_expired_at=new_expired_at,
        lte_gb_total=int(lte_gb_total or 0),
        method=str(method),
        migration_direction=migration_direction,
    )


async def _send_manual_payment_canceled_notifications_if_needed(
    *,
    user: Users,
    payment_id: str,
    amount_external: float,
    reason: str | None = None,
    method: str = "yookassa",
) -> None:
    async def _send_user_notification():
        await notify_payment_canceled_yookassa(user=user, reason=reason)

    try:
        await _send_payment_notification_if_needed(
            user_id=int(user.id),
            payment_id=str(payment_id),
            effect="user_cancel",
            sender=_send_user_notification,
        )
    except Exception as e:
        logger.warning(
            "Failed to send canceled user payment notification: %s",
            e,
            extra={
                "payment_id": payment_id,
                "user_id": user.id,
                "effect": "user_cancel",
            },
        )

    async def _send_admin_notification():
        await notify_manual_payment_canceled(
            user=user,
            payment_id=str(payment_id),
            amount=int(_round_rub(float(amount_external))),
            method=str(method),
            reason=reason,
        )

    try:
        await _send_payment_notification_if_needed(
            user_id=int(user.id),
            payment_id=str(payment_id),
            effect="admin_cancel",
            sender=_send_admin_notification,
        )
    except Exception as e:
        logger.warning(
            "Failed to send canceled admin payment notification: %s",
            e,
            extra={
                "payment_id": payment_id,
                "user_id": user.id,
                "effect": "admin_cancel",
            },
        )


def _build_payment_notification_mark_key(*, effect: str, payment_id: str) -> str:
    raw_payment_id = str(payment_id or "").strip()
    digest = (
        hashlib.sha1(raw_payment_id.encode("utf-8")).hexdigest()[:12]
        if raw_payment_id
        else "none"
    )
    raw_key = f"{str(effect).strip().lower()}:{raw_payment_id[:40]}:{digest}"
    return raw_key[:PAYMENT_NOTIFICATION_MARK_KEY_LIMIT]


async def _send_payment_notification_if_needed(
    *,
    user_id: int,
    payment_id: str,
    effect: str,
    sender,
) -> bool:
    mark_key = _build_payment_notification_mark_key(
        effect=effect, payment_id=payment_id
    )
    now = datetime.now(timezone.utc)
    async with in_transaction() as conn:
        # Lock processed payment row to serialize mark reservation under races.
        await (
            ProcessedPayments.select_for_update()
            .using_db(conn)
            .get_or_none(payment_id=str(payment_id))
        )

        existing_mark = (
            await NotificationMarks.filter(
                user_id=int(user_id),
                type=PAYMENT_NOTIFICATION_MARK_TYPE,
                key=mark_key,
            )
            .using_db(conn)
            .first()
        )
        if existing_mark:
            mark_meta = str(getattr(existing_mark, "meta", "") or "").strip().lower()
            if mark_meta != "pending":
                return False

            sent_at = getattr(existing_mark, "sent_at", None)
            age_seconds = PAYMENT_PROCESSING_STALE_SECONDS
            if sent_at is not None:
                ts = sent_at
                if getattr(ts, "tzinfo", None) is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_seconds = max(0.0, (now - ts).total_seconds())
            if age_seconds < PAYMENT_PROCESSING_STALE_SECONDS:
                return False

            await NotificationMarks.filter(id=existing_mark.id).using_db(conn).delete()

        await NotificationMarks.create(
            user_id=int(user_id),
            type=PAYMENT_NOTIFICATION_MARK_TYPE,
            key=mark_key,
            meta="pending",
            using_db=conn,
        )

    try:
        await sender()
    except Exception:
        await NotificationMarks.filter(
            user_id=int(user_id),
            type=PAYMENT_NOTIFICATION_MARK_TYPE,
            key=mark_key,
            meta="pending",
        ).delete()
        raise

    await NotificationMarks.filter(
        user_id=int(user_id),
        type=PAYMENT_NOTIFICATION_MARK_TYPE,
        key=mark_key,
        meta="pending",
    ).update(meta="sent")
    return True


async def _should_replay_payment_notifications(
    *, payment_id: str, user_id: int
) -> bool:
    row = await ProcessedPayments.get_or_none(payment_id=str(payment_id))
    if row is None:
        return False

    if int(getattr(row, "user_id", 0) or 0) != int(user_id):
        return False

    now = datetime.now(timezone.utc)
    state = str(getattr(row, "processing_state", "") or "").strip().lower()
    last_attempt_at = getattr(row, "last_attempt_at", None)
    if state == "processing" and last_attempt_at is not None:
        ts = last_attempt_at
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (now - ts).total_seconds() < PAYMENT_PROCESSING_STALE_SECONDS:
            return False

    if bool(getattr(row, "effect_applied", False)):
        return True

    if state in {"applied", "applied_guarded"}:
        return True

    status = str(getattr(row, "status", "") or "").strip().lower()
    return status == "succeeded"


async def _repair_processed_payment_financials(
    *,
    payment_id: str,
    user_id: int,
    amount_external: float,
    amount_from_balance: float,
    provider: str = PAYMENT_PROVIDER_YOOKASSA,
) -> None:
    amount_total = float(amount_external) + float(amount_from_balance)
    await _upsert_processed_payment(
        payment_id=str(payment_id),
        user_id=int(user_id),
        amount=float(amount_total),
        amount_external=float(amount_external),
        amount_from_balance=float(amount_from_balance),
        status="succeeded",
        provider=provider,
    )


async def _replay_payment_notifications_if_needed(
    *,
    user: Users,
    payment_id: str,
    days: int,
    amount_external: float,
    amount_from_balance: float,
    device_count: int,
    months: int,
    is_auto_payment: bool,
    discount_percent: int | None,
    old_expired_at,
    new_expired_at,
    lte_gb_total: int,
    method: str,
    tariff_kind: str | None = None,
) -> None:
    context = await _build_payment_notification_context(
        user=user,
        days=days,
        amount_external=amount_external,
        amount_from_balance=amount_from_balance,
        device_count=device_count,
        months=months,
        is_auto_payment=is_auto_payment,
        discount_percent=discount_percent,
        old_expired_at=old_expired_at,
        new_expired_at=new_expired_at,
        lte_gb_total=lte_gb_total,
        method=method,
        tariff_kind=tariff_kind,
    )

    async def _send_user_notification():
        await _notify_successful_purchase(
            user=user,
            days=context.days,
            amount_paid_via_yookassa=context.amount_external,
            amount_from_balance=context.amount_from_balance,
            device_count=context.device_count,
            migration_direction=context.migration_direction,
        )

    try:
        await _send_payment_notification_if_needed(
            user_id=int(user.id),
            payment_id=str(payment_id),
            effect="user",
            sender=_send_user_notification,
        )
    except Exception as e:
        logger.warning(
            "Failed to replay user payment notification: %s",
            e,
            extra={"payment_id": payment_id, "user_id": user.id, "effect": "user"},
        )

    async def _send_admin_notification():
        referrer = await user.referrer()
        await on_payment(
            user_id=user.id,
            is_sub=user.is_subscribed,
            referrer=referrer.name() if referrer else None,
            amount=int(
                _round_rub(
                    float(context.amount_external) + float(context.amount_from_balance)
                )
            ),
            months=context.months,
            method=context.method,
            payment_id=str(payment_id),
            is_auto=context.is_auto_payment,
            utm=user.utm if hasattr(user, "utm") else None,
            discount_percent=context.discount_percent,
            device_count=context.device_count,
            old_expired_at=context.old_expired_at,
            new_expired_at=context.new_expired_at,
            lte_gb_total=context.lte_gb_total,
            migration_direction=context.migration_direction,
        )

    try:
        await _send_payment_notification_if_needed(
            user_id=int(user.id),
            payment_id=str(payment_id),
            effect="admin",
            sender=_send_admin_notification,
        )
    except Exception as e:
        logger.error(
            "Failed to replay admin payment notification: %s",
            e,
            extra={"payment_id": payment_id, "user_id": user.id},
        )


async def _apply_referral_first_payment_reward(
    *,
    referred_user_id: int,
    payment_id: str,
    amount_rub: int | None,
    months: int | None,
    device_count: int,
) -> dict:
    """Apply referral rewards in a DB-transaction (ledger + subscription updates).

    Guarantees:
    - idempotent under webhook retries
    - safe under concurrent webhook deliveries (unique ledger key)
    - friend still gets +7 days once; referrer cashback is handled separately
    """
    today = date.today()
    m = int(months or 0)
    d = int(device_count or 1)

    async with in_transaction() as conn:
        # Lock referred user to avoid races with other operations on the same user.
        referred = (
            await Users.select_for_update().using_db(conn).get(id=referred_user_id)
        )
        ref_by = int(getattr(referred, "referred_by", 0) or 0)
        if not ref_by:
            logger.info(
                "referral_reward_skip_no_referrer user=%s payment=%s referred_by=%s",
                referred_user_id,
                payment_id,
                ref_by,
            )
            return {"applied": False}

        if is_partner_source_utm(getattr(referred, "utm", None)):
            logger.info(
                "referral_reward_skip_partner_source user=%s payment=%s referred_by=%s utm=%s",
                referred_user_id,
                payment_id,
                ref_by,
                getattr(referred, "utm", None),
            )
            return {"applied": False}

        logger.info(
            "referral_reward_attempt user=%s referrer=%s payment=%s months=%s devices=%s",
            referred_user_id,
            ref_by,
            payment_id,
            months,
            device_count,
        )

        # Try to insert a ledger row first (unique on (referred_user_id, kind)).
        # If it already exists -> reward already applied (or in progress previously).
        try:
            # We'll fill the rest after computing; keep defaults now.
            reward = await ReferralRewards.create(
                referred_user_id=referred.id,
                referrer_user_id=int(referred.referred_by),
                kind="first_payment",
                payment_id=payment_id,
                months=m,
                device_count=d,
                amount_rub=int(amount_rub) if amount_rub is not None else None,
                using_db=conn,
            )
        except IntegrityError:
            logger.info(
                "referral_reward_already_applied user=%s payment=%s",
                referred_user_id,
                payment_id,
            )
            return {"applied": False}

        referrer = (
            await Users.filter(id=int(referred.referred_by)).using_db(conn).first()
        )
        if not referrer:
            await ReferralRewards.filter(id=reward.id).using_db(conn).delete()
            logger.warning(
                "referral_reward_skip_missing_referrer user=%s payment=%s referrer=%s",
                referred_user_id,
                payment_id,
                int(referred.referred_by),
            )
            return {"applied": False}
        if bool(getattr(referrer, "is_partner", False)):
            await ReferralRewards.filter(id=reward.id).using_db(conn).delete()
            logger.info(
                "referral_reward_skip_partner_referrer user=%s payment=%s referrer=%s",
                referred_user_id,
                payment_id,
                int(referrer.id),
            )
            return {"applied": False}

        friend_bonus_days = 7
        referrer_bonus_days = _calc_referrer_bonus_days(months=m, device_count=d)

        # Family subscriptions should not be extended by referral days.
        friend_applied_to_subscription = False
        try:
            is_friend_family = await _has_active_family_entitlement(referred)
        except Exception:
            is_friend_family = False

        if not is_friend_family and friend_bonus_days > 0:
            start_friend = max(normalize_date(referred.expired_at) or today, today)
            new_friend_expired_at = start_friend + timedelta(days=friend_bonus_days)
            await (
                Users.filter(id=referred.id)
                .using_db(conn)
                .update(
                    expired_at=new_friend_expired_at,
                    referral_first_payment_rewarded=True,
                )
            )
            friend_applied_to_subscription = True
        else:
            await (
                Users.filter(id=referred.id)
                .using_db(conn)
                .update(
                    referral_first_payment_rewarded=True,
                )
            )
            # Keep rewards transparent for notifications/UI: +7 exists, but not applied to family expiry.
            friend_applied_to_subscription = False

        # The referrer no longer receives subscription days from the ordinary
        # program. Cashback is awarded separately from real external payments.
        applied_to_subscription = False

        # Update ledger with actual computed values.
        await (
            ReferralRewards.filter(id=reward.id)
            .using_db(conn)
            .update(
                friend_bonus_days=friend_bonus_days,
                referrer_bonus_days=referrer_bonus_days,
                applied_to_subscription=applied_to_subscription,
            )
        )

        return {
            "applied": True,
            "referrer_id": int(referrer.id),
            "friend_bonus_days": int(friend_bonus_days),
            "referrer_bonus_days": int(referrer_bonus_days),
            "months": int(m),
            "device_count": int(d),
            "applied_to_subscription": bool(applied_to_subscription),
            "friend_applied_to_subscription": bool(friend_applied_to_subscription),
        }


async def _upsert_processed_payment(
    *,
    payment_id: str,
    user_id: int,
    amount: float,
    amount_external: float,
    amount_from_balance: float,
    status: str,
    provider: str = PAYMENT_PROVIDER_YOOKASSA,
    client_request_id: str | None = None,
    payment_url: str | None = None,
    provider_payload: str | None = None,
) -> ProcessedPayments:
    """
    Idempotent write to `processed_payments`.
    Webhooks can be retried and we also have a fallback processor, so this must be safe to call multiple times.
    """

    derived_purpose = _derive_payment_purpose(provider_payload)

    async def _update_existing(existing: ProcessedPayments) -> ProcessedPayments:
        update_fields = [
            "user_id",
            "provider",
            "amount",
            "amount_external",
            "amount_from_balance",
            "status",
            "processing_state",
        ]
        existing.user_id = int(user_id)
        existing.provider = str(provider)
        existing.amount = float(amount)
        existing.amount_external = float(amount_external)
        existing.amount_from_balance = float(amount_from_balance)
        existing.status = str(status)
        if client_request_id is not None:
            existing.client_request_id = str(client_request_id)
            update_fields.append("client_request_id")
        if payment_url is not None:
            existing.payment_url = str(payment_url)
            update_fields.append("payment_url")
        if provider_payload is not None:
            existing.provider_payload = str(provider_payload)
            update_fields.append("provider_payload")
        # Только повышаем разрешение purpose: NULL → topup. Не перетираем
        # уже выставленное значение из колонки (например, если апсерт
        # пришёл с пустым payload, а row уже размечена явно).
        if derived_purpose is not None and getattr(existing, "payment_purpose", None) is None:
            existing.payment_purpose = derived_purpose
            update_fields.append("payment_purpose")
        if str(status).lower() == "pending" and not existing.effect_applied:
            existing.processing_state = "pending"
        elif str(status).lower() in {"canceled", "refunded"}:
            existing.processing_state = str(status).lower()
        await existing.save(update_fields=update_fields)
        return existing

    existing = await ProcessedPayments.get_or_none(payment_id=payment_id)
    if existing:
        return await _update_existing(existing)

    initial_state = (
        "pending" if str(status).lower() == "pending" else str(status).lower()
    )
    try:
        return await ProcessedPayments.create(
            payment_id=payment_id,
            user_id=int(user_id),
            provider=str(provider),
            client_request_id=client_request_id,
            payment_url=payment_url,
            provider_payload=provider_payload,
            amount=float(amount),
            amount_external=float(amount_external),
            amount_from_balance=float(amount_from_balance),
            status=str(status),
            processing_state=initial_state,
            payment_purpose=derived_purpose,
        )
    except IntegrityError:
        # Concurrent create race on unique(payment_id): fallback to read+update.
        existing_after_race = await ProcessedPayments.get_or_none(payment_id=payment_id)
        if existing_after_race is None:
            raise
        return await _update_existing(existing_after_race)


async def _claim_payment_effect_once(
    *,
    payment_id: str,
    user_id: int,
    source: str,
    provider: str = PAYMENT_PROVIDER_YOOKASSA,
) -> bool:
    now = datetime.now(timezone.utc)
    async with in_transaction() as conn:
        row = (
            await ProcessedPayments.select_for_update()
            .using_db(conn)
            .get_or_none(payment_id=payment_id)
        )
        if row is None:
            try:
                row = await ProcessedPayments.create(
                    payment_id=str(payment_id),
                    user_id=int(user_id),
                    provider=str(provider),
                    amount=0,
                    amount_external=0,
                    amount_from_balance=0,
                    status="pending",
                    processing_state="pending",
                    using_db=conn,
                )
            except IntegrityError:
                # Concurrent create race: another worker inserted the row first.
                row = (
                    await ProcessedPayments.select_for_update()
                    .using_db(conn)
                    .get_or_none(payment_id=payment_id)
                )
                if row is None:
                    return False

        if bool(getattr(row, "effect_applied", False)):
            return False

        state = str(getattr(row, "processing_state", "") or "").lower()
        last_attempt_at = getattr(row, "last_attempt_at", None)
        if (
            last_attempt_at is not None
            and getattr(last_attempt_at, "tzinfo", None) is None
        ):
            last_attempt_at = last_attempt_at.replace(tzinfo=timezone.utc)
        if (
            state == "processing"
            and last_attempt_at is not None
            and (now - last_attempt_at).total_seconds()
            < PAYMENT_PROCESSING_STALE_SECONDS
        ):
            return False

        row.attempt_count = int(getattr(row, "attempt_count", 0) or 0) + 1
        row.last_attempt_at = now
        row.last_source = str(source)[:32]
        row.last_error = None
        row.processing_state = "processing"
        row.provider = str(provider)
        row.user_id = int(user_id)
        await row.save(
            using_db=conn,
            update_fields=[
                "provider",
                "attempt_count",
                "last_attempt_at",
                "last_source",
                "last_error",
                "processing_state",
                "user_id",
            ],
        )
    return True


async def _refresh_payment_processing_lease(
    *, payment_id: str, user_id: int, source: str
) -> None:
    """Best-effort heartbeat for long-running payment processing sections."""
    await ProcessedPayments.filter(
        payment_id=str(payment_id),
        user_id=int(user_id),
        effect_applied=False,
        processing_state="processing",
    ).update(
        last_attempt_at=datetime.now(timezone.utc),
        last_source=str(source)[:32],
    )


async def _mark_payment_effect_success(
    *,
    payment_id: str,
    user_id: int,
    amount: float,
    amount_external: float,
    amount_from_balance: float,
    status: str = "succeeded",
    provider: str = PAYMENT_PROVIDER_YOOKASSA,
) -> None:
    now = datetime.now(timezone.utc)
    async with in_transaction() as conn:
        row = (
            await ProcessedPayments.select_for_update()
            .using_db(conn)
            .get_or_none(payment_id=payment_id)
        )
        if row is None:
            row = await ProcessedPayments.create(
                payment_id=str(payment_id),
                user_id=int(user_id),
                provider=str(provider),
                amount=float(amount),
                amount_external=float(amount_external),
                amount_from_balance=float(amount_from_balance),
                status=str(status),
                processing_state="applied",
                effect_applied=True,
                last_attempt_at=now,
                using_db=conn,
            )
            return
        row.user_id = int(user_id)
        row.provider = str(provider)
        row.amount = float(amount)
        row.amount_external = float(amount_external)
        row.amount_from_balance = float(amount_from_balance)
        row.status = str(status)
        row.processing_state = "applied"
        row.effect_applied = True
        row.last_error = None
        row.last_attempt_at = now
        await row.save(
            using_db=conn,
            update_fields=[
                "user_id",
                "provider",
                "amount",
                "amount_external",
                "amount_from_balance",
                "status",
                "processing_state",
                "effect_applied",
                "last_error",
                "last_attempt_at",
            ],
        )


async def _mark_payment_effect_failed(*, payment_id: str, error: str) -> None:
    await ProcessedPayments.filter(payment_id=payment_id).update(
        processing_state="failed",
        last_error=str(error)[:2000],
        last_attempt_at=datetime.now(timezone.utc),
    )


async def _was_balance_debit_applied(
    *, payment_id: str, expected_amount: float
) -> bool:
    row = await ProcessedPayments.get_or_none(payment_id=str(payment_id))
    if row is None:
        return False
    try:
        recorded_amount = float(getattr(row, "amount_from_balance", 0) or 0)
    except (TypeError, ValueError):
        recorded_amount = 0.0
    return recorded_amount >= float(expected_amount) - 1e-6


async def _mark_balance_debit_applied(
    *, payment_id: str, amount_from_balance: float
) -> None:
    await ProcessedPayments.filter(payment_id=str(payment_id)).update(
        amount=float(amount_from_balance),
        amount_external=0,
        amount_from_balance=float(amount_from_balance),
        status="pending",
        last_attempt_at=datetime.now(timezone.utc),
    )


async def _award_partner_cashback(
    *,
    payment_id: str,
    referral_user: Users,
    amount_rub_total: int,
) -> None:
    """
    Money-based partner rewards (cashback % from each purchase of referred users).
    Idempotent by payment_id via partner_earnings.payment_id unique constraint.
    """
    try:
        referrer_id = int(getattr(referral_user, "referred_by", 0) or 0)
        if not referrer_id:
            return
        referrer = await Users.get_or_none(id=referrer_id)
        if not referrer or not bool(getattr(referrer, "is_partner", False)):
            return

        # Partner percent: can be configured per-user or tiered by referrals count.
        try:
            percent = (
                int(referrer.referral_percent())
                if hasattr(referrer, "referral_percent")
                else int(getattr(referrer, "custom_referral_percent", 0) or 0)
            )
        except Exception:
            percent = int(getattr(referrer, "custom_referral_percent", 0) or 0)
        percent = max(0, percent)
        if percent <= 0:
            return

        # Resolve QR attribution from referral_user.utm if present.
        source = "referral_link"
        qr = None
        try:
            utm = (getattr(referral_user, "utm", None) or "").strip()
            if utm.startswith("qr_"):
                token = utm[3:]
                source = "qr"
                try:
                    qr_uuid = (
                        uuid.UUID(token) if len(token) != 32 else uuid.UUID(hex=token)
                    )
                    qr = await PartnerQr.get_or_none(id=qr_uuid)
                except Exception:
                    qr = None
                if not qr:
                    qr = await PartnerQr.get_or_none(slug=token)
        except Exception:
            qr = None

        existing = await PartnerEarnings.get_or_none(payment_id=payment_id)
        if existing:
            return

        reward = _round_rub(float(amount_rub_total) * float(percent) / 100.0)
        if reward <= 0:
            return

        # Fraud screening: if referrer and the paying referral share a HWID we
        # freeze the cashback at pending_review and notify admins in the bot
        # instead of crediting balance silently.
        from bloobcat.services.cashback_review import (
            _safe_detect,
            build_admin_review_text,
            should_freeze_cashback,
        )

        signals = await _safe_detect(referrer, referral_user)
        frozen = should_freeze_cashback(signals)

        review_status = "pending_review" if frozen else "active"

        earning = await PartnerEarnings.create(
            payment_id=str(payment_id),
            partner=referrer,
            referral_id=int(referral_user.id),
            qr_code=qr,
            source=source,
            amount_total_rub=int(amount_rub_total),
            reward_rub=int(reward),
            percent=int(percent),
            review_status=review_status,
            review_signals=signals if (frozen or signals.get("hwid_overlap")) else None,
        )

        if frozen:
            # Do NOT credit balance yet. Surface the case to admins for manual review.
            try:
                from bloobcat.bot.notifications.admin import send_admin_message
                from aiogram.types import (
                    InlineKeyboardButton,
                    InlineKeyboardMarkup,
                )

                review_text = build_admin_review_text(
                    earning_id=str(earning.id),
                    referrer=referrer,
                    referred=referral_user,
                    amount_total_rub=int(amount_rub_total),
                    reward_rub=int(reward),
                    percent=int(percent),
                    signals=signals,
                )
                review_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Одобрить",
                                callback_data=f"cashback_review:approve:{earning.id}",
                            ),
                            InlineKeyboardButton(
                                text="❌ Отменить",
                                callback_data=f"cashback_review:reject:{earning.id}",
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                text="💬 Связаться с партнёром",
                                url=f"tg://user?id={int(referrer.id)}",
                            ),
                        ],
                    ]
                )
                await send_admin_message(text=review_text, reply_markup=review_keyboard)
            except Exception as e_admin:
                logger.warning(
                    "Frozen cashback admin notification failed for payment %s: %s",
                    payment_id,
                    e_admin,
                )
            logger.info(
                "Partner cashback frozen for review payment=%s referrer=%s referred=%s "
                "signals=%s",
                payment_id,
                referrer.id,
                referral_user.id,
                signals,
            )
            return

        # Clean case: update partner available balance (atomic).
        await Users.filter(id=referrer.id).update(balance=F("balance") + int(reward))

        # Notify partner in bot (best-effort; do not fail payment processing).
        if notify_partner_earning is not None:
            try:
                await notify_partner_earning(
                    partner=referrer,
                    referral=referral_user,
                    amount_total_rub=int(amount_rub_total),
                    reward_rub=int(reward),
                    percent=int(percent),
                    source=str(source),
                    qr_title=(getattr(qr, "title", None) if qr else None),
                )
            except Exception as e_notify_partner:
                logger.warning(
                    "Partner cashback notification failed for payment %s: %s",
                    payment_id,
                    e_notify_partner,
                )
    except Exception as e:
        logger.warning(
            "Partner cashback award failed for payment %s: %s", payment_id, e
        )


async def _send_referral_cashback_notification(
    *,
    cashback_res: dict,
    referral_user: Users,
    amount_rub: int,
    first_payment_res: dict | None = None,
) -> None:
    if not cashback_res.get("applied"):
        return
    referrer_id = int(cashback_res.get("referrer_id") or 0)
    if not referrer_id:
        return
    referrer = await Users.get_or_none(id=referrer_id)
    if not referrer:
        return
    created_chests = cashback_res.get("created_chests") or []
    try:
        await on_referral_payment(
            user=referrer,
            referral=referral_user,
            amount=int(amount_rub or 0),
            bonus_days=int((first_payment_res or {}).get("referrer_bonus_days", 0) or 0),
            friend_bonus_days=int((first_payment_res or {}).get("friend_bonus_days", 0) or 0),
            months=int((first_payment_res or {}).get("months", 0) or 0),
            device_count=int((first_payment_res or {}).get("device_count", 1) or 1),
            applied_to_subscription=bool((first_payment_res or {}).get("applied_to_subscription", False)),
            cashback_rub=int(cashback_res.get("reward_rub") or 0),
            cashback_percent=int(cashback_res.get("cashback_percent") or 0),
            level_name=str(cashback_res.get("level_name") or ""),
            level_up_name=(
                str(created_chests[-1].get("levelName"))
                if created_chests and isinstance(created_chests[-1], dict)
                else None
            ),
        )
    except Exception as e_ref_notify:
        logger.warning(
            "referral_cashback_notification_failed referrer=%s referral=%s err=%s",
            referrer_id,
            getattr(referral_user, "id", None),
            e_ref_notify,
        )


async def _award_standard_referral_cashback(
    *,
    payment_id: str,
    referral_user: Users,
    amount_external_rub: int,
    first_payment_res: dict | None = None,
) -> dict:
    try:
        cashback_res = await award_referral_cashback(
            payment_id=str(payment_id),
            referral_user=referral_user,
            amount_external_rub=int(amount_external_rub or 0),
        )
        await _send_referral_cashback_notification(
            cashback_res=cashback_res,
            referral_user=referral_user,
            amount_rub=int(amount_external_rub or 0),
            first_payment_res=first_payment_res,
        )
        # Golden Period (PR3) optimistic payout. Fires after the standard
        # cashback so the GP credit is *additive* on top of the regular
        # cashback ladder. Wrapped in try/except so a Golden Period failure
        # never breaks the existing cashback path. The service itself
        # double-checks key_activated and the feature flag, so this is a
        # safe no-op when GP is off or the referred user hasn't activated.
        try:
            referrer_id = getattr(referral_user, "referred_by", None)
            if referrer_id:
                from bloobcat.services.golden_period import (
                    attempt_optimistic_payout,
                )

                referrer = await Users.get_or_none(id=int(referrer_id))
                if referrer is not None:
                    await attempt_optimistic_payout(
                        referrer=referrer, referred=referral_user
                    )
        except Exception as e_gp:  # noqa: BLE001
            logger.warning(
                "golden_period_payment_hook_failed user=%s payment=%s err=%s",
                getattr(referral_user, "id", None),
                payment_id,
                e_gp,
            )
        return cashback_res
    except Exception as e_cashback:
        logger.warning(
            "standard_referral_cashback_failed user=%s payment=%s err=%s",
            getattr(referral_user, "id", None),
            payment_id,
            e_cashback,
        )
        return {"applied": False, "reason": "error", "error": str(e_cashback)}


def _bot_chat_url_from_webapp_url(webapp_url: str | None) -> str | None:
    """
    Best-effort parse bot username from TELEGRAM_WEBAPP_URL.
    Expected: https://t.me/<bot>/<app>/...
    Returns: https://t.me/<bot>
    """
    raw = (webapp_url or "").strip()
    if not raw:
        return None
    try:
        u = urlparse(raw)
        host = (u.netloc or "").lower()
        if host not in ("t.me", "telegram.me", "www.t.me", "www.telegram.me"):
            return None
        parts = [p for p in (u.path or "").split("/") if p]
        if not parts:
            return None
        bot = parts[0].lstrip("@").strip()
        if not bot:
            return None
        return f"https://t.me/{bot}"
    except Exception:
        return None


def _is_telegram_https_link(url: str) -> bool:
    """
    Accept any Telegram universal link (t.me / telegram.me) over HTTPS.
    We use this as a safe fallback for YooKassa `return_url` so the "back" button is never empty.
    """
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        u = urlparse(raw)
        if (u.scheme or "").lower() != "https":
            return False
        host = (u.netloc or "").lower()
        if host not in ("t.me", "telegram.me", "www.t.me", "www.telegram.me"):
            return False
        parts = [p for p in (u.path or "").split("/") if p]
        return len(parts) >= 1
    except Exception:
        return False


def _is_bot_chat_link(url: str) -> bool:
    """
    Accept only "https://t.me/<username>" (optional trailing slash).
    We intentionally reject Mini App links (/bot/app) and deep links with query params.
    """
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        u = urlparse(raw)
        if (u.scheme or "").lower() != "https":
            return False
        host = (u.netloc or "").lower()
        if host not in ("t.me", "telegram.me", "www.t.me", "www.telegram.me"):
            return False
        parts = [p for p in (u.path or "").split("/") if p]
        if len(parts) != 1:
            return False
        if u.query or u.fragment:
            return False
        return True
    except Exception:
        return False


async def _resolve_payment_return_url() -> str:
    """
    After YooKassa redirect we MUST return the user to the bot chat (not to Mini App),
    because the payment status is delivered by the bot message.
    """
    raw = (telegram_settings.payment_return_url or "").strip()
    if raw and _is_bot_chat_link(raw):
        return raw
    if raw and not _is_bot_chat_link(raw):
        # Historically we allowed only a bot chat link here.
        # In practice, operators often configure a Mini App deep-link like:
        #   https://t.me/<bot>/<miniapp_short_name>?startapp=...
        # If we ignore it completely, YooKassa can end up with a useless return_url fallback.
        if _is_telegram_https_link(raw):
            logger.info(
                "TELEGRAM_PAYMENT_RETURN_URL is not a bot chat link; using as-is for YooKassa return_url"
            )
            return raw
        logger.warning("TELEGRAM_PAYMENT_RETURN_URL ignored: not a Telegram HTTPS link")

    parsed = _bot_chat_url_from_webapp_url(
        getattr(telegram_settings, "webapp_url", None)
    )
    if parsed:
        return parsed

    try:
        bot_username = (await get_bot_username() or "").strip().lstrip("@")
        if bot_username:
            return f"https://t.me/{bot_username}/"
    except Exception:
        pass

    # Last resort: return any configured HTTPS page so YooKassa button is still clickable.
    try:
        webapp_url = (getattr(telegram_settings, "webapp_url", None) or "").strip()
        if webapp_url and webapp_url.lower().startswith("https://"):
            return webapp_url
        miniapp_url = (getattr(telegram_settings, "miniapp_url", None) or "").strip()
        if miniapp_url and miniapp_url.lower().startswith("https://"):
            return miniapp_url
    except Exception:
        pass
    return "https://t.me/"


def _round_rub(value: float) -> int:
    try:
        dec = Decimal(str(value))
    except Exception:
        return 0
    return int(dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _meta_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    return default


def _to_positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except Exception:
        return max(1, int(default or 1))
    return parsed if parsed > 0 else max(1, int(default or 1))


def _compute_lte_carryover_gb(old_active_tariff: Any) -> int:
    """Unused LTE GBs from the old active_tariff carried over to its replacement.

    The legacy code refunded `remaining_gb * old.lte_price_per_gb` to the
    monetary balance, but this silently returned 0 when the old tariff had
    `lte_price_per_gb == 0` (trial / promo / synthetic active_tariff topped up
    via LTE top-up — which adds GB but does not update the stored price). The
    user paid for gigabytes; carrying gigabytes preserves what they paid for.
    `int(used)` floors so any partial-GB remainder rounds in the user's favor.
    """
    if old_active_tariff is None:
        return 0
    total = int(getattr(old_active_tariff, "lte_gb_total", 0) or 0)
    used = float(getattr(old_active_tariff, "lte_gb_used", 0.0) or 0.0)
    return max(0, total - int(used))


def _build_base_tariff_snapshot(
    *,
    tariff: Tariffs | None,
    device_count: int,
    lte_gb: int,
    lte_price_per_gb: float | None = None,
) -> dict[str, Any] | None:
    if tariff is None:
        return None

    _, effective_multiplier = tariff.get_effective_pricing()
    price_snapshot = tariff.calculate_price(max(1, int(device_count or 1)))
    resolved_lte_price = (
        float(lte_price_per_gb)
        if lte_price_per_gb is not None
        else float(getattr(tariff, "lte_price_per_gb", 0) or 0.0)
    )
    return {
        "base_tariff_name": str(tariff.name),
        "base_tariff_months": int(tariff.months),
        "base_tariff_price": int(price_snapshot),
        "base_hwid_limit": max(1, int(device_count or 1)),
        "base_lte_gb_total": max(0, int(lte_gb or 0)),
        "base_lte_gb_used": 0.0,
        "base_lte_price_per_gb": resolved_lte_price,
        "base_progressive_multiplier": float(effective_multiplier or 0.0),
        "base_residual_day_fraction": 0.0,
    }


def _build_active_base_tariff_snapshot(
    *,
    active_tariff: ActiveTariffs | None,
    device_count: int,
    lte_gb: int,
    lte_price_per_gb: float,
) -> dict[str, Any] | None:
    if active_tariff is None:
        return None

    return {
        "base_tariff_name": str(active_tariff.name),
        "base_tariff_months": int(active_tariff.months),
        "base_tariff_price": int(getattr(active_tariff, "price", 0) or 0),
        "base_hwid_limit": max(1, int(device_count or 1)),
        "base_lte_gb_total": max(0, int(lte_gb or 0)),
        "base_lte_gb_used": 0.0,
        "base_lte_price_per_gb": float(lte_price_per_gb or 0.0),
        "base_progressive_multiplier": float(
            getattr(active_tariff, "progressive_multiplier", 0.0) or 0.0
        ),
        "base_residual_day_fraction": float(
            getattr(active_tariff, "residual_day_fraction", 0.0) or 0.0
        ),
    }


async def resolve_purchase_kind(
    *,
    metadata: dict | None = None,
    months: int | None = None,
    device_count: int | None = None,
    tariff_id: int | None = None,
    tariff: Tariffs | None = None,
) -> str:
    meta = metadata or {}
    explicit_kind = normalize_tariff_kind(meta.get("tariff_kind"))
    if explicit_kind:
        return explicit_kind

    normalized_device_count = _to_positive_int(
        device_count if device_count is not None else meta.get("device_count"),
        default=1,
    )

    resolved_tariff = tariff
    resolved_tariff_id = tariff_id
    if resolved_tariff_id is None and meta.get("tariff_id") is not None:
        try:
            resolved_tariff_id = int(meta.get("tariff_id"))
        except Exception:
            resolved_tariff_id = None

    if resolved_tariff is None and resolved_tariff_id is not None:
        resolved_tariff = await Tariffs.get_or_none(id=resolved_tariff_id)
        if resolved_tariff:
            await resolved_tariff.sync_effective_pricing_fields()

    if resolved_tariff is not None:
        return resolve_tariff_kind_by_limits(
            months=int(getattr(resolved_tariff, "months", months or 0) or 0),
            device_count=normalized_device_count,
            default_devices_limit=int(
                getattr(resolved_tariff, "devices_limit_default", 1) or 1
            ),
            family_devices=int(
                getattr(
                    resolved_tariff,
                    "devices_limit_family",
                    _family_devices_limit(),
                )
                or _family_devices_limit()
            ),
            family_plan_enabled=bool(
                getattr(resolved_tariff, "family_plan_enabled", True)
            ),
        )

    resolved_months = _to_positive_int(
        months if months is not None else meta.get("month"),
        default=0,
    )
    return (
        "family"
        if is_family_purchase(
            months=resolved_months, device_count=normalized_device_count
        )
        else "base"
    )


async def _apply_purchase_extension_by_kind(
    *,
    user: Users,
    purchase_kind: str,
    purchased_days: int,
    base_tariff_snapshot: dict[str, Any] | None = None,
) -> tuple[bool, date | None]:
    normalized_days = max(0, int(purchased_days or 0))
    if purchase_kind == "family":
        family_expires_at = await _compute_family_overlay_expiry(
            user=user,
            purchased_days=normalized_days,
        )
        await freeze_base_subscription_if_needed(
            user, family_expires_at=family_expires_at
        )
        user.expired_at = family_expires_at
        return False, family_expires_at

    added_to_frozen_base = await apply_base_purchase_to_frozen_base_if_active(
        user,
        purchased_days=normalized_days,
        base_tariff_snapshot=base_tariff_snapshot,
    )
    if not added_to_frozen_base:
        await user.extend_subscription(normalized_days)
    return added_to_frozen_base, None


async def _compute_family_overlay_expiry(*, user: Users, purchased_days: int) -> date:
    today = date.today()
    extension_days = max(0, int(purchased_days or 0))
    active_base_overlay = await get_active_base_overlay(user)
    current_base_expiry = normalize_date(user.expired_at)
    if active_base_overlay and current_base_expiry and current_base_expiry >= today:
        carryover_family_days = max(
            0, int(active_base_overlay.base_remaining_days or 0)
        )
        return today + timedelta(days=carryover_family_days + extension_days)

    if not await has_active_family_overlay(user):
        return today + timedelta(days=extension_days)

    current_family_expiry = normalize_date(user.expired_at)
    extension_start = max(current_family_expiry or today, today)
    return extension_start + timedelta(days=extension_days)


def _format_range_start(start_date: date) -> str:
    start_dt = datetime.combine(start_date, time.min, tzinfo=MSK_TZ)
    return start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _format_range_end(end_dt: datetime) -> str:
    return end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def _fetch_today_lte_usage_gb(user_uuid: str) -> float | None:
    marker_upper = (remnawave_settings.lte_node_marker or "").upper()
    if not marker_upper:
        return 0.0
    client = RemnaWaveClient(
        remnawave_settings.url, remnawave_settings.token.get_secret_value()
    )
    try:
        msk_today = datetime.now(MSK_TZ).date()
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
    except Exception as e:
        logger.error(f"Ошибка получения LTE usage snapshot для {user_uuid}: {e}")
        return None
    finally:
        await client.close()


async def _apply_succeeded_payment_fallback(
    yk_payment,
    user: Users,
    meta: dict,
    *,
    fast_status: bool = False,
    provider: str = PAYMENT_PROVIDER_YOOKASSA,
) -> bool:
    """
    Fallback processor used by `/pay/status/{payment_id}` when webhook delivery fails.
    It applies the subscription update and creates ProcessedPayments record (idempotent).
    """
    pid = str(getattr(yk_payment, "id", "") or "").strip()
    if not pid:
        return False

    try:
        amount_external = float(
            getattr(getattr(yk_payment, "amount", None), "value", 0) or 0
        )
    except Exception:
        amount_external = 0.0

    amount_from_balance = _round_rub(meta.get("amount_from_balance", 0))

    device_count = 1
    try:
        device_count = int(meta.get("device_count", 1))
    except Exception:
        device_count = 1
    if device_count < 1:
        device_count = 1

    lte_gb = 0
    try:
        lte_gb = int(meta.get("lte_gb") or 0)
    except Exception:
        lte_gb = 0
    if lte_gb < 0:
        lte_gb = 0

    is_auto_payment = _meta_bool(meta.get("is_auto"), False)
    discount_percent = None
    try:
        if meta.get("discount_percent") is not None:
            discount_percent = int(meta.get("discount_percent"))
    except Exception:
        discount_percent = None
    discount_id = meta.get("discount_id")

    claimed = await _claim_payment_effect_once(
        payment_id=pid,
        user_id=int(user.id),
        source="fallback",
        provider=provider,
    )
    if not claimed:
        replay_eligible = await _should_replay_payment_notifications(
            payment_id=pid,
            user_id=int(user.id),
        )
        if not replay_eligible:
            return True

        await _repair_processed_payment_financials(
            payment_id=pid,
            user_id=int(user.id),
            amount_external=float(amount_external),
            amount_from_balance=float(amount_from_balance),
            provider=provider,
        )
        try:
            replay_months = int(meta.get("month") or 0)
        except Exception:
            replay_months = 0

        if replay_months > 0:
            await _replay_payment_notifications_if_needed(
                user=user,
                payment_id=pid,
                days=max(
                    0,
                    (add_months_safe(date.today(), replay_months) - date.today()).days,
                ),
                amount_external=float(amount_external),
                amount_from_balance=float(amount_from_balance),
                device_count=int(device_count or 1),
                months=int(replay_months),
                is_auto_payment=bool(is_auto_payment),
                discount_percent=discount_percent,
                old_expired_at=user.expired_at,
                new_expired_at=user.expired_at,
                lte_gb_total=int(getattr(user, "lte_gb_total", 0) or lte_gb),
                method=f"{provider}_fallback",
                tariff_kind=meta.get("tariff_kind"),
            )
        return True

    try:
        months = int(meta.get("month"))
    except Exception:
        await _mark_payment_effect_failed(
            payment_id=pid, error="Invalid or missing month in metadata"
        )
        return False

    await _refresh_payment_processing_lease(
        payment_id=pid,
        user_id=int(user.id),
        source="fallback",
    )

    old_expired_at = user.expired_at

    if amount_from_balance > 0:
        user.balance = max(0, int(user.balance or 0) - int(amount_from_balance))

    # Extend subscription days.
    current_date = date.today()
    target_date = add_months_safe(current_date, months)
    days = max(0, (target_date - current_date).days)

    # Persist tariff snapshot + device limit when metadata contains tariff_id.
    raw_tariff_id = meta.get("tariff_id")
    tariff_id = None
    if raw_tariff_id is not None:
        try:
            tariff_id = int(raw_tariff_id)
        except Exception:
            tariff_id = None

    original = (
        await Tariffs.get_or_none(id=tariff_id) if tariff_id is not None else None
    )
    if original:
        await original.sync_effective_pricing_fields()

    base_tariff_snapshot = _build_base_tariff_snapshot(
        tariff=original,
        device_count=device_count,
        lte_gb=lte_gb,
        lte_price_per_gb=(
            float(meta.get("lte_price_per_gb")) if "lte_price_per_gb" in meta else None
        ),
    )

    purchase_kind = await resolve_purchase_kind(
        metadata=meta,
        months=months,
        device_count=device_count,
        tariff_id=tariff_id,
        tariff=original,
    )
    added_to_frozen_base, _ = await _apply_purchase_extension_by_kind(
        user=user,
        purchase_kind=purchase_kind,
        purchased_days=days,
        base_tariff_snapshot=base_tariff_snapshot,
    )
    preserve_active_tariff_state = purchase_kind == "base" and added_to_frozen_base
    if tariff_id is not None and preserve_active_tariff_state:
        logger.info(
            "Fallback base purchase during active family overlay preserved current entitlement state user=%s payment_id=%s",
            user.id,
            pid,
        )

    fallback_lte_carryover_gb = 0
    if tariff_id is not None and not preserve_active_tariff_state:
        await _refresh_payment_processing_lease(
            payment_id=pid,
            user_id=int(user.id),
            source="fallback",
        )
        if user.active_tariff_id:
            old_active = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
            if old_active:
                fallback_lte_carryover_gb = _compute_lte_carryover_gb(old_active)
                if fallback_lte_carryover_gb > 0:
                    logger.info(
                        "Fallback: переносим остаток LTE %s GB на новый тариф пользователя %s",
                        fallback_lte_carryover_gb,
                        user.id,
                    )
                await old_active.delete()

        if "lte_price_per_gb" in meta:
            try:
                lte_price_snapshot = float(meta.get("lte_price_per_gb") or 0)
            except Exception:
                lte_price_snapshot = (
                    float(original.lte_price_per_gb or 0) if original else 0.0
                )
        else:
            lte_price_snapshot = (
                float(original.lte_price_per_gb or 0) if original else 0.0
            )

        msk_today = datetime.now(MSK_TZ).date()
        usage_snapshot = None
        if user.remnawave_uuid and not fast_status:
            await _refresh_payment_processing_lease(
                payment_id=pid,
                user_id=int(user.id),
                source="fallback_usage_pre",
            )
            try:
                usage_snapshot = await _await_payment_external_call(
                    _fetch_today_lte_usage_gb(str(user.remnawave_uuid)),
                    operation="fallback_fetch_today_lte_usage_gb",
                )
            except asyncio.TimeoutError:
                usage_snapshot = None
            finally:
                await _refresh_payment_processing_lease(
                    payment_id=pid,
                    user_id=int(user.id),
                    source="fallback_usage_post",
                )

        if original and bool(getattr(original, "is_active", True)):
            _, effective_multiplier = original.get_effective_pricing()
            calculated_price = int(original.calculate_price(device_count))
            active_name = original.name
            active_months = int(original.months)
            active_multiplier = effective_multiplier
        else:
            # Keep system consistent even if tariff was deleted/deactivated after payment start.
            calculated_price = _round_rub(amount_external + amount_from_balance)
            active_name = str(meta.get("tariff_name") or f"{months} months")
            active_months = int(months)
            active_multiplier = float(meta.get("progressive_multiplier") or 0.9)
            logger.warning(
                "Fallback payment used missing/inactive tariff snapshot",
                extra={"user_id": user.id, "tariff_id": tariff_id, "payment_id": pid},
            )

        effective_fallback_lte_total = int(lte_gb or 0) + int(
            fallback_lte_carryover_gb or 0
        )
        active_tariff = await ActiveTariffs.create(
            user=user,
            name=active_name,
            months=active_months,
            price=calculated_price,
            hwid_limit=device_count,
            lte_gb_total=effective_fallback_lte_total,
            lte_gb_used=0.0,
            lte_price_per_gb=lte_price_snapshot,
            lte_usage_last_date=msk_today,
            lte_usage_last_total_gb=usage_snapshot
            if usage_snapshot is not None
            else 0.0,
            progressive_multiplier=active_multiplier,
            residual_day_fraction=0.0,
        )
        user.active_tariff_id = active_tariff.id
        user.hwid_limit = device_count
        user.lte_gb_total = effective_fallback_lte_total

    is_auto_payment = _meta_bool(meta.get("is_auto"), False)
    if provider == PAYMENT_PROVIDER_YOOKASSA and not is_auto_payment:
        payment_method = getattr(yk_payment, "payment_method", None)
        saved_method_id = (
            getattr(payment_method, "id", None) if payment_method else None
        )
        saved_flag = (
            _meta_bool(getattr(payment_method, "saved", None), False)
            if payment_method
            else False
        )
        # In fallback flow we may receive stringified flags from provider metadata.
        # Keep renew_id when provider marks method as saved, or when id is present.
        if saved_method_id and (
            saved_flag or getattr(payment_method, "saved", None) is None
        ):
            user.renew_id = str(saved_method_id)
            user.is_subscribed = True

    if user.is_trial:
        user.is_trial = False

    await user.save(skip_remnawave_sync=fast_status)

    # Mark processed (idempotent) right after core DB mutations are persisted.
    total_amount = float(amount_external) + float(amount_from_balance)
    await _mark_payment_effect_success(
        payment_id=pid,
        user_id=user.id,
        amount=total_amount,
        amount_external=float(amount_external),
        amount_from_balance=float(amount_from_balance),
        status="succeeded",
        provider=provider,
    )

    try:
        await consume_discount_if_needed(discount_id)
    except Exception as discount_exc:
        logger.warning(
            "Failed to consume discount after committed fallback payment: user=%s payment_id=%s err=%s",
            user.id,
            pid,
            discount_exc,
        )

    # Best-effort RemnaWave sync for HWID limit.
    if (
        user.remnawave_uuid
        and user.hwid_limit
        and not preserve_active_tariff_state
        and user.is_device_per_user_enabled()
    ):
        await _sync_device_per_user_after_payment(user, source="fallback")
    if (
        user.remnawave_uuid
        and user.hwid_limit
        and not preserve_active_tariff_state
        and not user.is_device_per_user_enabled()
    ):
        await _refresh_payment_processing_lease(
            payment_id=pid,
            user_id=int(user.id),
            source="fallback",
        )
        remnawave_client = None
        try:
            remnawave_client = RemnaWaveClient(
                remnawave_settings.url,
                remnawave_settings.token.get_secret_value(),
            )
            if fast_status:
                await asyncio.wait_for(
                    remnawave_client.users.update_user(
                        uuid=user.remnawave_uuid,
                        expireAt=user.expired_at,
                        hwidDeviceLimit=int(user.hwid_limit),
                    ),
                    timeout=PAYMENT_FAST_STATUS_REMNAWAVE_TIMEOUT_SECONDS,
                )
            else:
                await _await_payment_external_call(
                    remnawave_client.users.update_user(
                        uuid=user.remnawave_uuid,
                        expireAt=user.expired_at,
                        hwidDeviceLimit=int(user.hwid_limit),
                    ),
                    operation="fallback_remnawave_update_user",
                )
            await _refresh_payment_processing_lease(
                payment_id=pid,
                user_id=int(user.id),
                source="fallback_remna_post",
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Fast status RemnaWave sync timed out: payment_id=%s user_id=%s timeout=%ss",
                pid,
                user.id,
                PAYMENT_FAST_STATUS_REMNAWAVE_TIMEOUT_SECONDS,
            )
        except Exception:
            pass
        finally:
            if remnawave_client:
                try:
                    await remnawave_client.close()
                except Exception:
                    pass
    elif user.remnawave_uuid and preserve_active_tariff_state:
        logger.info(
            "Fallback base purchase during active family overlay: skipped RemnaWave entitlement update for user %s",
            user.id,
        )

    # Apply referral rewards / partner cashback that normally happen in webhook.
    # This is critical for the "webhook didn't arrive" scenario: otherwise partners won't see earnings,
    # and neither side gets referral bonus notifications.
    try:
        amount_total_rub = int(_round_rub(total_amount))
    except Exception:
        amount_total_rub = int(total_amount) if total_amount else 0

    # Partner cashback (RUB) for partners.
    try:
        await _award_partner_cashback(
            payment_id=str(pid),
            referral_user=user,
            amount_rub_total=int(amount_total_rub),
        )
    except Exception:
        # Keep fallback resilient: this must not block subscription activation.
        pass

    # Standard referral program: friend +7 days on first external payment,
    # referrer gets internal-balance cashback from real external RUB only.
    if getattr(user, "referred_by", 0):
        try:
            reward_res = await _apply_referral_first_payment_reward(
                referred_user_id=user.id,
                payment_id=str(pid),
                amount_rub=int(amount_total_rub) if amount_total_rub else None,
                months=int(months or 0),
                device_count=int(device_count or 1),
            )
            amount_external_rub = int(_round_rub(float(amount_external or 0)))
            await _award_standard_referral_cashback(
                payment_id=str(pid),
                referral_user=user,
                amount_external_rub=amount_external_rub,
                first_payment_res=reward_res if reward_res.get("applied") else None,
            )
            if reward_res.get("applied") and on_referral_friend_bonus is not None:
                referrer = await Users.get(id=int(reward_res["referrer_id"]))
                try:
                    await on_referral_friend_bonus(
                        user=user,
                        referrer=referrer,
                        friend_bonus_days=int(reward_res["friend_bonus_days"]),
                        months=int(reward_res["months"]),
                        device_count=int(reward_res["device_count"]),
                    )
                except Exception as e_friend_notify:
                    logger.warning(
                        "referral_friend_bonus_notification_failed user=%s err=%s",
                        user.id,
                        e_friend_notify,
                    )
        except Exception as e_reward:
            logger.warning(
                "referral_reward_failed user=%s payment=%s err=%s",
                user.id,
                pid,
                e_reward,
            )

    await _replay_payment_notifications_if_needed(
        user=user,
        payment_id=pid,
        days=int(days),
        amount_external=float(amount_external),
        amount_from_balance=float(amount_from_balance),
        device_count=int(device_count or 1),
        months=int(months),
        is_auto_payment=bool(is_auto_payment),
        discount_percent=discount_percent,
        old_expired_at=old_expired_at,
        new_expired_at=user.expired_at,
        lte_gb_total=int(lte_gb),
        method=f"{provider}_fallback",
        tariff_kind=meta.get("tariff_kind"),
    )

    return True


def _payment_row_provider(row: ProcessedPayments | None) -> str:
    provider = str(getattr(row, "provider", "") or "").strip().lower() if row else ""
    if provider == PAYMENT_PROVIDER_PLATEGA:
        return PAYMENT_PROVIDER_PLATEGA
    return PAYMENT_PROVIDER_YOOKASSA


def _parse_platega_metadata_from_payload(payload: Any) -> dict[str, Any]:
    parsed = parse_platega_payload(payload)
    metadata = parsed.get("metadata")
    if isinstance(metadata, dict):
        return dict(metadata)
    return parsed


def _round_amount_for_compare(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except Exception:
        amount = Decimal("0")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _validate_platega_amount_currency(
    *,
    provider_amount: Any,
    provider_currency: Any,
    metadata: dict[str, Any],
) -> None:
    expected_currency = str(
        metadata.get("expected_currency") or PAYMENT_CURRENCY_RUB
    ).strip().upper()
    received_currency = str(provider_currency or "").strip().upper()
    if received_currency and received_currency != expected_currency:
        raise HTTPException(status_code=400, detail="Payment currency mismatch")

    expected_amount = metadata.get("expected_amount")
    if expected_amount is None:
        return
    if _round_amount_for_compare(provider_amount) != _round_amount_for_compare(
        expected_amount
    ):
        raise HTTPException(status_code=400, detail="Payment amount mismatch")


async def _ensure_platega_pending_row(
    *,
    payment_id: str,
    user_id: int,
    amount_external: float,
    amount_from_balance: float,
    status: str,
    metadata: dict[str, Any],
    provider_status: str,
    payment_url: str | None = None,
) -> ProcessedPayments:
    existing = await ProcessedPayments.get_or_none(payment_id=payment_id)
    if existing and (
        bool(getattr(existing, "effect_applied", False))
        or str(getattr(existing, "status", "") or "").strip().lower() == "succeeded"
    ):
        return existing

    provider_payload = _provider_payload_json(
        {
            "metadata": metadata,
            "provider_status": normalize_platega_status(provider_status),
        }
    )
    return await _upsert_processed_payment(
        payment_id=payment_id,
        user_id=int(user_id),
        amount=float(amount_external) + float(amount_from_balance),
        amount_external=float(amount_external),
        amount_from_balance=float(amount_from_balance),
        status=status,
        provider=PAYMENT_PROVIDER_PLATEGA,
        client_request_id=(
            str(metadata.get("client_request_id"))
            if metadata.get("client_request_id") is not None
            else None
        ),
        payment_url=payment_url,
        provider_payload=provider_payload,
    )


async def _apply_confirmed_platega_payment(
    *,
    payment_id: str,
    user: Users,
    metadata: dict[str, Any],
    amount_external: float,
    fast_status: bool = False,
) -> bool:
    amount_from_balance = _round_rub(metadata.get("amount_from_balance", 0))
    await _ensure_platega_pending_row(
        payment_id=payment_id,
        user_id=int(user.id),
        amount_external=float(amount_external),
        amount_from_balance=float(amount_from_balance),
        status="pending",
        metadata=metadata,
        provider_status=PLATEGA_STATUS_CONFIRMED,
    )

    # Route LTE / devices top-ups to shared provider-agnostic helpers BEFORE
    # _apply_succeeded_payment_fallback, which crashes on missing "month" field.
    if _meta_bool(metadata.get("upgrade_bundle"), False) or (
        str(metadata.get("payment_purpose") or "").strip().lower()
        == PAYMENT_PURPOSE_UPGRADE_BUNDLE
    ):
        claimed = await _claim_payment_effect_once(
            payment_id=payment_id,
            user_id=int(user.id),
            source="platega_upgrade_bundle",
            provider=PAYMENT_PROVIDER_PLATEGA,
        )
        if not claimed:
            return True  # already processed, idempotent
        await _refresh_payment_processing_lease(
            payment_id=payment_id,
            user_id=int(user.id),
            source="platega_upgrade_bundle",
        )
        ok, _reason = await _apply_upgrade_bundle_effect(
            payment_id=payment_id,
            user=user,
            meta=metadata,
            amount_external=float(amount_external),
            amount_from_balance=float(amount_from_balance),
            provider=PAYMENT_PROVIDER_PLATEGA,
        )
        return ok

    if _meta_bool(metadata.get("lte_topup"), False):
        claimed = await _claim_payment_effect_once(
            payment_id=payment_id,
            user_id=int(user.id),
            source="platega_lte_topup",
            provider=PAYMENT_PROVIDER_PLATEGA,
        )
        if not claimed:
            return True  # already processed, idempotent
        await _refresh_payment_processing_lease(
            payment_id=payment_id,
            user_id=int(user.id),
            source="platega_lte_topup",
        )
        ok, _reason = await _apply_lte_topup_effect(
            payment_id=payment_id,
            user=user,
            meta=metadata,
            amount_external=float(amount_external),
            amount_from_balance=float(amount_from_balance),
            provider=PAYMENT_PROVIDER_PLATEGA,
        )
        return ok

    if _meta_bool(metadata.get("devices_topup"), False):
        claimed = await _claim_payment_effect_once(
            payment_id=payment_id,
            user_id=int(user.id),
            source="platega_devices_topup",
            provider=PAYMENT_PROVIDER_PLATEGA,
        )
        if not claimed:
            return True  # already processed, idempotent
        await _refresh_payment_processing_lease(
            payment_id=payment_id,
            user_id=int(user.id),
            source="platega_devices_topup",
        )
        ok, _reason = await _apply_devices_topup_effect(
            payment_id=payment_id,
            user=user,
            meta=metadata,
            amount_external=float(amount_external),
            amount_from_balance=float(amount_from_balance),
            provider=PAYMENT_PROVIDER_PLATEGA,
        )
        return ok

    return await _apply_succeeded_payment_fallback(
        _platega_payment_stub(
            payment_id=payment_id,
            amount=float(amount_external),
            metadata=metadata,
        ),
        user,
        metadata,
        fast_status=fast_status,
        provider=PAYMENT_PROVIDER_PLATEGA,
    )


async def _get_platega_payment_status_response(
    *,
    payment_id: str,
    user: Users,
    processed: ProcessedPayments | None,
) -> dict[str, Any]:
    try:
        status_result = await PlategaClient(timeout_seconds=15.0).get_transaction_status(
            payment_id
        )
    except PlategaConfigError:
        raise HTTPException(status_code=503, detail="Payment provider is not configured")
    except PlategaAPIError as exc:
        logger.error(
            "Ошибка при получении статуса платежа Platega",
            extra={
                "payment_id": payment_id,
                "user_id": user.id,
                "status_code": exc.status_code,
            },
        )
        raise HTTPException(
            status_code=503, detail="Payment status service unavailable"
        )

    provider_status = normalize_platega_status(status_result.status)
    internal_status = map_platega_status_to_internal(provider_status)
    metadata = _parse_platega_metadata_from_payload(status_result.payload)
    if not metadata:
        metadata = _metadata_from_processed_payment(processed)

    meta_user_id: int | None = None
    raw_meta_user_id = metadata.get("user_id")
    if raw_meta_user_id is not None and str(raw_meta_user_id).strip():
        try:
            meta_user_id = int(raw_meta_user_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=404, detail="Payment not found")

    if meta_user_id is not None:
        if meta_user_id != int(user.id):
            raise HTTPException(status_code=404, detail="Payment not found")
    elif processed is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    if provider_status == PLATEGA_STATUS_CONFIRMED:
        _validate_platega_amount_currency(
            provider_amount=status_result.amount,
            provider_currency=status_result.currency,
            metadata=metadata,
        )
        processed_status = (
            str(getattr(processed, "status", "") or "").strip().lower()
            if processed
            else ""
        )
        if not processed or processed_status == "pending":
            applied = await _apply_confirmed_platega_payment(
                payment_id=payment_id,
                user=user,
                metadata=metadata,
                amount_external=float(status_result.amount or 0),
                fast_status=True,
            )
            if applied:
                processed = await ProcessedPayments.get_or_none(payment_id=payment_id)
    elif provider_status == PLATEGA_STATUS_CANCELED and processed:
        await _upsert_processed_payment(
            payment_id=payment_id,
            user_id=int(user.id),
            amount=float(status_result.amount or processed.amount or 0),
            amount_external=float(status_result.amount or processed.amount_external or 0),
            amount_from_balance=float(processed.amount_from_balance or 0),
            status="canceled",
            provider=PAYMENT_PROVIDER_PLATEGA,
            provider_payload=getattr(processed, "provider_payload", None),
        )
        processed = await ProcessedPayments.get_or_none(payment_id=payment_id)

    processed_status = (
        str(getattr(processed, "status", "") or "").strip().lower() if processed else ""
    )
    entitlements_ready = bool(
        internal_status == "succeeded"
        and processed
        and processed_status == "succeeded"
        and bool(getattr(processed, "effect_applied", False))
    )

    overlay_snapshot: dict[str, Any] | None = None
    if internal_status == "succeeded":
        try:
            overlay_snapshot = await get_overlay_payload(user)
        except Exception as e:
            logger.warning(
                "Failed to include overlay snapshot in Platega payment status response: payment_id=%s user_id=%s err=%s",
                payment_id,
                user.id,
                e,
            )

    response: dict[str, Any] = {
        "payment_id": payment_id,
        "provider": PAYMENT_PROVIDER_PLATEGA,
        "provider_status": provider_status,
        # Backward-compatible normalized status for existing frontend code.
        "yookassa_status": internal_status,
        "is_final": internal_status in ("succeeded", "canceled", "refunded"),
        "is_paid": internal_status == "succeeded",
        "amount": (
            str(status_result.amount) if status_result.amount is not None else None
        ),
        "currency": status_result.currency,
        "processed": bool(processed),
        "processed_status": (processed.status if processed else None),
        "entitlements_ready": entitlements_ready,
    }
    if overlay_snapshot is not None:
        response["overlay_snapshot"] = overlay_snapshot
    return response


@router.get("/tariffs")
async def get_tariffs():
    return await Tariffs.filter(is_active=True).order_by("order")


@router.get("/status/{payment_id}")
async def get_payment_status(
    payment_id: str,
    user: Users = Depends(validate),
):
    """
    Point payment status check by YooKassa payment_id.

    Why:
    - Webhook delivery / subscription activation can lag.
    - This endpoint allows the client to confirm the payment result (succeeded/canceled/pending)
      even before `/user` reflects the updated subscription.

    Security:
    - Requires authenticated user (Telegram initData or Bearer token).
    - Verifies YooKassa payment metadata user_id when available.
    - If the payment was already processed by our webhook, we also verify against ProcessedPayments.
    """
    if not payment_id or not str(payment_id).strip():
        raise HTTPException(status_code=400, detail="payment_id is required")

    processed = await ProcessedPayments.get_or_none(payment_id=payment_id)
    if processed and int(processed.user_id) != int(user.id):
        # Don't leak existence of foreign payment IDs.
        raise HTTPException(status_code=404, detail="Payment not found")

    provider = (
        _payment_row_provider(processed)
        if processed
        else _active_payment_provider()
    )
    if provider == PAYMENT_PROVIDER_PLATEGA:
        if processed is None:
            # Platega status lookups must first prove local ownership. The pay()
            # path creates a pending ProcessedPayments row before returning a
            # Platega transaction id, so a missing row is either unknown or not
            # owned by this user. Avoid using the provider as an oracle for
            # arbitrary transaction IDs.
            raise HTTPException(status_code=404, detail="Payment not found")
        return await _get_platega_payment_status_response(
            payment_id=payment_id,
            user=user,
            processed=processed,
        )

    if not _configure_yookassa_if_available():
        raise HTTPException(status_code=503, detail="YooKassa provider is not configured")

    try:
        yk_payment = await asyncio.wait_for(
            asyncio.to_thread(partial(Payment.find_one, payment_id)),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Payment status service timeout")
    except Exception as e:
        logger.error(
            f"Ошибка при получении статуса платежа YooKassa {payment_id}: {e}",
            extra={"payment_id": payment_id, "user_id": user.id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=503, detail="Payment status service unavailable"
        )

    if not yk_payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    status = str(getattr(yk_payment, "status", "") or "")
    amount_value: str | None = None
    currency: str | None = None
    try:
        amount_obj = getattr(yk_payment, "amount", None)
        amount_value = str(getattr(amount_obj, "value", "") or "") or None
        currency = str(getattr(amount_obj, "currency", "") or "") or None
    except Exception:
        amount_value = None
        currency = None

    meta_user_id: int | None = None
    meta_user_id_present = False
    meta = getattr(yk_payment, "metadata", None)
    if isinstance(meta, dict):
        raw_meta_user_id = meta.get("user_id")
        meta_user_id_present = (
            raw_meta_user_id is not None and str(raw_meta_user_id).strip() != ""
        )
        if meta_user_id_present:
            try:
                meta_user_id = int(raw_meta_user_id)
            except (TypeError, ValueError):
                # Fail closed when provider explicitly sends owner, but value is invalid.
                raise HTTPException(status_code=404, detail="Payment not found")

    if meta_user_id_present:
        if int(meta_user_id) != int(user.id):
            # Don't leak existence of foreign payment IDs.
            raise HTTPException(status_code=404, detail="Payment not found")
    elif not processed:
        # Metadata owner is missing: allow only when local processed row proves ownership.
        raise HTTPException(status_code=404, detail="Payment not found")

    # Reliability fallback:
    # If YooKassa says the payment is succeeded but our webhook didn't process it,
    # try to apply subscription here (idempotent via ProcessedPayments unique constraint).
    if status == "succeeded" and (
        not processed or (processed.status or "").strip().lower() == "pending"
    ):
        try:
            meta = getattr(yk_payment, "metadata", None)
            if isinstance(meta, dict) and str(meta.get("user_id", "")).strip():
                if int(meta.get("user_id")) == int(user.id):
                    applied = await _apply_succeeded_payment_fallback(
                        yk_payment,
                        user,
                        meta,
                        fast_status=True,
                    )
                    if applied:
                        processed = await ProcessedPayments.get_or_none(
                            payment_id=payment_id
                        )
        except Exception as e:
            logger.error(
                f"Fallback processing failed for succeeded payment {payment_id}: {e}",
                extra={"payment_id": payment_id, "user_id": user.id},
                exc_info=True,
            )

    processed_status = (
        str(getattr(processed, "status", "") or "").strip().lower() if processed else ""
    )
    entitlements_ready = bool(
        status == "succeeded"
        and processed
        and processed_status == "succeeded"
        and bool(getattr(processed, "effect_applied", False))
    )

    overlay_snapshot: dict[str, Any] | None = None
    if status == "succeeded":
        try:
            overlay_snapshot = await get_overlay_payload(user)
        except Exception as e:
            logger.warning(
                "Failed to include overlay snapshot in payment status response: payment_id=%s user_id=%s err=%s",
                payment_id,
                user.id,
                e,
            )

    response = {
        "payment_id": payment_id,
        "provider": PAYMENT_PROVIDER_YOOKASSA,
        "provider_status": status,
        "yookassa_status": status,
        "is_final": status in ("succeeded", "canceled"),
        "is_paid": status == "succeeded",
        "amount": amount_value,
        "currency": currency,
        "processed": bool(processed),
        "processed_status": (processed.status if processed else None),
        # Additive readiness signal for clients that need post-payment
        # entitlement coherence (overlay/freeze-safe success confirmation).
        "entitlements_ready": entitlements_ready,
    }
    if overlay_snapshot is not None:
        response["overlay_snapshot"] = overlay_snapshot
    return response


@router.post("/webhook/platega")
@webhook_router.post("/webhook/platega")
async def platega_webhook(request: Request):
    expected_merchant_id = str(platega_settings.merchant_id or "").strip()
    expected_secret = (
        platega_settings.secret_key.get_secret_value()
        if platega_settings.secret_key
        else ""
    ).strip()
    if not expected_merchant_id or not expected_secret:
        logger.error("Platega webhook received but provider is not configured")
        raise HTTPException(status_code=503, detail="Payment provider is not configured")

    merchant_id = str(request.headers.get("X-MerchantId") or "").strip()
    secret = str(request.headers.get("X-Secret") or "").strip()
    if not hmac.compare_digest(merchant_id, expected_merchant_id) or not hmac.compare_digest(
        secret, expected_secret
    ):
        logger.error("Получен webhook Platega с неверными заголовками авторизации")
        raise HTTPException(status_code=403, detail="Invalid Platega credentials")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid callback payload")

    payment_id = str(body.get("id") or body.get("transactionId") or "").strip()
    if not payment_id:
        raise HTTPException(status_code=400, detail="Missing transaction id")

    provider_status = normalize_platega_status(body.get("status"))
    internal_status = map_platega_status_to_internal(provider_status)
    try:
        amount_external = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        amount_external = 0.0
    currency = str(body.get("currency") or PAYMENT_CURRENCY_RUB).strip().upper()

    processed = await ProcessedPayments.get_or_none(payment_id=payment_id)
    if provider_status == PLATEGA_STATUS_CONFIRMED and processed is None:
        logger.error(
            "Confirmed Platega webhook references an unknown local payment",
            extra={"payment_id": payment_id, "provider_status": provider_status},
        )
        return {"status": "error", "message": "Unknown payment"}
    if processed is not None and getattr(processed, "provider", None) not in (None, PAYMENT_PROVIDER_PLATEGA):
        logger.error(
            "Platega webhook payment id collides with another provider",
            extra={"payment_id": payment_id, "provider": getattr(processed, "provider", None)},
        )
        return {"status": "error", "message": "Provider mismatch"}
    metadata = _parse_platega_metadata_from_payload(body.get("payload"))
    if not metadata:
        metadata = _metadata_from_processed_payment(processed)

    try:
        user_id = int(metadata.get("user_id") or (processed.user_id if processed else 0))
    except (TypeError, ValueError):
        user_id = 0
    if not user_id:
        logger.error(
            "Некорректные метаданные в webhook Platega",
            extra={"payment_id": payment_id, "provider_status": provider_status},
        )
        return {"status": "error", "message": "Invalid metadata"}

    user = await Users.get_or_none(id=user_id)
    if not user:
        logger.error(
            "Пользователь из webhook Platega не найден",
            extra={"payment_id": payment_id, "user_id": user_id},
        )
        return {"status": "error", "message": "User not found"}

    amount_from_balance = _round_rub(metadata.get("amount_from_balance", 0))
    provider_payload = _provider_payload_json(
        {
            "metadata": metadata,
            "provider_status": provider_status,
            "payment_method": body.get("paymentMethod"),
        }
    )

    if provider_status == PLATEGA_STATUS_CONFIRMED:
        _validate_platega_amount_currency(
            provider_amount=amount_external,
            provider_currency=currency,
            metadata=metadata,
        )
        await _ensure_platega_pending_row(
            payment_id=payment_id,
            user_id=user_id,
            amount_external=float(amount_external),
            amount_from_balance=float(amount_from_balance),
            status="pending",
            metadata=metadata,
            provider_status=provider_status,
        )
        await _apply_confirmed_platega_payment(
            payment_id=payment_id,
            user=user,
            metadata=metadata,
            amount_external=float(amount_external),
        )
        return {"status": "ok"}

    if provider_status == PLATEGA_STATUS_CANCELED:
        await _upsert_processed_payment(
            payment_id=payment_id,
            user_id=user_id,
            amount=float(amount_external) + float(amount_from_balance),
            amount_external=float(amount_external),
            amount_from_balance=float(amount_from_balance),
            status="canceled",
            provider=PAYMENT_PROVIDER_PLATEGA,
            client_request_id=(
                str(metadata.get("client_request_id"))
                if metadata.get("client_request_id") is not None
                else None
            ),
            provider_payload=provider_payload,
        )
        if not _meta_bool(metadata.get("is_auto"), False):
            await _send_manual_payment_canceled_notifications_if_needed(
                user=user,
                payment_id=payment_id,
                amount_external=float(amount_external),
                method=PAYMENT_PROVIDER_PLATEGA,
            )
        return {"status": "ok"}

    if provider_status in {PLATEGA_STATUS_CHARGEBACK, PLATEGA_STATUS_CHARGEBACKED}:
        from bloobcat.services.payment_revocation import revoke_access_for_refund

        revocation_report = await revoke_access_for_refund(
            user,
            payment_id=str(payment_id),
            reason="platega_chargeback",
        )
        await _upsert_processed_payment(
            payment_id=payment_id,
            user_id=user_id,
            amount=float(amount_external) + float(amount_from_balance),
            amount_external=float(amount_external),
            amount_from_balance=float(amount_from_balance),
            status="refunded",
            provider=PAYMENT_PROVIDER_PLATEGA,
            provider_payload=provider_payload,
        )
        logger.info(
            "Platega chargeback processed",
            extra={
                "payment_id": payment_id,
                "user_id": user_id,
                "revocation": revocation_report,
            },
        )
        return {"status": "ok"}

    logger.warning(
        "Unknown Platega webhook status",
        extra={
            "payment_id": payment_id,
            "provider_status": provider_status,
            "internal_status": internal_status,
        },
    )
    return {"status": "ok"}


async def _apply_lte_topup_effect(
    *,
    payment_id: str,
    user: Users,
    meta: dict,
    amount_external: float,
    amount_from_balance: float,
    provider: str,
) -> tuple[bool, str | None]:
    """Provider-agnostic LTE top-up effect: credits GB, marks payment succeeded,
    fires admin + user notifications, awards cashbacks.

    Preserves _claim_payment_effect_once / _mark_payment_effect_success idempotency.
    Called from both yookassa_webhook and _apply_confirmed_platega_payment.

    Returns (True, None) on success or (False, reason) on failure.
    """
    lte_gb_delta = int(meta.get("lte_gb_delta") or 0)
    lte_price_per_gb = float(meta.get("lte_price_per_gb") or 0)
    preserve_active_tariff_state = False

    if lte_gb_delta <= 0 and amount_external > 0:
        await _mark_payment_effect_failed(
            payment_id=payment_id,
            error="lte_gb_delta must be positive when amount_external > 0",
        )
        logger.error(
            "LTE пополнение: некорректная metadata — lte_gb_delta=%s при amount_external=%s (provider=%s, payment=%s)",
            lte_gb_delta,
            amount_external,
            provider,
            payment_id,
        )
        return False, "lte_gb_delta must be positive"

    active_tariff = (
        await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if user.active_tariff_id
        else None
    )
    if not active_tariff:
        logger.error(
            "LTE пополнение: активный тариф не найден для пользователя %s (payment %s, provider=%s)",
            user.id,
            payment_id,
            provider,
        )
        await _mark_payment_effect_failed(
            payment_id=payment_id,
            error="Active tariff not found for LTE topup",
        )
        return False, "Active tariff not found"

    lte_before = int(active_tariff.lte_gb_total or 0)
    old_hwid_limit = (
        int(active_tariff.hwid_limit or 0)
        if getattr(active_tariff, "hwid_limit", None) is not None
        else None
    )
    old_expired_at_for_log = user.expired_at

    if amount_from_balance > 0:
        initial_balance = user.balance
        user.balance = max(0, user.balance - amount_from_balance)
        await user.save(update_fields=["balance"])
        logger.info(
            "LTE пополнение: списано %.2f с бонусного баланса пользователя %s. "
            "Баланс до: %s, после: %s (provider=%s)",
            amount_from_balance,
            user.id,
            initial_balance,
            user.balance,
            provider,
        )

    update_fields = []
    if lte_gb_delta > 0:
        active_tariff.lte_gb_total = int(active_tariff.lte_gb_total or 0) + lte_gb_delta
        update_fields.append("lte_gb_total")
        user.lte_gb_total = int(active_tariff.lte_gb_total or 0)
        await user.save(update_fields=["lte_gb_total"])

    msk_today = datetime.now(MSK_TZ).date()
    usage_snapshot = None
    if user.remnawave_uuid and not preserve_active_tariff_state:
        await _refresh_payment_processing_lease(
            payment_id=payment_id,
            user_id=int(user.id),
            source="lte_topup_usage_pre",
        )
        try:
            usage_snapshot = await _await_payment_external_call(
                _fetch_today_lte_usage_gb(str(user.remnawave_uuid)),
                operation="lte_topup_fetch_today_lte_usage_gb",
            )
        except asyncio.TimeoutError:
            usage_snapshot = None
        finally:
            await _refresh_payment_processing_lease(
                payment_id=payment_id,
                user_id=int(user.id),
                source="lte_topup_usage_post",
            )
    if usage_snapshot is not None:
        active_tariff.lte_usage_last_date = msk_today
        active_tariff.lte_usage_last_total_gb = usage_snapshot
        update_fields.extend(["lte_usage_last_date", "lte_usage_last_total_gb"])
    elif active_tariff.lte_usage_last_date != msk_today:
        active_tariff.lte_usage_last_date = msk_today
        active_tariff.lte_usage_last_total_gb = 0.0
        update_fields.extend(["lte_usage_last_date", "lte_usage_last_total_gb"])

    if update_fields:
        await active_tariff.save(update_fields=update_fields)

    await NotificationMarks.filter(user_id=user.id, type="lte_usage").delete()

    pending_device_count = meta.get("pending_device_count")
    if pending_device_count is not None:
        try:
            pending_device_count = int(pending_device_count)
        except (TypeError, ValueError):
            pending_device_count = None
    if pending_device_count and pending_device_count > 0:
        pending_expired_at = None
        pending_expired_at_raw = meta.get("pending_expired_at")
        if pending_expired_at_raw:
            try:
                pending_expired_at = date.fromisoformat(str(pending_expired_at_raw))
            except Exception:
                pending_expired_at = None

        pending_active_tariff_price = meta.get("pending_active_tariff_price")
        try:
            pending_active_tariff_price = (
                int(pending_active_tariff_price)
                if pending_active_tariff_price is not None
                else None
            )
        except (TypeError, ValueError):
            pending_active_tariff_price = None

        pending_progressive_multiplier = meta.get("pending_progressive_multiplier")
        try:
            pending_progressive_multiplier = (
                float(pending_progressive_multiplier)
                if pending_progressive_multiplier is not None
                else None
            )
        except (TypeError, ValueError):
            pending_progressive_multiplier = None

        pending_residual_day_fraction = meta.get("pending_residual_day_fraction")
        try:
            pending_residual_day_fraction = (
                float(pending_residual_day_fraction)
                if pending_residual_day_fraction is not None
                else None
            )
        except (TypeError, ValueError):
            pending_residual_day_fraction = None

        pending_devices_decrease_count = meta.get("pending_devices_decrease_count")
        try:
            pending_devices_decrease_count = (
                int(pending_devices_decrease_count)
                if pending_devices_decrease_count is not None
                else None
            )
        except (TypeError, ValueError):
            pending_devices_decrease_count = None

        user.expired_at = pending_expired_at or user.expired_at
        user.hwid_limit = pending_device_count
        await user.save()

        active_tariff.hwid_limit = pending_device_count
        if pending_active_tariff_price is not None:
            active_tariff.price = pending_active_tariff_price
        if pending_progressive_multiplier is not None:
            active_tariff.progressive_multiplier = pending_progressive_multiplier
        if pending_residual_day_fraction is not None:
            active_tariff.residual_day_fraction = pending_residual_day_fraction
        if pending_devices_decrease_count is not None:
            active_tariff.devices_decrease_count = pending_devices_decrease_count
        await active_tariff.save()

        if user.remnawave_uuid and user.is_device_per_user_enabled():
            await _sync_device_per_user_after_payment(user, source="lte_topup_devices")
        if user.remnawave_uuid and not user.is_device_per_user_enabled():
            remnawave_client = None
            try:
                await _refresh_payment_processing_lease(
                    payment_id=payment_id,
                    user_id=int(user.id),
                    source="lte_topup_remna_pre",
                )
                remnawave_client = RemnaWaveClient(
                    remnawave_settings.url,
                    remnawave_settings.token.get_secret_value(),
                )
                await _await_payment_external_call(
                    remnawave_client.users.update_user(
                        uuid=user.remnawave_uuid,
                        expireAt=user.expired_at,
                        hwidDeviceLimit=pending_device_count,
                    ),
                    operation="lte_topup_remnawave_update_user",
                )
                await _refresh_payment_processing_lease(
                    payment_id=payment_id,
                    user_id=int(user.id),
                    source="lte_topup_remna_post",
                )
            except Exception as e:
                logger.error(
                    "LTE пополнение: ошибка обновления RemnaWave при изменении устройств для %s: %s (provider=%s)",
                    user.id,
                    e,
                    provider,
                )
            finally:
                if remnawave_client:
                    try:
                        await remnawave_client.close()
                    except Exception:
                        pass

    total_amount = amount_external + amount_from_balance
    await _mark_payment_effect_success(
        payment_id=payment_id,
        user_id=user.id,
        amount=total_amount,
        amount_external=amount_external,
        amount_from_balance=amount_from_balance,
        status="succeeded",
        provider=provider,
    )

    if user.remnawave_uuid and not preserve_active_tariff_state:
        try:
            effective_lte_total = (
                user.lte_gb_total
                if user.lte_gb_total is not None
                else (active_tariff.lte_gb_total or 0)
            )
            should_enable_lte = effective_lte_total > (active_tariff.lte_gb_used or 0)
            await _await_payment_external_call(
                set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable_lte),
                operation="lte_topup_set_lte_squad_status",
            )
        except Exception as e:
            logger.error(
                "LTE пополнение: ошибка обновления LTE-сквада для %s: %s (provider=%s)",
                user.id,
                e,
                provider,
            )

    logger.info(
        "LTE пополнение успешно: user=%s, delta=%s, price=%s, provider=%s",
        user.id,
        lte_gb_delta,
        lte_price_per_gb,
        provider,
    )
    lte_after = int(active_tariff.lte_gb_total or 0)
    try:
        await notify_lte_topup(
            user_id=user.id,
            payment_id=payment_id,
            method=f"{provider}_lte_topup",
            lte_gb_delta=lte_gb_delta,
            lte_gb_before=lte_before,
            lte_gb_after=lte_after,
            price_per_gb=lte_price_per_gb,
            amount_total=_round_rub(total_amount),
            amount_external=_round_rub(amount_external),
            amount_from_balance=int(amount_from_balance),
            old_hwid_limit=old_hwid_limit,
            new_hwid_limit=(
                int(active_tariff.hwid_limit)
                if getattr(active_tariff, "hwid_limit", None) is not None
                else None
            ),
            old_expired_at=old_expired_at_for_log,
            new_expired_at=user.expired_at,
        )
    except Exception as notify_exc:
        logger.error(
            "LTE пополнение: не удалось отправить админ-уведомление для %s: %s (provider=%s)",
            user.id,
            notify_exc,
            provider,
        )
    try:
        await notify_lte_topup_user(
            user=user,
            lte_gb_delta=lte_gb_delta,
            lte_gb_after=lte_after,
        )
    except Exception as user_notify_exc:
        logger.error(
            "LTE пополнение: не удалось отправить пользовательское уведомление для %s: %s (provider=%s)",
            user.id,
            user_notify_exc,
            provider,
        )
    try:
        await _award_partner_cashback(
            payment_id=payment_id,
            referral_user=user,
            amount_rub_total=int(_round_rub(total_amount)),
        )
    except Exception as e_partner:
        logger.warning(
            "LTE пополнение: не удалось начислить партнёрский кэшбек для payment %s: %s (provider=%s)",
            payment_id,
            e_partner,
            provider,
        )
    try:
        await _award_standard_referral_cashback(
            payment_id=payment_id,
            referral_user=user,
            amount_external_rub=int(_round_rub(amount_external)),
        )
    except Exception as e_ref_cashback:
        logger.warning(
            "LTE пополнение: не удалось начислить обычный реферальный кэшбек для payment %s: %s (provider=%s)",
            payment_id,
            e_ref_cashback,
            provider,
        )
    return True, None


async def _apply_devices_topup_effect(
    *,
    payment_id: str,
    user: Users,
    meta: dict,
    amount_external: float,
    amount_from_balance: float,
    provider: str,
) -> tuple[bool, str | None]:
    """Provider-agnostic devices top-up effect: updates hwid_limit, marks payment
    succeeded, fires admin notification, awards cashbacks.

    Preserves _claim_payment_effect_once / _mark_payment_effect_success idempotency.
    Called from both yookassa_webhook and _apply_confirmed_platega_payment.

    Returns (True, None) on success or (False, reason) on failure.
    """
    new_device_count = int(meta.get("new_device_count") or 0)
    new_active_tariff_price = meta.get("new_active_tariff_price")
    new_progressive_multiplier = meta.get("new_progressive_multiplier")

    active_tariff = (
        await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if user.active_tariff_id
        else None
    )
    if not active_tariff or new_device_count <= 0:
        logger.error(
            "Пополнение устройств: активный тариф или количество устройств "
            "не найдено для пользователя %s (payment %s, provider=%s)",
            user.id,
            payment_id,
            provider,
        )
        await _mark_payment_effect_failed(
            payment_id=payment_id,
            error="Active tariff or device count missing for devices topup",
        )
        return False, "Active tariff not found"

    old_hwid_limit = int(active_tariff.hwid_limit or 0)
    old_expired_at = user.expired_at

    if amount_from_balance > 0:
        initial_balance = user.balance
        user.balance = max(0, user.balance - amount_from_balance)
        await user.save(update_fields=["balance"])
        logger.info(
            "Пополнение устройств: списано %.2f с бонусного баланса "
            "пользователя %s. Баланс до: %s, после: %s (provider=%s)",
            amount_from_balance,
            user.id,
            initial_balance,
            user.balance,
            provider,
        )

    user.hwid_limit = new_device_count
    await user.save(update_fields=["hwid_limit"])

    active_tariff.hwid_limit = new_device_count
    update_fields = ["hwid_limit"]
    try:
        if new_active_tariff_price is not None:
            active_tariff.price = int(new_active_tariff_price)
            update_fields.append("price")
    except (TypeError, ValueError):
        pass
    try:
        if new_progressive_multiplier is not None:
            active_tariff.progressive_multiplier = float(new_progressive_multiplier)
            update_fields.append("progressive_multiplier")
    except (TypeError, ValueError):
        pass
    await active_tariff.save(update_fields=update_fields)

    if user.remnawave_uuid:
        if user.is_device_per_user_enabled():
            await _sync_device_per_user_after_payment(
                user, source="devices_topup"
            )
        else:
            remnawave_client = None
            try:
                await _refresh_payment_processing_lease(
                    payment_id=payment_id,
                    user_id=int(user.id),
                    source="devices_topup_remna_pre",
                )
                remnawave_client = RemnaWaveClient(
                    remnawave_settings.url,
                    remnawave_settings.token.get_secret_value(),
                )
                await _await_payment_external_call(
                    remnawave_client.users.update_user(
                        uuid=user.remnawave_uuid,
                        expireAt=user.expired_at,
                        hwidDeviceLimit=new_device_count,
                    ),
                    operation="devices_topup_remnawave_update_user",
                )
                await _refresh_payment_processing_lease(
                    payment_id=payment_id,
                    user_id=int(user.id),
                    source="devices_topup_remna_post",
                )
            except Exception as e:
                logger.error(
                    "Пополнение устройств: ошибка обновления RemnaWave для %s: %s (provider=%s)",
                    user.id,
                    e,
                    provider,
                )
            finally:
                if remnawave_client:
                    try:
                        await remnawave_client.close()
                    except Exception:
                        pass

    total_amount = amount_external + amount_from_balance
    await _mark_payment_effect_success(
        payment_id=payment_id,
        user_id=user.id,
        amount=total_amount,
        amount_external=amount_external,
        amount_from_balance=amount_from_balance,
        status="succeeded",
        provider=provider,
    )

    try:
        await notify_active_tariff_change(
            user=user,
            tariff_name=active_tariff.name,
            months=int(active_tariff.months),
            old_limit=old_hwid_limit,
            new_limit=new_device_count,
            old_lte_gb=int(active_tariff.lte_gb_total or 0),
            new_lte_gb=int(active_tariff.lte_gb_total or 0),
            old_price=int(
                meta.get("previous_active_tariff_price") or active_tariff.price
            ),
            new_price=int(active_tariff.price),
            old_expired_at=old_expired_at,
            new_expired_at=user.expired_at,
            auto_renew_enabled=(
                bool(user.renew_id)
                and payment_settings.auto_renewal_mode == "yookassa"
            ),
        )
    except Exception as e:
        logger.error(
            "Пополнение устройств: не удалось отправить уведомление пользователю %s: %s (provider=%s)",
            user.id,
            e,
            provider,
        )

    try:
        await _award_partner_cashback(
            payment_id=payment_id,
            referral_user=user,
            amount_rub_total=int(_round_rub(total_amount)),
        )
    except Exception as e_partner:
        logger.warning(
            "Пополнение устройств: не удалось начислить партнёрский кэшбек для payment %s: %s (provider=%s)",
            payment_id,
            e_partner,
            provider,
        )
    try:
        await _award_standard_referral_cashback(
            payment_id=payment_id,
            referral_user=user,
            amount_external_rub=int(_round_rub(amount_external)),
        )
    except Exception as e_ref_cashback:
        logger.warning(
            "Пополнение устройств: не удалось начислить обычный реферальный кэшбек для payment %s: %s (provider=%s)",
            payment_id,
            e_ref_cashback,
            provider,
        )
    logger.info(
        "Пополнение устройств успешно: user=%s, %s -> %s, price=%s, provider=%s",
        user.id,
        old_hwid_limit,
        new_device_count,
        int(active_tariff.price),
        provider,
    )
    return True, None


async def _apply_upgrade_bundle_effect(
    *,
    payment_id: str,
    user: Users,
    meta: dict,
    amount_external: float,
    amount_from_balance: float,
    provider: str,
) -> tuple[bool, str | None]:
    """Provider-agnostic combined upgrade-bundle effect: extends subscription
    period, raises device limit, and credits extra LTE GB — all in one DB
    transaction so partial application is impossible (matches the plan
    contract: never apply 2 of 3 deltas).

    Reads precomputed deltas from metadata produced by the upgrade_bundle
    endpoint:
      - ``target_device_count`` — absolute target after upgrade
      - ``target_lte_gb`` — absolute target after upgrade
      - ``target_extra_days`` — original requested days delta (logged only)
      - ``device_delta`` / ``lte_delta_gb`` / ``extra_days`` — actual deltas
        used as switches (>0 ⇒ apply that effect)
      - ``previous_active_tariff_price`` — for admin notification
      - ``new_active_tariff_price`` / ``new_progressive_multiplier`` — optional
        snapshot to persist on ActiveTariffs (if provided by the issuer)
    Preserves `_claim_payment_effect_once` / `_mark_payment_effect_success`
    idempotency. Called from both yookassa_webhook and
    `_apply_confirmed_platega_payment`.

    Returns (True, None) on success or (False, reason) on failure. On failure
    the whole transaction is rolled back and the payment is marked failed so
    the provider will retry / admins will be alerted.
    """
    try:
        device_delta = int(meta.get("device_delta") or 0)
    except (TypeError, ValueError):
        device_delta = 0
    try:
        lte_delta_gb = int(meta.get("lte_delta_gb") or 0)
    except (TypeError, ValueError):
        lte_delta_gb = 0
    try:
        extra_days = int(meta.get("extra_days") or 0)
    except (TypeError, ValueError):
        extra_days = 0
    try:
        target_device_count = int(meta.get("target_device_count") or 0)
    except (TypeError, ValueError):
        target_device_count = 0
    try:
        target_lte_gb = int(meta.get("target_lte_gb") or 0)
    except (TypeError, ValueError):
        target_lte_gb = 0

    if device_delta <= 0 and lte_delta_gb <= 0 and extra_days <= 0:
        await _mark_payment_effect_failed(
            payment_id=payment_id,
            error="upgrade_bundle metadata has no positive deltas",
        )
        logger.error(
            "Upgrade bundle: metadata без положительных дельт — "
            "device_delta=%s, lte_delta_gb=%s, extra_days=%s (provider=%s, payment=%s)",
            device_delta,
            lte_delta_gb,
            extra_days,
            provider,
            payment_id,
        )
        return False, "upgrade_bundle deltas are non-positive"

    # MAJOR 8: ideally this read would be `select_for_update(...).get_or_none(...)`
    # inside the transaction to block lost-update races against a parallel
    # devices/LTE topup. We rely on `_claim_payment_effect_once` for primary
    # idempotency + the MAJOR 4 delta-based application (current+delta, see
    # below) to make a parallel topup additive rather than a clobber. Adding
    # row-level lock here is a follow-up once the Tortoise-ORM
    # `select_for_update()` syntax is validated end-to-end.
    active_tariff = (
        await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if user.active_tariff_id
        else None
    )
    if not active_tariff:
        logger.error(
            "Upgrade bundle: активный тариф не найден для пользователя %s (payment %s, provider=%s)",
            user.id,
            payment_id,
            provider,
        )
        await _mark_payment_effect_failed(
            payment_id=payment_id,
            error="Active tariff not found for upgrade_bundle",
        )
        return False, "Active tariff not found"

    old_hwid_limit = int(active_tariff.hwid_limit or 0)
    old_lte_gb_total = int(active_tariff.lte_gb_total or 0)
    old_price = int(active_tariff.price or 0)
    old_expired_at = user.expired_at

    # MAJOR 1: detect drift between the metadata snapshot (captured at invoice
    # creation) and the live DB state. Drift typically means an admin made an
    # out-of-band edit (refund, manual adjustment) while the invoice was
    # outstanding. The desired behaviour for the primary use-case (parallel
    # devices/LTE topup) is to still apply the delta on top of live state, so
    # we only LOG drift here — visibility/alerting first, abort+refund later
    # as a follow-up. Each axis is checked independently because legitimate
    # parallel topups can legitimately mutate any of the three.
    snapshot_devices = meta.get("current_device_count")
    if snapshot_devices is not None and device_delta > 0:
        try:
            _snap_dev = int(snapshot_devices)
            if _snap_dev != old_hwid_limit:
                logger.warning(
                    "upgrade_bundle: device_count drift detected "
                    "(payment=%s, user=%s, snapshot=%s, current=%s, delta=%s, provider=%s)",
                    payment_id,
                    user.id,
                    _snap_dev,
                    old_hwid_limit,
                    device_delta,
                    provider,
                )
        except (TypeError, ValueError):
            pass
    snapshot_lte = meta.get("current_lte_gb_total")
    if snapshot_lte is not None and lte_delta_gb > 0:
        try:
            _snap_lte = int(snapshot_lte)
            if _snap_lte != old_lte_gb_total:
                logger.warning(
                    "upgrade_bundle: lte_gb_total drift detected "
                    "(payment=%s, user=%s, snapshot=%s, current=%s, delta=%s, provider=%s)",
                    payment_id,
                    user.id,
                    _snap_lte,
                    old_lte_gb_total,
                    lte_delta_gb,
                    provider,
                )
        except (TypeError, ValueError):
            pass
    snapshot_expired_at_ms = meta.get("current_expired_at_ms")
    if snapshot_expired_at_ms is not None and extra_days > 0 and old_expired_at is not None:
        try:
            _snap_ms = int(snapshot_expired_at_ms)
            _live_ms = int(
                datetime.combine(old_expired_at, datetime.min.time()).timestamp() * 1000
            )
            if _snap_ms != _live_ms:
                logger.warning(
                    "upgrade_bundle: expired_at drift detected "
                    "(payment=%s, user=%s, snapshot_ms=%s, current_ms=%s, delta_days=%s, provider=%s)",
                    payment_id,
                    user.id,
                    _snap_ms,
                    _live_ms,
                    extra_days,
                    provider,
                )
        except (TypeError, ValueError, OSError):
            pass

    # MAJOR 4: apply effects as deltas off the current DB state, not as
    # absolute targets captured at invoice creation. If the user did a
    # parallel devices/LTE topup between invoice and webhook, using
    # `target_*` would silently roll that back.
    new_device_count = (
        old_hwid_limit + device_delta if device_delta > 0 else old_hwid_limit
    )
    new_lte_gb_total = (
        old_lte_gb_total + lte_delta_gb if lte_delta_gb > 0 else old_lte_gb_total
    )

    # MAJOR 2: the user paid the price quoted at invoice creation
    # (`new_active_tariff_price` / `new_progressive_multiplier` in metadata).
    # If Tariffs.base_price changed in the meantime, recompute will yield a
    # different number — but the user did not consent to that new price, so we
    # PREFER the metadata snapshot. We still recompute to detect the drift and
    # log it for admin visibility; if recompute fails or metadata is absent we
    # fall back to whichever value is available.
    meta_price_raw = meta.get("new_active_tariff_price")
    meta_mult_raw = meta.get("new_progressive_multiplier")
    new_active_tariff_price = None
    new_progressive_multiplier = None
    if device_delta > 0 and new_device_count > 0:
        recomputed_price = None
        recomputed_mult = None
        try:
            from bloobcat.db.tariff import Tariffs as _Tariffs
            from bloobcat.services.upgrade_quote import (
                _compute_progressive_full_price,
            )

            _original = await _Tariffs.filter(
                name=active_tariff.name, months=active_tariff.months
            ).first()
            _new_price, _new_mult = _compute_progressive_full_price(
                active_tariff, _original, new_device_count
            )
            recomputed_price = int(_new_price)
            recomputed_mult = float(_new_mult)
        except Exception as price_exc:
            logger.warning(
                "Upgrade bundle: recompute progressive price failed for user=%s "
                "(falling back to metadata snapshot): %s",
                user.id,
                price_exc,
            )

        # Prefer the metadata snapshot — that's the price the user actually
        # consented to at invoice creation. Recompute is used only to detect
        # drift (admin lowered/raised Tariffs.base_price while invoice was
        # outstanding) for alerting purposes.
        try:
            meta_price_int = (
                int(meta_price_raw) if meta_price_raw is not None else None
            )
        except (TypeError, ValueError):
            meta_price_int = None
        try:
            meta_mult_float = (
                float(meta_mult_raw) if meta_mult_raw is not None else None
            )
        except (TypeError, ValueError):
            meta_mult_float = None

        if (
            meta_price_int is not None
            and recomputed_price is not None
            and meta_price_int != recomputed_price
        ):
            logger.warning(
                "upgrade_bundle: price drift detected "
                "(payment=%s, user=%s, quoted=%s, recomputed=%s, provider=%s)",
                payment_id,
                user.id,
                meta_price_int,
                recomputed_price,
                provider,
            )
        if (
            meta_mult_float is not None
            and recomputed_mult is not None
            and abs(meta_mult_float - recomputed_mult) > 1e-9
        ):
            logger.warning(
                "upgrade_bundle: progressive_multiplier drift detected "
                "(payment=%s, user=%s, quoted=%s, recomputed=%s, provider=%s)",
                payment_id,
                user.id,
                meta_mult_float,
                recomputed_mult,
                provider,
            )

        new_active_tariff_price = (
            meta_price_int if meta_price_int is not None else recomputed_price
        )
        new_progressive_multiplier = (
            meta_mult_float if meta_mult_float is not None else recomputed_mult
        )

    # Phase 2 fresh-equivalent: when pricing_mode=fresh_minus_refund, persist
    # the optimal SKU months onto active_tariff so the user's plan reflects the
    # new subscription period they actually paid for.
    pricing_mode_meta = meta.get("pricing_mode", "delta_legacy_shadow")
    new_active_tariff_months: int | None = None
    if pricing_mode_meta == "fresh_minus_refund":
        try:
            _opt_months_raw = meta.get("optimal_sku_months")
            if _opt_months_raw is not None:
                _opt_months = int(_opt_months_raw)
                if _opt_months > 0:
                    new_active_tariff_months = _opt_months
        except (TypeError, ValueError):
            pass

    try:
        async with in_transaction():
            if amount_from_balance > 0:
                initial_balance = user.balance
                updated_rows = await Users.filter(
                    id=user.id, balance__gte=int(amount_from_balance)
                ).update(balance=F("balance") - int(amount_from_balance))
                if not updated_rows:
                    raise RuntimeError(
                        "Insufficient bonus balance for upgrade_bundle debit"
                    )
                await user.refresh_from_db(fields=["balance"])
                logger.info(
                    "Upgrade bundle: списано %.2f с бонусного баланса пользователя %s. "
                    "Баланс до: %s, после: %s (provider=%s)",
                    amount_from_balance,
                    user.id,
                    initial_balance,
                    user.balance,
                    provider,
                )

            if extra_days > 0 and old_expired_at is not None:
                user.expired_at = old_expired_at + timedelta(days=extra_days)
                await user.save(update_fields=["expired_at"])

            if device_delta > 0 and new_device_count > 0:
                user.hwid_limit = new_device_count
                await user.save(update_fields=["hwid_limit"])
                active_tariff.hwid_limit = new_device_count
                update_fields = ["hwid_limit"]
                if new_active_tariff_price is not None:
                    try:
                        active_tariff.price = int(new_active_tariff_price)
                        update_fields.append("price")
                    except (TypeError, ValueError):
                        pass
                if new_progressive_multiplier is not None:
                    try:
                        active_tariff.progressive_multiplier = float(
                            new_progressive_multiplier
                        )
                        update_fields.append("progressive_multiplier")
                    except (TypeError, ValueError):
                        pass
                if new_active_tariff_months is not None:
                    try:
                        active_tariff.months = int(new_active_tariff_months)
                        update_fields.append("months")
                    except (TypeError, ValueError):
                        pass
                await active_tariff.save(update_fields=update_fields)

            if lte_delta_gb > 0:
                # Bug 1 (BE v4): do NOT update `active_tariff.lte_price_per_gb`
                # here. The snapshot is the original purchase price; live
                # Directus pricing is read fresh on every quote. Writing
                # the snapshot caused users to keep seeing yesterday's
                # rate after admin price changes. Metadata key
                # `new_lte_price_per_gb` is no longer emitted by the
                # quote builder either.
                active_tariff.lte_gb_total = new_lte_gb_total
                await active_tariff.save(update_fields=["lte_gb_total"])
                user.lte_gb_total = new_lte_gb_total
                await user.save(update_fields=["lte_gb_total"])
    except Exception as effect_exc:
        logger.error(
            "Upgrade bundle: ошибка применения эффектов для пользователя %s "
            "(payment %s, provider=%s): %s",
            user.id,
            payment_id,
            provider,
            effect_exc,
        )
        await _mark_payment_effect_failed(
            payment_id=payment_id,
            error=f"upgrade_bundle effect error: {effect_exc}",
        )
        return False, "upgrade_bundle effect failed"

    if user.remnawave_uuid:
        if user.is_device_per_user_enabled():
            try:
                await _sync_device_per_user_after_payment(
                    user, source="upgrade_bundle"
                )
            except Exception as sync_exc:
                logger.warning(
                    "Upgrade bundle: ошибка sync_device_per_user_after_payment для %s: %s (provider=%s)",
                    user.id,
                    sync_exc,
                    provider,
                )
        else:
            remnawave_client_local = None
            try:
                await _refresh_payment_processing_lease(
                    payment_id=payment_id,
                    user_id=int(user.id),
                    source="upgrade_bundle_remna_pre",
                )
                remnawave_client_local = RemnaWaveClient(
                    remnawave_settings.url,
                    remnawave_settings.token.get_secret_value(),
                )
                await _await_payment_external_call(
                    remnawave_client_local.users.update_user(
                        uuid=user.remnawave_uuid,
                        expireAt=user.expired_at,
                        hwidDeviceLimit=int(user.hwid_limit or new_device_count),
                    ),
                    operation="upgrade_bundle_remnawave_update_user",
                )
                await _refresh_payment_processing_lease(
                    payment_id=payment_id,
                    user_id=int(user.id),
                    source="upgrade_bundle_remna_post",
                )
            except Exception as e:
                logger.error(
                    "Upgrade bundle: ошибка обновления RemnaWave для %s: %s (provider=%s)",
                    user.id,
                    e,
                    provider,
                )
            finally:
                if remnawave_client_local:
                    try:
                        await remnawave_client_local.close()
                    except Exception:
                        pass

    # Period was extended: reschedule subscription-lifecycle tasks (reminder
    # / expiry / housekeeping) so the apscheduler-held jobs target the new
    # `expired_at`. Best-effort — must not roll back the committed effect
    # nor block the `_mark_payment_effect_success` ack to the provider.
    if extra_days > 0:
        try:
            from bloobcat.scheduler import schedule_user_tasks

            await schedule_user_tasks(user)
        except Exception as sched_exc:
            # MINOR 3: this is a functional regression (the reminder / expiry
            # job is now scheduled against the stale expired_at), not a soft
            # warning — log at error so production alerting picks it up.
            logger.error(
                "Upgrade bundle: schedule_user_tasks failed for user=%s (payment=%s, provider=%s): %s",
                user.id,
                payment_id,
                provider,
                sched_exc,
            )

    total_amount = amount_external + amount_from_balance
    await _mark_payment_effect_success(
        payment_id=payment_id,
        user_id=user.id,
        amount=total_amount,
        amount_external=amount_external,
        amount_from_balance=amount_from_balance,
        status="succeeded",
        provider=provider,
    )

    # Tag the row with payment_purpose='upgrade_bundle' so analytics/reports
    # and `_derive_payment_purpose` consumers do not have to re-derive from
    # the metadata. Best-effort — failure here must not roll back the effect.
    try:
        await ProcessedPayments.filter(payment_id=payment_id).update(
            payment_purpose=PAYMENT_PURPOSE_UPGRADE_BUNDLE,
        )
    except Exception as tag_exc:
        logger.warning(
            "Upgrade bundle: не удалось записать payment_purpose для payment %s: %s",
            payment_id,
            tag_exc,
        )

    try:
        await notify_active_tariff_change(
            user=user,
            tariff_name=active_tariff.name,
            months=int(active_tariff.months),
            old_limit=old_hwid_limit,
            new_limit=int(active_tariff.hwid_limit or new_device_count or old_hwid_limit),
            old_lte_gb=old_lte_gb_total,
            new_lte_gb=int(active_tariff.lte_gb_total or new_lte_gb_total),
            old_price=int(meta.get("previous_active_tariff_price") or old_price),
            new_price=int(active_tariff.price or old_price),
            old_expired_at=old_expired_at,
            new_expired_at=user.expired_at,
            auto_renew_enabled=(
                bool(user.renew_id)
                and payment_settings.auto_renewal_mode == "yookassa"
            ),
        )
    except Exception as e:
        logger.error(
            "Upgrade bundle: не удалось отправить уведомление пользователю %s: %s (provider=%s)",
            user.id,
            e,
            provider,
        )

    try:
        await _award_partner_cashback(
            payment_id=payment_id,
            referral_user=user,
            amount_rub_total=int(_round_rub(total_amount)),
        )
    except Exception as e_partner:
        logger.warning(
            "Upgrade bundle: не удалось начислить партнёрский кэшбек для payment %s: %s (provider=%s)",
            payment_id,
            e_partner,
            provider,
        )
    try:
        await _award_standard_referral_cashback(
            payment_id=payment_id,
            referral_user=user,
            amount_external_rub=int(_round_rub(amount_external)),
        )
    except Exception as e_ref_cashback:
        logger.warning(
            "Upgrade bundle: не удалось начислить обычный реферальный кэшбек для payment %s: %s (provider=%s)",
            payment_id,
            e_ref_cashback,
            provider,
        )

    logger.info(
        "Upgrade bundle успешно: user=%s, devices %s -> %s, lte_gb %s -> %s, "
        "expired_at %s -> %s, provider=%s, payment=%s",
        user.id,
        old_hwid_limit,
        int(active_tariff.hwid_limit or 0),
        old_lte_gb_total,
        int(active_tariff.lte_gb_total or 0),
        old_expired_at,
        user.expired_at,
        provider,
        payment_id,
    )
    return True, None


@router.post("/webhook/yookassa/{secret}")
@webhook_router.post("/webhook/yookassa/{secret}")
async def yookassa_webhook(request: Request, secret: str):
    payment = None
    expected_secret = str(getattr(yookassa_settings, "webhook_secret", "") or "").strip()
    if not expected_secret:
        logger.warning("YooKassa webhook received but provider is not configured")
        raise HTTPException(status_code=503, detail="YooKassa provider is not configured")
    if secret != expected_secret:
        logger.error("Получен webhook с неверным секретным ключом")
        raise HTTPException(status_code=403, detail="Invalid secret")

    try:
        # Получаем тело запроса
        body = await request.json()
        # Получаем заголовки
        headers = dict(request.headers)

        # Проверяем подпись и создаем объект уведомления
        notification = WebhookNotification(body, headers)

        event = notification.event
        payment = notification.object

        # Логируем событие
        logger.info(
            f"Получен webhook от YooKassa: {event}",
            extra={
                "payment_id": payment.id if payment else "unknown",
                "user_id": payment.metadata.get("user_id", "unknown")
                if payment
                else "unknown",
                "amount": payment.amount.value if payment else "unknown",
                "status": payment.status if payment else "unknown",
            },
        )

        try:
            data = payment.metadata
            user = await Users.get(id=data["user_id"])
        except (KeyError, ValueError) as e:
            logger.error(
                f"Некорректные метаданные в webhook'е YooKassa: {e}",
                extra={
                    "payment_id": payment.id if payment else "unknown",
                    "user_id": "unknown",
                    "amount": payment.amount.value if payment else "unknown",
                    "status": payment.status if payment else "unknown",
                },
            )
            return {"status": "error", "message": "Invalid metadata"}
        except Exception as e:
            logger.error(
                f"Ошибка при получении пользователя в webhook'е YooKassa: {e}",
                extra={"payment_id": payment.id if payment else "unknown"},
            )
            return {"status": "error", "message": "User not found"}

        old_expired_at = user.expired_at

        # Вычисляем will_retry для уведомлений об ошибках
        user_expired_at = normalize_date(user.expired_at)
        will_retry = (
            user_expired_at is not None and (user_expired_at - date.today()).days >= 0
        )

        # Проверяем, не обработан ли уже этот платеж
        if not payment.id:
            logger.error(
                "Отсутствует payment_id в webhook'е YooKassa",
                extra={"payment_id": "missing"},
            )
            return {"status": "error", "message": "Missing payment_id"}

        processed_payment = await ProcessedPayments.get_or_none(payment_id=payment.id)
        processed_status = (
            str(getattr(processed_payment, "status", "") or "").strip().lower()
            if processed_payment
            else ""
        )
        allow_safe_replay_on_webhook_duplicate = (
            processed_status == "succeeded"
            and event == WebhookNotificationEventType.PAYMENT_SUCCEEDED
            and payment.status == "succeeded"
        )
        if (
            processed_payment
            and processed_status != "pending"
            and not allow_safe_replay_on_webhook_duplicate
        ):
            logger.info(
                f"Платеж {payment.id} уже был обработан ранее",
                extra={
                    "payment_id": payment.id,
                    "user_id": user.id,
                    "amount": payment.amount.value,
                    "status": processed_status,
                },
            )
            return {"status": "ok"}

        # Обработка разных типов событий
        if event == WebhookNotificationEventType.REFUND_SUCCEEDED:
            # При возврате средств снимаем все привилегии: подписка, RemnaWave HWID,
            # семейные слоты. Без этого юзер сохраняет VPN-доступ после chargeback'а.
            from bloobcat.services.payment_revocation import revoke_access_for_refund

            revocation_report = await revoke_access_for_refund(
                user,
                payment_id=str(payment.id),
                reason="yookassa_refund",
            )

            # Сохраняем информацию о возврате (идемпотентно)
            await _upsert_processed_payment(
                payment_id=payment.id,
                user_id=user.id,
                amount=float(payment.amount.value),
                amount_external=float(payment.amount.value),
                amount_from_balance=0,
                status="refunded",
            )

            logger.info(
                f"Доступ отозван для пользователя {user.id} из-за возврата средств",
                extra={
                    "payment_id": payment.id,
                    "user_id": user.id,
                    "amount": payment.amount.value,
                    "status": "refunded",
                    "revocation": revocation_report,
                },
            )
            return {"status": "ok"}

        if event == WebhookNotificationEventType.PAYMENT_CANCELED:
            # Сохраняем информацию об отмене (идемпотентно)
            await _upsert_processed_payment(
                payment_id=payment.id,
                user_id=user.id,
                amount=float(payment.amount.value),
                amount_external=float(payment.amount.value),
                amount_from_balance=0,
                status="canceled",
            )
            # Резервации отключены

            logger.info(
                f"Автопродление отключено для пользователя {user.id} из-за отмены платежа",
                extra={
                    "payment_id": payment.id,
                    "user_id": user.id,
                    "amount": payment.amount.value,
                    "status": "canceled",
                },
            )
            # Ручные платежи: сообщаем пользователю outcome в боте,
            # чтобы после return_url он видел “успех/ошибка” без UI-поллинга в Mini App.
            is_auto_payment = _meta_bool(data.get("is_auto"), False)
            if not is_auto_payment:
                await _send_manual_payment_canceled_notifications_if_needed(
                    user=user,
                    payment_id=str(payment.id),
                    amount_external=float(payment.amount.value),
                )
            if is_auto_payment:
                disable = _meta_bool(data.get("disable_on_fail"), False)
                if disable:
                    user.is_subscribed = False
                    user.renew_id = None
                    await user.save()
                    # Уведомляем админа об отключении автопродления из-за отмены платежа
                    await cancel_subscription(user, reason="Автоплатеж был отменен")
                await notify_auto_renewal_failure(
                    user, reason="Платеж был отменен", will_retry=will_retry
                )
            return {"status": "ok"}

        if event != WebhookNotificationEventType.PAYMENT_SUCCEEDED:
            return {"status": "ok"}

        if payment.status != "succeeded":
            logger.warning(
                f"Автоплатеж {payment.id} для пользователя {user.id} завершился со статусом {payment.status}"
            )
            is_auto_payment = _meta_bool(data.get("is_auto"), False)
            if is_auto_payment:
                disable = _meta_bool(data.get("disable_on_fail"), False)
                if disable:
                    user.is_subscribed = False
                    user.renew_id = None
                    await user.save()
                    # Уведомляем админа об отключении автопродления из-за неуспешного платежа
                    await cancel_subscription(
                        user,
                        reason=f"Автоплатеж завершился со статусом: {payment.status}",
                    )
                await notify_auto_renewal_failure(
                    user,
                    reason=f"Платеж не прошел (статус: {payment.status})",
                    will_retry=will_retry,
                )
            return {"status": "ok"}

        claimed = await _claim_payment_effect_once(
            payment_id=str(payment.id),
            user_id=int(user.id),
            source="webhook",
        )
        if not claimed:
            logger.info("Payment %s effect already claimed/applied", payment.id)
            replay_eligible = await _should_replay_payment_notifications(
                payment_id=str(payment.id),
                user_id=int(user.id),
            )

            if replay_eligible:
                replay_amount_from_balance = _round_rub(
                    data.get("amount_from_balance", 0)
                )
                await _repair_processed_payment_financials(
                    payment_id=str(payment.id),
                    user_id=int(user.id),
                    amount_external=float(payment.amount.value),
                    amount_from_balance=float(replay_amount_from_balance),
                )

            try:
                replay_months = int(data.get("month") or 0)
            except Exception:
                replay_months = 0

            if replay_months > 0 and replay_eligible:
                try:
                    replay_device_count = int(data.get("device_count", 1) or 1)
                except Exception:
                    replay_device_count = 1
                if replay_device_count < 1:
                    replay_device_count = 1

                replay_discount_percent = None
                try:
                    if data.get("discount_percent") is not None:
                        replay_discount_percent = int(data.get("discount_percent"))
                except Exception:
                    replay_discount_percent = None

                replay_lte_gb = 0
                try:
                    replay_lte_gb = int(data.get("lte_gb") or 0)
                except Exception:
                    replay_lte_gb = 0

                await _replay_payment_notifications_if_needed(
                    user=user,
                    payment_id=str(payment.id),
                    days=max(
                        0,
                        (
                            add_months_safe(date.today(), replay_months) - date.today()
                        ).days,
                    ),
                    amount_external=float(payment.amount.value),
                    amount_from_balance=float(replay_amount_from_balance),
                    device_count=int(replay_device_count),
                    months=int(replay_months),
                    is_auto_payment=_meta_bool(data.get("is_auto"), False),
                    discount_percent=replay_discount_percent,
                    old_expired_at=old_expired_at,
                    new_expired_at=user.expired_at,
                    lte_gb_total=int(getattr(user, "lte_gb_total", 0) or replay_lte_gb),
                    method="yookassa",
                    tariff_kind=data.get("tariff_kind"),
                )
            return {"status": "ok"}

        await _refresh_payment_processing_lease(
            payment_id=str(payment.id),
            user_id=int(user.id),
            source="webhook",
        )

        if _meta_bool(data.get("upgrade_bundle"), False) or (
            str(data.get("payment_purpose") or "").strip().lower()
            == PAYMENT_PURPOSE_UPGRADE_BUNDLE
        ):
            ok, reason = await _apply_upgrade_bundle_effect(
                payment_id=str(payment.id),
                user=user,
                meta=data,
                amount_external=float(payment.amount.value),
                amount_from_balance=_round_rub(data.get("amount_from_balance", 0)),
                provider=provider,
            )
            if ok:
                return {"status": "ok"}
            return {
                "status": "error",
                "message": reason or "Upgrade bundle effect failed",
            }

        if _meta_bool(data.get("lte_topup"), False):
            ok, reason = await _apply_lte_topup_effect(
                payment_id=str(payment.id),
                user=user,
                meta=data,
                amount_external=float(payment.amount.value),
                amount_from_balance=_round_rub(data.get("amount_from_balance", 0)),
                provider=provider,
            )
            if ok:
                return {"status": "ok"}
            return {"status": "error", "message": reason or "LTE topup effect failed"}

        if _meta_bool(data.get("devices_topup"), False):
            ok, reason = await _apply_devices_topup_effect(
                payment_id=str(payment.id),
                user=user,
                meta=data,
                amount_external=float(payment.amount.value),
                amount_from_balance=_round_rub(data.get("amount_from_balance", 0)),
                provider=provider,
            )
            if ok:
                return {"status": "ok"}
            return {"status": "error", "message": reason or "Devices topup effect failed"}

        try:
            months = int(data["month"])
        except (KeyError, ValueError) as e:
            logger.error(
                f"Некорректное значение месяцев в webhook'е YooKassa: {e}",
                extra={"payment_id": payment.id},
            )
            await _mark_payment_effect_failed(
                payment_id=str(payment.id), error="Invalid month value"
            )
            return {"status": "error", "message": "Invalid month value"}

        lte_gb = int(data.get("lte_gb") or 0)
        lte_price_per_gb = float(data.get("lte_price_per_gb") or 0)
        lte_cost = _round_rub(data.get("lte_cost") or (lte_gb * lte_price_per_gb))
        discounted_raw = data.get("discounted_price")
        if discounted_raw is None:
            base_paid_price = float(data.get("base_full_price") or 0)
        else:
            base_paid_price = float(discounted_raw)

        # Достаём скидку, применённую при создании платежа (если была)
        discount_id = data.get("discount_id")
        discount_percent = int(data.get("discount_percent") or 0)

        # Read-only probe: do not decrement discount usage before entitlement commits
        discount_available = False
        consumed = False
        try:
            discount_available = await is_discount_available_if_needed(discount_id)
        except Exception:
            discount_available = False

        # ????? ????? ?? webhook
        active_tariff_for_lte = None
        raw_tariff_id = data.get("tariff_id")
        tariff_id = None
        if raw_tariff_id is not None:
            try:
                tariff_id = int(raw_tariff_id)
            except (TypeError, ValueError):
                tariff_id = None
        try:
            payment_device_count = int(data.get("device_count", 1) or 1)
        except (TypeError, ValueError):
            payment_device_count = 1
        if payment_device_count < 1:
            payment_device_count = 1
        new_tariff = None
        if tariff_id is not None:
            new_tariff = await Tariffs.get_or_none(id=tariff_id)
            if new_tariff:
                await new_tariff.sync_effective_pricing_fields()
            else:
                logger.error(f"Не найден тариф {tariff_id} при обработке платежа")
                await _mark_payment_effect_failed(
                    payment_id=str(payment.id), error=f"Tariff not found: {tariff_id}"
                )
                return {"status": "error", "message": "Tariff not found"}
        purchase_kind = await resolve_purchase_kind(
            metadata=data,
            months=months,
            device_count=payment_device_count,
            tariff_id=tariff_id,
            tariff=new_tariff,
        )
        base_tariff_snapshot = _build_base_tariff_snapshot(
            tariff=new_tariff,
            device_count=payment_device_count,
            lte_gb=lte_gb,
            lte_price_per_gb=lte_price_per_gb,
        )
        is_family_overlay_purchase = purchase_kind == "family"
        is_active_family_overlay = await has_active_family_overlay(user)
        if tariff_id is not None:
            # Проверяем, есть ли у пользователя активная подписка и активный тариф
            current_date = date.today()
            additional_days = 0
            user_expired_at = normalize_date(user.expired_at)

            if (
                (not is_family_overlay_purchase)
                and (not is_active_family_overlay)
                and user_expired_at
                and user_expired_at > current_date
                and user.active_tariff_id
            ):
                # У пользователя есть действующая подписка
                try:
                    # Получаем активный тариф
                    active_tariff = await ActiveTariffs.get(id=user.active_tariff_id)

                    # Вычисляем оставшиеся дни подписки
                    days_remaining = (user_expired_at - current_date).days
                    logger.info(
                        f"У пользователя {user.id} осталось {days_remaining} дней подписки"
                    )

                    # Рассчитываем количество дней, которое давал старый тариф
                    old_months = int(active_tariff.months)
                    old_target_date = add_months_safe(current_date, old_months)
                    old_total_days = (old_target_date - current_date).days

                    # Рассчитываем процент неиспользованной подписки
                    unused_percent = (
                        days_remaining / old_total_days if old_total_days > 0 else 0
                    )
                    unused_value = unused_percent * active_tariff.price

                    logger.info(
                        f"Неиспользованная часть подписки пользователя {user.id}: "
                        f"{days_remaining}/{old_total_days} дней ({unused_percent:.2%}), "
                        f"стоимость: {unused_value:.2f} руб."
                    )

                    # ИСПРАВЛЕННАЯ ЛОГИКА: рассчитываем через пропорцию от общей суммы
                    # Выполняем ТОЛЬКО если скидка не была списана (например, повторная оплата без скидки)
                    if new_tariff.price > 0 and not discount_available:
                        # Получаем device_count и рассчитываем правильную цену
                        try:
                            device_count = int(data.get("device_count", 1))
                        except (ValueError, TypeError):
                            device_count = 1
                        if device_count < 1:
                            device_count = 1

                        # Рассчитываем итоговую цену для указанного количества устройств
                        correct_new_tariff_price = new_tariff.calculate_price(
                            device_count
                        )

                        # Общая сумма = заплачено пользователем + компенсация за старый тариф
                        total_paid = max(0.0, base_paid_price)
                        total_amount = total_paid + unused_value

                        # Рассчитываем новый период подписки (стандартный для тарифа)
                        tariff_months = int(new_tariff.months)
                        new_target_date = add_months_safe(current_date, tariff_months)
                        new_total_days = (new_target_date - current_date).days

                        # Пропорция: x дней / общая_сумма = полный_период_тарифа / цена_тарифа
                        # x = общая_сумма * полный_период_тарифа / цена_тарифа
                        calculated_days = int(
                            total_amount * new_total_days / correct_new_tariff_price
                        )

                        logger.info(
                            f"ИСПРАВЛЕННЫЙ расчёт для пользователя {user.id}: "
                            f"Заплачено: {total_paid:.2f} руб + Компенсация: {unused_value:.2f} руб = "
                            f"Общая сумма: {total_amount:.2f} руб. "
                            f"Пропорция: {calculated_days} дней = {total_amount:.2f} * {new_total_days} / {correct_new_tariff_price:.2f}"
                        )

                        # Устанавливаем рассчитанные дни как итоговые (без additional_days)
                        additional_days = (
                            0  # Сбрасываем, так как используем calculated_days
                        )
                        days = calculated_days  # Переопределяем days
                except Exception as e:
                    logger.error(
                        f"Ошибка при расчете переноса подписки для {user.id}: {str(e)}"
                    )
                    additional_days = 0  # При ошибке не добавляем дополнительные дни

            # Рассчитываем точное количество дней для указанного количества месяцев
            # При смене тарифа days уже рассчитано через пропорцию
            if "calculated_days" not in locals():
                # Обычная покупка нового тарифа без смены
                current_date = date.today()
                target_date = add_months_safe(current_date, months)
                days = (target_date - current_date).days
                logger.info(f"Стандартное количество дней подписки: {days}")
            else:
                # days уже рассчитано через пропорцию при смене тарифа
                logger.info(
                    f"Итоговое количество дней подписки (через пропорцию): {days}"
                )
        else:
            # Если нет tariff_id, значит это автоплатеж или другой тип платежа, просто рассчитываем дни как обычно
            current_date = date.today()
            target_date = add_months_safe(current_date, months)
            days = (target_date - current_date).days
            logger.info(f"Стандартное количество дней подписки: {days}")

        # Рассчитываем точное количество дней для указанного количества месяцев
        # и выполняем пропорциональную коррекцию до применения entitlement,
        # если скидка не была списана.
        if not discount_available and tariff_id is not None:
            try:
                if new_tariff:
                    correct_new_tariff_price = new_tariff.calculate_price(
                        payment_device_count
                    )
                    amount_paid_by_user = float(payment.amount.value)
                    amount_from_balance_preview = _round_rub(
                        data.get("amount_from_balance", 0)
                    )
                    total_paid_now = amount_paid_by_user + amount_from_balance_preview
                    base_paid_now = max(0.0, total_paid_now - lte_cost)
                    current_date = date.today()
                    original_months = int(new_tariff.months)
                    new_target_date = add_months_safe(current_date, original_months)
                    new_total_days = (new_target_date - current_date).days
                    proportional_days = int(
                        base_paid_now
                        * new_total_days
                        / max(1, correct_new_tariff_price)
                    )
                    if proportional_days > 0 and proportional_days < days:
                        logger.info(
                            "Pre-entitlement days cap applied for payment %s user %s: %s -> %s (discount not consumed)",
                            payment.id,
                            user.id,
                            days,
                            proportional_days,
                        )
                        days = proportional_days
            except Exception:
                pass

        should_preserve_active_tariff_state = (
            purchase_kind == "base" and is_active_family_overlay
        )
        # Capture LTE carryover BEFORE the legacy code path can delete the old
        # active_tariff later (lines below ~4220). Used as a delta added to the
        # new tariff's lte_gb_total so users do not lose gigabytes they paid
        # for via top-up.
        webhook_lte_carryover_gb = 0
        try:
            if (
                tariff_id is not None
                and user.active_tariff_id
                and not should_preserve_active_tariff_state
            ):
                old_active_tariff = await ActiveTariffs.get_or_none(
                    id=user.active_tariff_id
                )
                if old_active_tariff:
                    webhook_lte_carryover_gb = _compute_lte_carryover_gb(
                        old_active_tariff
                    )
                    if webhook_lte_carryover_gb > 0:
                        logger.info(
                            "Переносим остаток LTE %s GB на новый тариф пользователя %s",
                            webhook_lte_carryover_gb,
                            user.id,
                        )
            elif (
                tariff_id is not None
                and user.active_tariff_id
                and should_preserve_active_tariff_state
            ):
                logger.info(
                    "Webhook base purchase during active family overlay: skip LTE carryover and active tariff replacement user=%s",
                    user.id,
                )

            amount_from_balance = _round_rub(data.get("amount_from_balance", 0))
            if amount_from_balance > 0:
                initial_balance = user.balance
                user.balance = max(0, user.balance - amount_from_balance)
                logger.info(
                    f"Списание с бонусного баланса пользователя {user.id}. "
                    f"Сумма: {amount_from_balance}. Баланс до: {initial_balance}, После: {user.balance}",
                    extra={
                        "payment_id": payment.id,
                        "user_id": user.id,
                        "amount_from_balance": amount_from_balance,
                    },
                )

            # Устанавливаем новую дату окончания подписки
            # В случае автопродления переходим на новый тариф, сбрасывая старую подписку
            is_auto = _meta_bool(data.get("is_auto"), False)
            added_to_frozen_base = False
            if is_auto:
                (
                    added_to_frozen_base,
                    family_expires_at,
                ) = await _apply_purchase_extension_by_kind(
                    user=user,
                    purchase_kind=purchase_kind,
                    purchased_days=days,
                    base_tariff_snapshot=base_tariff_snapshot,
                )
                if purchase_kind == "family":
                    logger.info(
                        "Webhook auto family purchase: updated overlay expiry user=%s family_expires_at=%s",
                        user.id,
                        family_expires_at,
                    )
                elif added_to_frozen_base:
                    logger.info(
                        "Webhook auto base purchase during active family overlay moved to frozen base days for user %s",
                        user.id,
                    )
                else:
                    logger.info(
                        f"Автопродление: подписка пользователя {user.id} продлена на {days} дней, новая дата истечения: {user.expired_at}"
                    )
            else:
                if (
                    purchase_kind == "base"
                    and ("calculated_days" in locals())
                    and (not is_active_family_overlay)
                ):
                    added_to_frozen_base = (
                        await apply_base_purchase_to_frozen_base_if_active(
                            user,
                            purchased_days=days,
                            base_tariff_snapshot=base_tariff_snapshot,
                        )
                    )
                    if added_to_frozen_base:
                        logger.info(
                            "Base purchase during active family overlay moved to frozen base days for user %s",
                            user.id,
                        )
                    else:
                        user.expired_at = current_date + timedelta(days=days)
                        logger.info(
                            f"Смена base-тарифа для пользователя {user.id}: установлена дата {user.expired_at} "
                            f"({days} дней от текущей даты, рассчитано через пропорцию)"
                        )
                else:
                    (
                        added_to_frozen_base,
                        family_expires_at,
                    ) = await _apply_purchase_extension_by_kind(
                        user=user,
                        purchase_kind=purchase_kind,
                        purchased_days=days,
                        base_tariff_snapshot=base_tariff_snapshot,
                    )
                    if purchase_kind == "family":
                        logger.info(
                            "Webhook family purchase: updated overlay expiry user=%s family_expires_at=%s",
                            user.id,
                            family_expires_at,
                        )
                    elif added_to_frozen_base:
                        logger.info(
                            "Base purchase during active family overlay moved to frozen base days for user %s",
                            user.id,
                        )
                    else:
                        logger.info(
                            f"Подписка пользователя {user.id} продлена на {days} дней, новая дата истечения: {user.expired_at} "
                            f"(с учетом оставшихся дней предыдущей подписки/триала)"
                        )

            preserve_active_tariff_state = (
                purchase_kind == "base" and added_to_frozen_base
            )

            # If a tariff_id is provided in metadata, ensure it's created in ActiveTariffs and assign to user
            original = new_tariff if tariff_id is not None else None
            if tariff_id is not None and not preserve_active_tariff_state:
                if original:
                    await original.sync_effective_pricing_fields()
                    _, effective_multiplier = original.get_effective_pricing()
                    # Получаем device_count из метаданных платежа
                    try:
                        device_count = int(data.get("device_count", 1))
                    except (ValueError, TypeError):
                        device_count = 1
                    if device_count < 1:
                        device_count = 1

                    # Рассчитываем итоговую цену для указанного количества устройств
                    calculated_price = original.calculate_price(device_count)

                    # Удаляем предыдущий активный тариф, если он есть
                    if user.active_tariff_id:
                        old_active_tariff = await ActiveTariffs.get_or_none(
                            id=user.active_tariff_id
                        )
                        if old_active_tariff:
                            logger.info(
                                f"Удаляем предыдущий активный тариф {user.active_tariff_id} пользователя {user.id}"
                            )
                            await old_active_tariff.delete()
                        else:
                            logger.warning(
                                f"Не найден активный тариф {user.active_tariff_id} для удаления у пользователя {user.id}"
                            )

                    # код по сбросу HWID временно отключен
                    # if user.remnawave_uuid:
                    # await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)

                    msk_today = datetime.now(MSK_TZ).date()
                    usage_snapshot = None
                    if user.remnawave_uuid:
                        await _refresh_payment_processing_lease(
                            payment_id=str(payment.id),
                            user_id=int(user.id),
                            source="webhook_usage_pre",
                        )
                        try:
                            usage_snapshot = await _await_payment_external_call(
                                _fetch_today_lte_usage_gb(str(user.remnawave_uuid)),
                                operation="webhook_fetch_today_lte_usage_gb",
                            )
                        except asyncio.TimeoutError:
                            usage_snapshot = None
                        finally:
                            await _refresh_payment_processing_lease(
                                payment_id=str(payment.id),
                                user_id=int(user.id),
                                source="webhook_usage_post",
                            )
                    # Create a new active tariff entry with random ID
                    if "lte_price_per_gb" in data:
                        lte_price_snapshot = float(data.get("lte_price_per_gb") or 0)
                    else:
                        lte_price_snapshot = float(original.lte_price_per_gb or 0)
                    effective_lte_gb_total = int(lte_gb or 0) + int(
                        webhook_lte_carryover_gb or 0
                    )
                    active_tariff = await ActiveTariffs.create(
                        user=user,  # Link to this user
                        name=original.name,
                        months=original.months,
                        price=calculated_price,  # Используем рассчитанную цену
                        hwid_limit=device_count,  # Используем выбранное количество устройств
                        lte_gb_total=effective_lte_gb_total,
                        lte_gb_used=0.0,
                        lte_price_per_gb=lte_price_snapshot,
                        lte_usage_last_date=msk_today,
                        lte_usage_last_total_gb=usage_snapshot
                        if usage_snapshot is not None
                        else 0.0,
                        progressive_multiplier=effective_multiplier,
                        residual_day_fraction=0.0,
                    )
                    active_tariff_for_lte = active_tariff
                    # Link user to this active tariff
                    user.active_tariff_id = active_tariff.id
                    user.lte_gb_total = effective_lte_gb_total

                    # Устанавливаем hwid_limit пользователю из выбранного количества устройств
                    user.hwid_limit = device_count
                    logger.info(
                        f"Created ActiveTariff {active_tariff.id} for user {user.id} based on tariff {original.id}, device_count={device_count}, установлен hwid_limit={device_count}"
                    )

                    # ВАЖНО: сохраняем active_tariff_id и hwid_limit в БД как можно раньше
                    # чтобы минимизировать race condition с remnawave_updater
                    try:
                        await user.save(
                            update_fields=["active_tariff_id", "hwid_limit"]
                        )
                        logger.debug(
                            f"Ранее сохранены active_tariff_id={active_tariff.id} и hwid_limit={device_count} для пользователя {user.id}"
                        )
                    except Exception as persist_exc:
                        logger.warning(
                            f"Не удалось рано сохранить active_tariff_id/hwid_limit для {user.id}: {persist_exc}"
                        )
                else:
                    logger.error(
                        f"Original tariff {tariff_id} not found; skipping ActiveTariffs"
                    )
            elif tariff_id is not None and preserve_active_tariff_state:
                logger.info(
                    "Webhook base purchase during active family overlay preserved active_tariff/hwid for user %s",
                    user.id,
                )

            # После успешной оплаты сбрасываем счётчик уменьшений лимита устройств
            if user.active_tariff_id:
                await ActiveTariffs.filter(id=user.active_tariff_id).update(
                    devices_decrease_count=0
                )

            # Если это автоплатеж и он успешен, обновляем статус подписки
            payment_method = getattr(payment, "payment_method", None)
            saved_method_id = (
                getattr(payment_method, "id", None) if payment_method else None
            )
            saved_flag = (
                _meta_bool(getattr(payment_method, "saved", None), False)
                if payment_method
                else False
            )
            if (
                not is_auto
                and saved_method_id
                and (saved_flag or getattr(payment_method, "saved", None) is None)
            ):
                user.renew_id = str(saved_method_id)
                user.is_subscribed = True

            # Если это автоплатеж и он успешен, обновляем статус подписки
            if is_auto and payment.status == "succeeded":
                user.is_subscribed = True

            # Если у пользователя был пробный период, сбрасываем флаг
            if user.is_trial:
                user.is_trial = False
                logger.info(
                    f"Сброшен флаг пробного периода для пользователя {user.id} после оплаты подписки",
                    extra={"payment_id": payment.id, "user_id": user.id},
                )

            await user.save()  # Сохраняем пользователя (включая обновленный баланс)

            amount_paid_via_yookassa = float(payment.amount.value)
            full_tariff_price_for_history = (
                amount_paid_via_yookassa + amount_from_balance
            )
            await _mark_payment_effect_success(
                payment_id=str(payment.id),
                user_id=user.id,
                amount=full_tariff_price_for_history,
                amount_external=amount_paid_via_yookassa,
                amount_from_balance=amount_from_balance,
                status="succeeded",
            )

            # Синхронизируем данные с RemnaWave
            if (
                user.remnawave_uuid
                and not preserve_active_tariff_state
                and user.is_device_per_user_enabled()
            ):
                await _sync_device_per_user_after_payment(user, source="webhook")
            if (
                user.remnawave_uuid
                and not preserve_active_tariff_state
                and not user.is_device_per_user_enabled()
            ):
                # Настройки бесконечных повторных попыток с ограничением по времени
                max_total_time = (
                    60  # Максимальное время в секундах для всех попыток (1 минута)
                )
                start_time = datetime.now()
                retry_count = 0
                remnawave_client = None
                success = False

                try:
                    # Подготавливаем параметры для обновления
                    update_params = {}

                    # Передаём дату в формате date; клиент внутри сам форматирует expireAt
                    update_params["expireAt"] = user.expired_at

                    # Определяем hwid_limit ТОЛЬКО для новых подписок (когда есть tariff_id),
                    # при автопродлении hwid_limit не меняем
                    hwid_limit = None
                    if (
                        tariff_id is not None
                        and original
                        and not preserve_active_tariff_state
                    ):
                        try:
                            device_count = int(data.get("device_count", 1))
                        except (ValueError, TypeError):
                            device_count = 1
                        if device_count < 1:
                            device_count = 1
                        hwid_limit = device_count
                        logger.info(
                            f"Новая подписка: устанавливаем hwid_limit={hwid_limit} из device_count для тарифа ID={original.id}"
                        )
                        update_params["hwidDeviceLimit"] = hwid_limit
                    elif preserve_active_tariff_state:
                        logger.info(
                            "Base purchase during active family overlay: skip RemnaWave hwid update for user %s",
                            user.id,
                        )
                    else:
                        logger.info(
                            f"Автопродление: hwid_limit не меняем, обновляем только дату истечения"
                        )

                    # Цикл повторных попыток обновления информации в RemnaWave
                    while not success:
                        await _refresh_payment_processing_lease(
                            payment_id=str(payment.id),
                            user_id=int(user.id),
                            source="webhook",
                        )
                        # Проверяем, не превысили ли мы общее время попыток
                        elapsed_time = (datetime.now() - start_time).total_seconds()
                        remaining_budget = max_total_time - elapsed_time
                        if remaining_budget <= 0:
                            logger.error(
                                f"Превышено максимальное время ({max_total_time} сек) для обновления пользователя {user.id} в RemnaWave. "
                                f"Выполнено {retry_count} попыток за {elapsed_time:.1f} сек."
                            )
                            break

                        try:
                            retry_count += 1

                            # Создаем клиент RemnaWave для каждой попытки
                            if remnawave_client:
                                await remnawave_client.close()
                            remnawave_client = RemnaWaveClient(
                                remnawave_settings.url,
                                remnawave_settings.token.get_secret_value(),
                            )

                            # Обновляем пользователя в RemnaWave
                            logger.info(
                                f"Попытка #{retry_count} [{elapsed_time:.1f} сек]: Обновляем пользователя {user.id} в RemnaWave (UUID: {user.remnawave_uuid}). "
                                f"Новая дата: {user.expired_at}"
                                + (
                                    f", hwid_limit: {hwid_limit}"
                                    if hwid_limit is not None
                                    else ", hwid_limit без изменений"
                                )
                            )

                            try:
                                await _refresh_payment_processing_lease(
                                    payment_id=str(payment.id),
                                    user_id=int(user.id),
                                    source="webhook_remna_pre",
                                )
                                await _await_payment_external_call(
                                    remnawave_client.users.update_user(
                                        uuid=user.remnawave_uuid,
                                        **update_params,
                                    ),
                                    operation="webhook_remnawave_update_user",
                                    timeout=remaining_budget,
                                )
                            except Exception as update_err:
                                # Если юзер удален в RemnaWave – пересоздаём и пытаемся снова
                                if any(
                                    token in str(update_err)
                                    for token in [
                                        "User not found",
                                        "A039",
                                        "Update user error",
                                    ]
                                ):
                                    recreated = await user.recreate_remnawave_user()
                                    if recreated and user.remnawave_uuid:
                                        elapsed_time = (
                                            datetime.now() - start_time
                                        ).total_seconds()
                                        remaining_budget = max_total_time - elapsed_time
                                        if remaining_budget <= 0:
                                            logger.error(
                                                f"Превышено максимальное время ({max_total_time} сек) для обновления пользователя {user.id} в RemnaWave. "
                                                f"Выполнено {retry_count} попыток за {elapsed_time:.1f} сек."
                                            )
                                            break
                                        await _await_payment_external_call(
                                            remnawave_client.users.update_user(
                                                uuid=user.remnawave_uuid,
                                                **update_params,
                                            ),
                                            operation="webhook_remnawave_update_user_recreated",
                                            timeout=remaining_budget,
                                        )
                                else:
                                    raise
                                await _refresh_payment_processing_lease(
                                    payment_id=str(payment.id),
                                    user_id=int(user.id),
                                    source="webhook_remna_post",
                                )

                            logger.info(
                                f"УСПЕХ! Пользователь {user.id} обновлен в RemnaWave с попытки #{retry_count} за {elapsed_time:.1f} сек"
                            )
                            success = True
                            break  # Успешное обновление, выходим из цикла

                        except Exception as retry_exc:
                            await _refresh_payment_processing_lease(
                                payment_id=str(payment.id),
                                user_id=int(user.id),
                                source="webhook_remna_err",
                            )
                            # Ограничиваем экспоненциальный рост задержки
                            backoff_time = min(
                                10,
                                0.5 * (2 ** min(retry_count, 5))
                                + random.uniform(0, 0.5),
                            )
                            logger.warning(
                                f"Ошибка при обновлении пользователя {user.id} в RemnaWave (попытка {retry_count}, прошло {elapsed_time:.1f} сек): {str(retry_exc)}. "
                                f"Повторная попытка через {backoff_time:.2f} сек."
                            )
                            elapsed_time = (datetime.now() - start_time).total_seconds()
                            remaining_budget = max_total_time - elapsed_time
                            if remaining_budget <= 0:
                                logger.error(
                                    f"Превышено максимальное время ({max_total_time} сек) для обновления пользователя {user.id} в RemnaWave. "
                                    f"Выполнено {retry_count} попыток за {elapsed_time:.1f} сек."
                                )
                                break
                            await asyncio.sleep(min(backoff_time, remaining_budget))

                    # Если не удалось обновить после всех попыток
                    if not success:
                        logger.error(
                            f"НЕ УДАЛОСЬ обновить пользователя {user.id} в RemnaWave даже после {retry_count} попыток. "
                            f"Общее время: {(datetime.now() - start_time).total_seconds():.1f} сек."
                        )

                except Exception as e:
                    logger.error(
                        f"Ошибка при обновлении пользователя {user.id} в RemnaWave: {str(e)}"
                    )
                    # Продолжаем обработку платежа, несмотря на ошибку синхронизации с RemnaWave
                finally:
                    # Закрываем клиент в любом случае
                    if remnawave_client:
                        try:
                            await remnawave_client.close()
                        except Exception as close_exc:
                            logger.warning(
                                f"Ошибка при закрытии клиента RemnaWave: {str(close_exc)}"
                            )
            elif user.remnawave_uuid and preserve_active_tariff_state:
                logger.info(
                    "Webhook base purchase during active family overlay: skipped RemnaWave entitlement update for user %s",
                    user.id,
                )

            active_tariff_current = active_tariff_for_lte
            if active_tariff_current is None and user.active_tariff_id:
                active_tariff_current = await ActiveTariffs.get_or_none(
                    id=user.active_tariff_id
                )
            if active_tariff_current:
                if is_auto and not preserve_active_tariff_state:
                    active_tariff_current.lte_gb_used = 0.0
                    msk_today = datetime.now(MSK_TZ).date()
                    update_fields = ["lte_gb_used"]
                    usage_snapshot = None
                    if user.remnawave_uuid:
                        await _refresh_payment_processing_lease(
                            payment_id=str(payment.id),
                            user_id=int(user.id),
                            source="webhook_auto_usage_pre",
                        )
                        try:
                            usage_snapshot = await _await_payment_external_call(
                                _fetch_today_lte_usage_gb(str(user.remnawave_uuid)),
                                operation="webhook_auto_fetch_today_lte_usage_gb",
                            )
                        except asyncio.TimeoutError:
                            usage_snapshot = None
                        finally:
                            await _refresh_payment_processing_lease(
                                payment_id=str(payment.id),
                                user_id=int(user.id),
                                source="webhook_auto_usage_post",
                            )
                    if usage_snapshot is not None:
                        active_tariff_current.lte_usage_last_date = msk_today
                        active_tariff_current.lte_usage_last_total_gb = usage_snapshot
                        update_fields.extend(
                            ["lte_usage_last_date", "lte_usage_last_total_gb"]
                        )
                    elif active_tariff_current.lte_usage_last_date != msk_today:
                        active_tariff_current.lte_usage_last_date = msk_today
                        active_tariff_current.lte_usage_last_total_gb = 0.0
                        update_fields.extend(
                            ["lte_usage_last_date", "lte_usage_last_total_gb"]
                        )
                    await active_tariff_current.save(update_fields=update_fields)
                elif is_auto and preserve_active_tariff_state:
                    logger.info(
                        "Webhook auto base purchase during active family overlay preserved active LTE usage state user=%s",
                        user.id,
                    )

                if not preserve_active_tariff_state:
                    await NotificationMarks.filter(
                        user_id=user.id, type="lte_usage"
                    ).delete()
                    if user.remnawave_uuid:
                        try:
                            effective_lte_total = (
                                user.lte_gb_total
                                if user.lte_gb_total is not None
                                else (active_tariff_current.lte_gb_total or 0)
                            )
                            should_enable_lte = effective_lte_total > (
                                active_tariff_current.lte_gb_used or 0
                            )
                            await _await_payment_external_call(
                                set_lte_squad_status(
                                    str(user.remnawave_uuid), enable=should_enable_lte
                                ),
                                operation="webhook_set_lte_squad_status",
                            )
                        except Exception as e:
                            logger.error(
                                f"Ошибка обновления LTE-сквада после оплаты для {user.id}: {e}"
                            )
                elif user.remnawave_uuid:
                    logger.info(
                        "Webhook base purchase during active family overlay: skipped LTE state sync for user %s",
                        user.id,
                    )

            # Partner program: award cashback to partner referrer (money-based).
            try:
                await _award_partner_cashback(
                    payment_id=str(payment.id),
                    referral_user=user,
                    amount_rub_total=int(
                        _round_rub(float(full_tariff_price_for_history))
                    ),
                )
            except Exception:
                # best-effort, do not affect payment flow
                pass

            logger.info(
                f"Успешно продлена подписка для пользователя {user.id} на {days} дней",
                extra={
                    "payment_id": payment.id,
                    "user_id": user.id,
                    "amount": payment.amount.value,  # Сумма платежа Yookassa
                    "amount_from_balance": amount_from_balance,  # Сумма списания с баланса
                    "status": "succeeded",
                    "is_auto": is_auto,
                    "discount_percent": discount_percent,
                    "discount_id": discount_id,
                },
            )

            # Списываем использование скидки напрямую, если не списали ранее
            if discount_available and not consumed:
                try:
                    consumed = await consume_discount_if_needed(discount_id)
                except Exception:
                    consumed = False

            if is_auto:
                # Начисление круток за автосписание: 1 крутка за каждый месяц
                try:
                    attempts_before = int(getattr(user, "prize_wheel_attempts", 0) or 0)
                    if months and months > 0:
                        user.prize_wheel_attempts = attempts_before + int(months)
                        await user.save()
                        logger.info(
                            f"Начислено {months} круток за автосписание пользователю {user.id}. Было: {attempts_before}, стало: {user.prize_wheel_attempts}"
                        )
                except Exception as award_exc:
                    logger.error(
                        f"Не удалось начислить крутки за автосписание для {user.id}: {award_exc}"
                    )
                # Сообщение пользователю о начислении круток
                try:
                    await notify_spin_awarded(
                        user=user,
                        added_attempts=int(months),
                        total_attempts=int(user.prize_wheel_attempts or 0),
                    )
                except Exception as e_notify_spins:
                    logger.error(
                        f"Ошибка уведомления о крутках (вебхук) для {user.id}: {e_notify_spins}"
                    )

        except Exception as e:
            logger.error(
                f"Ошибка при продлении подписки в webhook'е YooKassa: {e}",
                extra={"payment_id": payment.id},
            )
            await _mark_payment_effect_failed(payment_id=str(payment.id), error=str(e))
            return {"status": "error", "message": "Error extending subscription"}

        amount = payment.amount.value

        # Standard referral program: apply first-payment friend bonus once, then
        # award referrer cashback from the YooKassa external amount only.
        if user.referred_by:
            try:
                device_count = 1
                if isinstance(data, dict):
                    try:
                        device_count = int(data.get("device_count", 1) or 1)
                    except (TypeError, ValueError):
                        device_count = 1

                amount_external_rub = int(_round_rub(float(amount or 0)))
                reward_res = await _apply_referral_first_payment_reward(
                    referred_user_id=user.id,
                    payment_id=str(payment.id),
                    amount_rub=amount_external_rub if amount is not None else None,
                    months=int(months or 0),
                    device_count=device_count,
                )

                await _award_standard_referral_cashback(
                    payment_id=str(payment.id),
                    referral_user=user,
                    amount_external_rub=amount_external_rub,
                    first_payment_res=reward_res if reward_res.get("applied") else None,
                )

                if reward_res.get("applied") and on_referral_friend_bonus is not None:
                    referrer = await Users.get(id=int(reward_res["referrer_id"]))
                    try:
                        await on_referral_friend_bonus(
                            user=user,
                            referrer=referrer,
                            friend_bonus_days=int(reward_res["friend_bonus_days"]),
                            months=int(reward_res["months"]),
                            device_count=int(reward_res["device_count"]),
                        )
                    except Exception as e_friend_notify:
                        logger.error(
                            "Ошибка уведомления реферала %s о +%s днях: %s",
                            user.id,
                            reward_res.get("friend_bonus_days"),
                            e_friend_notify,
                        )
            except Exception as e:
                logger.error(
                    f"Ошибка при обработке реферала (ledger/cashback) в webhook'е YooKassa: {e}",
                    extra={"payment_id": payment.id},
                )

        lte_gb_for_log = None
        if isinstance(data, dict) and data.get("lte_gb") is not None:
            try:
                lte_gb_for_log = int(data.get("lte_gb"))
            except (TypeError, ValueError):
                lte_gb_for_log = None
        if lte_gb_for_log is None:
            lte_gb_for_log = (
                user.lte_gb_total if hasattr(user, "lte_gb_total") else None
            )

        await _replay_payment_notifications_if_needed(
            user=user,
            payment_id=str(payment.id),
            days=int(days),
            amount_external=float(amount_paid_via_yookassa),
            amount_from_balance=float(amount_from_balance),
            device_count=int(payment_device_count or 1),
            months=int(months),
            is_auto_payment=bool(is_auto),
            discount_percent=discount_percent,
            old_expired_at=old_expired_at,
            new_expired_at=user.expired_at,
            lte_gb_total=int(lte_gb_for_log or 0),
            method="yookassa",
            tariff_kind=data.get("tariff_kind"),
        )

        return {"status": "ok"}
    except Exception as e:
        logger.error(
            f"Непредвиденная ошибка в webhook'е YooKassa: {e}",
            extra={"payment_id": payment.id if payment else "unknown"},
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{tariff_id}")
async def pay_post(
    tariff_id: int,
    payload: CreatePaymentRequest,
    user: Users = Depends(validate),
):
    return await pay(
        tariff_id=tariff_id,
        email=payload.email,
        device_count=int(payload.device_count),
        lte_gb=int(payload.lte_gb),
        client_request_id=payload.client_request_id,
        user=user,
    )


@router.get("/{tariff_id}")
async def pay(
    tariff_id: int,
    email: str,
    device_count: int = 1,
    lte_gb: int = 0,
    client_request_id: str | None = None,
    user: Users = Depends(validate),
):
    tariff = await Tariffs.get_or_none(id=tariff_id)
    if tariff is None or not bool(getattr(tariff, "is_active", True)):
        raise HTTPException(status_code=404, detail="Tariff not found")
    await tariff.sync_effective_pricing_fields()
    normalized_client_request_id = _normalize_client_request_id(client_request_id)

    one_month_reference_tariff = None
    if int(getattr(tariff, "months", 1) or 1) > 1:
        one_month_reference_tariff = await Tariffs.filter(months=1, is_active=True).order_by("order").first()
    active_campaign = await select_active_campaign(user)
    quote = await build_subscription_quote(
        tariff=tariff,
        user_id=int(user.id),
        device_count=device_count,
        lte_gb=lte_gb,
        one_month_reference_tariff=one_month_reference_tariff,
        campaign=active_campaign,
    )
    device_count = int(quote.device_count)
    lte_gb = int(quote.lte_gb)
    months = int(quote.months)
    lte_price_per_gb = float(quote.lte_price_per_gb)
    lte_cost = int(quote.lte_price_rub)
    base_full_price = int(quote.subscription_price_rub)
    discounted_price = int(quote.discounted_subscription_price_rub)
    discount_id = quote.discount_id
    discount_percent = int(quote.discount_percent) if quote.discount_percent else None
    purchase_kind = quote.tariff_kind
    base_tariff_snapshot = _build_base_tariff_snapshot(
        tariff=tariff,
        device_count=device_count,
        lte_gb=lte_gb,
        lte_price_per_gb=lte_price_per_gb,
    )
    is_family_overlay_purchase = purchase_kind == "family"
    is_active_family_overlay = await has_active_family_overlay(user)
    full_price = int(discounted_price) + lte_cost
    user_balance = float(user.balance)
    old_expired_at = user.expired_at
    old_active_tariff = None
    balance_lte_carryover_gb = 0
    if user.active_tariff_id:
        old_active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if old_active_tariff and not (
            purchase_kind == "base" and is_active_family_overlay
        ):
            balance_lte_carryover_gb = _compute_lte_carryover_gb(old_active_tariff)

    try:
        current_date = date.today()
        target_date = add_months_safe(current_date, months)
        days = (target_date - current_date).days
    except Exception as e:
        logger.error(
            f"Ошибка при расчете дней подписки для пользователя {user.id} и тарифа {tariff_id}: {e}",
            extra={"user_id": user.id, "tariff_id": tariff_id, "months": months},
        )
        raise HTTPException(
            status_code=500, detail="Error calculating subscription days"
        )

    # Проверка полной оплаты с баланса.
    # Для retry после post-debit сбоя допускаем повторный вход в balance-ветку,
    # даже если текущий баланс уже уменьшен до нуля.
    balance_retry_payment_id = (
        _build_balance_payment_id(
            user_id=int(user.id), client_request_id=normalized_client_request_id
        )
        if normalized_client_request_id is not None
        else None
    )
    recovered_balance_retry = False
    if balance_retry_payment_id is not None:
        recovered_balance_retry = await _was_balance_debit_applied(
            payment_id=balance_retry_payment_id,
            expected_amount=float(full_price),
        )

    # Carryover replaces the legacy LTE refund: remaining GBs roll over to the
    # new tariff instead of being converted to balance, so balance eligibility
    # is checked against the cash balance only.
    effective_balance = user_balance
    if effective_balance >= full_price or recovered_balance_retry:
        logger.info(
            f"Оплата тарифа {tariff_id} для пользователя {user.id} полностью с баланса. "
            f"Цена: {full_price}, Баланс: {user_balance}, Скидка: {discount_percent}% (id={discount_id}), "
            f"LTE: {lte_gb} GB"
        )

        payment_id = (
            balance_retry_payment_id
            if balance_retry_payment_id is not None
            else f"balance_{user.id}_{int(datetime.now().timestamp())}_{randint(100, 999)}"
        )

        if normalized_client_request_id is not None:
            claimed_balance_effect = await _claim_payment_effect_once(
                payment_id=payment_id,
                user_id=int(user.id),
                source="balance",
            )
            if not claimed_balance_effect:
                existing_balance_payment = await ProcessedPayments.get_or_none(
                    payment_id=payment_id
                )
                if existing_balance_payment and (
                    bool(getattr(existing_balance_payment, "effect_applied", False))
                    or str(
                        getattr(existing_balance_payment, "status", "") or ""
                    ).lower()
                    == "succeeded"
                ):
                    user_after_retry = await Users.get(id=user.id)
                    await _replay_payment_notifications_if_needed(
                        user=user_after_retry,
                        payment_id=payment_id,
                        days=int(days),
                        amount_external=0.0,
                        amount_from_balance=float(full_price),
                        device_count=int(device_count or 1),
                        months=int(months),
                        is_auto_payment=False,
                        discount_percent=discount_percent,
                        old_expired_at=old_expired_at,
                        new_expired_at=user_after_retry.expired_at,
                        lte_gb_total=int(
                            getattr(user_after_retry, "lte_gb_total", lte_gb) or 0
                        ),
                        method="balance",
                        tariff_kind=purchase_kind,
                    )
                    return {
                        "status": "success",
                        "message": "Оплачено с бонусного баланса",
                        "already_processed": True,
                        "provider": "balance",
                    }
                raise HTTPException(status_code=409, detail="Платёж уже обрабатывается")

        balance_debit_already_applied = recovered_balance_retry
        if (
            normalized_client_request_id is not None
            and not balance_debit_already_applied
        ):
            balance_debit_already_applied = await _was_balance_debit_applied(
                payment_id=payment_id,
                expected_amount=float(full_price),
            )

        net_debit = float(full_price)
        if net_debit > 0:
            if not balance_debit_already_applied:
                updated_rows = await Users.filter(
                    id=user.id, balance__gte=net_debit
                ).update(balance=F("balance") - net_debit)
                if updated_rows == 0:
                    if normalized_client_request_id is not None:
                        await _mark_payment_effect_failed(
                            payment_id=payment_id,
                            error="Insufficient balance during debit",
                        )
                    raise HTTPException(
                        status_code=409, detail="Недостаточно средств на балансе"
                    )
                if normalized_client_request_id is not None:
                    await _mark_balance_debit_applied(
                        payment_id=payment_id,
                        amount_from_balance=float(full_price),
                    )
            else:
                logger.info(
                    "Skipping debit for recovered balance retry payment_id=%s user_id=%s",
                    payment_id,
                    user.id,
                )

        if balance_lte_carryover_gb > 0:
            logger.info(
                "Переносим остаток LTE %s GB на новый тариф пользователя %s (balance flow)",
                balance_lte_carryover_gb,
                user.id,
            )

        try:
            user = await Users.get(id=user.id)

            current_date = date.today()
            additional_days = 0
            user_expired_at = normalize_date(user.expired_at)
            # --- NEW: перерасчёт остатка по старому тарифу ---
            if (
                (not is_family_overlay_purchase)
                and (not is_active_family_overlay)
                and user_expired_at
                and user_expired_at > current_date
                and user.active_tariff_id
            ):
                try:
                    active_tariff = await ActiveTariffs.get(id=user.active_tariff_id)
                    days_remaining = (user_expired_at - current_date).days
                    logger.info(
                        f"У пользователя {user.id} осталось {days_remaining} дней подписки"
                    )
                    old_months = int(active_tariff.months)
                    old_target_date = add_months_safe(current_date, old_months)
                    old_total_days = (old_target_date - current_date).days
                    unused_percent = (
                        days_remaining / old_total_days if old_total_days > 0 else 0
                    )
                    unused_value = unused_percent * active_tariff.price
                    logger.info(
                        f"Неиспользованная часть подписки пользователя {user.id}: "
                        f"{days_remaining}/{old_total_days} дней (стоимость: {unused_value:.2f} руб.)"
                    )
                    if discounted_price > 0:
                        # ИСПРАВЛЕННАЯ ЛОГИКА: рассчитываем через пропорцию от общей суммы
                        # Общая сумма = оплата базового тарифа + компенсация за старый тариф
                        total_amount = float(discounted_price) + unused_value

                        # Рассчитываем новый период подписки (стандартный для тарифа)
                        tariff_months = int(tariff.months)
                        new_target_date = add_months_safe(current_date, tariff_months)
                        new_total_days = (new_target_date - current_date).days

                        # Пропорция: x дней / общая_сумма = полный_период_тарифа / цена_тарифа
                        # x = общая_сумма * полный_период_тарифа / цена_тарифа
                        calculated_days = int(
                            total_amount * new_total_days / float(discounted_price)
                        )

                        logger.info(
                            f"ИСПРАВЛЕННЫЙ расчёт (баланс) для пользователя {user.id}: "
                            f"Заплачено: {float(discounted_price):.2f} руб + Компенсация: {unused_value:.2f} руб = "
                            f"Общая сумма: {total_amount:.2f} руб. "
                            f"Пропорция: {calculated_days} дней = {total_amount:.2f} * {new_total_days} / {float(discounted_price):.2f}"
                        )

                        # Устанавливаем рассчитанные дни как итоговые
                        additional_days = (
                            0  # Сбрасываем, так как используем calculated_days
                        )
                        days = calculated_days  # Переопределяем days
                except Exception as e:
                    logger.error(
                        f"Ошибка при расчете переноса подписки для {user.id}: {str(e)}"
                    )
                    additional_days = 0
            # При смене тарифа days уже рассчитано через пропорцию
            if "calculated_days" not in locals():
                # Обычная покупка без смены тарифа - days уже рассчитано выше
                logger.info(f"Стандартное количество дней подписки: {days}")
            else:
                # days уже рассчитано через пропорцию при смене тарифа
                logger.info(
                    f"Итоговое количество дней подписки (через пропорцию): {days}"
                )

            # Balance was debited atomically before entitlement updates.

            # При смене тарифа компенсация уже учтена в calculated_days.
            # Поэтому для base-ветки без overlay фиксируем дату от текущего дня.
            added_to_frozen_base = False
            if (
                purchase_kind == "base"
                and ("calculated_days" in locals())
                and (not is_active_family_overlay)
            ):
                added_to_frozen_base = (
                    await apply_base_purchase_to_frozen_base_if_active(
                        user,
                        purchased_days=days,
                        base_tariff_snapshot=base_tariff_snapshot,
                    )
                )
                if added_to_frozen_base:
                    logger.info(
                        "Base purchase during active family overlay (balance) moved to frozen base days for user %s",
                        user.id,
                    )
                else:
                    user.expired_at = current_date + timedelta(days=days)
                    logger.info(
                        f"Смена base-тарифа (баланс) для пользователя {user.id}: установлена дата {user.expired_at} "
                        f"({days} дней от текущей даты, рассчитано через пропорцию)"
                    )
            else:
                (
                    added_to_frozen_base,
                    family_expires_at,
                ) = await _apply_purchase_extension_by_kind(
                    user=user,
                    purchase_kind=purchase_kind,
                    purchased_days=days,
                    base_tariff_snapshot=base_tariff_snapshot,
                )
                if purchase_kind == "family":
                    logger.info(
                        "Family purchase (balance): overlay expiry updated for user %s to %s",
                        user.id,
                        family_expires_at,
                    )
                elif added_to_frozen_base:
                    logger.info(
                        "Base purchase during active family overlay (balance) moved to frozen base days for user %s",
                        user.id,
                    )
                else:
                    logger.info(
                        "Base purchase (balance) extended active subscription for user %s to %s",
                        user.id,
                        user.expired_at,
                    )

            preserve_active_tariff_state = (
                purchase_kind == "base" and added_to_frozen_base
            )

            # Если у пользователя был пробный период, сбрасываем флаг
            if user.is_trial:
                user.is_trial = False
                logger.info(
                    f"Сброшен флаг пробного периода для пользователя {user.id} после оплаты с баланса"
                )

            # --- NEW: Создаём/обновляем ActiveTariffs и лимит устройств ---
            if preserve_active_tariff_state:
                logger.info(
                    "Base purchase during active family overlay preserved entitlement snapshot for user %s",
                    user.id,
                )
            else:
                # Удаляем предыдущий активный тариф, если есть
                if user.active_tariff_id:
                    if old_active_tariff is None:
                        old_active_tariff = await ActiveTariffs.get_or_none(
                            id=user.active_tariff_id
                        )
                    if old_active_tariff:
                        logger.info(
                            f"Удаляем предыдущий активный тариф {user.active_tariff_id} пользователя {user.id}"
                        )
                        await old_active_tariff.delete()
                    else:
                        logger.warning(
                            f"Не найден активный тариф {user.active_tariff_id} для удаления у пользователя {user.id}"
                        )

                # код по сбросу HWID временно отключен
                # if user.remnawave_uuid:
                # await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)

                msk_today = datetime.now(MSK_TZ).date()
                usage_snapshot = None
                if user.remnawave_uuid:
                    usage_snapshot = await _fetch_today_lte_usage_gb(
                        str(user.remnawave_uuid)
                    )
                # Создаём новый активный тариф
                # ВАЖНО: сохраняем в price базовую стоимость тарифа без персональной скидки,
                # чтобы автоплатежи не применяли скидку дважды
                _, effective_multiplier = tariff.get_effective_pricing()
                base_calculated_price = tariff.calculate_price(device_count)
                effective_balance_lte_total = int(lte_gb or 0) + int(
                    balance_lte_carryover_gb or 0
                )
                active_tariff = await ActiveTariffs.create(
                    user=user,
                    name=tariff.name,
                    months=tariff.months,
                    price=base_calculated_price,  # Цена без персональной скидки
                    hwid_limit=device_count,  # Используем выбранное количество устройств
                    lte_gb_total=effective_balance_lte_total,
                    lte_gb_used=0.0,
                    lte_price_per_gb=lte_price_per_gb,
                    lte_usage_last_date=msk_today,
                    lte_usage_last_total_gb=usage_snapshot
                    if usage_snapshot is not None
                    else 0.0,
                    progressive_multiplier=effective_multiplier,
                    residual_day_fraction=0.0,
                )
                user.active_tariff_id = active_tariff.id
                user.lte_gb_total = effective_balance_lte_total

                # Устанавливаем hwid_limit пользователю из выбранного количества устройств
                user.hwid_limit = device_count
                logger.info(
                    f"При покупке с баланса установлен hwid_limit={device_count} для пользователя {user.id}"
                )

                # ВАЖНО: сохраняем active_tariff_id и hwid_limit в БД как можно раньше
                # чтобы минимизировать race condition с remnawave_updater
                try:
                    await user.save(update_fields=["active_tariff_id", "hwid_limit"])
                    logger.debug(
                        f"Ранее сохранены active_tariff_id={active_tariff.id} и hwid_limit={device_count} для пользователя {user.id}"
                    )
                except Exception as persist_exc:
                    logger.warning(
                        f"Не удалось рано сохранить active_tariff_id/hwid_limit для {user.id}: {persist_exc}"
                    )

            # Сохраняем ВСЕ изменения пользователя (баланс, дата, is_trial и т.д.)
            await user.save()
            await NotificationMarks.filter(user_id=user.id, type="lte_usage").delete()

            # После оплаты с баланса также обнуляем счётчик уменьшений
            if user.active_tariff_id:
                await ActiveTariffs.filter(id=user.active_tariff_id).update(
                    devices_decrease_count=0
                )

            # Синхронизируем лимит устройств и дату окончания с RemnaWave
            if (
                user.remnawave_uuid
                and not preserve_active_tariff_state
                and user.is_device_per_user_enabled()
            ):
                await _sync_device_per_user_after_payment(user, source="balance")
            if (
                user.remnawave_uuid
                and not preserve_active_tariff_state
                and not user.is_device_per_user_enabled()
            ):
                remnawave_client = None
                try:
                    remnawave_client = RemnaWaveClient(
                        remnawave_settings.url,
                        remnawave_settings.token.get_secret_value(),
                    )
                    await _await_payment_external_call(
                        remnawave_client.users.update_user(
                            uuid=user.remnawave_uuid,
                            expireAt=user.expired_at,
                            hwidDeviceLimit=device_count,
                        ),
                        operation="balance_remnawave_update_user",
                    )
                    logger.info(
                        f"Синхронизирован hwid_limit={device_count} и expireAt={user.expired_at} для пользователя {user.id} в RemnaWave"
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка при синхронизации hwid_limit/expireAt с RemnaWave для пользователя {user.id}: {e}"
                    )
                finally:
                    if remnawave_client:
                        try:
                            await remnawave_client.close()
                        except Exception as close_exc:
                            logger.warning(
                                f"Ошибка при закрытии клиента RemnaWave: {close_exc}"
                            )
                try:
                    effective_lte_total = (
                        user.lte_gb_total
                        if user.lte_gb_total is not None
                        else int(lte_gb or 0)
                    )
                    active_lte_used = (
                        float(active_tariff.lte_gb_used or 0)
                        if "active_tariff" in locals() and active_tariff is not None
                        else 0.0
                    )
                    should_enable_lte = float(effective_lte_total or 0) > active_lte_used
                    await _await_payment_external_call(
                        set_lte_squad_status(
                            str(user.remnawave_uuid), enable=should_enable_lte
                        ),
                        operation="balance_set_lte_squad_status",
                    )
                except Exception as e:
                    logger.error(f"Ошибка обновления LTE-сквада для {user.id}: {e}")
            elif user.remnawave_uuid and preserve_active_tariff_state:
                logger.info(
                    "Skipped RemnaWave hwid update for base purchase during active family overlay user=%s",
                    user.id,
                )

            await _mark_payment_effect_success(
                payment_id=payment_id,
                user_id=user.id,
                amount=full_price,  # Итоговая стоимость (с учетом скидки)
                amount_external=0,
                amount_from_balance=full_price,
                status="succeeded",
            )
        except Exception as balance_flow_exc:
            if normalized_client_request_id is not None:
                await _mark_payment_effect_failed(
                    payment_id=payment_id,
                    error=f"balance_flow_failed:{type(balance_flow_exc).__name__}:{balance_flow_exc}",
                )
            raise

        # Partner program: award cashback on "balance" payments too.
        try:
            await _award_partner_cashback(
                payment_id=str(payment_id),
                referral_user=user,
                amount_rub_total=int(_round_rub(float(full_price))),
            )
        except Exception:
            pass

        # Списываем одно использование скидки (если не постоянная)
        try:
            await consume_discount_if_needed(discount_id)
        except Exception as discount_exc:
            logger.warning(
                "Failed to consume discount after committed balance payment: user=%s payment_id=%s err=%s",
                user.id,
                payment_id,
                discount_exc,
            )

        await _replay_payment_notifications_if_needed(
            user=user,
            payment_id=payment_id,
            days=int(days),
            amount_external=0.0,
            amount_from_balance=float(full_price),
            device_count=int(device_count or 1),
            months=int(months),
            is_auto_payment=False,
            discount_percent=discount_percent,
            old_expired_at=old_expired_at,
            new_expired_at=user.expired_at,
            lte_gb_total=int(lte_gb),
            method="balance",
            tariff_kind=purchase_kind,
        )

        return {
            "status": "success",
            "message": "Оплачено с бонусного баланса",
            "provider": "balance",
        }

    else:
        # Логика частичной оплаты
        amount_to_pay = max(
            1.0, full_price - user_balance
        )  # Минимум 1 рубль для Yookassa
        amount_from_balance = full_price - amount_to_pay

        logger.info(
            f"Создание платежа для пользователя {user.id}. "
            f"Тариф: {tariff_id}, Полная цена: {full_price}, Баланс: {user_balance}, "
            f"К оплате: {amount_to_pay}, С баланса: {amount_from_balance}"
        )

        metadata = {
            "user_id": user.id,
            "month": months,
            "amount_from_balance": amount_from_balance,  # Добавляем сумму списания с баланса
            "tariff_id": tariff.id,
            "device_count": device_count,  # Добавляем количество устройств
            "tariff_kind": purchase_kind,
            "base_full_price": base_full_price,
            "discounted_price": discounted_price,
            "discount_percent": discount_percent,
            "discount_id": discount_id,
            "lte_gb": lte_gb,
            "lte_price_per_gb": lte_price_per_gb,
            "lte_cost": lte_cost,
            **quote.metadata_dict(),
        }
        if normalized_client_request_id is not None:
            metadata["client_request_id"] = normalized_client_request_id

        # Build return_url safely:
        # Always return to bot chat (not Mini App). Status is delivered by bot message.
        return_url = await _resolve_payment_return_url()
        metadata["expected_amount"] = float(amount_to_pay)
        metadata["expected_currency"] = PAYMENT_CURRENCY_RUB
        metadata["payment_provider"] = _active_payment_provider()

        if _active_payment_provider() == PAYMENT_PROVIDER_PLATEGA:
            if normalized_client_request_id is not None:
                existing_payment = (
                    await ProcessedPayments.filter(
                        provider=PAYMENT_PROVIDER_PLATEGA,
                        user_id=int(user.id),
                        client_request_id=normalized_client_request_id,
                    )
                    .order_by("-processed_at")
                    .first()
                )
                if existing_payment:
                    existing_status = str(
                        getattr(existing_payment, "status", "") or ""
                    ).lower()
                    existing_url = str(
                        getattr(existing_payment, "payment_url", "") or ""
                    ).strip()
                    if existing_status == "pending" and existing_url:
                        return {
                            "redirect_to": existing_url,
                            "payment_id": existing_payment.payment_id,
                            "provider": PAYMENT_PROVIDER_PLATEGA,
                        }
                    if existing_status == "succeeded" and bool(
                        getattr(existing_payment, "effect_applied", False)
                    ):
                        return {
                            "status": "success",
                            "message": "Платёж уже обработан",
                            "already_processed": True,
                            "payment_id": existing_payment.payment_id,
                            "provider": PAYMENT_PROVIDER_PLATEGA,
                        }
                    raise HTTPException(
                        status_code=409, detail="Платёж уже обрабатывается"
                    )

            platega_payload = _provider_payload_json({"metadata": metadata})
            try:
                platega_payment = await PlategaClient().create_transaction(
                    amount=float(amount_to_pay),
                    currency=PAYMENT_CURRENCY_RUB,
                    description=f"Оплата подписки пользователя {user.id} (Тариф: {tariff.name})",
                    return_url=return_url,
                    failed_url=return_url,
                    payload=platega_payload,
                )
            except PlategaConfigError:
                logger.error("Platega selected but credentials are not configured")
                raise HTTPException(
                    status_code=503,
                    detail="Сервис оплаты временно недоступен. Пожалуйста, попробуйте позже.",
                )
            except PlategaAPIError as platega_error:
                logger.error(
                    "Ошибка при создании платежа Platega",
                    extra={
                        "user_id": user.id,
                        "tariff_id": tariff_id,
                        "amount": amount_to_pay,
                        "status_code": platega_error.status_code,
                    },
                )
                raise HTTPException(
                    status_code=503,
                    detail="Сервис оплаты временно недоступен. Пожалуйста, попробуйте позже.",
                )

            await _upsert_processed_payment(
                payment_id=platega_payment.transaction_id,
                user_id=user.id,
                amount=float(amount_to_pay) + float(amount_from_balance),
                amount_external=float(amount_to_pay),
                amount_from_balance=float(amount_from_balance),
                status="pending",
                provider=PAYMENT_PROVIDER_PLATEGA,
                client_request_id=normalized_client_request_id,
                payment_url=platega_payment.redirect_url,
                provider_payload=_provider_payload_json(
                    {
                        "metadata": metadata,
                        "provider_status": platega_payment.status,
                        "provider_response": {
                            "transactionId": platega_payment.transaction_id,
                            "status": platega_payment.status,
                            "redirect": platega_payment.redirect_url,
                        },
                    }
                ),
            )
            return {
                "redirect_to": platega_payment.redirect_url,
                "payment_id": platega_payment.transaction_id,
                "provider": PAYMENT_PROVIDER_PLATEGA,
            }

        # Обернуть синхронный вызов YooKassa в async с таймаутом
        if not _configure_yookassa_if_available():
            logger.error(
                "YooKassa payment requested but provider credentials are not configured",
                extra={"user_id": user.id, "tariff_id": tariff_id},
            )
            raise HTTPException(
                status_code=503,
                detail="Сервис оплаты временно недоступен. Пожалуйста, попробуйте позже.",
            )
        try:
            payment_data = {
                "amount": {"value": str(amount_to_pay), "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    # Return URL after the payment is completed in YooKassa.
                    #
                    # Why:
                    # - If the payment is opened in an external browser, returning to the hosted Mini App URL
                    #   keeps the user in that browser (doesn't bring them back to Telegram).
                    # - A Telegram deep link (t.me/...) can reopen Telegram from the external browser.
                    #
                    # Priority:
                    # 1) TELEGRAM_PAYMENT_RETURN_URL, but only if it's a bot chat link
                    # 2) fallback to bot chat derived from TELEGRAM_WEBAPP_URL / bot username
                    "return_url": return_url,
                },
                "metadata": metadata,
                "capture": True,
                "description": f"Оплата подписки пользователя {user.id} (Тариф: {tariff.name})",
                "save_payment_method": True,
                "receipt": {
                    "customer": {"email": email},
                    "items": [
                        {
                            "description": f"Подписка пользователя {user.id} ({tariff.name})",
                            "quantity": "1",
                            "amount": {"value": str(amount_to_pay), "currency": "RUB"},
                            "vat_code": 1,  # TODO: Проверить НДС
                            "payment_subject": "service",
                            "payment_mode": "full_payment",
                        }
                    ],
                },
            }

            idempotence_key = normalized_client_request_id or str(
                randint(100000, 999999999999)
            )

            # Используем asyncio.to_thread для неблокирующего вызова с таймаутом
            payment = await asyncio.wait_for(
                asyncio.to_thread(
                    partial(Payment.create, payment_data, idempotence_key)
                ),
                timeout=30.0,
            )

        except asyncio.TimeoutError:
            logger.error(
                f"Таймаут при создании платежа YooKassa для пользователя {user.id}. "
                f"Тариф: {tariff_id}, Сумма: {amount_to_pay}",
                extra={
                    "user_id": user.id,
                    "tariff_id": tariff_id,
                    "amount": amount_to_pay,
                },
            )
            raise HTTPException(
                status_code=503,
                detail="Сервис оплаты временно недоступен. Пожалуйста, попробуйте позже.",
            )
        except (
            ConnectTimeoutError,
            ReadTimeoutError,
            RequestsConnectionError,
            RequestsTimeout,
        ) as network_err:
            logger.error(
                f"Сетевая ошибка при создании платежа YooKassa для пользователя {user.id}: {network_err}",
                extra={
                    "user_id": user.id,
                    "tariff_id": tariff_id,
                    "amount": amount_to_pay,
                    "error_type": type(network_err).__name__,
                },
            )
            raise HTTPException(
                status_code=503,
                detail="Сервис оплаты временно недоступен. Пожалуйста, попробуйте позже.",
            )
        except Exception as e:
            logger.error(
                f"Неожиданная ошибка при создании платежа YooKassa для пользователя {user.id}: {e}",
                extra={
                    "user_id": user.id,
                    "tariff_id": tariff_id,
                    "amount": amount_to_pay,
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Ошибка при создании платежа. Пожалуйста, попробуйте позже.",
            )

        # Резервации отключены

        # Persist as "pending" so the server can reconcile even if webhook delivery fails
        # (e.g. user device was offline after payment).
        try:
            if payment and payment.id:
                await _upsert_processed_payment(
                    payment_id=payment.id,
                    user_id=user.id,
                    amount=float(amount_to_pay) + float(amount_from_balance),
                    amount_external=float(amount_to_pay),
                    amount_from_balance=float(amount_from_balance),
                    status="pending",
                )
        except Exception as e:
            logger.warning(
                f"Не удалось сохранить pending payment {getattr(payment, 'id', None)}: {e}",
                extra={"user_id": user.id, "tariff_id": tariff_id},
            )

        return {
            "redirect_to": payment.confirmation.confirmation_url,
            "payment_id": payment.id,
            "provider": PAYMENT_PROVIDER_YOOKASSA,
        }


async def create_auto_payment(user: Users, disable_on_fail: bool = True) -> bool:
    """
    Создает автоматический платеж для продления подписки
    Returns:
        bool: True если платеж успешно создан, False в случае ошибки
    """
    if not _auto_renewal_uses_yookassa():
        logger.info(
            "Auto-renewal is disabled by PAYMENT_AUTO_RENEWAL_MODE; skipping user=%s",
            user.id,
        )
        return False

    # Вычисляем will_retry один раз для всех уведомлений об ошибках
    user_expired_at = normalize_date(user.expired_at)
    will_retry = (
        user_expired_at is not None and (user_expired_at - date.today()).days >= 0
    )

    try:
        # --- Modify auto-payment logic to use active_tariff_id ---
        if not user.active_tariff_id:
            logger.error(
                f"У пользователя {user.id} не установлен active_tariff_id. Автопродление невозможно."
            )
            await notify_auto_renewal_failure(
                user,
                reason="Отсутствует информация о последнем активном тарифе",
                will_retry=will_retry,
            )
            # Отключаем подписку, если нет активного тарифа
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            # Уведомляем админа об отключении автопродления из-за отсутствия тарифа
            await cancel_subscription(
                user, reason="Отсутствует информация о последнем активном тарифе"
            )
            return False

        # Получаем детали тарифа из ActiveTariffs
        active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if not active_tariff:
            logger.error(
                f"Не найден активный тариф с ID {user.active_tariff_id} для пользователя {user.id}",
                extra={"user_id": user.id, "active_tariff_id": user.active_tariff_id},
            )
            # Отключаем автопродление, если активный тариф не найден
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            logger.warning(
                f"Автопродление отключено для {user.id} из-за отсутствия активного тарифа ID={user.active_tariff_id} в базе."
            )
            await notify_auto_renewal_failure(
                user,
                reason=f"Не найден активный тариф (ID: {user.active_tariff_id}) для автопродления",
                will_retry=will_retry,
            )
            # Уведомляем админа об отключении автопродления из-за отсутствия тарифа в базе
            await cancel_subscription(
                user,
                reason=f"Не найден активный тариф (ID: {user.active_tariff_id}) в базе",
            )
            return False

        logger.info(
            f"Автопродление для пользователя {user.id}. Используется активный тариф ID={active_tariff.id} (Name: {active_tariff.name}, Price: {active_tariff.price})"
        )

        preview = await build_auto_payment_preview(user, active_tariff=active_tariff)
        if preview is None:
            logger.error(
                "Не удалось собрать preview автосписания для пользователя %s: отсутствует активный тариф",
                user.id,
            )
            await notify_auto_renewal_failure(
                user,
                reason="Не удалось подготовить расчет автопродления",
                will_retry=will_retry,
            )
            return False

        months = int(preview.months)
        base_full_price = int(preview.base_full_price)
        lte_gb_total = int(preview.lte_gb_total)
        lte_price_per_gb = float(preview.lte_price_per_gb)
        lte_cost = int(preview.lte_cost)
        discounted_price = float(preview.discounted_price)
        discount_id = preview.discount_id
        discount_percent = preview.discount_percent
        full_price = int(preview.total_amount)
        user_balance = float(user.balance or 0)

        try:
            current_date = date.today()
            target_date = add_months_safe(current_date, months)
            days = (target_date - current_date).days
        except Exception as e:
            logger.error(
                f"Ошибка при расчете дней подписки для автоплатежа {user.id}, тариф {active_tariff.id}: {e}",
                extra={
                    "user_id": user.id,
                    "tariff_id": active_tariff.id,
                    "months": months,
                },
            )
            # Уведомляем пользователя о неудаче (здесь маловероятно, но все же)
            await notify_auto_renewal_failure(
                user, reason="Ошибка при расчете срока продления", will_retry=will_retry
            )
            return False

        # Проверка полной оплаты с баланса
        if preview.amount_external <= 0:
            old_expired_at = user.expired_at
            logger.info(
                f"Автопродление тарифа {active_tariff.id} для пользователя {user.id} полностью с баланса. "
                f"Цена: {full_price}, Баланс: {user_balance}, LTE: {lte_gb_total} GB"
            )

            initial_balance = user.balance
            user.balance -= full_price
            payment_hwid_limit = int(preview.device_count)
            purchase_kind = await resolve_purchase_kind(
                metadata={
                    "month": months,
                    "device_count": payment_hwid_limit,
                },
                months=months,
                device_count=payment_hwid_limit,
            )
            base_tariff_snapshot = _build_active_base_tariff_snapshot(
                active_tariff=active_tariff,
                device_count=payment_hwid_limit,
                lte_gb=lte_gb_total,
                lte_price_per_gb=lte_price_per_gb,
            )
            (
                added_to_frozen_base,
                family_expires_at,
            ) = await _apply_purchase_extension_by_kind(
                user=user,
                purchase_kind=purchase_kind,
                purchased_days=days,
                base_tariff_snapshot=base_tariff_snapshot,
            )
            if purchase_kind == "family":
                logger.info(
                    "Auto-renew family purchase: updated overlay expiry user=%s family_expires_at=%s",
                    user.id,
                    family_expires_at,
                )
            elif added_to_frozen_base:
                logger.info(
                    "Auto-renew base purchase during active family overlay moved to frozen base days for user %s",
                    user.id,
                )
            else:
                logger.info(
                    "Auto-renew base purchase extended active subscription user=%s new_expired_at=%s",
                    user.id,
                    user.expired_at,
                )
            preserve_active_tariff_state = (
                purchase_kind == "base" and added_to_frozen_base
            )
            if not preserve_active_tariff_state:
                active_tariff.lte_gb_used = 0.0
                update_fields = ["lte_gb_used"]
                usage_snapshot = None
                msk_today = datetime.now(MSK_TZ).date()
                if user.remnawave_uuid:
                    try:
                        usage_snapshot = await _await_payment_external_call(
                            _fetch_today_lte_usage_gb(str(user.remnawave_uuid)),
                            operation="balance_auto_fetch_today_lte_usage_gb",
                        )
                    except asyncio.TimeoutError:
                        usage_snapshot = None
                if usage_snapshot is not None:
                    active_tariff.lte_usage_last_date = msk_today
                    active_tariff.lte_usage_last_total_gb = usage_snapshot
                    update_fields.extend(
                        ["lte_usage_last_date", "lte_usage_last_total_gb"]
                    )
                elif active_tariff.lte_usage_last_date != msk_today:
                    active_tariff.lte_usage_last_date = msk_today
                    active_tariff.lte_usage_last_total_gb = 0.0
                    update_fields.extend(
                        ["lte_usage_last_date", "lte_usage_last_total_gb"]
                    )
                await active_tariff.save(update_fields=update_fields)
                await NotificationMarks.filter(
                    user_id=user.id, type="lte_usage"
                ).delete()
                if user.remnawave_uuid:
                    try:
                        should_enable_lte = float(lte_gb_total or 0) > float(
                            active_tariff.lte_gb_used or 0
                        )
                        await _await_payment_external_call(
                            set_lte_squad_status(
                                str(user.remnawave_uuid), enable=should_enable_lte
                            ),
                            operation="balance_auto_set_lte_squad_status",
                        )
                    except Exception as e:
                        logger.error(
                            f"Ошибка обновления LTE-сквада при автопродлении для {user.id}: {e}"
                        )
            else:
                logger.info(
                    "Auto-renew base purchase during active family overlay preserved active tariff LTE state user=%s",
                    user.id,
                )
            # Сбрасываем триал, если был (маловероятно для автоплатежа, но на всякий случай)
            if user.is_trial:
                user.is_trial = False
                logger.info(
                    f"Сброшен флаг пробного периода для {user.id} при автооплате с баланса"
                )
            await user.save()

            payment_id = f"balance_auto_{user.id}_{int(datetime.now().timestamp())}_{randint(100, 999)}"

            await _upsert_processed_payment(
                payment_id=payment_id,
                user_id=user.id,
                amount=full_price,  # Итоговая стоимость с учетом скидки
                amount_external=0,
                amount_from_balance=full_price,
                status="succeeded",  # Статус как при обычной успешной оплате
            )

            # Partner program: award cashback on balance auto-pay too.
            try:
                await _award_partner_cashback(
                    payment_id=str(payment_id),
                    referral_user=user,
                    amount_rub_total=int(_round_rub(float(full_price))),
                )
            except Exception:
                pass

            logger.info(
                f"Автоплатеж для пользователя {user.id} успешно выполнен с баланса. "
                f"Списано: {full_price}. Баланс до: {initial_balance}, После: {user.balance}, Скидка: {discount_percent}% (id={discount_id})"
            )

            # Списываем использование скидки (если не постоянная)
            try:
                await consume_discount_if_needed(discount_id)
            except Exception as discount_exc:
                logger.warning(
                    "Failed to consume discount after committed auto-balance payment: user=%s payment_id=%s err=%s",
                    user.id,
                    payment_id,
                    discount_exc,
                )

            await _replay_payment_notifications_if_needed(
                user=user,
                payment_id=payment_id,
                days=int(days),
                amount_external=0.0,
                amount_from_balance=float(full_price),
                device_count=int(payment_hwid_limit),
                months=int(months),
                is_auto_payment=True,
                discount_percent=discount_percent,
                old_expired_at=old_expired_at,
                new_expired_at=user.expired_at,
                lte_gb_total=int(lte_gb_total),
                method="balance_auto",
                tariff_kind=purchase_kind,
            )

            # Начисление круток за автосписание с баланса: 1 крутка за каждый месяц
            attempts_before = int(getattr(user, "prize_wheel_attempts", 0) or 0)
            attempts_after = attempts_before
            try:
                if months and months > 0:
                    attempts_after = attempts_before + int(months)
                    user.prize_wheel_attempts = attempts_after
                    await user.save()
                    logger.info(
                        f"Начислено {months} круток за автосписание (баланс) пользователю {user.id}. Было: {attempts_before}, стало: {user.prize_wheel_attempts}"
                    )
            except Exception as award_exc:
                logger.error(
                    f"Не удалось начислить крутки за автосписание (баланс) для {user.id}: {award_exc}"
                )

            # Сообщение пользователю о начислении круток
            try:
                await notify_spin_awarded(
                    user=user,
                    added_attempts=int(months),
                    total_attempts=int(attempts_after),
                )
            except Exception as e_notify_spins:
                logger.error(
                    f"Ошибка уведомления о крутках (баланс) для {user.id}: {e_notify_spins}"
                )

            return True  # Автоплатеж успешен

        else:
            # Логика частичной оплаты
            amount_to_pay = float(preview.amount_external)
            amount_from_balance = float(preview.amount_from_balance)
            payment_hwid_limit = int(preview.device_count)
            metadata_tariffs = await Tariffs.filter(
                months=months,
                is_active=True,
            ).order_by("order")
            metadata_tariff = None
            if metadata_tariffs:
                active_name = (
                    str(getattr(active_tariff, "name", "") or "").strip().lower()
                )
                if active_name:
                    metadata_tariff = next(
                        (
                            candidate
                            for candidate in metadata_tariffs
                            if str(getattr(candidate, "name", "") or "").strip().lower()
                            == active_name
                        ),
                        None,
                    )
                if metadata_tariff is None:
                    metadata_tariff = metadata_tariffs[0]
            if metadata_tariff is not None:
                await metadata_tariff.sync_effective_pricing_fields()
            resolved_auto_tariff_id = (
                int(metadata_tariff.id) if metadata_tariff else None
            )
            auto_tariff_kind = await resolve_purchase_kind(
                metadata={
                    "month": months,
                    "device_count": payment_hwid_limit,
                },
                months=months,
                device_count=payment_hwid_limit,
                tariff_id=resolved_auto_tariff_id,
                tariff=metadata_tariff,
            )

            logger.info(
                f"Создание автоплатежа Yookassa для пользователя {user.id}. "
                f"Тариф: {active_tariff.id}, Полная цена: {full_price}, Баланс: {user_balance}, "
                f"К оплате: {amount_to_pay}, С баланса: {amount_from_balance}"
            )

            metadata = {
                "user_id": user.id,
                "month": months,
                "is_auto": True,
                "amount_from_balance": amount_from_balance,
                "device_count": payment_hwid_limit,
                "tariff_kind": auto_tariff_kind,
                "disable_on_fail": disable_on_fail,
                "base_full_price": base_full_price,
                "discounted_price": discounted_price,
                "discount_percent": discount_percent,
                "discount_id": discount_id,
                "lte_gb": lte_gb_total,
                "lte_price_per_gb": lte_price_per_gb,
                "lte_cost": lte_cost,
            }
            if resolved_auto_tariff_id is not None:
                metadata["tariff_id"] = int(resolved_auto_tariff_id)

            # Создаем автоплатеж Yookassa с таймаутом
            if not _configure_yookassa_if_available():
                logger.warning(
                    "YooKassa auto payment skipped because provider credentials are not configured",
                    extra={"user_id": user.id, "tariff_id": active_tariff.id},
                )
                await notify_auto_renewal_failure(
                    user,
                    reason="Сервис автопродления временно недоступен",
                    will_retry=will_retry,
                )
                return False
            try:
                payment_data = {
                    "amount": {"value": str(amount_to_pay), "currency": "RUB"},
                    "payment_method_id": user.renew_id,
                    "metadata": metadata,
                    "capture": True,
                    "description": f"Автопродление подписки пользователя {user.id} ({active_tariff.name})",
                    "receipt": {
                        "customer": {
                            "email": user.email if user.email else "auto@vectraconnect.app"
                        },
                        "items": [
                            {
                                "description": f"Автопродление подписки пользователя {user.id} ({active_tariff.name})",
                                "quantity": "1",
                                "amount": {
                                    "value": str(amount_to_pay),
                                    "currency": "RUB",
                                },
                                "vat_code": 1,  # TODO: Проверить НДС
                                "payment_subject": "service",
                                "payment_mode": "full_payment",
                            }
                        ],
                    },
                }

                idempotence_key = str(randint(100000, 999999999999))

                # Используем asyncio.to_thread для неблокирующего вызова с таймаутом
                payment = await asyncio.wait_for(
                    asyncio.to_thread(
                        partial(Payment.create, payment_data, idempotence_key)
                    ),
                    timeout=30.0,
                )

            except asyncio.TimeoutError:
                logger.error(
                    f"Таймаут при создании автоплатежа YooKassa для пользователя {user.id}. "
                    f"Тариф: {active_tariff.id}, Сумма: {amount_to_pay}",
                    extra={
                        "user_id": user.id,
                        "tariff_id": active_tariff.id,
                        "amount": amount_to_pay,
                    },
                )
                await notify_auto_renewal_failure(
                    user,
                    reason="Сервис оплаты временно недоступен (таймаут)",
                    will_retry=will_retry,
                )
                return False
            except (
                ConnectTimeoutError,
                ReadTimeoutError,
                RequestsConnectionError,
                RequestsTimeout,
            ) as network_err:
                logger.error(
                    f"Сетевая ошибка при создании автоплатежа YooKassa для пользователя {user.id}: {network_err}",
                    extra={
                        "user_id": user.id,
                        "tariff_id": active_tariff.id,
                        "amount": amount_to_pay,
                        "error_type": type(network_err).__name__,
                    },
                )
                await notify_auto_renewal_failure(
                    user,
                    reason="Сервис оплаты временно недоступен (ошибка сети)",
                    will_retry=will_retry,
                )
                return False
            except Exception as create_exc:
                logger.error(
                    f"Неожиданная ошибка при создании автоплатежа YooKassa для пользователя {user.id}: {create_exc}",
                    extra={
                        "user_id": user.id,
                        "tariff_id": active_tariff.id,
                        "amount": amount_to_pay,
                        "error_type": type(create_exc).__name__,
                    },
                    exc_info=True,
                )
                # Для непредвиденных ошибок пробрасываем дальше
                raise

            # Сбрасываем триал, если пользователь платит первый раз (даже автоплатежом)
            if user.is_trial:
                user.is_trial = False
                await user.save()
                logger.info(
                    f"Сброшен флаг пробного периода для {user.id} при создании автоплатежа Yookassa",
                    extra={"payment_id": payment.id, "user_id": user.id},
                )

            logger.info(
                f"Создан автоплатеж Yookassa для пользователя {user.id}",
                extra={
                    "payment_id": payment.id,
                    "user_id": user.id,
                    "amount": payment.amount.value,
                    "status": payment.status,
                },
            )
            # Persist as "pending" so the server can reconcile even if webhook delivery fails.
            try:
                if payment and payment.id:
                    await _upsert_processed_payment(
                        payment_id=payment.id,
                        user_id=user.id,
                        amount=float(amount_to_pay) + float(amount_from_balance),
                        amount_external=float(amount_to_pay),
                        amount_from_balance=float(amount_from_balance),
                        status="pending",
                    )
            except Exception as e:
                logger.warning(
                    f"Не удалось сохранить pending auto payment {getattr(payment, 'id', None)}: {e}",
                    extra={"user_id": user.id},
                )
            return True  # Автоплатеж создан (результат будет в вебхуке)

    except Exception as e:
        logger.error(
            f"Ошибка при создании автоплатежа для пользователя {user.id}: {e}",
            extra={"user_id": user.id},
        )
        # Отключаем автопродление только если это последняя попытка
        if disable_on_fail:
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            # Уведомляем админа об отключении автопродления из-за ошибки
            await cancel_subscription(
                user, reason=f"Ошибка при создании автоплатежа: {str(e)}"
            )
        logger.warning(
            f"Автопродление отключено для {user.id} из-за ошибки при создании автоплатежа: {e}"
        )
        await notify_auto_renewal_failure(
            user,
            reason=f"Внутренняя ошибка сервера при попытке автопродления",
            will_retry=will_retry,
        )
        return False
