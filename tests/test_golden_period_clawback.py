"""Golden Period clawback math + audit fields + protected expired_at floor."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from tortoise import Tortoise

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _sqlite_datetime_compat import register_sqlite_datetime_compat

from tests.test_payments_no_yookassa import install_stubs


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
    register_sqlite_datetime_compat()
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
                        "bloobcat.db.referral_rewards",
                        "bloobcat.db.connections",
                        "bloobcat.db.golden_period",
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


@pytest.fixture(autouse=True)
def _stub_notifications(monkeypatch):
    import sys
    import types

    for module_path in (
        "bloobcat.bot.notifications.golden_period.activated",
        "bloobcat.bot.notifications.golden_period.payout",
        "bloobcat.bot.notifications.golden_period.cap_reached",
        "bloobcat.bot.notifications.golden_period.expired",
        "bloobcat.bot.notifications.golden_period.clawback",
    ):
        mod = types.ModuleType(module_path)
        fn_name = "notify_golden_period_" + module_path.rsplit(".", 1)[-1]

        async def _noop(*args, **kwargs):
            return None

        setattr(mod, fn_name, _noop)
        sys.modules[module_path] = mod


@pytest.fixture(autouse=True)
def _reset_active_days_cache():
    from bloobcat.services.golden_period import (
        invalidate_cumulative_active_days_cache,
    )

    invalidate_cumulative_active_days_cache()


async def _setup_payout(
    *,
    referrer_balance: int = 0,
    has_active_tariff: bool = False,
    tariff_price: int = 600,
    tariff_months: int = 1,
    lte_gb_total: int = 0,
    expired_at_offset_days: int = 30,
):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.golden_period import (
        GoldenPeriod,
        GoldenPeriodConfig,
        GoldenPeriodPayout,
    )
    from bloobcat.db.users import Users

    await GoldenPeriodConfig.create(slug="default", is_enabled=True, default_cap=15)

    today = date.today()
    referrer = await Users.create(
        id=8001,
        username="cb-ref",
        full_name="Clawback Referrer",
        is_registered=True,
        balance=referrer_balance,
        expired_at=today + timedelta(days=expired_at_offset_days),
    )
    if has_active_tariff:
        active = await ActiveTariffs.create(
            id="GP001",
            user_id=referrer.id,
            name="test-tariff",
            months=tariff_months,
            price=tariff_price,
            lte_gb_total=lte_gb_total,
        )
        referrer.active_tariff_id = active.id
        await referrer.save()

    referred = await Users.create(
        id=8002,
        username="cb-friend",
        full_name="Clawback Friend",
        is_registered=True,
        key_activated=True,
    )

    period = await GoldenPeriod.create(
        user_id=referrer.id,
        started_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        cap=15,
        payout_amount_rub=100,
        paid_out_count=1,
        total_paid_rub=100,
        triggered_by_active_days=3,
        status="active",
    )

    payout = await GoldenPeriodPayout.create(
        golden_period_id=period.id,
        referrer_user_id=referrer.id,
        referred_user_id=referred.id,
        amount_rub=100,
        status="optimistic",
    )
    return referrer, referred, period, payout


@pytest.mark.asyncio
async def test_clawback_balance_only():
    """Referrer has enough balance — 100₽ withdrawn from balance, expired_at unchanged."""
    from bloobcat.db.golden_period import GoldenPeriod, GoldenPeriodPayout
    from bloobcat.db.users import Users
    from bloobcat.services.golden_period_clawback import clawback_payout

    referrer, _, period, payout = await _setup_payout(referrer_balance=200)
    today_expires = referrer.expired_at

    signals = {
        "should_clawback": True,
        "primary_reason": "hwid_overlap",
        "snapshot": {"hwid_overlap_count": 1},
    }
    ok = await clawback_payout(payout, signals)
    assert ok is True

    refreshed_user = await Users.get(id=referrer.id)
    assert refreshed_user.balance == 100  # 200 - 100
    assert refreshed_user.expired_at == today_expires  # unchanged

    refreshed_payout = await GoldenPeriodPayout.get(id=payout.id)
    assert refreshed_payout.status == "clawed_back"
    assert refreshed_payout.clawback_balance_rub == 100
    assert refreshed_payout.clawback_days_removed in (None, 0)
    assert refreshed_payout.clawback_reason == "hwid_overlap"

    refreshed_period = await GoldenPeriod.get(id=period.id)
    assert refreshed_period.paid_out_count == 0
    assert refreshed_period.total_paid_rub == 0


@pytest.mark.asyncio
async def test_clawback_partial_refund():
    """Referrer balance has 60₽ — 60 from balance + 40 → days conversion."""
    from bloobcat.db.golden_period import GoldenPeriodPayout
    from bloobcat.db.users import Users
    from bloobcat.services.golden_period_clawback import clawback_payout

    referrer, _, _, payout = await _setup_payout(
        referrer_balance=60,
        has_active_tariff=True,
        tariff_price=600,  # → 20₽/day
        tariff_months=1,
    )

    signals = {
        "should_clawback": True,
        "primary_reason": "ip_block",
        "snapshot": {"ip_blocks_overlap": ["1.2.3.0"]},
    }
    ok = await clawback_payout(payout, signals)
    assert ok is True

    refreshed_user = await Users.get(id=referrer.id)
    assert refreshed_user.balance == 0  # 60 - 60

    refreshed_payout = await GoldenPeriodPayout.get(id=payout.id)
    assert refreshed_payout.clawback_balance_rub == 60
    # 40₽ remainder / 20₽/day = 2 days
    assert refreshed_payout.clawback_days_removed == 2


@pytest.mark.asyncio
async def test_clawback_full_refund_via_tariff():
    """Balance=0 → 100₽ → days + LTE proportional."""
    from bloobcat.db.golden_period import GoldenPeriodPayout
    from bloobcat.db.users import Users
    from bloobcat.services.golden_period_clawback import clawback_payout

    referrer, _, _, payout = await _setup_payout(
        referrer_balance=0,
        has_active_tariff=True,
        tariff_price=900,  # 1mo / 30d → 30₽/day → 100₽ ≈ 4 days (math.ceil)
        tariff_months=1,
        lte_gb_total=30,
    )

    signals = {
        "should_clawback": True,
        "primary_reason": "device_fp",
        "snapshot": {"device_fingerprint_overlap": ["abc123"]},
    }
    ok = await clawback_payout(payout, signals)
    assert ok is True

    refreshed_user = await Users.get(id=referrer.id)
    assert refreshed_user.balance == 0

    refreshed_payout = await GoldenPeriodPayout.get(id=payout.id)
    assert refreshed_payout.clawback_balance_rub == 0
    assert refreshed_payout.clawback_days_removed == 4
    assert refreshed_payout.clawback_lte_gb_removed is not None
    assert refreshed_payout.clawback_lte_gb_removed > Decimal("0")


@pytest.mark.asyncio
async def test_clawback_audit_payload_persisted():
    """The full signals dict survives as clawback_payload JSON."""
    from bloobcat.db.golden_period import GoldenPeriodPayout
    from bloobcat.services.golden_period_clawback import clawback_payout

    _, _, _, payout = await _setup_payout(referrer_balance=200)

    signals_payload = {
        "hwid_overlap": True,
        "ip_block_overlap": True,
        "should_clawback": True,
        "primary_reason": "hwid_overlap",
        "snapshot": {
            "hwid_overlap_count": 2,
            "ip_blocks_overlap": ["10.0.0.0", "192.168.1.0"],
            "tg_id_distance": 3,
            "registration_velocity_seconds": 12,
            "thresholds": {"ip_cidr": 24},
        },
    }
    await clawback_payout(payout, signals_payload)

    refreshed = await GoldenPeriodPayout.get(id=payout.id)
    assert refreshed.clawback_payload is not None
    persisted_snapshot = refreshed.clawback_payload.get("hwid_overlap_count")
    assert persisted_snapshot == 2  # snapshot hoisted from signals.snapshot
    assert refreshed.clawback_reason == "hwid_overlap"


@pytest.mark.asyncio
async def test_clawback_protects_expired_at_floor():
    """Even with a huge clawback, expired_at can't drop below today + trial_days/2."""
    from bloobcat.db.users import Users
    from bloobcat.services.golden_period_clawback import clawback_payout
    from bloobcat.settings import app_settings

    referrer, _, _, payout = await _setup_payout(
        referrer_balance=0,
        has_active_tariff=True,
        tariff_price=30,  # 1mo / 30d → 1₽/day, so 100₽ → 100 days
        tariff_months=1,
        expired_at_offset_days=10,  # only 10 days left
    )

    signals = {
        "should_clawback": True,
        "primary_reason": "hwid_overlap",
        "snapshot": {},
    }
    await clawback_payout(payout, signals)

    floor = date.today() + timedelta(days=int(app_settings.trial_days // 2))
    refreshed = await Users.get(id=referrer.id)
    assert refreshed.expired_at >= floor


@pytest.mark.asyncio
async def test_clawback_idempotent_on_already_clawed_back():
    """Calling clawback twice is a no-op the second time."""
    from bloobcat.db.golden_period import GoldenPeriodPayout
    from bloobcat.db.users import Users
    from bloobcat.services.golden_period_clawback import clawback_payout

    referrer, _, _, payout = await _setup_payout(referrer_balance=200)
    signals = {
        "should_clawback": True,
        "primary_reason": "hwid_overlap",
        "snapshot": {},
    }
    first = await clawback_payout(payout, signals)
    assert first is True

    refreshed = await GoldenPeriodPayout.get(id=payout.id)
    second = await clawback_payout(refreshed, signals)
    assert second is False  # already clawed back

    user_after = await Users.get(id=referrer.id)
    assert user_after.balance == 100  # not double-deducted


@pytest.mark.asyncio
async def test_reinstate_payout_restores_balance_and_counters():
    """Admin reinstate refunds balance and bumps period counters back."""
    from bloobcat.db.golden_period import GoldenPeriod, GoldenPeriodPayout
    from bloobcat.db.users import Users
    from bloobcat.services.golden_period_clawback import (
        clawback_payout,
        reinstate_payout,
    )

    referrer, _, period, payout = await _setup_payout(referrer_balance=200)
    signals = {
        "should_clawback": True,
        "primary_reason": "hwid_overlap",
        "snapshot": {},
    }
    await clawback_payout(payout, signals)

    user_after_clawback = await Users.get(id=referrer.id)
    assert user_after_clawback.balance == 100

    ok = await reinstate_payout(payout.id)
    assert ok is True

    refreshed = await GoldenPeriodPayout.get(id=payout.id)
    assert refreshed.status == "optimistic"
    assert refreshed.clawback_reason is None

    user_after_reinstate = await Users.get(id=referrer.id)
    assert user_after_reinstate.balance == 200  # refund restored

    refreshed_period = await GoldenPeriod.get(id=period.id)
    assert refreshed_period.paid_out_count == 1
    assert refreshed_period.total_paid_rub == 100
