"""Reverse Trial — 7-day full-feature trial for new users.

Lifecycle:
    1. ``grant_reverse_trial`` is called from ``Users._ensure_remnawave_user``
       on the first activation of a brand-new account. Gated by the
       ``REVERSE_TRIAL_ENABLED`` flag and skipped for partners and referral
       invitees (those have their own bundles). Creates a synthetic
       ``ActiveTariffs`` row with ``price=0`` and ``is_promo_synthetic=True``
       so the rest of the stack treats the user as a paying subscriber for
       device/LTE accounting purposes.
    2. ``downgrade_expired_reverse_trial`` is called by the daily 09:00 MSK
       scheduler for every active state with ``expires_at <= now()``. It
       wipes the synthetic ActiveTariffs row, resets the user back to free
       state with the regular trial LTE quota, issues a single-use
       ``PersonalDiscount`` of ``-50 %`` valid for 14 days (capped at 1
       month tariffs), and notifies the user.
    3. ``cancel_reverse_trial_on_paid_purchase`` is called from the payment
       webhook when a user buys a real tariff while their reverse trial is
       still active. The state is closed as ``converted_to_paid`` and the
       synthetic row is removed; no discount is issued because the user
       already paid full price.

All state mutations are idempotent: re-calling ``grant_reverse_trial`` for a
user that already has a state returns ``None``, ``downgrade`` is a no-op for
already-downgraded states, and ``redeem_reverse_trial_discount`` flips
``discount_used_at`` only on the first call.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from tortoise.transactions import in_transaction

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.discounts import PersonalDiscount
from bloobcat.db.reverse_trial import ReverseTrialState
from bloobcat.db.tariff import Tariffs
from bloobcat.db.users import Users
from bloobcat.services.trial_lte import read_trial_lte_limit_gb
from bloobcat.settings import app_settings

logger = logging.getLogger(__name__)

REVERSE_TRIAL_DISCOUNT_SOURCE = "reverse_trial"
REVERSE_TRIAL_TARIFF_NAME_FALLBACK = "Reverse Trial"


async def _resolve_top_tariff() -> Optional[Tariffs]:
    """Pick the tariff to grant for the reverse trial.

    Priority:
        1. ``app_settings.reverse_trial_tariff_sku`` matched against
           ``Tariffs.name`` (case-insensitive). This lets ops pin a specific
           SKU regardless of price ranking.
        2. Highest ``base_price`` among active tariffs. Ties broken by months
           (longer plan wins), then by ``order``.
    """
    sku = (app_settings.reverse_trial_tariff_sku or "").strip()
    if sku:
        candidate = (
            await Tariffs.filter(is_active=True, name__iexact=sku).order_by("-months").first()
        )
        if candidate:
            return candidate

    return (
        await Tariffs.filter(is_active=True)
        .order_by("-base_price", "-months", "order")
        .first()
    )


async def grant_reverse_trial(
    user: Users, *, is_referral_invite: bool = False
) -> Optional[ReverseTrialState]:
    """Grant a 7-day full-feature trial. Idempotent.

    Returns the created ``ReverseTrialState`` on success; returns ``None``
    if the feature is disabled, the user is a partner, the user is a
    referral invitee (those already get a richer 20-day bundle), or the
    user already has a reverse-trial state.
    """
    if not app_settings.reverse_trial_enabled:
        return None
    if is_referral_invite:
        return None
    if getattr(user, "is_partner", False):
        return None

    existing = await ReverseTrialState.filter(user_id=user.id).first()
    if existing is not None:
        return None

    top_tariff = await _resolve_top_tariff()

    days = max(1, int(app_settings.reverse_trial_days or 7))
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(days=days)

    months = int(top_tariff.months) if top_tariff is not None else 1
    hwid_limit = (
        int(top_tariff.devices_limit_default or 1) if top_tariff is not None else 1
    )
    if hwid_limit < 1:
        hwid_limit = 1
    lte_gb_total = (
        int(top_tariff.lte_max_gb or 0) if top_tariff is not None else 0
    )
    lte_price_per_gb = (
        float(top_tariff.lte_price_per_gb or 0.0) if top_tariff is not None else 0.0
    )
    progressive_multiplier = (
        float(top_tariff.get_effective_pricing()[1]) if top_tariff is not None else 0.9
    )
    tariff_name = (
        str(top_tariff.name) if top_tariff is not None else REVERSE_TRIAL_TARIFF_NAME_FALLBACK
    )

    async with in_transaction():
        synthetic = await ActiveTariffs.create(
            user=user,
            name=tariff_name,
            months=months,
            price=0,
            hwid_limit=hwid_limit,
            lte_gb_total=lte_gb_total,
            lte_gb_used=0.0,
            lte_price_per_gb=lte_price_per_gb,
            progressive_multiplier=progressive_multiplier,
            residual_day_fraction=0.0,
            is_promo_synthetic=True,
        )

        user.active_tariff_id = synthetic.id
        user.expired_at = expires_at.date()
        user.hwid_limit = hwid_limit
        user.lte_gb_total = lte_gb_total
        # NOT a regular trial: the standard `is_trial` flag stays False so the
        # legacy trial flows (extension, expiring notifications) do not fire.
        user.is_trial = False
        # `used_trial=True` blocks the standard trial-grant branch from
        # double-issuing a 10-day trial later.
        user.used_trial = True
        user.trial_started_at = now_utc
        await user.save()

        state = await ReverseTrialState.create(
            user=user,
            expires_at=expires_at,
            status="active",
            tariff_sku_snapshot=tariff_name[:64],
            tariff_active_id_snapshot=synthetic.id,
        )

    logger.info(
        "Reverse trial granted: user=%s tariff=%s expires_at=%s synthetic=%s",
        user.id,
        tariff_name,
        expires_at.isoformat(),
        synthetic.id,
    )

    try:
        from bloobcat.bot.notifications.reverse_trial.granted import (
            notify_reverse_trial_granted,
        )

        await notify_reverse_trial_granted(user, state)
    except Exception as exc:  # pragma: no cover - notification best-effort
        logger.warning(
            "Failed to deliver reverse-trial-granted notification user=%s: %s",
            user.id,
            exc,
        )

    return state


async def _delete_synthetic_active_tariff(state: ReverseTrialState, user: Users) -> None:
    """Remove the synthetic ActiveTariffs row created at grant time."""
    snapshot_id = state.tariff_active_id_snapshot
    if not snapshot_id:
        return
    if user.active_tariff_id != snapshot_id:
        return
    user.active_tariff_id = None
    await user.save(update_fields=["active_tariff_id"])
    try:
        await ActiveTariffs.filter(id=snapshot_id).delete()
    except Exception as exc:
        logger.warning(
            "Failed to delete synthetic ActiveTariffs %s for user=%s: %s",
            snapshot_id,
            user.id,
            exc,
        )


async def downgrade_expired_reverse_trial(state: ReverseTrialState) -> None:
    """Downgrade an expired reverse trial. Idempotent: returns early if the
    state has already been moved out of ``active``.
    """
    if state.status != "active":
        return

    user = await Users.get_or_none(id=state.user_id)
    if user is None:
        # Ghost row — user was deleted. Mark cancelled so the scheduler
        # stops touching it next pass.
        state.status = "cancelled"
        state.downgraded_at = datetime.now(timezone.utc)
        await state.save()
        return

    discount_percent = max(1, min(100, int(app_settings.reverse_trial_discount_percent or 50)))
    discount_ttl_days = max(1, int(app_settings.reverse_trial_discount_ttl_days or 14))
    discount_expires_on = date.today() + timedelta(days=discount_ttl_days)
    fallback_lte = int(round(await read_trial_lte_limit_gb()))

    async with in_transaction():
        await _delete_synthetic_active_tariff(state, user)

        # Reset to free state. `is_trial=True` keeps the legacy code paths
        # (LTE limiter, free-tier UX) consistent with a regular post-trial
        # account. `expired_at=None` signals "no active subscription".
        user.lte_gb_total = fallback_lte
        user.is_trial = True
        user.used_trial = True
        user.expired_at = None
        await user.save(
            update_fields=[
                "lte_gb_total",
                "is_trial",
                "used_trial",
                "expired_at",
            ]
        )

        discount = await PersonalDiscount.create(
            user_id=user.id,
            percent=discount_percent,
            is_permanent=False,
            remaining_uses=1,
            expires_at=discount_expires_on,
            source=REVERSE_TRIAL_DISCOUNT_SOURCE,
            metadata={"reverse_trial_state_id": int(state.id)},
            min_months=None,
            max_months=1,
        )

        state.status = "expired"
        state.discount_personal_id = int(discount.id)
        state.downgraded_at = datetime.now(timezone.utc)
        await state.save()

    logger.info(
        "Reverse trial downgraded: user=%s state=%s discount=%s expires_on=%s",
        user.id,
        state.id,
        discount.id,
        discount_expires_on.isoformat(),
    )

    try:
        from bloobcat.bot.notifications.reverse_trial.downgraded import (
            notify_reverse_trial_downgraded,
        )

        await notify_reverse_trial_downgraded(user, state, discount)
    except Exception as exc:  # pragma: no cover - notification best-effort
        logger.warning(
            "Failed to deliver reverse-trial-downgraded notification user=%s: %s",
            user.id,
            exc,
        )


async def cancel_reverse_trial_on_paid_purchase(user: Users) -> None:
    """Close an active reverse-trial state when the user makes a real paid
    purchase. No discount is issued — they already paid full price.

    Idempotent: returns silently if the user has no state or the state is
    already closed.
    """
    state = await ReverseTrialState.filter(user_id=user.id).first()
    if state is None or state.status != "active":
        return

    async with in_transaction():
        await _delete_synthetic_active_tariff(state, user)
        state.status = "converted_to_paid"
        state.downgraded_at = datetime.now(timezone.utc)
        await state.save()

    logger.info(
        "Reverse trial converted_to_paid: user=%s state=%s",
        user.id,
        state.id,
    )


async def get_reverse_trial_state_payload(user: Users) -> Dict[str, Any]:
    """Return a frontend-friendly payload describing the user's reverse
    trial state. Always returns a dict; ``status`` is ``"none"`` when the
    user has never received a reverse trial.
    """
    state = await ReverseTrialState.filter(user_id=user.id).first()
    if state is None:
        return {
            "status": "none",
            "granted_at_ms": None,
            "expires_at_ms": None,
            "days_remaining": 0,
            "tariff_name": None,
            "discount": {
                "available": False,
                "percent": int(app_settings.reverse_trial_discount_percent or 0),
                "expires_at_ms": None,
                "used": False,
            },
        }

    now_utc = datetime.now(timezone.utc)
    granted_ms = (
        int(state.granted_at.timestamp() * 1000) if state.granted_at else None
    )
    expires_ms = (
        int(state.expires_at.timestamp() * 1000) if state.expires_at else None
    )
    if state.status == "active" and state.expires_at:
        seconds_left = max(0, int((state.expires_at - now_utc).total_seconds()))
        days_remaining = (seconds_left + 86399) // 86400  # ceil so 5h left == 1d
    else:
        days_remaining = 0

    discount_available = False
    discount_percent = int(app_settings.reverse_trial_discount_percent or 0)
    discount_expires_ms: Optional[int] = None
    discount_used = state.discount_used_at is not None

    if state.discount_personal_id:
        discount = await PersonalDiscount.filter(id=state.discount_personal_id).first()
        if discount is not None:
            discount_percent = int(discount.percent or discount_percent)
            if discount.expires_at:
                # `expires_at` on PersonalDiscount is a `date`. Convert to
                # UTC midnight ms for FE consistency with the other fields.
                discount_expires_dt = datetime.combine(
                    discount.expires_at, datetime.min.time(), tzinfo=timezone.utc
                )
                discount_expires_ms = int(discount_expires_dt.timestamp() * 1000)
            still_valid = (
                discount.expires_at is None or discount.expires_at >= date.today()
            )
            has_uses = bool(discount.is_permanent) or int(discount.remaining_uses or 0) > 0
            discount_available = (not discount_used) and still_valid and has_uses

    return {
        "status": state.status,
        "granted_at_ms": granted_ms,
        "expires_at_ms": expires_ms,
        "days_remaining": int(days_remaining),
        "tariff_name": state.tariff_sku_snapshot,
        "discount": {
            "available": discount_available,
            "percent": discount_percent,
            "expires_at_ms": discount_expires_ms,
            "used": discount_used,
        },
    }


async def redeem_reverse_trial_discount(user: Users) -> Dict[str, Any]:
    """Idempotently mark the reverse-trial discount as redeemed.

    Returns ``{"applicable": False, ...}`` when there is nothing to redeem
    (no state, already used, expired, or not yet downgraded). Returns
    ``{"applicable": True, "discount_id": ..., "percent": ...}`` on the
    first successful call. Subsequent calls return the same payload with
    ``applicable=False``.
    """
    state = await ReverseTrialState.filter(user_id=user.id).first()
    if state is None or not state.discount_personal_id:
        return {"applicable": False, "discount_id": None, "percent": 0}

    discount = await PersonalDiscount.filter(id=state.discount_personal_id).first()
    if discount is None:
        return {"applicable": False, "discount_id": None, "percent": 0}

    already_used = state.discount_used_at is not None
    expired = bool(discount.expires_at) and discount.expires_at < date.today()
    out_of_uses = (not discount.is_permanent) and int(discount.remaining_uses or 0) <= 0

    if already_used or expired or out_of_uses:
        return {
            "applicable": False,
            "discount_id": int(discount.id),
            "percent": int(discount.percent or 0),
        }

    state.discount_used_at = datetime.now(timezone.utc)
    await state.save(update_fields=["discount_used_at"])
    # We deliberately do NOT consume the discount here — `consume_one` runs at
    # actual checkout. This endpoint just records the user's intent so the FE
    # can stop nagging with the downgrade modal.
    return {
        "applicable": True,
        "discount_id": int(discount.id),
        "percent": int(discount.percent or 0),
    }
