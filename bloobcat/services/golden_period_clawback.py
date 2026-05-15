"""Golden Period clawback — reverse a suspect optimistic payout.

Triggered by the 6h scanner (`bloobcat/tasks/golden_period_clawback.py`) when
abuse signals from `detect_golden_overlap_signals` cross the
`should_clawback` threshold within `clawback_window_days`. Reversal order:

    1. Deduct as much as possible from the referrer's balance.
    2. Convert any remainder to days subtracted from the referrer's active
       paid tariff. math.ceil rounds against the user (favors the service).
    3. Proportional LTE GB reduction: `lte_gb_total *= (days_removed /
       total_days)`. Only `lte_gb_total` is reduced — `lte_gb_used` is
       untouched, so an over-quota user is handled by the existing
       LTE-limiter task on its next pass.

A floor of `today + (trial_days // 2)` protects `expired_at` from being
pushed below today — disconnecting a user mid-clawback would create much
worse UX than partially recovering the bonus.

All clawback fields are persisted on the GoldenPeriodPayout row for audit
(reason, payload snapshot, balance_rub, days_removed, lte_gb_removed) and
the parent GoldenPeriod's `paid_out_count` / `total_paid_rub` are
decremented atomically so the cap accounting stays correct.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from tortoise.expressions import F
from tortoise.transactions import in_transaction

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.golden_period import GoldenPeriod, GoldenPeriodPayout
from bloobcat.db.users import Users
from bloobcat.settings import app_settings

logger = logging.getLogger(__name__)


def _expiration_floor() -> date:
    """Earliest date `expired_at` is allowed to settle on after clawback."""
    half_trial = max(0, int(getattr(app_settings, "trial_days", 10) or 10) // 2)
    return date.today() + timedelta(days=half_trial)


def _compute_tariff_clawback(
    *, remainder_rub: int, active: ActiveTariffs | None, current_expired_at: date | None
) -> tuple[int, Decimal, date | None]:
    """Translate `remainder_rub` to (days_removed, lte_gb_removed, new_expired_at).

    Pure function so tests can pin the math without DB. `active=None` means
    "no paid tariff to bite into", returns (0, 0, current).
    """
    if remainder_rub <= 0 or active is None:
        return 0, Decimal("0"), current_expired_at

    months = int(getattr(active, "months", 0) or 0)
    price = int(getattr(active, "price", 0) or 0)
    if months <= 0 or price <= 0:
        return 0, Decimal("0"), current_expired_at

    total_days = months * 30
    if total_days <= 0:
        return 0, Decimal("0"), current_expired_at

    price_per_day = Decimal(price) / Decimal(total_days)
    if price_per_day <= 0:
        return 0, Decimal("0"), current_expired_at

    days_removed_raw = Decimal(remainder_rub) / price_per_day
    # Round against the user (math.ceil) so we don't under-recover.
    days_removed = int(math.ceil(float(days_removed_raw)))
    if days_removed <= 0:
        return 0, Decimal("0"), current_expired_at

    floor = _expiration_floor()
    if current_expired_at is None:
        new_expired = floor
    else:
        candidate = current_expired_at - timedelta(days=days_removed)
        new_expired = max(floor, candidate)
    actual_days_removed = (
        (current_expired_at - new_expired).days
        if current_expired_at is not None
        else 0
    )
    if actual_days_removed < 0:
        actual_days_removed = 0

    lte_total = getattr(active, "lte_gb_total", None)
    lte_gb_removed = Decimal("0")
    if lte_total and total_days > 0 and actual_days_removed > 0:
        ratio = Decimal(actual_days_removed) / Decimal(total_days)
        # Round to 2 dp against the user (ROUND_UP semantics via ceiling).
        raw = Decimal(int(lte_total)) * ratio
        lte_gb_removed = (raw * Decimal(100)).to_integral_value(
            rounding="ROUND_UP"
        ) / Decimal(100)

    return actual_days_removed, lte_gb_removed, new_expired


async def clawback_payout(
    payout: GoldenPeriodPayout,
    signals: dict[str, Any],
) -> bool:
    """Reverse an optimistic payout. Returns True if the payout was clawed back.

    No-op (returns False) if the payout is already non-optimistic. Safe to
    call repeatedly — the status check is the idempotency key.
    """
    if str(payout.status) != "optimistic":
        return False

    referrer = await Users.get_or_none(id=int(payout.referrer_user_id))
    if referrer is None:
        # Referrer was deleted between payout and clawback. Nothing to do.
        logger.warning(
            "golden_clawback_referrer_missing payout=%s referrer=%s",
            payout.id,
            payout.referrer_user_id,
        )
        return False

    amount = int(payout.amount_rub or 0)
    current_balance = int(getattr(referrer, "balance", 0) or 0)
    deducted_balance = min(current_balance, amount)
    remainder = amount - deducted_balance

    active: ActiveTariffs | None = None
    if remainder > 0:
        active_id = getattr(referrer, "active_tariff_id", None)
        if active_id:
            active = await ActiveTariffs.get_or_none(id=active_id)

    days_removed, lte_gb_removed, new_expired = _compute_tariff_clawback(
        remainder_rub=remainder,
        active=active,
        current_expired_at=getattr(referrer, "expired_at", None),
    )

    primary_reason = str(signals.get("primary_reason") or "unknown")
    payload_snapshot = signals.get("snapshot") or signals
    now_utc = datetime.now(timezone.utc)

    try:
        async with in_transaction():
            # Deduct from balance. Use F() so we don't fight a concurrent
            # balance mutation. Floor at zero just in case (cannot go negative).
            if deducted_balance > 0:
                await Users.filter(id=int(referrer.id)).update(
                    balance=F("balance") - int(deducted_balance),
                )

            # Apply tariff clawback if any. We update ActiveTariffs row for
            # LTE_total reduction and the user's expired_at.
            if days_removed > 0 and active is not None:
                user_updates: dict[str, Any] = {"expired_at": new_expired}
                await Users.filter(id=int(referrer.id)).update(**user_updates)

                if lte_gb_removed > 0:
                    new_total = max(
                        0,
                        int(getattr(active, "lte_gb_total", 0) or 0)
                        - int(lte_gb_removed),
                    )
                    await ActiveTariffs.filter(id=str(active.id)).update(
                        lte_gb_total=new_total
                    )

            # Mark the payout clawed back with full audit fields.
            await GoldenPeriodPayout.filter(id=int(payout.id)).update(
                status="clawed_back",
                clawed_back_at=now_utc,
                clawback_reason=primary_reason,
                clawback_payload=payload_snapshot,
                clawback_balance_rub=int(deducted_balance),
                clawback_days_removed=int(days_removed) if days_removed else None,
                clawback_lte_gb_removed=(
                    lte_gb_removed if lte_gb_removed > 0 else None
                ),
            )

            # Decrement the parent period counters. The accounting must stay
            # correct so the cap is re-armed for legitimate invitees.
            await GoldenPeriod.filter(id=int(payout.golden_period_id)).update(
                paid_out_count=F("paid_out_count") - 1,
                total_paid_rub=F("total_paid_rub") - int(amount),
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "golden_clawback_transaction_failed payout=%s err=%s",
            payout.id,
            exc,
        )
        return False

    logger.warning(
        "Golden Period clawback applied payout=%s referrer=%s amount=%s "
        "balance_rub=%s days=%s lte_gb=%s reason=%s",
        payout.id,
        referrer.id,
        amount,
        deducted_balance,
        days_removed,
        lte_gb_removed,
        primary_reason,
    )

    # Notify the user about the clawback (warning) — outside the transaction
    # so notification failures do not roll back the deduction.
    try:
        from bloobcat.bot.notifications.golden_period.clawback import (
            notify_golden_period_clawback,
        )

        period = await GoldenPeriod.get(id=int(payout.golden_period_id))
        # Re-fetch the payout so the notification sees the persisted
        # clawback fields (status, clawed_back_at, clawback_*).
        refreshed = await GoldenPeriodPayout.get(id=int(payout.id))
        breakdown = {
            "amount": amount,
            "balance_rub": int(deducted_balance),
            "days_removed": int(days_removed),
            "lte_gb_removed": float(lte_gb_removed),
            "reason": primary_reason,
        }
        await notify_golden_period_clawback(
            referrer, period, refreshed, breakdown
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "golden_clawback_notification_failed payout=%s err=%s",
            payout.id,
            exc,
        )

    return True


async def confirm_payouts_past_clawback_window() -> int:
    """Move optimistic payouts past the clawback window into 'confirmed' state.

    Called by the scanner so older rows fall out of the candidate set and
    no longer waste signal-collection cycles. Returns count flipped.
    """
    from bloobcat.services.golden_period import (
        get_active_golden_period_config,
    )

    config = await get_active_golden_period_config()
    window_days = max(1, int(config.clawback_window_days or 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    now_utc = datetime.now(timezone.utc)
    updated = await GoldenPeriodPayout.filter(
        status="optimistic",
        paid_at__lt=cutoff,
    ).update(status="confirmed", confirmed_at=now_utc)
    if updated:
        logger.info(
            "Golden Period: confirmed %s payouts past clawback window", updated
        )
    return int(updated)


async def reinstate_payout(payout_id: int) -> bool:
    """Admin override: undo a clawback, re-credit the referrer.

    Restores the payout status to 'optimistic', re-credits the balance, and
    bumps the parent period counters. Tariff days/LTE clawed back are NOT
    restored — admin should manually adjust the active tariff if needed
    (the clawback row keeps the audit fields).
    """
    payout = await GoldenPeriodPayout.get_or_none(id=int(payout_id))
    if payout is None:
        return False
    if str(payout.status) != "clawed_back":
        return False

    amount = int(payout.amount_rub or 0)
    refund_balance = int(payout.clawback_balance_rub or 0)

    try:
        async with in_transaction():
            if refund_balance > 0:
                await Users.filter(id=int(payout.referrer_user_id)).update(
                    balance=F("balance") + int(refund_balance),
                )

            await GoldenPeriodPayout.filter(id=int(payout.id)).update(
                status="optimistic",
                clawed_back_at=None,
                clawback_reason=None,
                clawback_payload=None,
                clawback_balance_rub=None,
                clawback_days_removed=None,
                clawback_lte_gb_removed=None,
            )

            await GoldenPeriod.filter(id=int(payout.golden_period_id)).update(
                paid_out_count=F("paid_out_count") + 1,
                total_paid_rub=F("total_paid_rub") + int(amount),
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "golden_reinstate_failed payout=%s err=%s", payout.id, exc
        )
        return False

    logger.info(
        "Golden Period: payout %s reinstated by admin (refund_balance=%s)",
        payout.id,
        refund_balance,
    )
    return True
