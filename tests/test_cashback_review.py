"""Regression tests for cashback fraud screening (H-2).

Pins the contract that:
  - shared HWID between referrer and referred triggers freeze
  - same telegram_id triggers freeze
  - clean cases stay 'active' so balance is credited as before
  - admin-card text contains the key signals so reviewers can decide
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class _FakeUser(SimpleNamespace):
    def __init__(self, **kwargs):
        kwargs.setdefault("id", 100)
        kwargs.setdefault("username", None)
        kwargs.setdefault("remnawave_uuid", None)
        super().__init__(**kwargs)


def test_should_freeze_cashback_for_hwid_overlap():
    from bloobcat.services.cashback_review import should_freeze_cashback

    assert should_freeze_cashback({"hwid_overlap": ["hwid-abc"], "same_telegram_id": False}) is True


def test_should_freeze_cashback_for_same_telegram_id():
    from bloobcat.services.cashback_review import should_freeze_cashback

    assert should_freeze_cashback({"hwid_overlap": [], "same_telegram_id": True}) is True


def test_should_not_freeze_cashback_when_clean():
    from bloobcat.services.cashback_review import should_freeze_cashback

    assert should_freeze_cashback({"hwid_overlap": [], "same_telegram_id": False}) is False


def test_should_not_freeze_for_empty_signals():
    from bloobcat.services.cashback_review import should_freeze_cashback

    assert should_freeze_cashback({}) is False


@pytest.mark.asyncio
async def test_detect_overlap_returns_shared_hwids():
    from bloobcat.services import cashback_review

    referrer = _FakeUser(id=1)
    referred = _FakeUser(id=2)

    async def fake_collect(user):
        if user.id == 1:
            return {"hwid-shared", "hwid-only-referrer"}
        if user.id == 2:
            return {"hwid-shared", "hwid-only-referred"}
        return set()

    with patch.object(cashback_review, "_collect_user_hwids", side_effect=fake_collect):
        signals = await cashback_review.detect_referral_overlap_signals(referrer, referred)

    assert signals["hwid_overlap"] == ["hwid-shared"]
    assert signals["same_telegram_id"] is False


@pytest.mark.asyncio
async def test_detect_overlap_flags_same_user_id_short_circuit():
    from bloobcat.services import cashback_review

    user = _FakeUser(id=42)

    with patch.object(
        cashback_review, "_collect_user_hwids", new=AsyncMock(return_value=set())
    ) as collect:
        signals = await cashback_review.detect_referral_overlap_signals(user, user)

    assert signals["same_telegram_id"] is True
    # Short-circuit: HWID collection must not run when the IDs already match.
    collect.assert_not_called()


@pytest.mark.asyncio
async def test_detect_overlap_returns_empty_when_no_shared_hwid():
    from bloobcat.services import cashback_review

    referrer = _FakeUser(id=1)
    referred = _FakeUser(id=2)

    async def fake_collect(user):
        if user.id == 1:
            return {"hwid-x"}
        return {"hwid-y"}

    with patch.object(cashback_review, "_collect_user_hwids", side_effect=fake_collect):
        signals = await cashback_review.detect_referral_overlap_signals(referrer, referred)

    assert signals["hwid_overlap"] == []
    assert signals["same_telegram_id"] is False


def test_admin_review_text_includes_key_signals():
    from bloobcat.services.cashback_review import build_admin_review_text

    referrer = _FakeUser(id=111, username="alice_partner")
    referred = _FakeUser(id=222, username="bob_payer")

    text = build_admin_review_text(
        earning_id="abc-earning",
        referrer=referrer,
        referred=referred,
        amount_total_rub=1500,
        reward_rub=300,
        percent=20,
        signals={"hwid_overlap": ["hwid-shared-1", "hwid-shared-2"], "same_telegram_id": False},
    )

    assert "1500" in text
    assert "300" in text
    assert "20%" in text
    assert "111" in text
    assert "222" in text
    assert "@alice_partner" in text
    assert "@bob_payer" in text
    assert "hwid-shared-1" in text
    assert "hwid-shared-2" in text
    assert "abc-earning" in text


def test_admin_review_text_handles_missing_username():
    from bloobcat.services.cashback_review import build_admin_review_text

    referrer = _FakeUser(id=111, username=None)
    referred = _FakeUser(id=222, username=None)

    text = build_admin_review_text(
        earning_id="x",
        referrer=referrer,
        referred=referred,
        amount_total_rub=1000,
        reward_rub=200,
        percent=20,
        signals={"hwid_overlap": [], "same_telegram_id": True},
    )

    assert "(нет username)" in text
    assert "ДА" in text  # same_telegram_id flag rendered
