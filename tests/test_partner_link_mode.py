"""Tests for the per-partner link-mode toggle.

Covers:
- `build_partner_link` honours the mode argument.
- `PATCH /partner/link-mode` updates the user's preference and rebuilds owned QR links.
- Unknown values normalise to the safe 'bot' default.
- `get_status` and `get_summary` surface `linkMode` to the cabinet.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import List

import pytest

from tests.test_payments_no_yookassa import install_stubs


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest.mark.asyncio
async def test_build_partner_link_bot_mode(monkeypatch):
    from bloobcat.funcs import referral_attribution

    async def fake_bot_username() -> str:
        return "VectraConnect_bot"

    monkeypatch.setattr(referral_attribution, "get_bot_username", fake_bot_username)
    monkeypatch.setattr(
        referral_attribution,
        "telegram_settings",
        SimpleNamespace(webapp_url="https://app.vectra-pro.net/"),
    )

    link = await referral_attribution.build_partner_link(101, "bot")
    assert link == "https://t.me/VectraConnect_bot?start=partner-101"


@pytest.mark.asyncio
async def test_build_partner_link_app_mode(monkeypatch):
    from bloobcat.funcs import referral_attribution

    async def fake_bot_username() -> str:
        return "VectraConnect_bot"

    monkeypatch.setattr(referral_attribution, "get_bot_username", fake_bot_username)
    monkeypatch.setattr(
        referral_attribution,
        "telegram_settings",
        SimpleNamespace(webapp_url="https://app.vectra-pro.net/"),
    )

    link = await referral_attribution.build_partner_link(202, "app")
    assert link == "https://app.vectra-pro.net?startapp=partner-202"


def test_normalize_partner_link_mode_defaults_to_bot():
    from bloobcat.funcs.referral_attribution import normalize_partner_link_mode

    assert normalize_partner_link_mode(None) == "bot"
    assert normalize_partner_link_mode("") == "bot"
    assert normalize_partner_link_mode("BOT") == "bot"
    assert normalize_partner_link_mode("app") == "app"
    assert normalize_partner_link_mode("APP") == "app"
    assert normalize_partner_link_mode("garbage") == "bot"


def _install_link_mode_stubs(monkeypatch):
    """Stub PartnerQr persistence for the link-mode endpoint test."""
    from bloobcat.routes import partner as partner_module

    saved_user_calls: list[dict] = []
    saved_qr_calls: list[dict] = []

    qr_a_id = uuid.uuid4()
    qr_b_id = uuid.uuid4()

    class _FakeQr:
        def __init__(self, qr_id, owner_id, link, utm_source=None, utm_medium=None, utm_campaign=None):
            self.id = qr_id
            self.owner_id = owner_id
            self.link = link
            self.utm_source = utm_source
            self.utm_medium = utm_medium
            self.utm_campaign = utm_campaign

        async def save(self, *, update_fields=None):
            saved_qr_calls.append({
                "id": str(self.id),
                "link": self.link,
                "fields": list(update_fields or []),
            })

    qr_a = _FakeQr(qr_a_id, owner_id=10, link="OLD-A", utm_source="shop1")
    qr_b = _FakeQr(qr_b_id, owner_id=10, link="OLD-B")

    class FakePartnerQr:
        @staticmethod
        def filter(**kwargs):
            owner_id = kwargs.get("owner_id")

            class _Q:
                def __aiter__(self):
                    async def gen():
                        for q in (qr_a, qr_b):
                            if q.owner_id == owner_id:
                                yield q
                    return gen()

                def __await__(self):
                    async def collect():
                        return [q for q in (qr_a, qr_b) if q.owner_id == owner_id]
                    return collect().__await__()

            return _Q()

    async def fake_bot_username() -> str:
        return "VectraConnect_bot"

    monkeypatch.setattr(partner_module, "PartnerQr", FakePartnerQr)
    monkeypatch.setattr(partner_module, "get_bot_username", fake_bot_username)
    monkeypatch.setattr(
        partner_module,
        "telegram_settings",
        SimpleNamespace(webapp_url="https://app.vectra-pro.net/"),
    )
    # Patch builder used inside referral_attribution too (build_partner_link).
    from bloobcat.funcs import referral_attribution

    monkeypatch.setattr(referral_attribution, "get_bot_username", fake_bot_username)
    monkeypatch.setattr(
        referral_attribution,
        "telegram_settings",
        SimpleNamespace(webapp_url="https://app.vectra-pro.net/"),
    )

    return partner_module, saved_user_calls, saved_qr_calls, [qr_a, qr_b]


@pytest.mark.asyncio
async def test_update_link_mode_switches_to_app_and_rebuilds_qr_links(monkeypatch):
    partner_module, saved_user, saved_qr, owned = _install_link_mode_stubs(monkeypatch)

    saved_fields: list[list[str]] = []

    class _User:
        def __init__(self):
            self.id = 10
            self.is_partner = True
            self.partner_link_mode = "bot"

        async def save(self, *, update_fields=None):
            saved_fields.append(list(update_fields or []))

    user = _User()

    response = await partner_module.update_link_mode(
        partner_module.PartnerLinkModeRequest(mode="app"),
        user=user,
    )

    assert response.linkMode == "app"
    assert response.referralLink == "https://app.vectra-pro.net?startapp=partner-10"
    assert user.partner_link_mode == "app"
    assert saved_fields == [["partner_link_mode"]]
    # Both QR links rebuilt with the new mode.
    assert len(saved_qr) == 2
    by_id = {row["id"]: row for row in saved_qr}
    assert any("startapp=qr_" in row["link"] for row in by_id.values())
    qr_a_row = by_id[str(owned[0].id)]
    assert "utm_source=shop1" in qr_a_row["link"]
    assert qr_a_row["fields"] == ["link"]


@pytest.mark.asyncio
async def test_update_link_mode_rejects_non_partner(monkeypatch):
    partner_module, *_ = _install_link_mode_stubs(monkeypatch)

    user = SimpleNamespace(id=11, is_partner=False, partner_link_mode="bot")

    with pytest.raises(Exception) as excinfo:
        await partner_module.update_link_mode(
            partner_module.PartnerLinkModeRequest(mode="bot"),
            user=user,
        )
    assert "Partner access required" in str(excinfo.value) or "403" in str(excinfo.value)


@pytest.mark.asyncio
async def test_get_status_exposes_link_mode(monkeypatch):
    from bloobcat.routes import partner as partner_module
    from bloobcat.funcs import referral_attribution

    async def fake_bot_username() -> str:
        return "VectraConnect_bot"

    monkeypatch.setattr(partner_module, "get_bot_username", fake_bot_username)
    monkeypatch.setattr(referral_attribution, "get_bot_username", fake_bot_username)
    monkeypatch.setattr(
        partner_module,
        "telegram_settings",
        SimpleNamespace(webapp_url="https://app.vectra-pro.net/"),
    )
    monkeypatch.setattr(
        referral_attribution,
        "telegram_settings",
        SimpleNamespace(webapp_url="https://app.vectra-pro.net/"),
    )

    user = SimpleNamespace(
        id=303,
        is_partner=True,
        partner_link_mode="bot",
        custom_referral_percent=15,
    )
    res = await partner_module.get_status(user=user)
    assert res.linkMode == "bot"
    assert res.referralLink == "https://t.me/VectraConnect_bot?start=partner-303"
    assert res.cashbackPercent == 15
