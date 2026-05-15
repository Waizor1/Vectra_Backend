"""Trial Early-Bird discount — −50 % stimulus issued to new trial users.

Lifecycle:
    1. ``grant_trial_early_bird_discount`` is called from the standard trial
       grant branch in ``Users._ensure_remnawave_user`` right after
       ``_grant_trial_if_unclaimed`` succeeds. Gated by the
       ``TRIAL_EARLY_BIRD_ENABLED`` flag and skipped for partners and
       referral invitees (those have their own bundles). Creates a single
       ``PersonalDiscount`` row with ``source='trial_early_bird'``,
       ``percent=50`` (configurable), ``max_months=1``, ``remaining_uses=1``,
       and ``expires_at`` pinned to ``user.expired_at`` — the discount dies
       with the trial, no extra grace period.
    2. Idempotent: re-calling the helper for a user that already has a
       ``trial_early_bird`` row returns the existing row rather than creating
       a duplicate. Safe under retry / re-grant flows.
    3. ``get_trial_early_bird_state_payload`` returns a frontend-friendly
       payload (snake_case) used by the route adapter to render the banner
       countdown. ``active`` flips to ``False`` once the discount is used,
       expired, or absent.

Distinct from Reverse Trial: this is an *early-bird* — the discount is
available DURING the trial as a stimulus to convert. Reverse Trial gives a
discount AFTER the trial expires. The two features are independent and can
co-exist (though in practice Reverse Trial replaces the standard trial when
its env flag is on, so the early-bird never fires for reverse-trial users).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from bloobcat.db.discounts import PersonalDiscount
from bloobcat.db.users import Users
from bloobcat.settings import app_settings

logger = logging.getLogger(__name__)

TRIAL_EARLY_BIRD_DISCOUNT_SOURCE = "trial_early_bird"


async def grant_trial_early_bird_discount(
    user: Users, *, is_referral_invite: bool = False
) -> Optional[PersonalDiscount]:
    """Issue the early-bird −50 % discount to a fresh trial user. Idempotent.

    Returns the created (or pre-existing) ``PersonalDiscount`` on success;
    returns ``None`` if the feature is disabled, the user is a partner, the
    user is a referral invitee (richer bundle path), or the trial has no
    expiry date set.
    """
    if not app_settings.trial_early_bird_enabled:
        return None
    if is_referral_invite:
        return None
    if getattr(user, "is_partner", False):
        return None

    trial_expiry = getattr(user, "expired_at", None)
    if trial_expiry is None:
        # No trial expiry on the user row — nothing to anchor the discount to.
        return None

    existing = await PersonalDiscount.filter(
        user_id=user.id, source=TRIAL_EARLY_BIRD_DISCOUNT_SOURCE
    ).first()
    if existing is not None:
        return existing

    percent = max(1, min(100, int(app_settings.trial_early_bird_percent or 50)))

    discount = await PersonalDiscount.create(
        user_id=user.id,
        percent=percent,
        is_permanent=False,
        remaining_uses=1,
        expires_at=trial_expiry,
        source=TRIAL_EARLY_BIRD_DISCOUNT_SOURCE,
        metadata={"trial_expired_at": trial_expiry.isoformat()},
        min_months=None,
        max_months=1,
    )

    logger.info(
        "Trial early-bird discount granted: user=%s percent=%s expires_on=%s",
        user.id,
        percent,
        trial_expiry.isoformat(),
    )

    return discount


async def get_trial_early_bird_state_payload(user: Users) -> Dict[str, Any]:
    """Return a snake_case payload describing the user's early-bird discount.

    The route adapter converts this to camelCase. ``active=False`` covers all
    "show nothing" cases: no row, expired, or used (remaining_uses=0).
    """
    discount = await PersonalDiscount.filter(
        user_id=user.id, source=TRIAL_EARLY_BIRD_DISCOUNT_SOURCE
    ).first()

    if discount is None:
        return {
            "active": False,
            "percent": int(app_settings.trial_early_bird_percent or 0),
            "expires_at_ms": None,
            "used": False,
        }

    expires_at_ms: Optional[int] = None
    if discount.expires_at:
        expires_dt = datetime.combine(
            discount.expires_at, datetime.min.time(), tzinfo=timezone.utc
        )
        expires_at_ms = int(expires_dt.timestamp() * 1000)

    used = (not bool(discount.is_permanent)) and int(discount.remaining_uses or 0) <= 0
    expired = bool(discount.expires_at) and discount.expires_at < date.today()
    active = (not used) and (not expired) and int(discount.percent or 0) > 0

    return {
        "active": active,
        "percent": int(discount.percent or 0),
        "expires_at_ms": expires_at_ms,
        "used": used,
    }
