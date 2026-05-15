"""Golden Period signal detection — HWID, IP, TG family, registration velocity."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
                        "bloobcat.db.user_devices",
                        "bloobcat.db.hwid_local",
                        "bloobcat.db.push_subscriptions",
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


async def _make_users(*, distance: int = 100, velocity_seconds: int = 3600):
    from bloobcat.db.users import Users

    base_id = 9000
    a = await Users.create(
        id=base_id,
        username="signal-a",
        full_name="Signal A",
        is_registered=True,
        registration_date=datetime.now(timezone.utc) - timedelta(seconds=velocity_seconds),
    )
    b = await Users.create(
        id=base_id + distance,
        username="signal-b",
        full_name="Signal B",
        is_registered=True,
        registration_date=datetime.now(timezone.utc),
    )
    return a, b


@pytest.mark.asyncio
async def test_signals_hwid_overlap_detected():
    from bloobcat.db.user_devices import UserDevice
    from bloobcat.services.cashback_review import detect_golden_overlap_signals

    a, b = await _make_users(distance=100, velocity_seconds=3600)
    await UserDevice.create(
        user_id=a.id, kind="legacy_hwid", hwid="HWID-SHARED", platform="ios"
    )
    await UserDevice.create(
        user_id=b.id, kind="legacy_hwid", hwid="HWID-SHARED", platform="ios"
    )

    signals = await detect_golden_overlap_signals(a, b)
    assert signals["hwid_overlap"] is True
    assert signals["should_clawback"] is True
    assert signals["primary_reason"] == "hwid_overlap"


@pytest.mark.asyncio
async def test_signals_ip_block_overlap_24():
    from bloobcat.db.user_devices import UserDevice
    from bloobcat.services.cashback_review import detect_golden_overlap_signals

    a, b = await _make_users(distance=100, velocity_seconds=3600)
    await UserDevice.create(
        user_id=a.id,
        kind="legacy_hwid",
        hwid="HW-A",
        platform="ios",
        metadata={"ip": "203.0.113.10"},
    )
    await UserDevice.create(
        user_id=b.id,
        kind="legacy_hwid",
        hwid="HW-B",
        platform="ios",
        metadata={"ip": "203.0.113.99"},  # same /24
    )

    signals = await detect_golden_overlap_signals(a, b)
    assert signals["ip_block_overlap"] is True
    # No HWID overlap, so primary should be ip_block (or higher-priority signal absent).
    assert signals["should_clawback"] is True
    assert signals["primary_reason"] in {"ip_block", "tg_family"}


@pytest.mark.asyncio
async def test_signals_tg_id_family_proximity():
    from bloobcat.services.cashback_review import detect_golden_overlap_signals

    # distance=2 < default threshold of 5
    a, b = await _make_users(distance=2, velocity_seconds=3600)
    signals = await detect_golden_overlap_signals(a, b)
    assert signals["tg_id_family"] is True
    assert signals["should_clawback"] is True


@pytest.mark.asyncio
async def test_signals_registration_velocity_alone_does_not_trigger():
    """Velocity is the weakest signal — only fires `should_clawback` when paired."""
    from bloobcat.services.cashback_review import detect_golden_overlap_signals

    # distance=100 (no tg_family) + velocity=10s (under default 60)
    a, b = await _make_users(distance=100, velocity_seconds=10)
    signals = await detect_golden_overlap_signals(a, b)
    # The raw `registration_velocity` flag is True because the threshold is
    # tripped, but the decision logic intentionally requires a stronger
    # co-signal before deciding to clawback.
    assert signals["registration_velocity"] is True
    assert signals["should_clawback"] is False


@pytest.mark.asyncio
async def test_signals_no_overlap_no_clawback():
    from bloobcat.services.cashback_review import detect_golden_overlap_signals

    a, b = await _make_users(distance=100, velocity_seconds=3600)
    signals = await detect_golden_overlap_signals(a, b)
    assert signals["hwid_overlap"] is False
    assert signals["ip_block_overlap"] is False
    assert signals["device_fingerprint_overlap"] is False
    assert signals["tg_id_family"] is False
    assert signals["registration_velocity"] is False
    assert signals["should_clawback"] is False
    assert signals["primary_reason"] == "none"


@pytest.mark.asyncio
async def test_signals_thresholds_override_via_kwarg():
    """Custom thresholds passed in widen / narrow the trigger window."""
    from bloobcat.services.cashback_review import detect_golden_overlap_signals

    # distance=10 — outside default tg_id_distance=5, inside threshold=20
    a, b = await _make_users(distance=10, velocity_seconds=3600)
    default = await detect_golden_overlap_signals(a, b)
    assert default["tg_id_family"] is False

    custom = await detect_golden_overlap_signals(
        a, b, thresholds={"tg_id_distance": 20}
    )
    assert custom["tg_id_family"] is True


@pytest.mark.asyncio
async def test_signals_same_user_id_treated_as_strong_clawback():
    from bloobcat.services.cashback_review import detect_golden_overlap_signals

    a, _ = await _make_users()
    # Same user passed for both = should_clawback shortcut
    signals = await detect_golden_overlap_signals(a, a)
    assert signals["should_clawback"] is True
    assert signals["primary_reason"] == "hwid_overlap"
