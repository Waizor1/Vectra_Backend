from __future__ import annotations

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
                        "bloobcat.db.reverse_trial",
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
def _enable_reverse_trial(monkeypatch):
    """Most tests need the feature flag on; one explicitly disables it."""
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "reverse_trial_enabled", True)
    monkeypatch.setattr(app_settings, "reverse_trial_days", 7)
    monkeypatch.setattr(app_settings, "reverse_trial_discount_percent", 50)
    monkeypatch.setattr(app_settings, "reverse_trial_discount_ttl_days", 14)
    monkeypatch.setattr(app_settings, "reverse_trial_tariff_sku", "")


@pytest.fixture(autouse=True)
def _stub_reverse_trial_notifications(monkeypatch):
    """Notifications hit the bot stack — replace with no-ops for tests."""
    import sys
    import types

    granted_mod = types.ModuleType("bloobcat.bot.notifications.reverse_trial.granted")

    async def notify_reverse_trial_granted(*args, **kwargs):
        return None

    granted_mod.notify_reverse_trial_granted = notify_reverse_trial_granted
    sys.modules["bloobcat.bot.notifications.reverse_trial.granted"] = granted_mod

    downgraded_mod = types.ModuleType(
        "bloobcat.bot.notifications.reverse_trial.downgraded"
    )

    async def notify_reverse_trial_downgraded(*args, **kwargs):
        return None

    downgraded_mod.notify_reverse_trial_downgraded = notify_reverse_trial_downgraded
    sys.modules["bloobcat.bot.notifications.reverse_trial.downgraded"] = downgraded_mod

    pre_expiry_mod = types.ModuleType(
        "bloobcat.bot.notifications.reverse_trial.pre_expiry"
    )

    async def notify_reverse_trial_pre_expiry(*args, **kwargs):
        return None

    pre_expiry_mod.notify_reverse_trial_pre_expiry = notify_reverse_trial_pre_expiry
    sys.modules["bloobcat.bot.notifications.reverse_trial.pre_expiry"] = pre_expiry_mod


async def _make_user(*, user_id: int = 9001, is_partner: bool = False, used_trial: bool = False):
    from bloobcat.db.users import Users

    return await Users.create(
        id=user_id,
        username=f"rt-{user_id}",
        full_name="Reverse Trial User",
        is_registered=True,
        is_partner=is_partner,
        used_trial=used_trial,
        balance=0,
    )


async def _make_top_tariff(name: str = "premium_12m"):
    from bloobcat.db.tariff import Tariffs

    return await Tariffs.create(
        name=name,
        months=12,
        base_price=2400,
        progressive_multiplier=0.9,
        order=1,
        is_active=True,
        devices_limit_default=3,
        devices_limit_family=10,
        family_plan_enabled=True,
        lte_enabled=True,
        lte_price_per_gb=1.5,
        lte_min_gb=0,
        lte_max_gb=500,
        lte_step_gb=1,
    )


@pytest.mark.asyncio
async def test_grant_reverse_trial_creates_state_and_synthetic_tariff():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.db.users import Users
    from bloobcat.services.reverse_trial import grant_reverse_trial

    await _make_top_tariff()
    user = await _make_user()

    state = await grant_reverse_trial(user)

    assert state is not None
    assert state.status == "active"
    assert state.tariff_sku_snapshot == "premium_12m"
    assert state.tariff_active_id_snapshot is not None
    assert state.expires_at is not None
    expected_expiry = datetime.now(timezone.utc) + timedelta(days=7)
    delta = abs((state.expires_at - expected_expiry).total_seconds())
    assert delta < 60, f"expires_at off by {delta}s"

    persisted = await ReverseTrialState.get(user_id=user.id)
    assert persisted.id == state.id

    refreshed = await Users.get(id=user.id)
    assert refreshed.is_trial is False
    assert refreshed.used_trial is True
    assert refreshed.active_tariff_id == state.tariff_active_id_snapshot
    assert refreshed.expired_at == state.expires_at.date()
    assert refreshed.hwid_limit == 3
    assert refreshed.lte_gb_total == 500

    synthetic = await ActiveTariffs.get(id=state.tariff_active_id_snapshot)
    assert synthetic.is_promo_synthetic is True
    assert synthetic.price == 0
    assert synthetic.name == "premium_12m"


@pytest.mark.asyncio
async def test_grant_skipped_when_feature_disabled(monkeypatch):
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.services.reverse_trial import grant_reverse_trial
    from bloobcat.settings import app_settings

    await _make_top_tariff()
    user = await _make_user(user_id=9101)
    monkeypatch.setattr(app_settings, "reverse_trial_enabled", False)

    state = await grant_reverse_trial(user)

    assert state is None
    assert await ReverseTrialState.filter(user_id=user.id).count() == 0


@pytest.mark.asyncio
async def test_grant_skipped_for_partner_user():
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.services.reverse_trial import grant_reverse_trial

    await _make_top_tariff()
    partner = await _make_user(user_id=9201, is_partner=True)

    state = await grant_reverse_trial(partner)

    assert state is None
    assert await ReverseTrialState.filter(user_id=partner.id).count() == 0


@pytest.mark.asyncio
async def test_grant_skipped_when_already_has_state():
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.services.reverse_trial import grant_reverse_trial

    await _make_top_tariff()
    user = await _make_user(user_id=9301)

    first = await grant_reverse_trial(user)
    assert first is not None

    second = await grant_reverse_trial(user)
    assert second is None
    assert await ReverseTrialState.filter(user_id=user.id).count() == 1


@pytest.mark.asyncio
async def test_grant_skipped_for_referral_invite():
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.services.reverse_trial import grant_reverse_trial

    await _make_top_tariff()
    user = await _make_user(user_id=9401)

    state = await grant_reverse_trial(user, is_referral_invite=True)

    assert state is None
    assert await ReverseTrialState.filter(user_id=user.id).count() == 0


@pytest.mark.asyncio
async def test_downgrade_creates_discount_and_resets_user(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.db.users import Users
    from bloobcat.services.reverse_trial import (
        downgrade_expired_reverse_trial,
        grant_reverse_trial,
    )

    # Stub out the trial_lte runtime read (which talks to Directus) with a
    # static fallback equal to AppSettings default.
    async def _fake_read_trial_lte() -> float:
        return 1.0

    import bloobcat.services.reverse_trial as rt_module

    monkeypatch.setattr(rt_module, "read_trial_lte_limit_gb", _fake_read_trial_lte)

    await _make_top_tariff()
    user = await _make_user(user_id=9501)

    state = await grant_reverse_trial(user)
    assert state is not None
    synthetic_id = state.tariff_active_id_snapshot

    # Backdate so the downgrade fires.
    state.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await state.save()

    await downgrade_expired_reverse_trial(state)

    after_state = await ReverseTrialState.get(id=state.id)
    assert after_state.status == "expired"
    assert after_state.discount_personal_id is not None
    assert after_state.downgraded_at is not None

    discount = await PersonalDiscount.get(id=after_state.discount_personal_id)
    assert discount.percent == 50
    assert discount.source == "reverse_trial"
    assert discount.max_months == 1
    assert discount.remaining_uses == 1
    assert discount.expires_at is not None
    expected_expiry = date.today() + timedelta(days=14)
    assert discount.expires_at == expected_expiry

    refreshed = await Users.get(id=user.id)
    assert refreshed.is_trial is True
    assert refreshed.used_trial is True
    assert refreshed.expired_at is None
    assert refreshed.active_tariff_id is None
    assert refreshed.lte_gb_total == 1

    # Synthetic ActiveTariffs row was wiped.
    assert await ActiveTariffs.filter(id=synthetic_id).count() == 0


@pytest.mark.asyncio
async def test_downgrade_idempotent(monkeypatch):
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.services.reverse_trial import (
        downgrade_expired_reverse_trial,
        grant_reverse_trial,
    )

    async def _fake_read_trial_lte() -> float:
        return 1.0

    import bloobcat.services.reverse_trial as rt_module

    monkeypatch.setattr(rt_module, "read_trial_lte_limit_gb", _fake_read_trial_lte)

    await _make_top_tariff()
    user = await _make_user(user_id=9601)
    state = await grant_reverse_trial(user)
    assert state is not None
    state.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await state.save()

    await downgrade_expired_reverse_trial(state)

    discount_count_after_first = await PersonalDiscount.filter(
        user_id=user.id, source="reverse_trial"
    ).count()
    assert discount_count_after_first == 1

    refreshed_state = await ReverseTrialState.get(id=state.id)
    await downgrade_expired_reverse_trial(refreshed_state)

    discount_count_after_second = await PersonalDiscount.filter(
        user_id=user.id, source="reverse_trial"
    ).count()
    assert discount_count_after_second == 1


@pytest.mark.asyncio
async def test_redeem_discount_marks_used(monkeypatch):
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.services.reverse_trial import (
        downgrade_expired_reverse_trial,
        grant_reverse_trial,
        redeem_reverse_trial_discount,
    )

    async def _fake_read_trial_lte() -> float:
        return 1.0

    import bloobcat.services.reverse_trial as rt_module

    monkeypatch.setattr(rt_module, "read_trial_lte_limit_gb", _fake_read_trial_lte)

    await _make_top_tariff()
    user = await _make_user(user_id=9701)
    state = await grant_reverse_trial(user)
    assert state is not None
    state.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await state.save()
    await downgrade_expired_reverse_trial(state)

    first = await redeem_reverse_trial_discount(user)
    assert first["applicable"] is True
    assert first["percent"] == 50
    assert first["discount_id"] is not None

    second = await redeem_reverse_trial_discount(user)
    assert second["applicable"] is False
    assert second["discount_id"] == first["discount_id"]

    refreshed = await ReverseTrialState.get(user_id=user.id)
    assert refreshed.discount_used_at is not None


@pytest.mark.asyncio
async def test_cancel_on_paid_purchase():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.db.users import Users
    from bloobcat.services.reverse_trial import (
        cancel_reverse_trial_on_paid_purchase,
        grant_reverse_trial,
    )

    await _make_top_tariff()
    user = await _make_user(user_id=9801)
    state = await grant_reverse_trial(user)
    assert state is not None
    synthetic_id = state.tariff_active_id_snapshot

    refreshed_user = await Users.get(id=user.id)
    await cancel_reverse_trial_on_paid_purchase(refreshed_user)

    after = await ReverseTrialState.get(id=state.id)
    assert after.status == "converted_to_paid"
    assert after.discount_personal_id is None
    assert (
        await PersonalDiscount.filter(
            user_id=user.id, source="reverse_trial"
        ).count()
        == 0
    )
    assert await ActiveTariffs.filter(id=synthetic_id).count() == 0

    # Idempotent — second call is a no-op.
    refreshed_user_after = await Users.get(id=user.id)
    await cancel_reverse_trial_on_paid_purchase(refreshed_user_after)
    after_again = await ReverseTrialState.get(id=state.id)
    assert after_again.status == "converted_to_paid"


@pytest.mark.asyncio
async def test_get_state_payload_shape(monkeypatch):
    from bloobcat.services.reverse_trial import (
        downgrade_expired_reverse_trial,
        get_reverse_trial_state_payload,
        grant_reverse_trial,
    )

    async def _fake_read_trial_lte() -> float:
        return 1.0

    import bloobcat.services.reverse_trial as rt_module

    monkeypatch.setattr(rt_module, "read_trial_lte_limit_gb", _fake_read_trial_lte)

    await _make_top_tariff()
    user = await _make_user(user_id=9901)

    # Before any grant: status="none".
    pre_payload = await get_reverse_trial_state_payload(user)
    assert pre_payload["status"] == "none"
    assert pre_payload["days_remaining"] == 0
    assert pre_payload["discount"]["available"] is False

    state = await grant_reverse_trial(user)
    assert state is not None

    active_payload = await get_reverse_trial_state_payload(user)
    assert active_payload["status"] == "active"
    assert active_payload["days_remaining"] >= 6
    assert active_payload["days_remaining"] <= 7
    assert active_payload["tariff_name"] == "premium_12m"
    assert active_payload["granted_at_ms"] is not None
    assert active_payload["expires_at_ms"] is not None
    assert active_payload["discount"]["available"] is False
    assert active_payload["discount"]["used"] is False

    # After downgrade: status="expired" and discount becomes available.
    state.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await state.save()
    await downgrade_expired_reverse_trial(state)

    post_payload = await get_reverse_trial_state_payload(user)
    assert post_payload["status"] == "expired"
    assert post_payload["days_remaining"] == 0
    assert post_payload["discount"]["available"] is True
    assert post_payload["discount"]["percent"] == 50
    assert post_payload["discount"]["expires_at_ms"] is not None
    assert post_payload["discount"]["used"] is False


def test_route_adapter_emits_camelcase_and_maps_statuses():
    """Service uses snake_case + 'none'/'converted_to_paid' internally; the
    HTTP route adapter must convert to camelCase + 'absent'/'converted' to
    match the FE contract (see Vectra_Frontend src/hooks/useReverseTrialState).
    """
    from bloobcat.routes.reverse_trial import (
        _service_payload_to_response,
        _service_redeem_to_response,
    )

    none_resp = _service_payload_to_response(
        {
            "status": "none",
            "granted_at_ms": None,
            "expires_at_ms": None,
            "days_remaining": 0,
            "tariff_name": None,
            "discount": {
                "available": False,
                "percent": 50,
                "expires_at_ms": None,
                "used": False,
            },
        }
    )
    dumped = none_resp.model_dump()
    assert dumped["status"] == "absent"
    assert "grantedAtMs" in dumped and "granted_at_ms" not in dumped
    assert "daysRemaining" in dumped and "days_remaining" not in dumped
    assert "tariffName" in dumped and "tariff_name" not in dumped
    assert dumped["discount"]["expiresAtMs"] is None
    assert "expires_at_ms" not in dumped["discount"]

    converted_resp = _service_payload_to_response(
        {
            "status": "converted_to_paid",
            "granted_at_ms": 1737000000000,
            "expires_at_ms": 1737604800000,
            "days_remaining": 0,
            "tariff_name": "premium_12m",
            "discount": {
                "available": False,
                "percent": 0,
                "expires_at_ms": None,
                "used": True,
            },
        }
    )
    dumped_c = converted_resp.model_dump()
    assert dumped_c["status"] == "converted"
    assert dumped_c["grantedAtMs"] == 1737000000000
    assert dumped_c["tariffName"] == "premium_12m"

    redeem_dumped = _service_redeem_to_response(
        {"applicable": True, "discount_id": 42, "percent": 50}
    ).model_dump()
    assert redeem_dumped["applicable"] is True
    assert redeem_dumped["discountId"] == 42
    assert "discount_id" not in redeem_dumped
