import types
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
                        "bloobcat.db.active_tariff",
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
    models_to_create = []
    try:
        maybe_models = generator._get_models_to_create(models_to_create)
        if maybe_models is not None:
            models_to_create = maybe_models
    except TypeError:
        models_to_create = generator._get_models_to_create()
    tables = [generator._get_table_sql(model, safe=True) for model in models_to_create]
    creation_sql = "\n".join([t["table_creation_string"] for t in tables] + [m for t in tables for m in t["m2m_tables"]])
    await generator.generate_from_string(creation_sql)
    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_get_user_scrubs_stale_freezes_after_hard_delete_recreate(monkeypatch):
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users

    async def _ensure_stub(self):
        return True

    monkeypatch.setattr(Users, "_ensure_remnawave_user", _ensure_stub)

    old_user = await Users.create(id=777001, username="old", full_name="Old User")
    await SubscriptionFreezes.create(
        user_id=old_user.id,
        base_remaining_days=7,
        family_expires_at=old_user.created_at.date(),
    )
    await Users.filter(id=old_user.id).delete()

    assert await SubscriptionFreezes.filter(user_id=old_user.id).count() == 1

    telegram_user = types.SimpleNamespace(
        id=old_user.id,
        username="new",
        first_name="New",
        last_name="User",
    )
    recreated_user, is_new = await Users.get_user(telegram_user=telegram_user)

    assert is_new is True
    assert recreated_user is not None
    assert recreated_user.id == old_user.id
    assert await SubscriptionFreezes.filter(user_id=old_user.id).count() == 0


@pytest.mark.asyncio
async def test_recreate_path_returns_clean_overlay_after_stale_freeze_scrub(monkeypatch):
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.services.subscription_overlay import get_overlay_payload

    async def _ensure_stub(self):
        return True

    monkeypatch.setattr(Users, "_ensure_remnawave_user", _ensure_stub)

    old_user = await Users.create(id=777003, username="old-overlay", full_name="Old Overlay")
    await SubscriptionFreezes.create(
        user_id=old_user.id,
        base_remaining_days=11,
        family_expires_at=old_user.created_at.date(),
    )
    await Users.filter(id=old_user.id).delete()

    assert await SubscriptionFreezes.filter(user_id=old_user.id).count() == 1

    telegram_user = types.SimpleNamespace(
        id=old_user.id,
        username="new-overlay",
        first_name="New",
        last_name="Overlay",
    )
    recreated_user, is_new = await Users.get_user(telegram_user=telegram_user)

    assert is_new is True
    assert recreated_user is not None
    assert recreated_user.id == old_user.id
    assert await SubscriptionFreezes.filter(user_id=old_user.id).count() == 0

    overlay = await get_overlay_payload(recreated_user)
    assert overlay == {
        "has_frozen_base": False,
        "active_kind": "base",
    }


@pytest.mark.asyncio
async def test_scrub_stale_freezes_uses_created_at_boundary():
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users

    boundary = datetime.now(timezone.utc)
    user_id = 777002

    stale = await SubscriptionFreezes.create(
        user_id=user_id,
        base_remaining_days=5,
        family_expires_at=date.today(),
    )
    fresh = await SubscriptionFreezes.create(
        user_id=user_id,
        base_remaining_days=6,
        family_expires_at=date.today(),
    )

    await SubscriptionFreezes.filter(id=stale.id).update(created_at=boundary - timedelta(minutes=5))
    await SubscriptionFreezes.filter(id=fresh.id).update(created_at=boundary + timedelta(minutes=5))

    await Users._scrub_stale_subscription_freezes(user_id, created_before=boundary)

    assert await SubscriptionFreezes.filter(id=stale.id).count() == 0
    assert await SubscriptionFreezes.filter(id=fresh.id).count() == 1

    # Idempotent cleanup: rerun should keep fresh row intact.
    await Users._scrub_stale_subscription_freezes(user_id, created_before=boundary)
    assert await SubscriptionFreezes.filter(id=fresh.id).count() == 1
