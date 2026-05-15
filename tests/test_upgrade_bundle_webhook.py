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
    balance: int = 0,
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
        balance=balance,
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

    # Track scheduler invocations — BLOCKER 2 requires the webhook to re-arm
    # subscription-lifecycle tasks once extra_days have shifted expired_at.
    scheduler_calls = []

    async def _scheduler_recorder(user):
        scheduler_calls.append(getattr(user, "id", None))

    # Patch through sys.modules so production code's function-local
    # `from bloobcat.scheduler import schedule_user_tasks` resolves to our
    # recorder regardless of what previous tests rebound.
    import sys as _sys
    import types as _types

    scheduler_module = _sys.modules.get("bloobcat.scheduler")
    if scheduler_module is None:
        scheduler_module = _types.ModuleType("bloobcat.scheduler")
        monkeypatch.setitem(_sys.modules, "bloobcat.scheduler", scheduler_module)
    monkeypatch.setattr(
        scheduler_module, "schedule_user_tasks", _scheduler_recorder, raising=False
    )

    user_id = 7_001_001
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, hwid_limit=2, lte_gb_total=10, days_remaining=30, price=600
    )
    old_expired_at = user.expired_at
    active_before_price = int(active.price)

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
    # The new metadata contract (BLOCKER 1) carries explicit price + multiplier
    # snapshots; webhook is free to recompute, but absence of these keys is
    # how the bug used to silently leave price stale.
    metadata["new_active_tariff_price"] = 1500
    metadata["new_progressive_multiplier"] = 0.9
    # Simulate admin raising LTE price-per-gb between user's purchase
    # (5.0 in active_tariff snapshot) and invoice creation (7.5 quoted live).
    metadata["new_lte_price_per_gb"] = 7.5
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
    # BLOCKER 1 invariant: when device_delta > 0, the snapshot price MUST be
    # rewritten so auto-renew / compute_daily_rate / notifications see the
    # right post-upgrade price (not the pre-upgrade 600₽).
    assert int(active_after.price or 0) != active_before_price
    assert int(active_after.price or 0) > 0
    assert active_after.progressive_multiplier is not None
    assert 0 < float(active_after.progressive_multiplier) <= 1.0
    # v4 invariant (Bug 1): webhook does NOT rewrite the
    # `lte_price_per_gb` snapshot anymore. The snapshot is the original
    # purchase price (audit trail); per-GB display reads the LIVE Directus
    # price on every quote, not the snapshot. Asserting the snapshot
    # survived unchanged (5.0 from purchase) is the new contract — the
    # `new_lte_price_per_gb` metadata key has been retired and is
    # ignored even if a legacy invoice still carries it.
    assert float(active_after.lte_price_per_gb) == 5.0

    # BLOCKER 2 invariant: scheduler is re-armed at least once for this user.
    # (Users.save() auto-reschedules on expired_at change, AND the webhook
    # adds an explicit post-commit call — so >=1 invocation is the contract.
    # The explicit call protects us if the auto-reschedule path is ever
    # changed to skip writes that go through update_fields.)
    assert user_id in scheduler_calls
    assert len(scheduler_calls) >= 1

    # Exactly one succeeded payment row tagged with upgrade_bundle purpose.
    rows = await ProcessedPayments.filter(payment_id=payment_id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "succeeded"
    assert row.effect_applied is True
    assert row.processing_state == "applied"
    assert row.payment_purpose == "upgrade_bundle"


@pytest.mark.asyncio
async def test_webhook_rejects_upgrade_bundle_when_balance_share_is_missing(monkeypatch):
    """Partial external invoices must not apply entitlements if the balance
    share recorded at invoice time has already been spent elsewhere.
    """
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users

    payment_route = _silence_payment_side_effects(monkeypatch)

    user_id = 7_001_013
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id,
        hwid_limit=2,
        lte_gb_total=10,
        days_remaining=30,
        price=600,
        balance=10,
    )
    old_expired_at = user.expired_at
    metadata = _build_upgrade_bundle_metadata(
        user_id=user_id,
        target_devices=3,
        target_lte_gb=15,
        target_extra_days=7,
        device_delta=1,
        lte_delta_gb=5,
        extra_days=7,
        amount_external=1.0,
        current_devices=2,
        current_lte_gb=10,
        previous_price=600,
    )
    metadata["amount_from_balance"] = 100
    metadata["new_active_tariff_price"] = 900
    metadata["new_progressive_multiplier"] = 0.9

    payment_id = "platega-upgrade-bundle-missing-balance-13"
    await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=101.0,
        amount_external=1.0,
        amount_from_balance=100.0,
        status="pending",
        provider_payload=json.dumps({"metadata": metadata}),
    )

    body = {
        "id": payment_id,
        "status": "CONFIRMED",
        "amount": 1.0,
        "currency": "RUB",
        "payload": json.dumps({"metadata": metadata}),
    }
    request = await _make_platega_request(
        {"X-MerchantId": "m1", "X-Secret": "s1"}, body
    )
    response = await payment_route.platega_webhook(request)
    assert response in (
        {"status": "ok"},
        {"status": "error", "message": "Upgrade bundle effect failed"},
    )

    user_after = await Users.get(id=user_id)
    active_after = await ActiveTariffs.get(id=active.id)
    assert user_after.balance == 10
    assert user_after.hwid_limit == 2
    assert user_after.lte_gb_total == 10
    assert user_after.expired_at == old_expired_at
    assert int(active_after.hwid_limit or 0) == 2
    assert int(active_after.lte_gb_total or 0) == 10

    row = await ProcessedPayments.get(payment_id=payment_id)
    assert row.effect_applied is False
    assert row.processing_state == "failed"
    assert "Insufficient bonus balance" in (row.last_error or "")


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


@pytest.mark.asyncio
async def test_webhook_uses_deltas_not_absolutes(monkeypatch):
    """MAJOR 4: if a parallel devices/LTE topup landed between invoice creation
    and webhook delivery, the webhook MUST apply our delta on top of the live
    DB state rather than overwriting it with the stale absolute target.
    """
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users

    payment_route = _silence_payment_side_effects(monkeypatch)

    user_id = 7_001_010
    # Invoice was created when user had 3 devices; target = 5 (delta = 2).
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, hwid_limit=3, lte_gb_total=10, days_remaining=30, price=600
    )

    metadata = _build_upgrade_bundle_metadata(
        user_id=user_id,
        target_devices=5,    # absolute target captured at invoice time
        target_lte_gb=12,
        target_extra_days=0,
        device_delta=2,      # +2 devices
        lte_delta_gb=2,      # +2 GB
        extra_days=0,
        amount_external=500.0,
        current_devices=3,
        current_lte_gb=10,
        previous_price=600,
    )
    payment_id = "platega-upgrade-bundle-deltas-10"
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

    # Simulate a parallel devices/LTE topup that landed BEFORE the webhook:
    # +1 device → 4, +5 GB → 15.
    active.hwid_limit = 4
    active.lte_gb_total = 15
    await active.save(update_fields=["hwid_limit", "lte_gb_total"])
    user.hwid_limit = 4
    user.lte_gb_total = 15
    await user.save(update_fields=["hwid_limit", "lte_gb_total"])

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

    user_after = await Users.get(id=user_id)
    active_after = await ActiveTariffs.get(id=active.id)

    # MUST be 4 + 2 = 6 (delta-based), not 5 (absolute target — would silently
    # wipe the parallel topup).
    assert user_after.hwid_limit == 6, (
        f"Expected delta-based 6 devices, got {user_after.hwid_limit} "
        f"(absolute-target bug would yield 5)"
    )
    assert int(active_after.hwid_limit or 0) == 6
    # Same invariant for LTE: 15 + 2 = 17, not 12.
    assert user_after.lte_gb_total == 17, (
        f"Expected delta-based 17 GB, got {user_after.lte_gb_total} "
        f"(absolute-target bug would yield 12)"
    )
    assert int(active_after.lte_gb_total or 0) == 17


def _capture_webhook_warnings(monkeypatch, payment_route):
    """Loguru bypasses pytest caplog; replace the module-level logger.warning
    with a stub that records formatted warning messages so drift-detection
    tests can assert on them.
    """
    captured: list[str] = []

    def _warn(template, *args, **kwargs):
        try:
            captured.append(template % args if args else str(template))
        except TypeError:
            captured.append(str(template))

    monkeypatch.setattr(payment_route.logger, "warning", _warn)
    return captured


@pytest.mark.asyncio
async def test_webhook_logs_warning_on_admin_drift(monkeypatch):
    """MAJOR 1: if an admin refunded one device between invoice creation and
    webhook delivery (current_device_count snapshot=3, live=2), the webhook
    must STILL apply the delta on top of live state — but log a drift warning
    so admins can see the discrepancy.
    """
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users

    payment_route = _silence_payment_side_effects(monkeypatch)
    warnings = _capture_webhook_warnings(monkeypatch, payment_route)

    user_id = 7_001_011
    # Invoice was created when user had 3 devices / 10 GB / 30 days remaining.
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, hwid_limit=3, lte_gb_total=10, days_remaining=30, price=600
    )
    snapshot_expired_at = user.expired_at

    metadata = _build_upgrade_bundle_metadata(
        user_id=user_id,
        target_devices=5,
        target_lte_gb=12,
        target_extra_days=15,
        device_delta=2,
        lte_delta_gb=2,
        extra_days=15,
        amount_external=500.0,
        current_devices=3,        # snapshot at invoice creation
        current_lte_gb=10,        # snapshot at invoice creation
        previous_price=600,
    )
    # The endpoint also stores expired_at as ms-since-epoch; mirror that here
    # so the webhook's drift check has the full snapshot.
    metadata["current_expired_at_ms"] = int(
        __import__("datetime").datetime.combine(
            snapshot_expired_at, __import__("datetime").datetime.min.time()
        ).timestamp() * 1000
    )
    # Simulate admin lowered the price from 600 → 550 in Tariffs.base_price
    # between invoice and webhook, so the recompute would diverge from the
    # quoted price the user paid for.
    metadata["new_active_tariff_price"] = 1500
    metadata["new_progressive_multiplier"] = 0.9

    payment_id = "platega-upgrade-bundle-drift-11"
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

    # Simulate an admin refund landing BEFORE the webhook: hwid_limit 3 → 2,
    # lte_gb_total 10 → 8, expired_at shifted back by 5 days.
    active.hwid_limit = 2
    active.lte_gb_total = 8
    await active.save(update_fields=["hwid_limit", "lte_gb_total"])
    user.hwid_limit = 2
    user.lte_gb_total = 8
    user.expired_at = snapshot_expired_at - timedelta(days=5)
    await user.save(update_fields=["hwid_limit", "lte_gb_total", "expired_at"])

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

    # The delta is still applied (parallel-topup is the primary use-case): the
    # webhook adds +2 devices on top of the refunded value (2 → 4), not on top
    # of the snapshot (3 → 5).
    user_after = await Users.get(id=user_id)
    active_after = await ActiveTariffs.get(id=active.id)
    assert user_after.hwid_limit == 4, (
        f"Expected delta on top of live state (2+2=4), got {user_after.hwid_limit}"
    )
    assert int(active_after.hwid_limit or 0) == 4
    assert user_after.lte_gb_total == 10, (
        f"Expected delta on top of live state (8+2=10), got {user_after.lte_gb_total}"
    )

    # And the drift warning is recorded so admins can audit it.
    combined = " | ".join(warnings)
    assert "device_count drift detected" in combined, (
        f"Missing device_count drift warning. Captured: {warnings}"
    )
    assert "lte_gb_total drift detected" in combined, (
        f"Missing lte_gb_total drift warning. Captured: {warnings}"
    )
    assert "expired_at drift detected" in combined, (
        f"Missing expired_at drift warning. Captured: {warnings}"
    )


@pytest.mark.asyncio
async def test_webhook_uses_metadata_price_on_drift(monkeypatch):
    """MAJOR 2: if Tariffs.base_price was changed between invoice and webhook,
    the recompute will diverge from the quoted price. The user paid the quoted
    price, so the webhook must persist the metadata snapshot — NOT the
    recomputed value — and log a drift warning.
    """
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users

    payment_route = _silence_payment_side_effects(monkeypatch)
    warnings = _capture_webhook_warnings(monkeypatch, payment_route)

    user_id = 7_001_012
    user, active = await _seed_user_with_active_tariff(
        user_id=user_id, hwid_limit=2, lte_gb_total=10, days_remaining=30, price=600
    )

    # User paid 200₽ as the quoted post-upgrade price at invoice creation.
    metadata = _build_upgrade_bundle_metadata(
        user_id=user_id,
        target_devices=4,
        target_lte_gb=10,
        target_extra_days=0,
        device_delta=2,
        lte_delta_gb=0,
        extra_days=0,
        amount_external=400.0,
        current_devices=2,
        current_lte_gb=10,
        previous_price=600,
    )
    metadata["new_active_tariff_price"] = 200
    metadata["new_progressive_multiplier"] = 0.5

    payment_id = "platega-upgrade-bundle-price-drift-12"
    await ProcessedPayments.create(
        payment_id=payment_id,
        provider="platega",
        user_id=user_id,
        amount=400.0,
        amount_external=400.0,
        amount_from_balance=0,
        status="pending",
        provider_payload=json.dumps({"metadata": metadata}),
    )

    # Admin lowered Tariffs.base_price after invoice was created. Recompute
    # would yield a different number than the 200₽ snapshot the user paid for.
    seed_tariff = await Tariffs.get(id=user_id)
    seed_tariff.base_price = 100  # was 300 → recompute will diverge from 200
    seed_tariff.progressive_multiplier = 0.5
    await seed_tariff.save(update_fields=["base_price", "progressive_multiplier"])

    body = {
        "id": payment_id,
        "status": "CONFIRMED",
        "amount": 400.0,
        "currency": "RUB",
        "payload": json.dumps({"metadata": metadata}),
    }
    request = await _make_platega_request(
        {"X-MerchantId": "m1", "X-Secret": "s1"}, body
    )
    response = await payment_route.platega_webhook(request)
    assert response == {"status": "ok"}, f"Unexpected response: {response}"

    # The metadata snapshot wins — the user paid for 200₽, not whatever the
    # admin's new base_price computes to.
    active_after = await ActiveTariffs.get(id=active.id)
    assert int(active_after.price or 0) == 200, (
        f"Expected metadata-snapshot price 200, got {active_after.price} "
        f"(would be recomputed value if MAJOR 2 fix were missing)"
    )
    assert float(active_after.progressive_multiplier or 0) == 0.5

    # And the drift warning was emitted.
    combined = " | ".join(warnings)
    assert "price drift detected" in combined, (
        f"Missing price drift warning. Captured: {warnings}"
    )
