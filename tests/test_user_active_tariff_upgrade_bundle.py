"""Тесты для combined upgrade-bundle endpoint /user/active_tariff/upgrade_bundle.

Покрывают:
- dry_run quote ветки (devices-only, lte-only, period-only, all three);
- валидация (target ниже текущего, выше cap, расширение > 365 дней,
  zero-delta, family member rejection);
- apply ветка (баланс хватает → списание, expired_at сдвигается, единая
  запись ProcessedPayments с payment_purpose='upgrade_bundle';
- apply ветка (баланса не хватает → status=payment_required с redirect_to).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from tortoise import Tortoise

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
    from tests._payment_test_stubs import install_stubs
except ModuleNotFoundError:  # pragma: no cover - import path compat
    from _sqlite_datetime_compat import register_sqlite_datetime_compat
    from _payment_test_stubs import install_stubs


register_sqlite_datetime_compat()


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    restore = install_stubs()
    try:
        yield
    finally:
        restore()


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
    models_to_create: Any = []
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


async def _setup_user_with_active_tariff(
    *,
    user_id: int,
    balance: int,
    hwid_limit: int = 2,
    lte_gb_total: int = 10,
    lte_price_per_gb: float = 5.0,
    days_remaining: int = 20,
    price: int = 600,
    months: int = 1,
):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users

    base_tariff = await Tariffs.create(
        id=user_id,
        name="1m",
        months=months,
        base_price=300,
        progressive_multiplier=0.9,
        order=1,
        devices_limit_family=30,
        lte_max_gb=500,
        lte_price_per_gb=lte_price_per_gb,
        lte_enabled=True,
    )

    today = date.today()
    user = await Users.create(
        id=user_id,
        username=f"u{user_id}",
        full_name=f"User {user_id}",
        is_registered=True,
        balance=balance,
        hwid_limit=hwid_limit,
        lte_gb_total=lte_gb_total,
        expired_at=today + timedelta(days=days_remaining),
    )

    active = await ActiveTariffs.create(
        id=user_id,
        name="1m",
        months=months,
        price=price,
        hwid_limit=hwid_limit,
        lte_gb_total=lte_gb_total,
        lte_price_per_gb=lte_price_per_gb,
        progressive_multiplier=0.9,
        user_id=user.id,
    )

    user.active_tariff_id = active.id
    await user.save(update_fields=["active_tariff_id"])
    user = await Users.get(id=user.id)
    return user, active, base_tariff


def _silence_side_effects(monkeypatch):
    """Глушим внешние сервисы (RemnaWave, уведомления, partner cashback)."""
    from bloobcat.routes import payment as payment_module
    from bloobcat.routes import user as user_module

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(user_module, "notify_active_tariff_change", _noop)
    monkeypatch.setattr(
        payment_module, "_award_partner_cashback", _noop, raising=False
    )

    _allowed_family_filter_keys = {
        "id",
        "owner_id",
        "member_id",
        "owner",
        "member",
        "status",
        "allocated_devices",
        "created_at",
        "updated_at",
    }

    class _StubFamilyFilter:
        def __init__(self, value):
            self._value = value

        async def exists(self):
            return self._value

    class _StubFamilyMembers:
        _next_value = False

        @classmethod
        def filter(cls, **kwargs):
            for key in kwargs:
                base = key.split("__", 1)[0]
                if base not in _allowed_family_filter_keys:
                    raise AssertionError(
                        f"FamilyMembers.filter got unknown field '{key}'. "
                        f"Allowed: {sorted(_allowed_family_filter_keys)}"
                    )
            return _StubFamilyFilter(cls._next_value)

    monkeypatch.setattr(user_module, "FamilyMembers", _StubFamilyMembers)

    class _StubRemnawaveUsers:
        async def update_user(self, *_args, **_kwargs):
            return None

    class _StubRemnawaveClient:
        def __init__(self):
            self.users = _StubRemnawaveUsers()

    monkeypatch.setattr(user_module, "remnawave_client", _StubRemnawaveClient())
    return _StubFamilyMembers


# ---------------------------------------------------------------------------
# Quote / dry_run tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quote_devices_only(monkeypatch):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9001, balance=10_000, hwid_limit=2, lte_gb_total=10
    )

    payload = UpgradeBundleRequest(
        target_device_count=3,
        target_lte_gb=10,
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert result["device_delta"] == 1
    assert result["lte_delta_gb"] == 0
    assert result["extra_days"] == 0
    assert result["lte_extra_cost_rub"] == 0
    assert result["period_extra_cost_rub"] == 0
    assert result["device_extra_cost_rub"] > 0
    assert result["total_extra_cost_rub"] == result["device_extra_cost_rub"]
    assert result["validation_errors"] == []


@pytest.mark.asyncio
async def test_quote_lte_only(monkeypatch):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9002, balance=10_000, lte_gb_total=10, lte_price_per_gb=5.0
    )

    payload = UpgradeBundleRequest(
        target_device_count=2,  # same as current
        target_lte_gb=15,
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert result["device_delta"] == 0
    assert result["lte_delta_gb"] == 5
    assert result["lte_extra_cost_rub"] == 25  # 5 GB * 5 ₽
    assert result["device_extra_cost_rub"] == 0
    assert result["period_extra_cost_rub"] == 0
    assert result["total_extra_cost_rub"] == 25
    assert result["validation_errors"] == []


@pytest.mark.asyncio
async def test_quote_period_only(monkeypatch):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9003, balance=10_000, price=600, months=1
    )
    # daily_rate = 600 / 30 = 20 ₽/day
    payload = UpgradeBundleRequest(
        target_device_count=2,
        target_lte_gb=10,
        target_extra_days=10,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert result["device_delta"] == 0
    assert result["lte_delta_gb"] == 0
    assert result["extra_days"] == 10
    assert result["period_extra_cost_rub"] == 200  # 20 ₽ * 10 дней
    assert result["total_extra_cost_rub"] == 200
    assert abs(result["daily_rate"] - 20.0) < 0.01
    assert result["validation_errors"] == []


@pytest.mark.asyncio
async def test_quote_all_three(monkeypatch):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9004, balance=10_000, hwid_limit=2, lte_gb_total=10
    )

    payload = UpgradeBundleRequest(
        target_device_count=3,
        target_lte_gb=15,
        target_extra_days=10,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert result["device_delta"] == 1
    assert result["lte_delta_gb"] == 5
    assert result["extra_days"] == 10
    # Total — strict sum invariant.
    assert (
        result["total_extra_cost_rub"]
        == result["device_extra_cost_rub"]
        + result["lte_extra_cost_rub"]
        + result["period_extra_cost_rub"]
    )
    assert result["validation_errors"] == []


@pytest.mark.asyncio
async def test_quote_zero_delta_returns_validation_error(monkeypatch):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9005, balance=10_000, hwid_limit=2, lte_gb_total=10
    )

    payload = UpgradeBundleRequest(
        target_device_count=2,
        target_lte_gb=10,
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert result["total_extra_cost_rub"] == 0
    assert "nothing_to_upgrade" in result["validation_errors"]


@pytest.mark.asyncio
async def test_quote_exceeds_devices_cap(monkeypatch):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9006, balance=100_000, hwid_limit=2
    )

    payload = UpgradeBundleRequest(
        target_device_count=31,  # > 30 cap
        target_lte_gb=10,
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert "target_devices_exceeds_cap" in result["validation_errors"]
    assert result["device_extra_cost_rub"] == 0  # not priced when out-of-bounds


@pytest.mark.asyncio
async def test_quote_exceeds_lte_cap(monkeypatch):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9007, balance=100_000, lte_gb_total=10
    )

    payload = UpgradeBundleRequest(
        target_device_count=2,
        target_lte_gb=501,
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert "target_lte_exceeds_cap" in result["validation_errors"]


@pytest.mark.asyncio
async def test_quote_extra_days_over_year(monkeypatch):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    # User has 100 days remaining → max extra = 365 - 100 = 265 days.
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9008, balance=100_000, days_remaining=100
    )

    payload = UpgradeBundleRequest(
        target_device_count=2,
        target_lte_gb=10,
        target_extra_days=300,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert "extra_days_exceeds_year" in result["validation_errors"]


# ---------------------------------------------------------------------------
# Apply tests (dry_run=false)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_with_sufficient_balance(monkeypatch):
    from unittest.mock import AsyncMock

    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_module
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)

    # Replace cashback + scheduler so we can assert they were exercised.
    referral_cashback_mock = AsyncMock(return_value={"applied": False})
    monkeypatch.setattr(
        payment_module,
        "_award_standard_referral_cashback",
        referral_cashback_mock,
        raising=False,
    )
    scheduler_mock = AsyncMock(return_value=None)
    # Patch through sys.modules to survive any prior test that may have
    # rebound `bloobcat.scheduler` to its own stub: production code does a
    # function-local `from bloobcat.scheduler import schedule_user_tasks`
    # so it always resolves through sys.modules at call time.
    import sys as _sys
    import types as _types

    scheduler_module = _sys.modules.get("bloobcat.scheduler")
    if scheduler_module is None:
        scheduler_module = _types.ModuleType("bloobcat.scheduler")
        monkeypatch.setitem(_sys.modules, "bloobcat.scheduler", scheduler_module)
    monkeypatch.setattr(
        scheduler_module, "schedule_user_tasks", scheduler_mock, raising=False
    )

    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9101, balance=50_000, hwid_limit=2, lte_gb_total=10
    )
    initial_expired = user.expired_at

    # First fetch a dry_run quote to know the bill so we can assert
    # exact balance debit afterwards.
    dry_run = await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=3,
            target_lte_gb=15,
            target_extra_days=10,
            dry_run=True,
        ),
        user=user,
    )
    expected_total = int(dry_run["total_extra_cost_rub"])
    assert expected_total > 0

    user = await Users.get(id=user.id)  # refresh
    result = await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=3,
            target_lte_gb=15,
            target_extra_days=10,
            dry_run=False,
        ),
        user=user,
    )

    assert result["status"] == "ok"
    assert result["devices_limit"] == 3
    assert result["lte_gb_total"] == 15
    assert result["extra_days"] == 10
    assert result["expired_at"] == initial_expired + timedelta(days=10)

    refreshed_user = await Users.get(id=user.id)
    assert refreshed_user.balance == 50_000 - expected_total
    assert refreshed_user.hwid_limit == 3
    assert refreshed_user.lte_gb_total == 15

    refreshed_active = await ActiveTariffs.get(id=_active.id)
    assert refreshed_active.hwid_limit == 3
    assert refreshed_active.lte_gb_total == 15

    # Exactly one ProcessedPayments row with the right purpose.
    payments = await ProcessedPayments.filter(
        user_id=user.id, payment_purpose="upgrade_bundle"
    ).all()
    assert len(payments) == 1
    assert int(payments[0].amount) == expected_total
    assert int(payments[0].amount_from_balance) == expected_total
    assert int(payments[0].amount_external) == 0
    assert payments[0].status == "succeeded"

    # Standard referral cashback must be awarded (symmetric with webhook flow).
    assert referral_cashback_mock.await_count == 1
    ref_call_kwargs = referral_cashback_mock.await_args.kwargs
    assert int(ref_call_kwargs["amount_external_rub"]) == 0
    assert getattr(ref_call_kwargs["referral_user"], "id", None) == user.id

    # Subscription period changed → scheduler must be re-armed at least once
    # for this user. (Users.save() auto-reschedules on expired_at change AND
    # the endpoint adds an explicit post-commit call as a defence-in-depth
    # against future refactors of the save override.)
    assert scheduler_mock.await_count >= 1
    awaited_user_ids = {
        getattr(call.args[0], "id", None) for call in scheduler_mock.await_args_list
    }
    assert user.id in awaited_user_ids


@pytest.mark.asyncio
async def test_apply_dry_run_does_not_mutate_state(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9102, balance=50_000, hwid_limit=2, lte_gb_total=10
    )

    snapshot_balance = user.balance
    snapshot_expired = user.expired_at
    snapshot_hwid = user.hwid_limit
    snapshot_lte = user.lte_gb_total

    await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=4,
            target_lte_gb=20,
            target_extra_days=10,
            dry_run=True,
        ),
        user=user,
    )

    refreshed = await Users.get(id=user.id)
    assert refreshed.balance == snapshot_balance
    assert refreshed.expired_at == snapshot_expired
    assert refreshed.hwid_limit == snapshot_hwid
    assert refreshed.lte_gb_total == snapshot_lte
    payments = await ProcessedPayments.filter(user_id=user.id).all()
    assert len(payments) == 0
    refreshed_active = await ActiveTariffs.get(id=_active.id)
    assert refreshed_active.hwid_limit == snapshot_hwid
    assert refreshed_active.lte_gb_total == snapshot_lte


@pytest.mark.asyncio
async def test_apply_with_insufficient_balance_creates_external_invoice(monkeypatch):
    """Когда баланса не хватает, должен сформироваться внешний платёж."""
    from bloobcat.routes import user as user_module
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9103, balance=1, hwid_limit=2, lte_gb_total=10
    )

    async def _fake_external_payment(
        *, user, active_tariff, amount_to_pay, amount_from_balance, metadata
    ):
        return {
            "status": "payment_required",
            "redirect_to": "https://example.test/checkout/abc",
            "payment_id": "fake_tx_abc",
            "provider": "platega",
            "_metadata": metadata,
        }

    monkeypatch.setattr(
        user_module,
        "_create_external_upgrade_bundle_payment",
        _fake_external_payment,
    )

    result = await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=3,
            target_lte_gb=15,
            target_extra_days=10,
            dry_run=False,
        ),
        user=user,
    )

    assert result["status"] == "payment_required"
    assert result["redirect_to"].startswith("https://")
    meta = result["_metadata"]
    assert meta["upgrade_bundle"] is True
    assert meta["target_device_count"] == 3
    assert meta["target_lte_gb"] == 15
    assert meta["target_extra_days"] == 10
    assert meta["payment_purpose"] == "upgrade_bundle"


@pytest.mark.asyncio
async def test_family_member_cannot_upgrade(monkeypatch):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    StubFM = _silence_side_effects(monkeypatch)
    StubFM._next_value = True  # simulate "current user is an active family member"
    try:
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=9104, balance=10_000
        )

        payload = UpgradeBundleRequest(
            target_device_count=3,
            target_lte_gb=15,
            target_extra_days=10,
            dry_run=False,
        )
        with pytest.raises(HTTPException) as exc_info:
            await upgrade_bundle(payload=payload, user=user)
        assert exc_info.value.status_code == 403
    finally:
        StubFM._next_value = False


@pytest.mark.asyncio
async def test_apply_zero_total_returns_400(monkeypatch):
    """dry_run=false с нулевой суммой → 400 nothing_to_upgrade."""
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9105, balance=10_000, hwid_limit=2, lte_gb_total=10
    )

    payload = UpgradeBundleRequest(
        target_device_count=2,
        target_lte_gb=10,
        target_extra_days=0,
        dry_run=False,
    )
    with pytest.raises(HTTPException) as exc_info:
        await upgrade_bundle(payload=payload, user=user)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Pure-function precision tests (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "price, months, extra_days, expected",
    [
        # 599/180*30 = 99.833 → 99 (float-bug produced 100).
        (599, 6, 30, 99),
        # 599/180*365 = 1214.638 → 1214.
        (599, 6, 365, 1214),
        # 1000/360*30 = 83.333 → 83.
        (1000, 12, 30, 83),
        # 1000/360*365 = 1013.888 → 1013 (ROUND_DOWN: round in customer's
        # favor; see _compute_period_extra_cost docstring).
        (1000, 12, 365, 1013),
        (0, 6, 30, 0),
        (599, 0, 30, 0),     # zero months — safety
        (599, 6, 0, 0),      # zero days — safety
        (-100, 6, 30, 0),    # negative — safety
    ],
)
def test_period_cost_decimal_precision(price, months, extra_days, expected):
    """`_compute_period_extra_cost` must round only once via Decimal so we
    don't accumulate float drift (cf. BLOCKER 3 in the upgrade_bundle review).
    """
    from bloobcat.services.upgrade_quote import _compute_period_extra_cost

    assert _compute_period_extra_cost(price, months, extra_days) == expected


@pytest.mark.asyncio
async def test_external_metadata_includes_new_active_tariff_price(monkeypatch):
    """BLOCKER 1: when balance is insufficient and device_delta > 0, the
    metadata sent to Platega/YooKassa MUST include `new_active_tariff_price`
    and `new_progressive_multiplier`, otherwise the webhook leaves the price
    stale and breaks auto-renew / daily_rate / notifications.
    """
    from bloobcat.routes import user as user_module
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9201, balance=1, hwid_limit=2, lte_gb_total=10
    )

    captured = {}

    async def _fake_external_payment(
        *, user, active_tariff, amount_to_pay, amount_from_balance, metadata
    ):
        captured.update(metadata)
        return {
            "status": "payment_required",
            "redirect_to": "https://example.test/x",
            "payment_id": "fake",
            "provider": "platega",
            "_metadata": metadata,
        }

    monkeypatch.setattr(
        user_module,
        "_create_external_upgrade_bundle_payment",
        _fake_external_payment,
    )

    result = await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=3,
            target_lte_gb=10,
            target_extra_days=0,
            dry_run=False,
        ),
        user=user,
    )

    assert result["status"] == "payment_required"
    assert captured.get("device_delta") == 1
    assert "new_active_tariff_price" in captured
    assert int(captured["new_active_tariff_price"]) > 0
    assert "new_progressive_multiplier" in captured
    assert 0 < float(captured["new_progressive_multiplier"]) <= 1.0


@pytest.mark.asyncio
async def test_external_metadata_skips_price_when_no_device_delta(monkeypatch):
    """When device_delta == 0, metadata must NOT carry stale price fields —
    they would override an unrelated tariff state in the webhook."""
    from bloobcat.routes import user as user_module
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9202, balance=1, hwid_limit=2, lte_gb_total=10
    )

    captured = {}

    async def _fake_external_payment(
        *, user, active_tariff, amount_to_pay, amount_from_balance, metadata
    ):
        captured.update(metadata)
        return {
            "status": "payment_required",
            "redirect_to": "https://example.test/x",
            "payment_id": "fake",
            "provider": "platega",
            "_metadata": metadata,
        }

    monkeypatch.setattr(
        user_module,
        "_create_external_upgrade_bundle_payment",
        _fake_external_payment,
    )

    await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=2,  # same as current → device_delta = 0
            target_lte_gb=15,
            target_extra_days=0,
            dry_run=False,
        ),
        user=user,
    )

    assert captured.get("device_delta") == 0
    assert "new_active_tariff_price" not in captured
    assert "new_progressive_multiplier" not in captured


@pytest.mark.asyncio
async def test_dry_run_response_includes_is_actionable_flag(monkeypatch):
    """MINOR: dry_run quote must expose `is_actionable` to the frontend so
    it can disable the Pay button without re-deriving the rule client-side.
    """
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9203, balance=10_000, hwid_limit=2, lte_gb_total=10
    )

    actionable = await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=3,
            target_lte_gb=15,
            target_extra_days=10,
            dry_run=True,
        ),
        user=user,
    )
    assert actionable["is_actionable"] is True
    assert actionable["total_extra_cost_rub"] > 0

    not_actionable = await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=2,
            target_lte_gb=10,
            target_extra_days=0,
            dry_run=True,
        ),
        user=user,
    )
    assert not_actionable["is_actionable"] is False
