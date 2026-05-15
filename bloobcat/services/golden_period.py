"""Golden Period — 24h invite blitz lifecycle.

This module owns three primitives:

    `get_active_golden_period_config`
        Returns the singleton GoldenPeriodConfig. Creates the seed row on first
        call if the migration's INSERT ... ON CONFLICT did not run (test envs,
        legacy DBs).

    `maybe_activate_golden_period`
        Idempotent activation. Skipped if the feature flag is off, the user is
        a partner, the user already has any GoldenPeriod row (one-shot
        lifetime), or the user has fewer than `eligibility_min_active_days`
        cumulative active VPN-session days.

    `attempt_optimistic_payout`
        Called from two integration sites — the payment success path and the
        `key_activated` flip site in the RemnaWave catcher. Idempotent via the
        UNIQUE(golden_period_id, referred_user_id) constraint, cap-protected
        via `paid_out_count__lt=F('cap')` filter on the UPDATE.

All notification dispatches are deferred to the bot/notifications/golden_period
package and wrapped in try/except so a notification failure does NOT roll back
the payout — the user has already been credited at that point.
"""

from __future__ import annotations

import asyncio
import logging
import time as time_mod
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from tortoise.expressions import F
from tortoise.transactions import in_transaction

from bloobcat.db.golden_period import (
    GoldenPeriod,
    GoldenPeriodConfig,
    GoldenPeriodPayout,
)
from bloobcat.db.users import Users
from bloobcat.settings import app_settings

if TYPE_CHECKING:  # type-only imports
    pass

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_SLUG = "default"

# In-process cache for cumulative active days. The query is bounded but still
# expensive on the `connections` hot table; a 60-min bucket cache cuts repeat
# work for the dispatcher scheduler that re-evaluates every user every hour.
_ACTIVE_DAYS_CACHE: dict[int, tuple[float, int]] = {}
_ACTIVE_DAYS_CACHE_LOCK = asyncio.Lock()
_ACTIVE_DAYS_CACHE_TTL_SECONDS = 60 * 60


async def get_active_golden_period_config() -> GoldenPeriodConfig:
    """Return the singleton config, creating it on first miss."""
    config = await GoldenPeriodConfig.filter(slug=DEFAULT_CONFIG_SLUG).first()
    if config is not None:
        return config
    # Create with the same defaults as the migration seed. A race between two
    # callers is fine — the UNIQUE on `slug` makes the second insert a no-op
    # and we re-fetch.
    try:
        config = await GoldenPeriodConfig.create(slug=DEFAULT_CONFIG_SLUG)
    except Exception:  # noqa: BLE001 - integrity error race
        config = await GoldenPeriodConfig.get(slug=DEFAULT_CONFIG_SLUG)
    return config


async def get_cumulative_active_days(
    user_id: int, *, lookback_days: Optional[int] = None
) -> int:
    """Distinct count of online days from `connections` over the lookback window.

    Cached for 60 minutes per user. Returns 0 on any error so the rest of the
    flow can keep running (fail-open: a missing signal must not block legit
    payouts and must not crash the dispatcher).
    """
    now_ts = time_mod.monotonic()
    # Cache check + cleanup outside the lock for speed; stale reads just skip
    # the cache.
    cached = _ACTIVE_DAYS_CACHE.get(int(user_id))
    if cached and now_ts - cached[0] < _ACTIVE_DAYS_CACHE_TTL_SECONDS:
        return cached[1]

    lookback = int(
        lookback_days
        if lookback_days is not None
        else app_settings.golden_period_active_days_lookback_days
    )
    if lookback <= 0:
        lookback = 90

    try:
        from bloobcat.db.connections import Connections  # noqa: WPS433

        cutoff = (
            datetime.now(timezone.utc).date() - timedelta(days=lookback)
        )
        # The `at` column is a DateField (one row per day per user), so a plain
        # COUNT over the filter window is the correct distinct-day count. The
        # UNIQUE(user_id, at) Meta constraint enforces that.
        days = await Connections.filter(
            user_id=int(user_id), at__gte=cutoff
        ).count()
    except Exception as exc:  # noqa: BLE001 - fail-open on any DB issue
        logger.debug(
            "golden_period_active_days_query_failed user=%s err=%s",
            user_id,
            exc,
        )
        days = 0

    async with _ACTIVE_DAYS_CACHE_LOCK:
        _ACTIVE_DAYS_CACHE[int(user_id)] = (now_ts, int(days))
    return int(days)


def invalidate_cumulative_active_days_cache(user_id: int | None = None) -> None:
    """Test helper / hook callable when a connection mutation should bust the cache."""
    if user_id is None:
        _ACTIVE_DAYS_CACHE.clear()
        return
    _ACTIVE_DAYS_CACHE.pop(int(user_id), None)


async def maybe_activate_golden_period(user: Users) -> Optional[GoldenPeriod]:
    """Activate Golden Period for a user if eligible. Idempotent.

    Returns the new GoldenPeriod on success, None otherwise. Skip reasons:
        * config.is_enabled is False
        * user.is_partner (partners have their own cashback funnel)
        * user already has ANY GoldenPeriod row (one-shot lifetime)
        * cumulative active days < eligibility_min_active_days
    """
    config = await get_active_golden_period_config()
    if not config.is_enabled:
        return None
    if getattr(user, "is_partner", False):
        return None

    existing = await GoldenPeriod.filter(user_id=int(user.id)).first()
    if existing is not None:
        return None

    active_days = await get_cumulative_active_days(int(user.id))
    if active_days < int(config.eligibility_min_active_days or 3):
        return None

    now_utc = datetime.now(timezone.utc)
    window_hours = max(1, int(config.window_hours or 24))
    expires_at = now_utc + timedelta(hours=window_hours)

    try:
        period = await GoldenPeriod.create(
            user_id=int(user.id),
            config_id=int(config.id),
            started_at=now_utc,
            expires_at=expires_at,
            cap=int(config.default_cap or 15),
            payout_amount_rub=int(config.payout_amount_rub or 100),
            triggered_by_active_days=int(active_days),
            status="active",
        )
    except Exception as exc:  # noqa: BLE001 - race on the partial UNIQUE
        logger.debug(
            "golden_period_activation_race user=%s err=%s", user.id, exc
        )
        return None

    logger.info(
        "Golden Period activated user=%s active_days=%s expires_at=%s",
        user.id,
        active_days,
        expires_at.isoformat(),
    )

    # Notify outside the create transaction — failures must not roll back.
    try:
        from bloobcat.bot.notifications.golden_period.activated import (
            notify_golden_period_activated,
        )

        await notify_golden_period_activated(user, period)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "golden_period_activation_notification_failed user=%s err=%s",
            user.id,
            exc,
        )

    return period


async def attempt_optimistic_payout(
    *, referrer: Users, referred: Users
) -> dict:
    """Credit +N₽ to the referrer's balance for an activated invitee.

    Pre-conditions:
        * Referrer has an active GoldenPeriod with expires_at > now and
          paid_out_count < cap.
        * Referred user has key_activated=True (the spec ties payout to the
          VPN-key flip, not the payment).
        * No prior payout exists for (period, referred_user) — UNIQUE
          enforces this regardless of concurrent callers.

    Returns a dict shaped like::

        {"applied": True, "payout_id": int, "amount_rub": int, "cap_reached": bool}
        {"applied": False, "reason": "feature_disabled" | "no_active_period"
                                       | "cap_reached" | "referred_not_activated"
                                       | "duplicate_referred" | "self_referral"
                                       | "error"}
    """
    if int(getattr(referrer, "id", 0) or 0) == int(
        getattr(referred, "id", 0) or 0
    ):
        return {"applied": False, "reason": "self_referral"}
    if not getattr(referred, "key_activated", False):
        return {"applied": False, "reason": "referred_not_activated"}

    config = await get_active_golden_period_config()
    if not config.is_enabled:
        return {"applied": False, "reason": "feature_disabled"}

    now_utc = datetime.now(timezone.utc)
    period = (
        await GoldenPeriod.filter(
            user_id=int(referrer.id),
            status="active",
            expires_at__gt=now_utc,
        )
        .order_by("-id")
        .first()
    )
    if period is None:
        return {"applied": False, "reason": "no_active_period"}

    if int(period.paid_out_count or 0) >= int(period.cap or 0):
        return {"applied": False, "reason": "cap_reached"}

    amount = int(period.payout_amount_rub or 100)
    cap_reached_now = False

    try:
        async with in_transaction():
            try:
                payout = await GoldenPeriodPayout.create(
                    golden_period_id=int(period.id),
                    referrer_user_id=int(referrer.id),
                    referred_user_id=int(referred.id),
                    amount_rub=amount,
                    status="optimistic",
                )
            except Exception as exc:  # noqa: BLE001 — UNIQUE violation
                logger.debug(
                    "golden_period_payout_duplicate referrer=%s referred=%s err=%s",
                    referrer.id,
                    referred.id,
                    exc,
                )
                return {"applied": False, "reason": "duplicate_referred"}

            # Cap-race protection: bump only if paid_out_count is still < cap.
            # If 0 rows updated, another concurrent payout filled the cap.
            updated = await GoldenPeriod.filter(
                id=int(period.id),
                paid_out_count__lt=F("cap"),
            ).update(
                paid_out_count=F("paid_out_count") + 1,
                total_paid_rub=F("total_paid_rub") + amount,
            )
            if not updated:
                # Roll back the payout we just created — UNIQUE will let a
                # later "real" attempt try again only after status moves off
                # active. In practice, the cap is reached, so the next caller
                # gets cap_reached.
                await payout.delete()
                return {"applied": False, "reason": "cap_reached"}

            # Refresh to know whether this UPDATE was the one that filled
            # the cap (for the "cap reached" notification).
            refreshed_period = await GoldenPeriod.get(id=int(period.id))
            cap_reached_now = (
                int(refreshed_period.paid_out_count or 0)
                >= int(refreshed_period.cap or 0)
            )

            # Credit balance atomically. If this fails the transaction rolls
            # back the payout + cap counter together.
            await Users.filter(id=int(referrer.id)).update(
                balance=F("balance") + amount,
            )
    except Exception as exc:  # noqa: BLE001 - any unforeseen failure
        logger.warning(
            "golden_period_payout_failed referrer=%s referred=%s err=%s",
            referrer.id,
            referred.id,
            exc,
        )
        return {"applied": False, "reason": "error", "error": str(exc)}

    logger.info(
        "Golden Period payout applied referrer=%s referred=%s amount=%s cap_reached=%s",
        referrer.id,
        referred.id,
        amount,
        cap_reached_now,
    )

    # Out-of-transaction notifications.
    try:
        from bloobcat.bot.notifications.golden_period.payout import (
            notify_golden_period_payout,
        )

        await notify_golden_period_payout(referrer, period, payout)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "golden_period_payout_notification_failed referrer=%s err=%s",
            referrer.id,
            exc,
        )

    if cap_reached_now:
        try:
            from bloobcat.bot.notifications.golden_period.cap_reached import (
                notify_golden_period_cap_reached,
            )

            await notify_golden_period_cap_reached(referrer, period)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "golden_period_cap_reached_notification_failed referrer=%s err=%s",
                referrer.id,
                exc,
            )

    return {
        "applied": True,
        "payout_id": int(payout.id),
        "amount_rub": amount,
        "cap_reached": cap_reached_now,
    }


def _anonymize_handle(name: str | None, username: str | None) -> str:
    """Privacy-preserving display name for the FE invitees list."""
    if username:
        u = str(username).lstrip("@")
        if len(u) <= 4:
            return f"@{u[0]}***"
        return f"@{u[0]}***{u[-3:]}"
    if name:
        n = str(name)
        if len(n) <= 4:
            return f"{n[0]}***"
        return f"{n[0]}***{n[-3:]}"
    return "***"


def _to_ms(value: datetime | None) -> Optional[int]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


async def build_golden_period_status(user: Users) -> dict | None:
    """Build the FE payload exposed via /referrals/status -> goldenPeriod field.

    Returns None if the feature is off or the user has no active period.
    """
    config = await get_active_golden_period_config()
    if not config.is_enabled:
        return None

    now_utc = datetime.now(timezone.utc)
    period = (
        await GoldenPeriod.filter(
            user_id=int(user.id),
            status="active",
            expires_at__gt=now_utc,
        )
        .order_by("-id")
        .first()
    )
    if period is None:
        return None

    payouts = await GoldenPeriodPayout.filter(
        golden_period_id=int(period.id)
    ).order_by("-paid_at")

    invitees: list[dict] = []
    for p in payouts:
        # Lookup referred user once for display fields. Fail-open on missing.
        try:
            referred = await Users.get_or_none(id=int(p.referred_user_id))
        except Exception:  # noqa: BLE001
            referred = None
        invitees.append(
            {
                "id": str(p.id),
                "displayName": _anonymize_handle(
                    getattr(referred, "full_name", None),
                    getattr(referred, "username", None),
                ),
                "status": (
                    "paid"
                    if p.status == "optimistic" or p.status == "confirmed"
                    else "clawed_back"
                ),
                "paidAtMs": _to_ms(p.paid_at),
                "clawbackReason": p.clawback_reason,
            }
        )

    return {
        "active": True,
        "startedAtMs": _to_ms(period.started_at),
        "expiresAtMs": _to_ms(period.expires_at),
        "cap": int(period.cap or 0),
        "paidOutCount": int(period.paid_out_count or 0),
        "totalPaidRub": int(period.total_paid_rub or 0),
        "payoutAmount": int(period.payout_amount_rub or 100),
        "seen": period.seen_at is not None,
        "invitees": invitees,
    }


async def mark_period_seen(user: Users) -> bool:
    """Idempotent: set seen_at=NOW on the user's active period. Returns True if updated."""
    now_utc = datetime.now(timezone.utc)
    updated = await GoldenPeriod.filter(
        user_id=int(user.id),
        status="active",
        seen_at__isnull=True,
    ).update(seen_at=now_utc)
    return bool(updated)


async def list_user_payouts(user: Users, *, limit: int = 20) -> list[dict]:
    """Return the user's recent Golden Period payouts (history endpoint)."""
    rows = (
        await GoldenPeriodPayout.filter(referrer_user_id=int(user.id))
        .order_by("-paid_at")
        .limit(max(1, min(int(limit or 20), 100)))
    )
    out: list[dict] = []
    for r in rows:
        try:
            referred = await Users.get_or_none(id=int(r.referred_user_id))
        except Exception:  # noqa: BLE001
            referred = None
        out.append(
            {
                "id": int(r.id),
                "displayName": _anonymize_handle(
                    getattr(referred, "full_name", None),
                    getattr(referred, "username", None),
                ),
                "amountRub": int(r.amount_rub or 0),
                "status": str(r.status),
                "paidAtMs": _to_ms(r.paid_at),
                "clawedBackAtMs": _to_ms(r.clawed_back_at),
                "clawbackReason": r.clawback_reason,
            }
        )
    return out
