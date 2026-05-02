from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_payments_no_yookassa import install_stubs


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest.mark.asyncio
async def test_partner_qr_create_builds_stable_link_before_persist(monkeypatch):
    from bloobcat.routes import partner as partner_module

    created_rows: list[dict] = []

    class FakePartnerQr:
        @staticmethod
        async def create(**kwargs):
            assert kwargs["link"], "QR link must be built before persisting"
            created_rows.append(kwargs)
            return SimpleNamespace(**kwargs)

    async def fake_bot_username() -> str:
        return "VectraConnect_bot"

    monkeypatch.setattr(partner_module, "PartnerQr", FakePartnerQr)
    monkeypatch.setattr(partner_module, "get_bot_username", fake_bot_username)
    monkeypatch.setattr(
        partner_module,
        "telegram_settings",
        SimpleNamespace(webapp_url="https://app.vectra-pro.net/"),
    )

    response = await partner_module.create_qr_code(
        partner_module.PartnerQrCreateRequest(title="  Salon QR  "),
        user=SimpleNamespace(id=101, is_partner=True),
    )

    assert len(created_rows) == 1
    created = created_rows[0]
    assert created["title"] == "Salon QR"
    assert created["slug"] == "salon_qr"
    assert created["link"] == response.link
    assert created["id"].hex in response.link
    assert response.link.startswith("https://app.vectra-pro.net?startapp=qr_")
