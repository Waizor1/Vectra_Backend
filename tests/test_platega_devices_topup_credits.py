"""Regression test: Platega webhook with devices_topup=True metadata must update
hwid_limit and mark payment succeeded, without crashing on missing 'month' field.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import AsyncMock

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


async def _seed_user_with_active_tariff(*, user_id: int, hwid_limit: int = 3):
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
        hwid_limit=hwid_limit,
    )
    active = await ActiveTariffs.create(
        user=user,
        name="Month",
        months=1,
        price=1000,
        hwid_limit=hwid_limit,
        lte_gb_total=0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active.id
    await user.save(update_fields=["active_tariff_id"])
    return user, active


@pytest.mark.asyncio
async def test_platega_devices_topup_credits_hwid_and_marks_succeeded(monkeypatch):
    """devices_topup=True without 'month' must update hwid_limit and succeed."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_route

    user_id = 6_001_001
    user, active = await _seed_user_with_active_tariff(user_id=user_id, hwid_limit=3)

    metadata = {
        "user_id": user_id,
        "devices_topup": True,
        "new_device_count": 5,
        "new_active_tariff_price": 1200,
        "amount_from_balance": 0,
        "expected_amount": 200.0,
        "expected_currency": "RUB",
        "payment_provider": "platega",
    }
    payment_id = "platega-dev-topup-01"
    await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=200.0,
        amount_external=200.0,
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
    notify_calls: list = []

    async def _fake_notify_active_tariff_change(*args, **kwargs):
        notify_calls.append(kwargs)

    monkeypatch.setattr(
        payment_route, "notify_active_tariff_change", _fake_notify_active_tariff_change
    )

    body = {
        "id": payment_id,
        "status": "CONFIRMED",
        "amount": 200.0,
        "currency": "RUB",
        "payload": json.dumps({"metadata": metadata}),
    }
    request = await _make_platega_request(
        {"X-MerchantId": "m1", "X-Secret": "s1"}, body
    )
    response = await payment_route.platega_webhook(request)
    assert response == {"status": "ok"}, f"Unexpected response: {response}"

    # hwid_limit updated
    active_after = await ActiveTariffs.get(id=active.id)
    assert int(active_after.hwid_limit or 0) == 5
    assert int(active_after.price or 0) == 1200

    user_after = await Users.get(id=user_id)
    assert int(user_after.hwid_limit or 0) == 5

    # Payment record succeeded
    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.status == "succeeded"
    assert row.effect_applied is True
    assert row.processing_state == "applied"

    # Admin notification fired
    assert len(notify_calls) == 1
    assert notify_calls[0]["new_limit"] == 5


@pytest.mark.asyncio
async def test_platega_devices_topup_idempotent(monkeypatch):
    """Duplicate webhook must not double-update hwid_limit."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.routes import payment as payment_route

    user_id = 6_001_002
    user, active = await _seed_user_with_active_tariff(user_id=user_id, hwid_limit=3)

    metadata = {
        "user_id": user_id,
        "devices_topup": True,
        "new_device_count": 5,
        "amount_from_balance": 0,
        "expected_amount": 200.0,
        "expected_currency": "RUB",
        "payment_provider": "platega",
    }
    payment_id = "platega-dev-idem-02"
    await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=200.0,
        amount_external=200.0,
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
    monkeypatch.setattr(payment_route, "notify_active_tariff_change", AsyncMock())

    body = {
        "id": payment_id,
        "status": "CONFIRMED",
        "amount": 200.0,
        "currency": "RUB",
        "payload": json.dumps({"metadata": metadata}),
    }
    req1 = await _make_platega_request({"X-MerchantId": "m1", "X-Secret": "s1"}, body)
    await payment_route.platega_webhook(req1)

    req2 = await _make_platega_request({"X-MerchantId": "m1", "X-Secret": "s1"}, body)
    await payment_route.platega_webhook(req2)

    active_after = await ActiveTariffs.get(id=active.id)
    assert int(active_after.hwid_limit or 0) == 5
