from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_payments_no_yookassa import install_stubs


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


def _install_partner_module_stubs(monkeypatch):
    """Stub the heavy persistence layer so route handlers can run unit-style."""
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

    async def fake_earnings_by_qr(partner_id, qr_ids):
        return {}

    monkeypatch.setattr(partner_module, "PartnerQr", FakePartnerQr)
    monkeypatch.setattr(partner_module, "get_bot_username", fake_bot_username)
    monkeypatch.setattr(partner_module, "_earnings_by_qr", fake_earnings_by_qr)
    monkeypatch.setattr(
        partner_module,
        "telegram_settings",
        SimpleNamespace(webapp_url="https://app.vectra-pro.net/"),
    )
    return partner_module, created_rows


@pytest.mark.asyncio
async def test_partner_qr_create_builds_stable_link_before_persist(monkeypatch):
    partner_module, created_rows = _install_partner_module_stubs(monkeypatch)

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
    # No UTM provided => link must be plain (no `utm_*` query tail).
    assert "utm_source" not in response.link
    assert "utm_medium" not in response.link
    assert "utm_campaign" not in response.link
    assert created["utm_source"] is None
    assert created["utm_medium"] is None
    assert created["utm_campaign"] is None


@pytest.mark.asyncio
async def test_partner_qr_create_appends_sanitized_utm_query(monkeypatch):
    partner_module, created_rows = _install_partner_module_stubs(monkeypatch)

    response = await partner_module.create_qr_code(
        partner_module.PartnerQrCreateRequest(
            title="Salon",
            # Whitespace + cyrillic + symbols must be stripped to the analytics-safe subset.
            utmSource="  shop one  ",
            utmMedium="qr-code",
            utmCampaign="летний промо!",
        ),
        user=SimpleNamespace(id=42, is_partner=True),
    )

    assert len(created_rows) == 1
    created = created_rows[0]
    # `  shop one  ` -> "shopone" because spaces are stripped and not in the safe subset.
    assert created["utm_source"] == "shopone"
    assert created["utm_medium"] == "qr-code"
    # Cyrillic + punctuation has zero safe chars -> sanitizer returns None.
    assert created["utm_campaign"] is None
    # Link reflects the same shape: only the tags that survived sanitization are present.
    assert "utm_source=shopone" in response.link
    assert "utm_medium=qr-code" in response.link
    assert "utm_campaign" not in response.link


@pytest.mark.asyncio
async def test_partner_qr_create_drops_unsafe_utm_to_none(monkeypatch):
    partner_module, created_rows = _install_partner_module_stubs(monkeypatch)

    response = await partner_module.create_qr_code(
        partner_module.PartnerQrCreateRequest(
            title="Salon",
            utmSource="abc.def_ghi-1",
            utmMedium=" ! @ # ",  # nothing in the safe subset -> None
            utmCampaign="campaign01",
        ),
        user=SimpleNamespace(id=42, is_partner=True),
    )

    assert len(created_rows) == 1
    created = created_rows[0]
    assert created["utm_source"] == "abc.def_ghi-1"
    assert created["utm_medium"] is None
    assert created["utm_campaign"] == "campaign01"
    # Resulting link includes the surviving tags after the startapp payload.
    assert "startapp=qr_" in response.link
    assert "utm_source=abc.def_ghi-1" in response.link
    assert "utm_medium=" not in response.link  # None -> not appended
    assert "utm_campaign=campaign01" in response.link


def test_sanitize_utm_value_strips_unsafe_characters():
    from bloobcat.routes import partner as partner_module

    assert partner_module._sanitize_utm_value(None, max_length=64) is None
    assert partner_module._sanitize_utm_value("   ", max_length=64) is None
    assert partner_module._sanitize_utm_value("  salon-1  ", max_length=64) == "salon-1"
    assert partner_module._sanitize_utm_value("кириллица", max_length=64) is None
    long = "a" * 200
    assert partner_module._sanitize_utm_value(long, max_length=120) == "a" * 120


@pytest.mark.asyncio
async def test_build_qr_link_appends_utm_pairs_when_set(monkeypatch):
    from bloobcat.routes import partner as partner_module

    async def fake_bot_username() -> str:
        return "VectraConnect_bot"

    monkeypatch.setattr(partner_module, "get_bot_username", fake_bot_username)
    monkeypatch.setattr(
        partner_module,
        "telegram_settings",
        SimpleNamespace(webapp_url="https://app.vectra-pro.net/"),
    )

    plain = await partner_module._build_qr_link_from_payload("qr_abc")
    assert plain == "https://app.vectra-pro.net?startapp=qr_abc"

    rich = await partner_module._build_qr_link_from_payload(
        "qr_abc",
        utm_source="shop1",
        utm_medium="offline",
        utm_campaign="autumn",
    )
    assert rich.startswith("https://app.vectra-pro.net?startapp=qr_abc")
    assert "&utm_source=shop1" in rich
    assert "&utm_medium=offline" in rich
    assert "&utm_campaign=autumn" in rich
