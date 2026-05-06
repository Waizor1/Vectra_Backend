"""Revoke user access on chargeback / refund.

Without this revocation step, refunding a payment leaves the user with active
RemnaWave HWID slots and any family-owner allocations they were paying for, so
the user keeps full VPN access until the next reconcile pass. Both Platega and
YooKassa chargeback webhooks must call ``revoke_access_for_refund`` to bring
local DB, RemnaWave, and family allocations back to a no-paid-access state.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from bloobcat.db.family_members import FamilyMembers
from bloobcat.db.users import Users
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings

logger = logging.getLogger(__name__)


async def _zero_out_remnawave_hwid_limit(user: Users) -> None:
    if not user.remnawave_uuid:
        return
    client = RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )
    try:
        await client.users.update_user(user.remnawave_uuid, hwidDeviceLimit=0)
    except Exception as exc:  # noqa: BLE001 — best-effort; webhook must not 500
        logger.error(
            "remnawave_hwid_zero_failed user=%s uuid=%s err=%s",
            user.id,
            user.remnawave_uuid,
            exc,
        )
    finally:
        await client.close()


async def _cancel_owned_family_memberships(owner_id: int) -> int:
    affected = await FamilyMembers.filter(owner_id=owner_id, status="active").update(
        status="cancelled"
    )
    return int(affected or 0)


async def _detach_user_as_family_member(user: Users) -> bool:
    membership = await FamilyMembers.get_or_none(member_id=user.id, status="active")
    if not membership:
        return False
    membership.status = "cancelled"
    await membership.save(update_fields=["status", "updated_at"])
    return True


async def revoke_access_for_refund(
    user: Users,
    *,
    payment_id: str,
    reason: str = "chargeback",
) -> dict:
    """Bring `user` back to a no-paid-access state after a refund/chargeback.

    Idempotent: safe to call repeatedly for the same payment.

    Returns a small report dict for logging/observability.
    """

    yesterday = date.today() - timedelta(days=1)

    user.is_subscribed = False
    user.renew_id = None
    user.active_tariff_id = None
    if user.expired_at is None or user.expired_at > yesterday:
        user.expired_at = yesterday
    user.hwid_limit = 0
    await user.save(
        update_fields=[
            "is_subscribed",
            "renew_id",
            "active_tariff_id",
            "expired_at",
            "hwid_limit",
        ]
    )

    cancelled_owned = await _cancel_owned_family_memberships(int(user.id))
    detached_self = await _detach_user_as_family_member(user)

    await _zero_out_remnawave_hwid_limit(user)

    logger.info(
        "payment_refund_access_revoked user=%s payment=%s reason=%s "
        "cancelled_owned_memberships=%s detached_self_membership=%s",
        user.id,
        payment_id,
        reason,
        cancelled_owned,
        detached_self,
    )

    return {
        "user_id": int(user.id),
        "payment_id": str(payment_id),
        "reason": reason,
        "cancelled_owned_memberships": cancelled_owned,
        "detached_self_membership": bool(detached_self),
    }
