"""Golden Period — eligibility, activation, payout idempotency, cap race."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

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
    """Replace all golden_period notification dispatchers with no-ops for tests."""
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
        # Each module exposes a single `notify_golden_period_*` function.
        fn_name = (
            "notify_golden_period_"
            + module_path.rsplit(".", 1)[-1]
        )

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


async def _enable_config(*, cap: int = 15, payout: int = 100, eligibility: int = 3):
    from bloobcat.db.golden_period import GoldenPeriodConfig

    cfg = await GoldenPeriodConfig.create(
        slug="default",
        is_enabled=True,
        default_cap=cap,
        payout_amount_rub=payout,
        eligibility_min_active_days=eligibility,
    )
    return cfg


async def _disable_config():
    from bloobcat.db.golden_period import GoldenPeriodConfig

    cfg = await GoldenPeriodConfig.create(slug="default", is_enabled=False)
    return cfg


async def _make_user(
    *,
    user_id: int = 5001,
    is_partner: bool = False,
    balance: int = 0,
    key_activated: bool = True,
    referred_by: int | None = None,
    expired_at: date | None = None,
):
    from bloobcat.db.users import Users

    return await Users.create(
        id=user_id,
        username=f"gp-{user_id}",
        full_name="Golden User",
        is_registered=True,
        is_partner=is_partner,
        balance=balance,
        key_activated=key_activated,
        referred_by=referred_by,
        expired_at=expired_at,
    )


async def _seed_active_days(user_id: int, count: int):
    from bloobcat.db.connections import Connections

    today = date.today()
    for i in range(count):
        await Connections.create(user_id=int(user_id), at=today - timedelta(days=i))


@pytest.mark.asyncio
async def test_eligibility_requires_3_active_days():
    from bloobcat.db.connections import Connections
    from bloobcat.services.golden_period import (
        invalidate_cumulative_active_days_cache,
        maybe_activate_golden_period,
    )

    await _enable_config()
    user = await _make_user(user_id=6001)
    # Seed 2 distinct days first
    today = date.today()
    await Connections.create(user_id=int(user.id), at=today)
    await Connections.create(user_id=int(user.id), at=today - timedelta(days=1))
    period = await maybe_activate_golden_period(user)
    assert period is None

    # Add a third day to cross the threshold
    await Connections.create(user_id=int(user.id), at=today - timedelta(days=2))
    invalidate_cumulative_active_days_cache(user.id)

    period = await maybe_activate_golden_period(user)
    assert period is not None
    assert period.status == "active"
    assert period.cap == 15
    assert period.payout_amount_rub == 100


@pytest.mark.asyncio
async def test_activation_skipped_when_disabled():
    from bloobcat.services.golden_period import maybe_activate_golden_period

    await _disable_config()
    user = await _make_user(user_id=6002)
    await _seed_active_days(user.id, 5)
    period = await maybe_activate_golden_period(user)
    assert period is None


@pytest.mark.asyncio
async def test_activation_skipped_for_partner():
    from bloobcat.services.golden_period import maybe_activate_golden_period

    await _enable_config()
    user = await _make_user(user_id=6003, is_partner=True)
    await _seed_active_days(user.id, 5)
    period = await maybe_activate_golden_period(user)
    assert period is None


@pytest.mark.asyncio
async def test_activation_skipped_when_already_has_period():
    from bloobcat.services.golden_period import maybe_activate_golden_period

    await _enable_config()
    user = await _make_user(user_id=6004)
    await _seed_active_days(user.id, 5)
    first = await maybe_activate_golden_period(user)
    assert first is not None

    second = await maybe_activate_golden_period(user)
    assert second is None


@pytest.mark.asyncio
async def test_optimistic_payout_credits_balance():
    from bloobcat.db.users import Users
    from bloobcat.services.golden_period import (
        attempt_optimistic_payout,
        maybe_activate_golden_period,
    )

    await _enable_config()
    referrer = await _make_user(user_id=7001, balance=0)
    await _seed_active_days(referrer.id, 5)
    period = await maybe_activate_golden_period(referrer)
    assert period is not None

    referred = await _make_user(user_id=7002, key_activated=True)
    res = await attempt_optimistic_payout(referrer=referrer, referred=referred)
    assert res["applied"] is True
    assert res["amount_rub"] == 100
    refreshed = await Users.get(id=referrer.id)
    assert refreshed.balance == 100


@pytest.mark.asyncio
async def test_optimistic_payout_idempotent_on_referred_user():
    from bloobcat.db.users import Users
    from bloobcat.services.golden_period import (
        attempt_optimistic_payout,
        maybe_activate_golden_period,
    )

    await _enable_config()
    referrer = await _make_user(user_id=7011, balance=0)
    await _seed_active_days(referrer.id, 5)
    await maybe_activate_golden_period(referrer)
    referred = await _make_user(user_id=7012)

    first = await attempt_optimistic_payout(referrer=referrer, referred=referred)
    assert first["applied"] is True

    second = await attempt_optimistic_payout(referrer=referrer, referred=referred)
    assert second["applied"] is False
    assert second["reason"] == "duplicate_referred"
    refreshed = await Users.get(id=referrer.id)
    assert refreshed.balance == 100  # not double-credited


@pytest.mark.asyncio
async def test_optimistic_payout_skipped_when_key_not_activated():
    from bloobcat.services.golden_period import (
        attempt_optimistic_payout,
        maybe_activate_golden_period,
    )

    await _enable_config()
    referrer = await _make_user(user_id=7021)
    await _seed_active_days(referrer.id, 5)
    await maybe_activate_golden_period(referrer)
    referred = await _make_user(user_id=7022, key_activated=False)

    res = await attempt_optimistic_payout(referrer=referrer, referred=referred)
    assert res["applied"] is False
    assert res["reason"] == "referred_not_activated"


@pytest.mark.asyncio
async def test_optimistic_payout_skipped_when_cap_reached():
    from bloobcat.db.golden_period import GoldenPeriod
    from bloobcat.services.golden_period import (
        attempt_optimistic_payout,
        maybe_activate_golden_period,
    )

    await _enable_config(cap=2)
    referrer = await _make_user(user_id=7031)
    await _seed_active_days(referrer.id, 5)
    await maybe_activate_golden_period(referrer)

    for i in range(3):
        referred = await _make_user(user_id=7040 + i)
        res = await attempt_optimistic_payout(referrer=referrer, referred=referred)
        if i < 2:
            assert res["applied"] is True
        else:
            assert res["applied"] is False
            assert res["reason"] == "cap_reached"

    period = await GoldenPeriod.get(user_id=referrer.id)
    assert period.paid_out_count == 2


@pytest.mark.asyncio
async def test_cap_enforced_under_concurrency():
    from bloobcat.db.golden_period import GoldenPeriod, GoldenPeriodPayout
    from bloobcat.services.golden_period import (
        attempt_optimistic_payout,
        maybe_activate_golden_period,
    )

    await _enable_config(cap=5)
    referrer = await _make_user(user_id=7100)
    await _seed_active_days(referrer.id, 5)
    await maybe_activate_golden_period(referrer)

    # Create 20 invitees and try them all in parallel.
    invitees = []
    for i in range(20):
        u = await _make_user(user_id=7200 + i)
        invitees.append(u)

    results = await asyncio.gather(
        *[
            attempt_optimistic_payout(referrer=referrer, referred=u)
            for u in invitees
        ]
    )
    applied = [r for r in results if r["applied"]]
    assert len(applied) == 5

    period = await GoldenPeriod.get(user_id=referrer.id)
    assert period.paid_out_count == 5
    assert period.total_paid_rub == 500

    payouts = await GoldenPeriodPayout.filter(golden_period_id=period.id)
    assert len(payouts) == 5


@pytest.mark.asyncio
async def test_self_referral_rejected():
    from bloobcat.services.golden_period import (
        attempt_optimistic_payout,
        maybe_activate_golden_period,
    )

    await _enable_config()
    user = await _make_user(user_id=7300)
    await _seed_active_days(user.id, 5)
    await maybe_activate_golden_period(user)
    res = await attempt_optimistic_payout(referrer=user, referred=user)
    assert res["applied"] is False
    assert res["reason"] == "self_referral"


@pytest.mark.asyncio
async def test_status_payload_anonymizes_invitees():
    from bloobcat.services.golden_period import (
        attempt_optimistic_payout,
        build_golden_period_status,
        maybe_activate_golden_period,
    )

    await _enable_config(cap=3)
    referrer = await _make_user(user_id=7400)
    await _seed_active_days(referrer.id, 5)
    await maybe_activate_golden_period(referrer)

    referred = await _make_user(user_id=7401)
    referred.username = "longnameuser"
    await referred.save()
    await attempt_optimistic_payout(referrer=referrer, referred=referred)

    payload = await build_golden_period_status(referrer)
    assert payload is not None
    assert payload["active"] is True
    assert payload["paidOutCount"] == 1
    invitees = payload["invitees"]
    assert len(invitees) == 1
    handle = invitees[0]["displayName"]
    assert "longnameuser" not in handle
    assert handle.startswith("@l***")


@pytest.mark.asyncio
async def test_seen_marker_idempotent():
    from bloobcat.db.golden_period import GoldenPeriod
    from bloobcat.services.golden_period import (
        mark_period_seen,
        maybe_activate_golden_period,
    )

    await _enable_config()
    user = await _make_user(user_id=7500)
    await _seed_active_days(user.id, 5)
    period = await maybe_activate_golden_period(user)
    assert period.seen_at is None

    first = await mark_period_seen(user)
    assert first is True
    second = await mark_period_seen(user)
    # Second call returns False because seen_at__isnull filter no longer matches
    assert second is False
    refreshed = await GoldenPeriod.get(id=period.id)
    assert refreshed.seen_at is not None
