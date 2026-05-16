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
    is_trial: bool = False,
    is_promo_synthetic: bool = False,
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
        is_trial=is_trial,
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
        is_promo_synthetic=is_promo_synthetic,
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
    import bloobcat.services.upgrade_quote as _uq
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: False)
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
    import bloobcat.services.upgrade_quote as _uq
    from bloobcat.services.upgrade_quote import compute_total_period_days
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: False)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9003, balance=10_000, price=600, months=1
    )
    # Calendar-aware: total_days = compute_total_period_days(1, today). For a
    # 1-month tariff, total_days is the calendar gap from today to today+1mo
    # (28-31 depending on the month). daily_rate = 600 / total_days_today.
    today = date.today()
    expected_total_days = compute_total_period_days(1, anchor=today)
    expected_period_cost = (600 * 10) // expected_total_days  # ROUND_DOWN
    expected_daily_rate = 600 / expected_total_days

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
    assert result["period_extra_cost_rub"] == expected_period_cost
    assert result["total_extra_cost_rub"] == expected_period_cost
    assert abs(result["daily_rate"] - expected_daily_rate) < 0.01
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
async def test_quote_lte_topup_above_tariff_line_is_allowed(monkeypatch):
    """Business principle: «человек платит — получает услугу». Tariff-level
    `lte_max_gb` is informational; top-up flow allows up to the absolute
    cap (`LTE_TOPUP_FALLBACK_TOTAL_MAX_GB`) so a user at 500/500 can always
    pay for more. Verifies the regression from v2 where tariff cap blocked
    revenue."""
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9007, balance=100_000, lte_gb_total=10
    )

    # tariff.lte_max_gb=500 (default) but we target 1500 ГБ — should succeed.
    payload = UpgradeBundleRequest(
        target_device_count=2,
        target_lte_gb=1500,
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert "target_lte_exceeds_cap" not in result["validation_errors"]
    assert result["lte_delta_gb"] == 1490


@pytest.mark.asyncio
async def test_quote_lte_uses_live_price_not_snapshot(monkeypatch):
    """Fix 3 (Live LTE pricing): when admin raised Directus
    `Tariffs.lte_price_per_gb` from 1.5 → 2.0 after user's purchase, the
    snapshot stays at 1.5 (cohort) but new GB MUST be priced at the live 2.0
    rate. Verifies UI shows the same price backend charges — eliminating
    «UI says 1.5, debit 2» confusion the user reported."""
    from bloobcat.db.tariff import Tariffs
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, active, tariff = await _setup_user_with_active_tariff(
        user_id=9020,
        balance=100_000,
        lte_gb_total=10,
        lte_price_per_gb=1.5,  # snapshot
    )
    # Admin raised the live price after user's purchase.
    await Tariffs.filter(id=tariff.id).update(lte_price_per_gb=2.0)

    payload = UpgradeBundleRequest(
        target_device_count=2,
        target_lte_gb=15,  # +5 GB
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert result["lte_delta_gb"] == 5
    # Live price (2.0) × 5 GB = 10 — not snapshot 1.5 × 5 = 7.
    assert result["lte_extra_cost_rub"] == 10
    assert result["lte_price_per_gb"] == 2.0


@pytest.mark.asyncio
async def test_quote_lte_fallback_uses_any_live_tariff_with_lte(monkeypatch):
    """Bug 1 (BE v4): when the strict (name, months) lookup misses — e.g.
    admin renamed a tariff in Directus, leaving the user's snapshot pointing
    at a now-nonexistent line — the quote MUST broaden the search to "any
    live tariff with lte_enabled" so the user sees the current Directus LTE
    rate, not the stale snapshot. This is the failure mode the user reported:
    "видел 2 ₽ вместо 1.5 ₽" after a rename.
    """
    import bloobcat.services.upgrade_quote as _uq
    from bloobcat.db.tariff import Tariffs
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: False)
    user, _active, tariff = await _setup_user_with_active_tariff(
        user_id=9021,
        balance=100_000,
        lte_gb_total=10,
        # Snapshot price the user paid at purchase: 2.0 ₽/ГБ.
        lte_price_per_gb=2.0,
    )
    # Admin renamed the original tariff (so name+months lookup misses) AND
    # dropped Directus LTE to 1.5. We seed a different tariff line that
    # still has lte_enabled — fallback should land on it.
    await Tariffs.filter(id=tariff.id).delete()
    await Tariffs.create(
        id=99021,
        name="1m-renamed",
        months=2,  # different months too, to avoid the second-stage fallback
        base_price=300,
        progressive_multiplier=0.9,
        order=2,
        devices_limit_family=30,
        lte_max_gb=500,
        lte_price_per_gb=1.5,
        lte_enabled=True,
    )

    payload = UpgradeBundleRequest(
        target_device_count=2,
        target_lte_gb=15,
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    # Live fallback price (1.5) × 5 = 7 (ROUND_DOWN of 7.5) — NOT snapshot
    # 2.0 × 5 = 10. This proves the fallback escaped the snapshot.
    assert result["lte_price_per_gb"] == 1.5
    assert result["lte_extra_cost_rub"] == 7


@pytest.mark.asyncio
async def test_quote_lte_falls_back_to_snapshot_when_no_live_tariff_with_lte(monkeypatch):
    """Final fallback (Bug 1, BE v4): if NO live tariff with lte_enabled
    exists at all (extreme exotic edge — admin retired every tariff or
    disabled LTE everywhere), the quote falls back to the historical
    snapshot price so we don't crash. This is the only path where
    `lte_price_per_gb` reads from `active_tariff.lte_price_per_gb`.
    """
    from bloobcat.db.tariff import Tariffs
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, tariff = await _setup_user_with_active_tariff(
        user_id=90211, balance=100_000, lte_gb_total=10, lte_price_per_gb=3.0
    )
    # Wipe every Tariffs row — both the name+months match AND the
    # broadened lte_enabled fallback now miss.
    await Tariffs.filter(id=tariff.id).delete()

    payload = UpgradeBundleRequest(
        target_device_count=2,
        target_lte_gb=15,
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    # snapshot 3.0 × 5 = 15
    assert result["lte_extra_cost_rub"] == 15
    assert result["lte_price_per_gb"] == 3.0


@pytest.mark.asyncio
async def test_device_cost_includes_extra_days(monkeypatch):
    """Bug 2 (BE v4): when a user upgrades +1 device AND +N extra days in
    one bundle, the new device serves for the FULL window (days_remaining
    + extra_days), not just the current remaining days. Charging only
    `days_remaining_now` left the extra-days portion of the device cost
    unpaid. This test asserts combined > devices-only at the same
    device_delta — the only way that holds is if extra_days widened
    the prorated window for the device axis.
    """
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    # 12-month tariff (base_price=300, multiplier=0.9 from fixture →
    # geometric sum for 2 seats = 570, for 3 seats = 813). 100 days
    # remaining out of ~365. With +1 device:
    #   devices_only: device cost ∝ (813-570) × 100 / 365
    #   combined +30 days: device cost ∝ (813-570) × 130 / 365 → strictly larger.
    # The price=570 keeps the snapshot consistent with the geometric sum,
    # avoiding a negative full_price_delta that would zero out both quotes.
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9100,
        balance=100_000,
        hwid_limit=2,
        days_remaining=100,
        price=570,
        months=12,
    )

    devices_only = await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=3,
            target_lte_gb=10,
            target_extra_days=0,
            dry_run=True,
        ),
        user=user,
    )
    combined = await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=3,
            target_lte_gb=10,
            target_extra_days=30,
            dry_run=True,
        ),
        user=user,
    )

    assert devices_only["status"] == "quote"
    assert combined["status"] == "quote"
    assert devices_only["device_delta"] == 1
    assert combined["device_delta"] == 1
    # Same device_delta, but combined paid for a longer window for the
    # new device → strictly larger device extra cost.
    assert combined["device_extra_cost_rub"] > devices_only["device_extra_cost_rub"]


@pytest.mark.asyncio
async def test_quote_returns_device_discount_breakdown(monkeypatch):
    """Bug 3 (BE v4): quote response must include `device_discount_rub`
    and `device_discount_percent` so the frontend can render the
    progressive-discount badge («−10% прогрессивная скидка за 3 устройства»)
    without re-deriving math client-side.
    In legacy mode the progressive discount fields are populated normally.
    """
    import bloobcat.services.upgrade_quote as _uq
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: False)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9101,
        balance=100_000,
        hwid_limit=1,
        days_remaining=30,
        price=300,
        months=1,
    )

    payload = UpgradeBundleRequest(
        target_device_count=3,
        target_lte_gb=10,
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert result["applies_progressive_discount"] is True
    assert result["device_discount_rub"] > 0
    assert 0 < result["device_discount_percent"] <= 100


@pytest.mark.asyncio
async def test_quote_no_discount_when_single_device(monkeypatch):
    """Discount breakdown stays zeroed when only one seat is requested —
    no progressive multiplier applies, so the badge must not render.
    """
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9102,
        balance=100_000,
        hwid_limit=1,
        days_remaining=30,
        price=300,
        months=1,
    )

    payload = UpgradeBundleRequest(
        target_device_count=1,  # same as current — no device delta
        target_lte_gb=15,  # only LTE changes
        target_extra_days=0,
        dry_run=True,
    )
    result = await upgrade_bundle(payload=payload, user=user)

    assert result["status"] == "quote"
    assert result["applies_progressive_discount"] is False
    assert result["device_discount_rub"] == 0
    assert result["device_discount_percent"] == 0.0


@pytest.mark.asyncio
async def test_quote_pydantic_rejects_over_absolute_lte_cap():
    """Pydantic guard rejects `target_lte_gb > 10000` before reaching the
    service. This protects RemnaWave + DB from absurd inputs and matches
    `LTE_TOPUP_FALLBACK_TOTAL_MAX_GB=10000`."""
    import pytest
    from pydantic import ValidationError
    from bloobcat.routes.user import UpgradeBundleRequest

    with pytest.raises(ValidationError) as exc_info:
        UpgradeBundleRequest(
            target_device_count=2,
            target_lte_gb=10001,
            target_extra_days=0,
            dry_run=True,
        )
    assert "target_lte_gb" in str(exc_info.value)


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
@pytest.mark.parametrize(
    ("is_trial", "is_promo_synthetic"),
    [
        (True, False),
        (False, True),
    ],
)
async def test_freebie_subscription_cannot_upgrade_bundle(
    monkeypatch, is_trial, is_promo_synthetic
):
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

    _silence_side_effects(monkeypatch)
    user, _active, _tariff = await _setup_user_with_active_tariff(
        user_id=9106 + int(is_promo_synthetic),
        balance=10_000,
        is_trial=is_trial,
        is_promo_synthetic=is_promo_synthetic,
    )

    payload = UpgradeBundleRequest(
        target_device_count=3,
        target_lte_gb=10,
        target_extra_days=0,
        dry_run=False,
    )
    with pytest.raises(HTTPException) as exc_info:
        await upgrade_bundle(payload=payload, user=user)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Апгрейд доступен только для платной подписки"


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
    "price, months, extra_days, anchor, expected",
    [
        # 12mo anchor 2026-01-15 → 365 days. 2190*365/365 = 2190 exactly
        # (yearly regression — same as buying a fresh year today).
        (2190, 12, 365, date(2026, 1, 15), 2190),
        # 6mo anchor 2026-01-15 → 181 days (Jan 15 → Jul 15).
        # 1290*30/181 = 213.81 → 213.
        (1290, 6, 30, date(2026, 1, 15), 213),
        # 1mo anchor 2026-01-15 → 31 days (Jan 15 → Feb 15).
        # 290*7/31 = 65.48 → 65.
        (290, 1, 7, date(2026, 1, 15), 65),
        # 3mo anchor 2026-01-15 → 90 days (Jan 15 → Apr 15).
        # 749*30/90 = 249.67 → 249.
        (749, 3, 30, date(2026, 1, 15), 249),
        # Safety branches — anchor irrelevant for these, but supplied for
        # determinism.
        (0, 6, 30, date(2026, 1, 15), 0),
        (1290, 0, 30, date(2026, 1, 15), 0),
        (1290, 6, 0, date(2026, 1, 15), 0),
        (-100, 6, 30, date(2026, 1, 15), 0),
        (599, 0, 30, date(2026, 1, 15), 0),
    ],
)
def test_period_cost_calendar_aware(price, months, extra_days, anchor, expected):
    """`_compute_period_extra_cost` uses calendar-aware month length
    (`add_months_safe`), the same convention as `pay()` and
    `_apply_devices_topup_effect`. Anchor is supplied explicitly so the
    expected values are deterministic regardless of `date.today()`.

    Replaces the old fixed-30-day formulation which overcharged by ~5d/year.
    """
    from bloobcat.services.upgrade_quote import _compute_period_extra_cost

    assert (
        _compute_period_extra_cost(price, months, extra_days, anchor=anchor)
        == expected
    )


@pytest.mark.asyncio
async def test_upgrade_uses_live_multiplier_matches_regular_constructor(monkeypatch):
    """Pricing symmetry: upgrade quote MUST use the LIVE tariff multiplier
    (not the snapshot), so that adding a device on an existing subscription
    costs the same as the difference between two regular-constructor quotes.

    Previously the helper mixed live `base_price` with snapshot
    `progressive_multiplier`, which produced absurd quotes (~600₽/device on
    annual) when admin had edited live pricing after the user's purchase.
    The user asked us to align upgrade economics with the regular
    constructor; this test guards that alignment.
    """
    from bloobcat.services.upgrade_quote import _compute_progressive_full_price

    _silence_side_effects(monkeypatch)
    user, active, tariff = await _setup_user_with_active_tariff(
        user_id=9301, balance=10_000, hwid_limit=2
    )

    # User's snapshot multiplier at purchase was 0.85.
    active.progressive_multiplier = 0.85
    await active.save(update_fields=["progressive_multiplier"])

    # Admin later edits the live tariff to a steeper discount (cheaper devices).
    tariff.progressive_multiplier = 0.5
    await tariff.save(update_fields=["progressive_multiplier"])
    tariff = await tariff.__class__.get(id=tariff.id)  # re-read effective fields

    price_rub, multiplier = _compute_progressive_full_price(
        active, tariff, target_device_count=3
    )
    # Must reflect 0.5 (live), not 0.85 (snapshot). The user gets today's
    # price for new seats — identical to a fresh-purchase quote.
    assert abs(multiplier - 0.5) < 1e-9, (
        f"Multiplier did not follow live tariff: expected 0.5 (live), "
        f"got {multiplier}"
    )
    # And the price equals exactly `tariff.calculate_price(3)` — same path
    # the regular tariff constructor uses (`tariff_quote.py:227`).
    expected_price = int(tariff.calculate_price(3))
    assert price_rub == expected_price, (
        f"Upgrade price {price_rub} diverged from regular constructor "
        f"`calculate_price(3)` = {expected_price}; live pricing broken"
    )


@pytest.mark.asyncio
async def test_progressive_multiplier_above_one_is_clamped_to_design_range(monkeypatch):
    """Regression guard: a Directus admin can accidentally save
    `progressive_multiplier` outside the documented [0.1, 1.0] range
    (the field's docstring says smaller = bigger discount, and 1.0 means
    no discount; a value > 1.0 inverts the math into per-device escalation).

    The user-reported symptom: 2→3 devices showed «87 ₽/устр/мес» while
    2→7 showed «101 ₽/устр/мес» — per-device cost was rising with delta,
    the opposite of a volume discount. Root cause: snapshot fallback and
    the returned `multiplier` scalar from `_compute_progressive_full_price`
    were not running through `Tariffs._sanitize_multiplier`, so values
    above 1.0 escaped into the geometric-sum math and into
    `applies_progressive_discount` (which then read False because the raw
    1.2 was > 1.0).

    After the clamp: live + snapshot paths both clamp into
    [0.1, 0.9999]; downstream `applies_progressive_discount` flips back
    to True, and per-device-per-month math monotonically falls (or stays
    flat at 0.9999) with growing delta.
    """
    from bloobcat.db.tariff import Tariffs
    from bloobcat.services.upgrade_quote import _compute_progressive_full_price

    _silence_side_effects(monkeypatch)
    user, active, tariff = await _setup_user_with_active_tariff(
        user_id=9305, balance=10_000, hwid_limit=2
    )

    # Drift the live tariff multiplier to an invalid escalation value.
    tariff.progressive_multiplier = 1.2
    await tariff.save(update_fields=["progressive_multiplier"])
    tariff = await tariff.__class__.get(id=tariff.id)

    _, returned_multiplier = _compute_progressive_full_price(
        active, tariff, target_device_count=3
    )
    assert returned_multiplier <= 0.9999, (
        f"live-path multiplier {returned_multiplier} escaped the "
        "[0.1, 0.9999] design range — escalation will leak into "
        "applies_progressive_discount and discount-breakdown math"
    )
    assert returned_multiplier == Tariffs._sanitize_multiplier(1.2), (
        f"live-path multiplier {returned_multiplier} not equal to the "
        "documented clamp output"
    )

    # Snapshot fallback (original_tariff=None) must also clamp.
    active.progressive_multiplier = 1.2
    await active.save(update_fields=["progressive_multiplier"])
    _, snapshot_multiplier = _compute_progressive_full_price(
        active, None, target_device_count=3
    )
    assert snapshot_multiplier <= 0.9999, (
        f"snapshot-fallback multiplier {snapshot_multiplier} escaped "
        "the [0.1, 0.9999] design range"
    )


@pytest.mark.asyncio
async def test_upgrade_delta_matches_regular_constructor_delta(monkeypatch):
    """Regression guard: the prorated `device_extra_cost_rub` returned by
    `build_upgrade_bundle_quote` MUST equal (within ≤1₽ truncation) the
    cost difference a user would pay if they bought the same device count
    fresh on the regular constructor. Anchors the «match regular constructor»
    contract the user explicitly requested after seeing ~600₽/device.
    This test pins legacy mode so the delta-math contract stays verifiable
    independently of fresh-equivalent pricing.
    """
    import bloobcat.services.upgrade_quote as _uq
    from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle
    from bloobcat.services.upgrade_quote import compute_total_period_days

    _silence_side_effects(monkeypatch)
    monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: False)

    # 12-month tariff, user has 2 seats, full year ahead. The snapshot
    # `active_tariff.price` is intentionally left at the fixture default —
    # the new live-pricing math derives current_full_price from
    # `tariff.calculate_price(current_devices)` and ignores the snapshot,
    # so the test result is independent of whatever was stored at purchase.
    today = date.today()
    full_year_days = compute_total_period_days(12, anchor=today)
    user, _active, tariff = await _setup_user_with_active_tariff(
        user_id=9302,
        balance=100_000,
        hwid_limit=2,
        days_remaining=full_year_days,
        months=12,
    )

    # +1 device, no LTE / period changes, full year window.
    result = await upgrade_bundle(
        payload=UpgradeBundleRequest(
            target_device_count=3,
            target_lte_gb=10,
            target_extra_days=0,
            dry_run=True,
        ),
        user=user,
    )
    assert result["status"] == "quote"

    # Regular constructor delta for the same +1 device step.
    regular_delta_full_period = int(tariff.calculate_price(3)) - int(
        tariff.calculate_price(2)
    )
    # Full-year window means the prorating factor is 1.0 → upgrade delta
    # equals the regular constructor delta within at most ±1₽ ROUND_DOWN.
    diff = abs(result["device_extra_cost_rub"] - regular_delta_full_period)
    assert diff <= 1, (
        f"Upgrade device cost {result['device_extra_cost_rub']} diverges "
        f"from regular constructor delta {regular_delta_full_period} by "
        f"{diff}₽ — pricing symmetry broken"
    )


def test_period_cost_yearly_regression():
    """Regression: a 12-month tariff (2190₽ — real Vectra seed) extended by
    exactly one year of days should bill the user the same 2190₽ — never
    more. Under the old `months * 30 = 360` formula this returned 2220₽
    (15₽ overcharge on every yearly customer).
    """
    from bloobcat.services.upgrade_quote import _compute_period_extra_cost

    # Anchor in a non-leap window so the calendar yields exactly 365 days.
    anchor = date(2026, 1, 15)
    assert (
        _compute_period_extra_cost(2190, 12, 365, anchor=anchor) == 2190
    ), "12-month tariff extended by 1 year must cost exactly its own price"


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


# ---------------------------------------------------------------------------
# Phase 1 Shadow mode tests
# ---------------------------------------------------------------------------


async def _setup_four_prod_skus(base_id: int = 80000) -> list:
    """Seed 4 prod-like SKUs (1mo/3mo/6mo/12mo) with real Vectra pricing.

    Real prod tariffs verified 2026-05-15:
    - 1mo:  base=150, mult=0.9616, final_price_default=150, final_price_family=2700,  devices_limit_default=1, devices_limit_family=30
    - 3mo:  base=399, mult=0.9564, default=399,   family=6751, 1/30
    - 6mo:  base=749, mult=0.9423, default=749,   family=10800, 1/30
    - 12mo: base=1299, mult=0.9164, default=1299, family=14399, 1/30
    """
    from bloobcat.db.tariff import Tariffs

    skus = []
    for i, (months, base, mult, fpd, fpf) in enumerate([
        (1,  150,  0.9616, 150,  2700),
        (3,  399,  0.9564, 399,  6751),
        (6,  749,  0.9423, 749,  10800),
        (12, 1299, 0.9164, 1299, 14399),
    ]):
        sku = await Tariffs.create(
            id=base_id + i,
            name=f"{months}m_prod",
            months=months,
            base_price=base,
            progressive_multiplier=mult,
            order=i + 1,
            is_active=True,
            devices_limit_default=1,
            devices_limit_family=30,
            final_price_default=fpd,
            final_price_family=fpf,
            lte_max_gb=500,
            lte_price_per_gb=1.5,
            lte_enabled=True,
        )
        skus.append(sku)
    return skus


class TestShadowMode:
    @pytest.mark.asyncio
    async def test_shadow_fields_present_with_default_zero_when_no_skus(self, monkeypatch):
        """When DB has no Tariffs at all, shadow fields default to 0/None.
        In Phase 2 fresh-mode with no SKUs, pricing_mode becomes snapshot_fallback.
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.db.tariff import Tariffs
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        user, _active, tariff = await _setup_user_with_active_tariff(
            user_id=88001, balance=50_000, hwid_limit=1, lte_gb_total=0, days_remaining=5
        )
        # Wipe all Tariffs so SKU lookup returns nothing.
        await Tariffs.all().delete()

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=3,
                target_lte_gb=0,
                target_extra_days=30,
                dry_run=True,
            ),
            user=user,
        )

        assert result["fresh_equivalent_total_rub"] == 0
        assert result["refund_rub"] == 0
        assert result["optimal_sku_months"] == 0
        assert result["optimal_sku_id"] is None
        # Phase 2: no SKU found with fresh mode on → snapshot_fallback
        assert result["pricing_mode"] == "snapshot_fallback"

    @pytest.mark.asyncio
    async def test_shadow_fresh_equivalent_uses_optimal_sku(self, monkeypatch):
        """User on 1mo SKU, 5 days remaining, target +28 devices + 240 days.
        Assert: optimal_sku_months==12, fresh_equivalent_total_rub>0,
        refund_rub>0, total_extra_cost_rub UNCHANGED (legacy).
        """
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        # Create user on 1mo tariff with 5 days remaining.
        user, _active, base_tariff = await _setup_user_with_active_tariff(
            user_id=88002,
            balance=50_000,
            hwid_limit=2,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=5,
            price=150,
            months=1,
        )
        skus = await _setup_four_prod_skus(base_id=88100)

        # Get the legacy baseline first.
        legacy_result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=30,
                target_lte_gb=0,
                target_extra_days=240,
                dry_run=True,
            ),
            user=user,
        )

        assert legacy_result["optimal_sku_months"] == 12, (
            f"expected optimal_sku_months=12, got {legacy_result['optimal_sku_months']}"
        )
        assert legacy_result["fresh_equivalent_total_rub"] > 0
        assert legacy_result["refund_rub"] > 0
        # Phase 1: total is still legacy, not changed.
        assert legacy_result["total_extra_cost_rub"] == (
            legacy_result["device_extra_cost_rub"]
            + legacy_result["lte_extra_cost_rub"]
            + legacy_result["period_extra_cost_rub"]
        )

    @pytest.mark.asyncio
    async def test_shadow_refund_prorated_by_days(self, monkeypatch):
        """active_tariff.price=1200, months=12, days_remaining=half-year → refund≈600."""
        from datetime import date as date_cls
        from bloobcat.services.upgrade_quote import _compute_refund, compute_total_period_days
        from bloobcat.db.active_tariff import ActiveTariffs

        today = date_cls(2026, 1, 15)
        total_days = compute_total_period_days(12, anchor=today)  # 365

        active = ActiveTariffs.__new__(ActiveTariffs)
        active.price = 1200
        active.months = 12

        half = total_days // 2
        refund = _compute_refund(active, half, today)
        # ROUND_DOWN: 1200 * 182 / 365 = 598.35... → 598 (or 600 if total_days=360)
        expected = int((1200 * half) // total_days)
        assert refund == expected
        assert refund > 0

    @pytest.mark.asyncio
    async def test_shadow_picks_smallest_covering_sku(self, monkeypatch):
        """target_total_days=100 → picks 6mo (covers ~180d, since 3mo≈90d doesn't)."""
        from datetime import date as date_cls
        from bloobcat.services.upgrade_quote import _pick_optimal_sku, compute_total_period_days
        from bloobcat.db.active_tariff import ActiveTariffs

        _silence_side_effects(monkeypatch)
        await _setup_four_prod_skus(base_id=88200)

        active = ActiveTariffs.__new__(ActiveTariffs)
        active.months = 1

        today = date_cls(2026, 1, 15)
        sku, sku_days = await _pick_optimal_sku(active, 100, today)

        assert sku is not None
        # 3mo from 2026-01-15 = 90 days (Jan→Apr 15), doesn't cover 100.
        # 6mo from 2026-01-15 = 181 days (Jan→Jul 15), covers 100.
        assert sku.months == 6, f"expected 6mo, got {sku.months}mo"
        assert sku_days >= 100

    @pytest.mark.asyncio
    async def test_shadow_falls_back_to_longest_when_target_exceeds_all(self, monkeypatch):
        """target_total_days=1000 → picks 12mo (longest available)."""
        from datetime import date as date_cls
        from bloobcat.services.upgrade_quote import _pick_optimal_sku
        from bloobcat.db.active_tariff import ActiveTariffs

        _silence_side_effects(monkeypatch)
        await _setup_four_prod_skus(base_id=88300)

        active = ActiveTariffs.__new__(ActiveTariffs)
        active.months = 1

        today = date_cls(2026, 1, 15)
        sku, _days = await _pick_optimal_sku(active, 1000, today)

        assert sku is not None
        assert sku.months == 12, f"expected 12mo fallback, got {sku.months}mo"

    @pytest.mark.asyncio
    async def test_legacy_total_when_flag_off(self, monkeypatch):
        """With UPGRADE_PRICING_FRESH_MODE disabled, total_extra_cost_rub must
        equal the legacy axis sum regardless of shadow fresh values.
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: False)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=88004,
            balance=50_000,
            hwid_limit=2,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=5,
            price=150,
            months=1,
        )
        await _setup_four_prod_skus(base_id=88400)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=30,
                target_lte_gb=0,
                target_extra_days=240,
                dry_run=True,
            ),
            user=user,
        )

        # Legacy sum invariant must hold when flag is off.
        assert result["total_extra_cost_rub"] == (
            result["device_extra_cost_rub"]
            + result["lte_extra_cost_rub"]
            + result["period_extra_cost_rub"]
        )
        # Shadow fields are present in response.
        assert "fresh_equivalent_total_rub" in result
        assert "refund_rub" in result
        assert "pricing_mode" in result
        assert result["pricing_mode"] == "delta_legacy_shadow"


# ---------------------------------------------------------------------------
# Phase 2: fresh-equivalent pricing tests
# ---------------------------------------------------------------------------


class TestFreshEquivalentMode:
    """Tests for UPGRADE_PRICING_FRESH_MODE=true (Phase 2 flip)."""

    @pytest.mark.asyncio
    async def test_pricing_mode_is_fresh_minus_refund(self, monkeypatch):
        """With flag on and SKUs available, pricing_mode must be fresh_minus_refund."""
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89001,
            balance=50_000,
            hwid_limit=2,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=5,
            price=150,
            months=1,
        )
        await _setup_four_prod_skus(base_id=89100)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=30,
                target_lte_gb=0,
                target_extra_days=240,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"

    @pytest.mark.asyncio
    async def test_total_equals_fresh_minus_refund(self, monkeypatch):
        """total_extra_cost_rub == max(0, fresh_equivalent - refund) in Phase 2."""
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89002,
            balance=50_000,
            hwid_limit=2,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=5,
            price=150,
            months=1,
        )
        await _setup_four_prod_skus(base_id=89200)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=30,
                target_lte_gb=0,
                target_extra_days=240,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        expected_total = max(
            0,
            result["fresh_equivalent_total_rub"] - result["refund_rub"],
        )
        assert result["total_extra_cost_rub"] == expected_total

    @pytest.mark.asyncio
    async def test_axes_sum_to_total_in_fresh_mode(self, monkeypatch):
        """device + lte + period axes must always sum to total in fresh mode."""
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89003,
            balance=50_000,
            hwid_limit=2,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=5,
            price=150,
            months=1,
        )
        await _setup_four_prod_skus(base_id=89300)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=10,
                target_lte_gb=20,
                target_extra_days=60,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        axis_sum = (
            result["device_extra_cost_rub"]
            + result["lte_extra_cost_rub"]
            + result["period_extra_cost_rub"]
        )
        assert axis_sum == result["total_extra_cost_rub"]

    @pytest.mark.asyncio
    async def test_zero_delta_stays_zero_in_fresh_mode(self, monkeypatch):
        """Zero-delta quote (no upgrade on any axis) must remain 0 in fresh mode."""
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89004,
            balance=50_000,
            hwid_limit=2,
            lte_gb_total=10,
            lte_price_per_gb=1.5,
            days_remaining=30,
            price=150,
            months=1,
        )
        await _setup_four_prod_skus(base_id=89400)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=2,   # same as current — no device delta
                target_lte_gb=10,        # same as current — no LTE delta
                target_extra_days=0,     # no period extension
                dry_run=True,
            ),
            user=user,
        )

        # has_any_upgrade is False → must stay at 0 regardless of flag.
        assert result["total_extra_cost_rub"] == 0
        assert result["device_extra_cost_rub"] == 0
        assert result["lte_extra_cost_rub"] == 0
        assert result["period_extra_cost_rub"] == 0

    @pytest.mark.asyncio
    async def test_fresh_mode_lower_than_legacy_for_renewal_upgrade(self, monkeypatch):
        """For a user close to expiry doing a device upgrade + period extension,
        fresh-equivalent pricing must be <= legacy delta pricing (the 'fairness'
        guarantee that drove Phase 2).
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89005,
            balance=50_000,
            hwid_limit=2,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=5,
            price=150,
            months=1,
        )
        await _setup_four_prod_skus(base_id=89500)

        # Get legacy total.
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: False)
        legacy_result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=30,
                target_lte_gb=0,
                target_extra_days=240,
                dry_run=True,
            ),
            user=user,
        )
        legacy_total = legacy_result["total_extra_cost_rub"]

        # Get fresh total.
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        fresh_result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=30,
                target_lte_gb=0,
                target_extra_days=240,
                dry_run=True,
            ),
            user=user,
        )
        fresh_total = fresh_result["total_extra_cost_rub"]

        # Fresh should be <= legacy for near-expiry + big device + big period upgrade.
        assert fresh_total <= legacy_total, (
            f"fresh={fresh_total} > legacy={legacy_total} — fairness contract broken"
        )

    @pytest.mark.asyncio
    async def test_progressive_discount_zeroed_in_fresh_mode(self, monkeypatch):
        """In fresh-equivalent mode applies_progressive_discount must be False
        since the SKU multiplier is already baked into the fresh price.
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89006,
            balance=50_000,
            hwid_limit=1,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=30,
            price=300,
            months=1,
        )
        await _setup_four_prod_skus(base_id=89600)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=5,
                target_lte_gb=0,
                target_extra_days=0,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        assert result["applies_progressive_discount"] is False
        assert result["device_discount_rub"] == 0
        assert result["device_discount_percent"] == 0.0

    @pytest.mark.asyncio
    async def test_optimal_sku_fields_populated_in_fresh_mode(self, monkeypatch):
        """optimal_sku_months and optimal_sku_id must be set in fresh mode."""
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89007,
            balance=50_000,
            hwid_limit=2,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=5,
            price=150,
            months=1,
        )
        skus = await _setup_four_prod_skus(base_id=89700)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=10,
                target_lte_gb=0,
                target_extra_days=240,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        assert result["optimal_sku_months"] > 0
        assert result["optimal_sku_id"] is not None
        # The picked SKU id must be one of the seeded SKUs.
        seeded_ids = {sku.id for sku in skus}
        assert result["optimal_sku_id"] in seeded_ids

    @pytest.mark.asyncio
    async def test_fresh_mode_huge_refund_preserves_lte_cost(self, monkeypatch):
        """Regression for the «LTE расчёт уходит в минус» bug (2026-05-16).

        Scenario: user freshly bought an annual paid tariff (price=1800₽,
        ~350 days remaining). They want +10 GB LTE at the tariff's live rate
        of 1.5 ₽/GB.

        Before the fix, the `fresh_minus_refund` block clamped
        `new_lte = min(shadow_lte_share, new_total)`, and `new_total` was
        `max(0, fresh_total - huge_refund) = 0`, so `lte_extra_cost_rub`
        silently dropped to 0. The user was charged ~0 ₽ for 10 fresh GB —
        in production this looked like "kнопка не активируется".

        Contract: `lte_extra_cost_rub == lte_delta_gb × live_lte_price_per_gb`
        regardless of how big the refund is on the period axis.
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89010,
            balance=200_000,
            hwid_limit=1,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=350,
            price=1800,
            months=12,
        )
        await _setup_four_prod_skus(base_id=89800)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=1,   # no device delta
                target_lte_gb=10,        # +10 GB
                target_extra_days=0,     # no period extension
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        # LTE component must be 10 × 1.5 = 15 ₽ — never zeroed by refund.
        assert result["lte_extra_cost_rub"] == 15, (
            f"LTE was clamped: got {result['lte_extra_cost_rub']}, expected 15"
        )
        # Total ≥ LTE component (period/device may add to it via reallocation,
        # but the LTE cost itself stays linear).
        assert result["total_extra_cost_rub"] >= 15

    @pytest.mark.asyncio
    async def test_fresh_mode_lte_uses_live_price_not_shadow_sku(self, monkeypatch):
        """The user's CURRENT tariff rate is the source of truth for the LTE
        line cost — NOT the optimal SKU's `lte_price_per_gb`. This keeps the
        fairness chip (`fresh_equivalent_total_rub`) consistent with what the
        user is actually charged and matches what the user saw in their pricing
        hint before clicking «Pay».
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.db.tariff import Tariffs
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        # Current tariff: live LTE rate 1.5 ₽/GB.
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89011,
            balance=50_000,
            hwid_limit=1,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=30,
            price=300,
            months=1,
        )
        # Seed a "cheaper LTE" optimal SKU candidate for the 12mo window.
        # Without the override, the SKU's 0.5 ₽/GB would dilute the line —
        # with the override the user pays the 1.5 ₽/GB their tariff page
        # advertised.
        await Tariffs.create(
            id=89900,
            name="12m_cheap_lte",
            months=12,
            base_price=1299,
            progressive_multiplier=0.9164,
            order=99,
            is_active=True,
            devices_limit_default=1,
            devices_limit_family=30,
            final_price_default=1299,
            final_price_family=14399,
            lte_max_gb=500,
            lte_price_per_gb=0.5,   # ← cheaper than user's current tariff
            lte_enabled=True,
        )

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=1,
                target_lte_gb=10,
                target_extra_days=0,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        # Must charge by USER'S current tariff rate (1.5), not SKU's (0.5).
        assert result["lte_extra_cost_rub"] == 15
        assert result["lte_price_per_gb"] == 1.5

    @pytest.mark.asyncio
    async def test_fresh_mode_lte_with_all_three_axes(self, monkeypatch):
        """+devices, +LTE, +period combined. LTE line stays linear at live ×
        delta; non-LTE total is `max(0, fresh_non_lte - refund)`; axes sum to
        total.
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89012,
            balance=50_000,
            hwid_limit=2,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=30,
            price=300,
            months=1,
        )
        await _setup_four_prod_skus(base_id=89930)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=5,
                target_lte_gb=20,
                target_extra_days=60,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        # LTE always = 20 × 1.5 = 30 (linear, refund-independent).
        assert result["lte_extra_cost_rub"] == 30
        # Axes sum to total.
        axis_sum = (
            result["device_extra_cost_rub"]
            + result["lte_extra_cost_rub"]
            + result["period_extra_cost_rub"]
        )
        assert axis_sum == result["total_extra_cost_rub"]

    @pytest.mark.asyncio
    async def test_fresh_mode_lte_unavailable_still_errors(self, monkeypatch):
        """When the user's tariff has no live LTE price (rate 0) AND the
        legacy LTE block errored with `lte_unavailable_for_tariff`, the fix
        must not silently inject a non-zero LTE cost via the fresh-mode
        path. Behavior must stay: 0 LTE cost, error preserved.
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        # lte_price_per_gb=0 in BOTH active tariff AND the only seeded Tariffs
        # row → `effective_lte_price_per_gb == 0` → LTE delta dropped, error
        # appended. Use the tariff seeded by `_setup_user_with_active_tariff`
        # which mirrors the active_tariff price-per-gb (here 0).
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89013,
            balance=50_000,
            hwid_limit=1,
            lte_gb_total=0,
            lte_price_per_gb=0.0,
            days_remaining=30,
            price=300,
            months=1,
        )
        # Wipe any other LTE-enabled tariffs so the wide-fallback can't find
        # a live LTE price elsewhere.
        from bloobcat.db.tariff import Tariffs as _T
        await _T.filter(id__not=user.id).delete()
        # Also disable LTE on the only remaining (active-tariff) row so the
        # fallback doesn't pick it via `lte_enabled=True`.
        await _T.filter(id=user.id).update(lte_enabled=False, lte_price_per_gb=0)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=1,
                target_lte_gb=10,
                target_extra_days=0,
                dry_run=True,
            ),
            user=user,
        )

        assert result["lte_extra_cost_rub"] == 0
        assert "lte_unavailable_for_tariff" in result.get("validation_errors", [])

    @pytest.mark.asyncio
    async def test_legacy_mode_lte_unchanged_by_fresh_fix(self, monkeypatch):
        """With UPGRADE_PRICING_FRESH_MODE=false the legacy delta path is the
        source of truth. The LTE preservation fix lives in the fresh-mode
        branch only, so the legacy total must equal the simple linear formula
        regardless of refund size.
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: False)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89014,
            balance=50_000,
            hwid_limit=1,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=350,
            price=1800,
            months=12,
        )

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=1,
                target_lte_gb=10,
                target_extra_days=0,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "delta_legacy_shadow"
        assert result["lte_extra_cost_rub"] == 15
        assert result["total_extra_cost_rub"] == 15

    @pytest.mark.asyncio
    async def test_fresh_mode_devices_only_huge_refund_invariants(self, monkeypatch):
        """Symmetric sanity for the device axis under the same «freshly-bought
        yearly» refund profile that broke LTE.

        Unlike LTE, the device axis legitimately participates in the
        fresh_minus_refund flip: a +N-device upgrade on a tariff the user
        almost just bought IS a fairness-bound exchange (refund of unused
        period vs. fresh price of the upgraded pack). The test asserts the
        invariants that protect the user from the LTE-style accidental
        zeroing without forcing a magic number:

        - total ≥ 0 (no negative quotes ever leak),
        - lte and period axes stay at 0 when their delta is 0,
        - axes always sum to total,
        - device cost <= legacy delta cost (Phase 2 fairness contract).
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89020,
            balance=200_000,
            hwid_limit=1,
            lte_gb_total=10,
            lte_price_per_gb=1.5,
            days_remaining=350,
            price=1800,
            months=12,
        )
        await _setup_four_prod_skus(base_id=90000)

        # Legacy total for comparison.
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: False)
        legacy = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=3,
                target_lte_gb=10,
                target_extra_days=0,
                dry_run=True,
            ),
            user=user,
        )

        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=3,
                target_lte_gb=10,
                target_extra_days=0,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        assert result["lte_extra_cost_rub"] == 0
        assert result["period_extra_cost_rub"] == 0
        assert result["total_extra_cost_rub"] >= 0
        axis_sum = (
            result["device_extra_cost_rub"]
            + result["lte_extra_cost_rub"]
            + result["period_extra_cost_rub"]
        )
        assert axis_sum == result["total_extra_cost_rub"]
        # Phase 2 fairness contract: fresh-mode total <= legacy delta.
        assert result["total_extra_cost_rub"] <= legacy["total_extra_cost_rub"]

    @pytest.mark.asyncio
    async def test_fresh_mode_period_only_huge_refund_may_be_zero_by_design(
        self, monkeypatch
    ):
        """Period-only upgrade on a freshly-bought yearly tariff: total CAN
        legitimately be 0 because the user has already paid for the days in
        the current window (refund eats the fresh period cost). This is the
        intended Phase 2 fairness behavior — NOT a bug like the LTE clamp
        was. The test pins the axes-coherence invariants so a future change
        cannot accidentally re-introduce «period uses LTE-style preservation»
        semantics by mistake.
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89021,
            balance=200_000,
            hwid_limit=1,
            lte_gb_total=10,
            lte_price_per_gb=1.5,
            days_remaining=350,
            price=1800,
            months=12,
        )
        await _setup_four_prod_skus(base_id=90100)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=1,
                target_lte_gb=10,
                target_extra_days=10,   # +10 days
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        # No LTE / device delta → those stay 0 (axis isolation).
        assert result["lte_extra_cost_rub"] == 0
        assert result["device_extra_cost_rub"] == 0
        # total may be 0 by Phase 2 design when fresh_period < refund.
        assert result["total_extra_cost_rub"] >= 0
        # Axes sum to total — coherence even when total == 0.
        assert (
            result["device_extra_cost_rub"]
            + result["lte_extra_cost_rub"]
            + result["period_extra_cost_rub"]
        ) == result["total_extra_cost_rub"]

    @pytest.mark.asyncio
    async def test_fresh_mode_devices_plus_lte_lte_never_loses_to_refund(
        self, monkeypatch
    ):
        """When devices + LTE are upgraded together on a fresh paid tariff,
        the LTE component is ALWAYS the live linear cost — refund only
        reduces the non-LTE (device) component, never the LTE one. This is
        the explicit invariant the bug-fix establishes.
        """
        import bloobcat.services.upgrade_quote as _uq
        from bloobcat.routes.user import UpgradeBundleRequest, upgrade_bundle

        _silence_side_effects(monkeypatch)
        monkeypatch.setattr(_uq, "_upgrade_fresh_mode_enabled", lambda: True)
        user, _active, _tariff = await _setup_user_with_active_tariff(
            user_id=89022,
            balance=200_000,
            hwid_limit=1,
            lte_gb_total=0,
            lte_price_per_gb=1.5,
            days_remaining=350,
            price=1800,
            months=12,
        )
        await _setup_four_prod_skus(base_id=90200)

        result = await upgrade_bundle(
            payload=UpgradeBundleRequest(
                target_device_count=2,
                target_lte_gb=20,
                target_extra_days=0,
                dry_run=True,
            ),
            user=user,
        )

        assert result["pricing_mode"] == "fresh_minus_refund"
        # LTE = 20 × 1.5 = 30, ALWAYS, regardless of refund eating devices.
        assert result["lte_extra_cost_rub"] == 30
        assert result["period_extra_cost_rub"] == 0
        # total = lte + non_lte_after_refund (device only here).
        assert result["total_extra_cost_rub"] >= result["lte_extra_cost_rub"]
        assert (
            result["device_extra_cost_rub"]
            + result["lte_extra_cost_rub"]
            + result["period_extra_cost_rub"]
        ) == result["total_extra_cost_rub"]
