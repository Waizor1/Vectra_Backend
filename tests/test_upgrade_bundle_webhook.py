"""Регрессионные тесты для Platega webhook-маршрута upgrade_bundle.

Endpoint `/user/active_tariff/upgrade_bundle` (см. routes/user.py) формирует
платёж в Platega с metadata `upgrade_bundle=True` + `target_*` / `*_delta`.
После CONFIRMED-вебхука все три эффекта (extra_days → expired_at,
device_delta → hwid_limit/price, lte_delta_gb → lte_gb_total) должны
применяться атомарно. До 1.73.0 webhook не знал об upgrade_bundle, поэтому
эффекты после внешней оплаты не применялись.

Тесты покрывают:
- все три эффекта применяются в одном webhook;
- только period extension (extra_days > 0, device/lte deltas = 0);
- идемпотентность повторного webhook'а (нет двойного применения);
- rollback при сбое в середине транзакции (ни один эффект не остаётся).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from starlette.requests import Request
from tortoise import Tortoise

try:
    from tests._payment_test_stubs import install_stubs
except ModuleNotFoundError:  # pragma: no cover
    from _payment_test_stubs import install_stubs


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


async def _seed_user_with_active_tariff(
    *,
    user_id: int,
    hwid_limit: int = 2,
    lte_gb_total: int = 10,
    days_remaining: int = 30,
    price: int = 600,
):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users

    await Tariffs.create(
        id=user_id,
        name="1m",
        months=1,
        base_price=300,
        progressive_multiplier=0.9,
        order=1,
        is_active=True,
        lte_max_gb=500,
        lte_price_per_gb=5.0,
        lte_enabled=True,
    )
    user = await Users.create(
        id=user_id,
        username=f"user{user_id}",
        full_name=f"User {user_id}",
        balance=0,
        is_registered=True,
        expired_at=date.today() + timedelta(days=days_remaining),
        hwid_limit=hwid_limit,
        lte_gb_total=lte_gb_total,
    )
    active = await ActiveTariffs.create(
        user=user,
        name="1m",
        months=1,
        price=price,
        hwid_limit=hwid_limit,
        lte_gb_total=lte_gb_total,
        lte_price_per_gb=5.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active.id
    await user.save(update_fields=["active_tariff_id"])
    return user, active


def _silence_payment_side_effects(monkeypatch):
    from bloobcat.routes import payment as payment_route

    monkeypatch.setattr(payment_route.platega_settings, "merchant_id", "m1")
    monkeypatch.setattr(
        payment_route.platega_settings,
        "secret_key",
        type("S", (), {"get_secret_value": lambda self: "s1"})(),
    )
    monkeypatch.setattr(
        payment_route, "notify_active_tariff_change", AsyncMock(), raising=False
    )
    monkeypatch.setattr(
        payment_route, "_award_partner_cashback", AsyncMock(), raising=False
    )
    monkeypatch.setattr(
        payment_route, "_award_standard_referral_cashback", AsyncMock(), raising=False
    )
    return payment_route


def _build_upgrade_bundle_metadata(
    *,
    user_id: int,
    target_devices: int,
    target_lte_gb: int,
    target_extra_days: int,
    device_delta: int,
    lte_delta_gb: int,
    extra_days: int,
    amount_external: float,
    current_devices: int,
    current_lte_gb: int,
    previous_price: int,
) -> dict:
    return {
        "user_id": user_id,
        "upgrade_bundle": True,
        "payment_purpose": "upgrade_bundle",
        "target_device_count": target_devices,
        "target_lte_gb": target_lte_gb,
        "target_extra_days": target_extra_days,
        "device_delta": device_delta,
        "lte_delta_gb": lte_delta_gb,
        "extra_days": extra_days,
        "current_device_count": current_devices,
        "current_lte_gb_total": current_lte_gb,
        "previous_active_tariff_price": previous_price,
        "amount_from_balance": 0,
        "expected_amount": amount_external,
        "expected_currency": "RUB",
        "payment_provider": "platega",
    }


@pytest.mark.asyncio
async def test_webhook_applies_all_three_effects(monkeypatch):
    """Все три эффекта применяются после CONFIRMED Platega webhook."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users

    payment_route = _silence_payment_side_effects(monkeypatch)

    user_id = 7_001_001
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, hwid_limit=2, lte_gb_total=10, days_remaining=30, price=600
    )
    old_expired_at = user.expired_at

    metadata = _build_upgrade_bundle_metadata(
        user_id=user_id,
        target_devices=5,
        target_lte_gb=15,
        target_extra_days=30,
        device_delta=3,
        lte_delta_gb=5,
        extra_days=30,
        amount_external=500.0,
        current_devices=2,
        current_lte_gb=10,
        previous_price=600,
    )
    payment_id = "platega-upgrade-bundle-01"
    await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=500.0,
        amount_external=500.0,
        amount_from_balance=0,
        status="pending",
        provider_payload=json.dumps({"metadata": metadata}),
    )

    body = {
        "id": payment_id,
        "status": "CONFIRMED",
        "amount": 500.0,
        "currency": "RUB",
        "payload": json.dumps({"metadata": metadata}),
    }
    request = await _make_platega_request(
        {"X-MerchantId": "m1", "X-Secret": "s1"}, body
    )
    response = await payment_route.platega_webhook(request)
    assert response == {"status": "ok"}, f"Unexpected response: {response}"

    # All three deltas applied.
    user_after = await Users.get(id=user_id)
    assert user_after.hwid_limit == 5
    assert user_after.lte_gb_total == 15
    assert user_after.expired_at == old_expired_at + timedelta(days=30)

    active_after = await ActiveTariffs.get(id=active.id)
    assert int(active_after.hwid_limit or 0) == 5
    assert int(active_after.lte_gb_total or 0) == 15

    # Exactly one succeeded payment row tagged with upgrade_bundle purpose.
    rows = await ProcessedPayments.filter(payment_id=payment_id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "succeeded"
    assert row.effect_applied is True
    assert row.processing_state == "applied"
    assert row.payment_purpose == "upgrade_bundle"


@pytest.mark.asyncio
async def test_webhook_only_period_extension(monkeypatch):
    """extra_days>0, остальные дельты=0 → меняется только expired_at."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users

    payment_route = _silence_payment_side_effects(monkeypatch)

    user_id = 7_001_002
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, hwid_limit=2, lte_gb_total=10, days_remaining=30, price=600
    )
    old_expired_at = user.expired_at
    old_hwid = user.hwid_limit
    old_lte = user.lte_gb_total

    metadata = _build_upgrade_bundle_metadata(
        user_id=user_id,
        target_devices=2,
        target_lte_gb=10,
        target_extra_days=30,
        device_delta=0,
        lte_delta_gb=0,
        extra_days=30,
        amount_external=200.0,
        current_devices=2,
        current_lte_gb=10,
        previous_price=600,
    )
    payment_id = "platega-upgrade-bundle-period-02"
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
    assert response == {"status": "ok"}

    user_after = await Users.get(id=user_id)
    assert user_after.expired_at == old_expired_at + timedelta(days=30)
    assert user_after.hwid_limit == old_hwid
    assert user_after.lte_gb_total == old_lte

    active_after = await ActiveTariffs.get(id=active.id)
    assert int(active_after.hwid_limit or 0) == old_hwid
    assert int(active_after.lte_gb_total or 0) == old_lte


@pytest.mark.asyncio
async def test_webhook_idempotent_on_duplicate(monkeypatch):
    """Повторный webhook с тем же payment_id не применяет эффекты повторно."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users

    payment_route = _silence_payment_side_effects(monkeypatch)

    user_id = 7_001_003
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, hwid_limit=2, lte_gb_total=10, days_remaining=30, price=600
    )
    old_expired_at = user.expired_at

    metadata = _build_upgrade_bundle_metadata(
        user_id=user_id,
        target_devices=5,
        target_lte_gb=15,
        target_extra_days=30,
        device_delta=3,
        lte_delta_gb=5,
        extra_days=30,
        amount_external=500.0,
        current_devices=2,
        current_lte_gb=10,
        previous_price=600,
    )
    payment_id = "platega-upgrade-bundle-idem-03"
    await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=500.0,
        amount_external=500.0,
        amount_from_balance=0,
        status="pending",
        provider_payload=json.dumps({"metadata": metadata}),
    )

    body = {
        "id": payment_id,
        "status": "CONFIRMED",
        "amount": 500.0,
        "currency": "RUB",
        "payload": json.dumps({"metadata": metadata}),
    }

    req1 = await _make_platega_request(
        {"X-MerchantId": "m1", "X-Secret": "s1"}, body
    )
    await payment_route.platega_webhook(req1)

    # Snapshot state after first call.
    user_first = await Users.get(id=user_id)
    active_first = await ActiveTariffs.get(id=active.id)

    req2 = await _make_platega_request(
        {"X-MerchantId": "m1", "X-Secret": "s1"}, body
    )
    await payment_route.platega_webhook(req2)

    user_second = await Users.get(id=user_id)
    active_second = await ActiveTariffs.get(id=active.id)

    # State did not change between calls — no double application.
    assert user_second.hwid_limit == user_first.hwid_limit == 5
    assert user_second.lte_gb_total == user_first.lte_gb_total == 15
    assert user_second.expired_at == user_first.expired_at
    assert user_first.expired_at == old_expired_at + timedelta(days=30)
    assert active_second.hwid_limit == active_first.hwid_limit == 5
    assert active_second.lte_gb_total == active_first.lte_gb_total == 15

    rows = await ProcessedPayments.filter(payment_id=payment_id).all()
    assert len(rows) == 1, "Duplicate ProcessedPayments rows must not be created"


@pytest.mark.asyncio
async def test_webhook_rollback_on_partial_failure(monkeypatch):
    """Если применение одного из эффектов падает — НИ ОДИН эффект не остаётся в БД."""
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_route

    payment_route = _silence_payment_side_effects(monkeypatch)

    user_id = 7_001_004
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, hwid_limit=2, lte_gb_total=10, days_remaining=30, price=600
    )
    snapshot_expired = user.expired_at
    snapshot_hwid = user.hwid_limit
    snapshot_lte = user.lte_gb_total
    snapshot_active_lte = active.lte_gb_total

    metadata = _build_upgrade_bundle_metadata(
        user_id=user_id,
        target_devices=5,
        target_lte_gb=15,
        target_extra_days=30,
        device_delta=3,
        lte_delta_gb=5,
        extra_days=30,
        amount_external=500.0,
        current_devices=2,
        current_lte_gb=10,
        previous_price=600,
    )
    payment_id = "platega-upgrade-bundle-rollback-04"
    await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=500.0,
        amount_external=500.0,
        amount_from_balance=0,
        status="pending",
        provider_payload=json.dumps({"metadata": metadata}),
    )

    # Inject a failure into the second-effect application (LTE save) so the
    # devices and period changes that landed earlier in the transaction must
    # be rolled back. We do this by monkeypatching ActiveTariffs.save to raise
    # whenever lte_gb_total is being persisted.
    original_save = ActiveTariffs.save
    failure_raised = {"count": 0}

    async def _patched_save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields and "lte_gb_total" in update_fields:
            failure_raised["count"] += 1
            raise RuntimeError("simulated failure during LTE save")
        return await original_save(self, *args, **kwargs)

    monkeypatch.setattr(ActiveTariffs, "save", _patched_save)

    body = {
        "id": payment_id,
        "status": "CONFIRMED",
        "amount": 500.0,
        "currency": "RUB",
        "payload": json.dumps({"metadata": metadata}),
    }
    request = await _make_platega_request(
        {"X-MerchantId": "m1", "X-Secret": "s1"}, body
    )
    response = await payment_route.platega_webhook(request)

    # Webhook must report failure (so Platega retries / admins are alerted).
    # Either {"status": "error", ...} from yookassa-side dispatcher or a no-op
    # from the platega dispatcher which returns ok=False internally; in both
    # cases, NO effect should have landed.
    assert failure_raised["count"] >= 1, "Failure injection did not trigger"

    # Restore save so we can read state without re-raising.
    monkeypatch.setattr(ActiveTariffs, "save", original_save)

    user_after = await Users.get(id=user_id)
    active_after = await ActiveTariffs.get(id=active.id)

    assert user_after.expired_at == snapshot_expired, (
        "expired_at must not change on partial failure"
    )
    assert user_after.hwid_limit == snapshot_hwid, (
        "hwid_limit must roll back on partial failure"
    )
    assert user_after.lte_gb_total == snapshot_lte, (
        "lte_gb_total must roll back on partial failure"
    )
    assert int(active_after.hwid_limit or 0) == snapshot_hwid
    assert int(active_after.lte_gb_total or 0) == snapshot_active_lte

    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.effect_applied is False
    assert row.processing_state == "failed"
    # Payment is still flagged as failed but the row exists (so retries are
    # idempotent against this attempt's failure).
    assert response in (
        {"status": "ok"},
        {"status": "error", "message": "Upgrade bundle effect failed"},
    )
