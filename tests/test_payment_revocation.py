"""Regression tests for chargeback / refund access revocation (C-1).

Without this revocation step, refunding a payment leaves the user with active
RemnaWave HWID slots and family allocations — they keep full access until the
next batch reconcile pass. These tests pin the contract that revoke_access_for_refund
zeroes the user's entitlement state and detaches family memberships.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class _FakeUser(SimpleNamespace):
    """Mimics the slice of `Users` ORM that revoke_access_for_refund touches."""

    def __init__(self, **kwargs):
        kwargs.setdefault("id", 1001)
        kwargs.setdefault("is_subscribed", True)
        kwargs.setdefault("renew_id", "renew-abc")
        kwargs.setdefault("active_tariff_id", 42)
        kwargs.setdefault("expired_at", date.today() + timedelta(days=30))
        kwargs.setdefault("hwid_limit", 5)
        kwargs.setdefault("remnawave_uuid", "uuid-fake-1234")
        super().__init__(**kwargs)
        self._save_calls: list[list[str] | None] = []

    async def save(self, update_fields=None):
        self._save_calls.append(list(update_fields) if update_fields else None)


@pytest.mark.asyncio
async def test_revoke_access_clears_subscription_state_and_zeroes_hwid():
    from bloobcat.services import payment_revocation

    user = _FakeUser()

    with patch.object(
        payment_revocation, "_cancel_owned_family_memberships", new=AsyncMock(return_value=0)
    ), patch.object(
        payment_revocation, "_detach_user_as_family_member", new=AsyncMock(return_value=False)
    ), patch.object(
        payment_revocation, "_zero_out_remnawave_hwid_limit", new=AsyncMock()
    ) as zero_hwid:
        report = await payment_revocation.revoke_access_for_refund(
            user, payment_id="pay-123", reason="test"
        )

    assert user.is_subscribed is False
    assert user.renew_id is None
    assert user.active_tariff_id is None
    assert user.hwid_limit == 0
    assert user.expired_at <= date.today() - timedelta(days=1)
    zero_hwid.assert_awaited_once_with(user)
    assert report["user_id"] == user.id
    assert report["payment_id"] == "pay-123"


@pytest.mark.asyncio
async def test_revoke_access_cancels_owned_family_memberships():
    from bloobcat.services import payment_revocation

    user = _FakeUser(id=2002)

    with patch.object(
        payment_revocation,
        "_cancel_owned_family_memberships",
        new=AsyncMock(return_value=3),
    ) as cancel_owned, patch.object(
        payment_revocation, "_detach_user_as_family_member", new=AsyncMock(return_value=False)
    ), patch.object(
        payment_revocation, "_zero_out_remnawave_hwid_limit", new=AsyncMock()
    ):
        report = await payment_revocation.revoke_access_for_refund(
            user, payment_id="pay-fam", reason="test"
        )

    cancel_owned.assert_awaited_once_with(2002)
    assert report["cancelled_owned_memberships"] == 3


@pytest.mark.asyncio
async def test_revoke_access_detaches_self_when_user_is_a_family_member():
    from bloobcat.services import payment_revocation

    user = _FakeUser(id=3003)

    with patch.object(
        payment_revocation, "_cancel_owned_family_memberships", new=AsyncMock(return_value=0)
    ), patch.object(
        payment_revocation,
        "_detach_user_as_family_member",
        new=AsyncMock(return_value=True),
    ) as detach_self, patch.object(
        payment_revocation, "_zero_out_remnawave_hwid_limit", new=AsyncMock()
    ):
        report = await payment_revocation.revoke_access_for_refund(
            user, payment_id="pay-self", reason="test"
        )

    detach_self.assert_awaited_once_with(user)
    assert report["detached_self_membership"] is True


@pytest.mark.asyncio
async def test_revoke_access_idempotent_for_already_revoked_user():
    from bloobcat.services import payment_revocation

    user = _FakeUser(
        is_subscribed=False,
        renew_id=None,
        active_tariff_id=None,
        expired_at=date.today() - timedelta(days=10),
        hwid_limit=0,
        remnawave_uuid=None,
    )

    with patch.object(
        payment_revocation, "_cancel_owned_family_memberships", new=AsyncMock(return_value=0)
    ), patch.object(
        payment_revocation, "_detach_user_as_family_member", new=AsyncMock(return_value=False)
    ), patch.object(
        payment_revocation, "_zero_out_remnawave_hwid_limit", new=AsyncMock()
    ) as zero_hwid:
        report = await payment_revocation.revoke_access_for_refund(
            user, payment_id="pay-replay", reason="test"
        )

    # Existing already-past expired_at must not be moved forward to today-1.
    assert user.expired_at <= date.today() - timedelta(days=1)
    # Zeroing RemnaWave is a no-op when remnawave_uuid is None — still called for idempotency.
    zero_hwid.assert_awaited_once_with(user)
    assert report["cancelled_owned_memberships"] == 0
    assert report["detached_self_membership"] is False


@pytest.mark.asyncio
async def test_zero_out_remnawave_hwid_skips_when_no_uuid():
    from bloobcat.services import payment_revocation

    user = _FakeUser(remnawave_uuid=None)

    with patch.object(
        payment_revocation, "RemnaWaveClient"
    ) as client_factory:
        await payment_revocation._zero_out_remnawave_hwid_limit(user)

    client_factory.assert_not_called()
