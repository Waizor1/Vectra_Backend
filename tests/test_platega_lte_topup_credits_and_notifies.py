"""Regression test: Platega webhook with lte_topup=True metadata must credit GB
and notify, without crashing on missing 'month' field.

Before fix 1.61.0: _apply_succeeded_payment_fallback read int(meta.get("month"))
before any lte_topup branch check → TypeError → _mark_payment_effect_failed
→ LTE never credited, Platega retried 80+ times.

After fix: _apply_confirmed_platega_payment routes lte_topup to _apply_lte_topup_effect
before calling _apply_succeeded_payment_fallback.
"""

from __future__ import annotations

import json
import types
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from starlette.requests import Request
from tortoise import Tortoise

from tests._payment_test_stubs import install_stubs


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": [
                        "bloobcat.db.users",
                        "bloobcat.db.tariff",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.family_members",
                        "bloobcat.db.payments",
                        "bloobcat.db.discounts",
                        "bloobcat.db.notifications",
                        "bloobcat.db.referral_rewards",
                        "bloobcat.db.subscription_freezes",
                        "bloobcat.db.segment_campaigns",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )
    from bloobcat.db.users import Users

    Users._meta.fk_fields.discard("active_tariff")
    users_active_tariff_fk = Users._meta.fields_map.get("active_tariff")
    if users_active_tariff_fk is not None:
        users_active_tariff_fk.reference = False
        users_active_tariff_fk.db_constraint = False

    from tortoise.backends.sqlite.schema_generator import SqliteSchemaGenerator

    client = Tortoise.get_connection("default")
    generator = SqliteSchemaGenerator(client)
    models_to_create = []
    try:
        maybe_models = generator._get_models_to_create(models_to_create)
        if maybe_models is not None:
            models_to_create = maybe_models
    except TypeError:
        models_to_create = generator._get_models_to_create()
    tables = [generator._get_table_sql(model, safe=True) for model in models_to_create]
    creation_sql = "\n".join(
        [t["table_creation_string"] for t in tables]
        + [m for t in tables for m in t["m2m_tables"]]
    )
    await generator.generate_from_string(creation_sql)
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _make_platega_request(headers: dict, body: dict) -> Request:
    raw_body = json.dumps(body).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/pay/webhook/platega",
            "headers": [
                (k.lower().encode("latin-1"), v.encode("latin-1"))
                for k, v in headers.items()
            ],
        },
        receive,
    )


async def _seed_user_with_active_tariff(*, user_id: int, lte_gb_total: int = 5):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users

    await Tariffs.create(
        id=user_id,
        name="Month",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
        is_active=True,
    )
    user = await Users.create(
        id=user_id,
        username=f"user{user_id}",
        full_name=f"User {user_id}",
        balance=0,
        is_registered=True,
        expired_at=date.today() + timedelta(days=30),
        hwid_limit=3,
        lte_gb_total=lte_gb_total,
    )
    active = await ActiveTariffs.create(
        user=user,
        name="Month",
        months=1,
        price=1000,
        hwid_limit=3,
        lte_gb_total=lte_gb_total,
        lte_gb_used=2.0,
        lte_price_per_gb=1.5,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active.id
    await user.save(update_fields=["active_tariff_id"])
    return user, active


@pytest.mark.asyncio
async def test_platega_lte_topup_credits_gb_and_marks_succeeded(monkeypatch):
    """Core regression: lte_topup=True without 'month' must succeed, not fail."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_route

    user_id = 5_039_001
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, lte_gb_total=5
    )

    metadata = {
        "user_id": user_id,
        "lte_topup": True,
        "lte_gb_delta": 5,
        "lte_price_per_gb": 1.5,
        "amount_from_balance": 0,
        "expected_amount": 7.5,
        "expected_currency": "RUB",
        "payment_provider": "platega",
    }
    payment_id = "platega-lte-regression-01"
    await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=7.5,
        amount_external=7.5,
        amount_from_balance=0,
        status="pending",
        provider_payload=json.dumps({"metadata": metadata}),
    )

    monkeypatch.setattr(payment_route.platega_settings, "merchant_id", "m1")
    monkeypatch.setattr(
        payment_route.platega_settings,
        "secret_key",
        type("S", (), {"get_secret_value": lambda self: "s1"})(),
    )
    admin_notify_calls: list = []

    async def _fake_notify_lte_topup(*args, **kwargs):
        admin_notify_calls.append(kwargs)

    user_notify_calls: list = []

    async def _fake_notify_lte_topup_user(*args, **kwargs):
        user_notify_calls.append(kwargs)

    monkeypatch.setattr(payment_route, "notify_lte_topup", _fake_notify_lte_topup)
    monkeypatch.setattr(payment_route, "notify_lte_topup_user", _fake_notify_lte_topup_user)

    body = {
        "id": payment_id,
        "status": "CONFIRMED",
        "amount": 7.5,
        "currency": "RUB",
        "payload": json.dumps({"metadata": metadata}),
    }
    request = await _make_platega_request(
        {"X-MerchantId": "m1", "X-Secret": "s1"}, body
    )
    response = await payment_route.platega_webhook(request)
    assert response == {"status": "ok"}, f"Unexpected response: {response}"

    # LTE credited
    active_after = await ActiveTariffs.get(id=active.id)
    assert int(active_after.lte_gb_total or 0) == 10, (
        f"Expected lte_gb_total=10, got {active_after.lte_gb_total}"
    )
    user_after = await Users.get(id=user_id)
    assert int(user_after.lte_gb_total or 0) == 10

    # Payment record succeeded
    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.status == "succeeded"
    assert row.effect_applied is True
    assert row.processing_state == "applied"

    # Admin notification fired
    assert len(admin_notify_calls) == 1
    # User notification fired
    assert len(user_notify_calls) == 1
    assert user_notify_calls[0]["lte_gb_delta"] == 5


@pytest.mark.asyncio
async def test_platega_lte_topup_idempotent_on_duplicate_webhook(monkeypatch):
    """Second webhook with the same payment_id must not double-credit."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.routes import payment as payment_route

    user_id = 5_039_002
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, lte_gb_total=5
    )
    metadata = {
        "user_id": user_id,
        "lte_topup": True,
        "lte_gb_delta": 5,
        "lte_price_per_gb": 1.5,
        "amount_from_balance": 0,
        "expected_amount": 7.5,
        "expected_currency": "RUB",
        "payment_provider": "platega",
    }
    payment_id = "platega-lte-idem-02"
    await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=7.5,
        amount_external=7.5,
        amount_from_balance=0,
        status="pending",
        provider_payload=json.dumps({"metadata": metadata}),
    )

    monkeypatch.setattr(payment_route.platega_settings, "merchant_id", "m1")
    monkeypatch.setattr(
        payment_route.platega_settings,
        "secret_key",
        type("S", (), {"get_secret_value": lambda self: "s1"})(),
    )
    monkeypatch.setattr(payment_route, "notify_lte_topup", AsyncMock())
    monkeypatch.setattr(payment_route, "notify_lte_topup_user", AsyncMock())

    body = {
        "id": payment_id,
        "status": "CONFIRMED",
        "amount": 7.5,
        "currency": "RUB",
        "payload": json.dumps({"metadata": metadata}),
    }

    # First call
    req1 = await _make_platega_request({"X-MerchantId": "m1", "X-Secret": "s1"}, body)
    await payment_route.platega_webhook(req1)

    # Second call (duplicate)
    req2 = await _make_platega_request({"X-MerchantId": "m1", "X-Secret": "s1"}, body)
    await payment_route.platega_webhook(req2)

    active_after = await ActiveTariffs.get(id=active.id)
    assert int(active_after.lte_gb_total or 0) == 10, (
        f"Double credit detected: lte_gb_total={active_after.lte_gb_total}"
    )
