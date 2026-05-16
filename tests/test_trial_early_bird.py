from __future__ import annotations

from datetime import date, timedelta

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
def _enable_early_bird(monkeypatch):
    """Most tests need the feature flag on; one explicitly disables it."""
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "trial_early_bird_enabled", True)
    monkeypatch.setattr(app_settings, "trial_early_bird_percent", 50)


async def _make_user(
    *,
    user_id: int = 9001,
    is_partner: bool = False,
    trial_expiry_days: int = 10,
    expired_at: date | None = "use_default",
):
    from bloobcat.db.users import Users

    if expired_at == "use_default":
        expired_at = date.today() + timedelta(days=trial_expiry_days)

    return await Users.create(
        id=user_id,
        username=f"eb-{user_id}",
        full_name="Early Bird User",
        is_registered=True,
        is_partner=is_partner,
        used_trial=True,
        is_trial=True,
        balance=0,
        expired_at=expired_at,
    )


@pytest.mark.asyncio
async def test_grant_creates_discount_when_enabled():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.services.trial_early_bird import (
        TRIAL_EARLY_BIRD_DISCOUNT_SOURCE,
        grant_trial_early_bird_discount,
    )

    user = await _make_user()
    discount = await grant_trial_early_bird_discount(user)

    assert discount is not None
    assert discount.percent == 50
    assert discount.source == TRIAL_EARLY_BIRD_DISCOUNT_SOURCE
    assert discount.max_months is None
    assert discount.min_months is None
    assert discount.remaining_uses == 1
    assert discount.is_permanent is False
    assert discount.expires_at == user.expired_at

    persisted = await PersonalDiscount.get(id=discount.id)
    assert persisted.user_id == user.id


@pytest.mark.asyncio
async def test_grant_skipped_when_disabled(monkeypatch):
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.services.trial_early_bird import (
        TRIAL_EARLY_BIRD_DISCOUNT_SOURCE,
        grant_trial_early_bird_discount,
    )
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "trial_early_bird_enabled", False)

    user = await _make_user(user_id=9101)
    discount = await grant_trial_early_bird_discount(user)

    assert discount is None
    assert (
        await PersonalDiscount.filter(
            user_id=user.id, source=TRIAL_EARLY_BIRD_DISCOUNT_SOURCE
        ).count()
        == 0
    )


@pytest.mark.asyncio
async def test_grant_skipped_for_partner():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.services.trial_early_bird import (
        TRIAL_EARLY_BIRD_DISCOUNT_SOURCE,
        grant_trial_early_bird_discount,
    )

    partner = await _make_user(user_id=9201, is_partner=True)
    discount = await grant_trial_early_bird_discount(partner)

    assert discount is None
    assert (
        await PersonalDiscount.filter(
            user_id=partner.id, source=TRIAL_EARLY_BIRD_DISCOUNT_SOURCE
        ).count()
        == 0
    )


@pytest.mark.asyncio
async def test_grant_skipped_for_referral_invite():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.services.trial_early_bird import (
        TRIAL_EARLY_BIRD_DISCOUNT_SOURCE,
        grant_trial_early_bird_discount,
    )

    user = await _make_user(user_id=9301)
    discount = await grant_trial_early_bird_discount(user, is_referral_invite=True)

    assert discount is None
    assert (
        await PersonalDiscount.filter(
            user_id=user.id, source=TRIAL_EARLY_BIRD_DISCOUNT_SOURCE
        ).count()
        == 0
    )


@pytest.mark.asyncio
async def test_grant_idempotent():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.services.trial_early_bird import (
        TRIAL_EARLY_BIRD_DISCOUNT_SOURCE,
        grant_trial_early_bird_discount,
    )

    user = await _make_user(user_id=9401)
    first = await grant_trial_early_bird_discount(user)
    assert first is not None

    second = await grant_trial_early_bird_discount(user)
    assert second is not None
    assert second.id == first.id

    count = await PersonalDiscount.filter(
        user_id=user.id, source=TRIAL_EARLY_BIRD_DISCOUNT_SOURCE
    ).count()
    assert count == 1


@pytest.mark.asyncio
async def test_grant_uses_trial_expiry_date():
    from bloobcat.services.trial_early_bird import grant_trial_early_bird_discount

    custom_expiry = date.today() + timedelta(days=7)
    user = await _make_user(user_id=9501, expired_at=custom_expiry)

    discount = await grant_trial_early_bird_discount(user)
    assert discount is not None
    assert discount.expires_at == custom_expiry


@pytest.mark.asyncio
async def test_state_endpoint_returns_active_when_present():
    from bloobcat.services.trial_early_bird import (
        get_trial_early_bird_state_payload,
        grant_trial_early_bird_discount,
    )

    user = await _make_user(user_id=9601)
    await grant_trial_early_bird_discount(user)

    payload = await get_trial_early_bird_state_payload(user)
    assert payload["active"] is True
    assert payload["percent"] == 50
    assert payload["used"] is False
    assert payload["expires_at_ms"] is not None


@pytest.mark.asyncio
async def test_state_endpoint_returns_inactive_when_expired():
    from bloobcat.services.trial_early_bird import (
        get_trial_early_bird_state_payload,
        grant_trial_early_bird_discount,
    )

    expired_date = date.today() - timedelta(days=1)
    # Construct a user with an already-past expiry; bypass the
    # trial_expiry guard by setting it to a real past date.
    user = await _make_user(user_id=9701, expired_at=expired_date)

    discount = await grant_trial_early_bird_discount(user)
    assert discount is not None
    assert discount.expires_at == expired_date

    payload = await get_trial_early_bird_state_payload(user)
    assert payload["active"] is False
    assert payload["used"] is False  # still has uses, but expired
    assert payload["expires_at_ms"] is not None


@pytest.mark.asyncio
async def test_state_endpoint_returns_inactive_when_used():
    from bloobcat.services.trial_early_bird import (
        get_trial_early_bird_state_payload,
        grant_trial_early_bird_discount,
    )

    user = await _make_user(user_id=9801)
    discount = await grant_trial_early_bird_discount(user)
    assert discount is not None

    # Simulate consumption at checkout.
    await discount.consume_one()

    payload = await get_trial_early_bird_state_payload(user)
    assert payload["active"] is False
    assert payload["used"] is True


@pytest.mark.asyncio
async def test_state_endpoint_returns_inactive_when_no_discount():
    from bloobcat.services.trial_early_bird import (
        get_trial_early_bird_state_payload,
    )

    user = await _make_user(user_id=9851)
    # No discount granted.
    payload = await get_trial_early_bird_state_payload(user)
    assert payload["active"] is False
    assert payload["used"] is False
    assert payload["expires_at_ms"] is None
    assert payload["percent"] == 50  # falls back to settings default


@pytest.mark.asyncio
async def test_grant_skipped_when_no_trial_expiry():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.services.trial_early_bird import (
        TRIAL_EARLY_BIRD_DISCOUNT_SOURCE,
        grant_trial_early_bird_discount,
    )

    user = await _make_user(user_id=9901, expired_at=None)
    discount = await grant_trial_early_bird_discount(user)

    assert discount is None
    assert (
        await PersonalDiscount.filter(
            user_id=user.id, source=TRIAL_EARLY_BIRD_DISCOUNT_SOURCE
        ).count()
        == 0
    )


def test_route_adapter_emits_camelcase():
    """Service uses snake_case internally; the HTTP route adapter must
    convert to camelCase to match the FE contract (matches the convention
    used by /reverse-trial/state and /referrals/golden/payouts).
    """
    from bloobcat.routes.trial_early_bird import _service_payload_to_response

    response = _service_payload_to_response(
        {
            "active": True,
            "percent": 50,
            "expires_at_ms": 1737604800000,
            "used": False,
        }
    )
    dumped = response.model_dump()
    assert dumped["active"] is True
    assert dumped["percent"] == 50
    assert "expiresAtMs" in dumped and "expires_at_ms" not in dumped
    assert dumped["expiresAtMs"] == 1737604800000
    assert dumped["used"] is False

    inactive_response = _service_payload_to_response(
        {
            "active": False,
            "percent": 50,
            "expires_at_ms": None,
            "used": True,
        }
    )
    dumped_inactive = inactive_response.model_dump()
    assert dumped_inactive["active"] is False
    assert dumped_inactive["expiresAtMs"] is None
    assert dumped_inactive["used"] is True
