from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from tortoise import Tortoise

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests._sqlite_datetime_compat import register_sqlite_datetime_compat


_STUB_KEYS = (
    "bloobcat.routes",
    "bloobcat.routes.remnawave",
    "bloobcat.bot",
    "bloobcat.bot.notifications",
    "bloobcat.bot.notifications.trial",
    "bloobcat.logger",
    "bloobcat.bot.notifications.admin",
    "bloobcat.bot.bot",
    "bloobcat.bot.notifications.trial.granted",
    "bloobcat.scheduler",
    "bloobcat.routes.remnawave.client",
)
_MISSING = object()


def install_stubs() -> dict[str, object]:
    originals = {key: sys.modules.get(key, _MISSING) for key in _STUB_KEYS}
    routes_pkg = types.ModuleType("bloobcat.routes")
    routes_pkg.__path__ = [str(PROJECT_ROOT / "bloobcat" / "routes")]
    remnawave_pkg = types.ModuleType("bloobcat.routes.remnawave")
    remnawave_pkg.__path__ = [str(PROJECT_ROOT / "bloobcat" / "routes" / "remnawave")]
    sys.modules["bloobcat.routes"] = routes_pkg
    sys.modules["bloobcat.routes.remnawave"] = remnawave_pkg

    bot_pkg = types.ModuleType("bloobcat.bot")
    bot_pkg.__path__ = []
    notifications_pkg = types.ModuleType("bloobcat.bot.notifications")
    notifications_pkg.__path__ = []
    trial_notifications_pkg = types.ModuleType("bloobcat.bot.notifications.trial")
    trial_notifications_pkg.__path__ = []
    sys.modules["bloobcat.bot"] = bot_pkg
    sys.modules["bloobcat.bot.notifications"] = notifications_pkg
    sys.modules["bloobcat.bot.notifications.trial"] = trial_notifications_pkg

    logger_mod = types.ModuleType("bloobcat.logger")

    class DummyLogger:
        def bind(self, **kwargs):
            return self

        def debug(self, *args, **kwargs):
            return None

        def info(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

    logger_mod.get_logger = lambda _name: DummyLogger()
    logger_mod.configured_logger = DummyLogger()
    sys.modules["bloobcat.logger"] = logger_mod

    admin_notif = types.ModuleType("bloobcat.bot.notifications.admin")

    async def _noop(*args, **kwargs):
        return None

    admin_notif.on_activated_bot = _noop
    sys.modules["bloobcat.bot.notifications.admin"] = admin_notif

    bot_mod = types.ModuleType("bloobcat.bot.bot")
    bot_mod.get_bot_username = _noop
    sys.modules["bloobcat.bot.bot"] = bot_mod

    trial_granted_mod = types.ModuleType("bloobcat.bot.notifications.trial.granted")
    trial_granted_mod.notify_trial_granted = _noop
    sys.modules["bloobcat.bot.notifications.trial.granted"] = trial_granted_mod

    scheduler_mod = types.ModuleType("bloobcat.scheduler")
    scheduler_mod.schedule_user_tasks = _noop
    scheduler_mod.cancel_user_tasks = lambda *_args, **_kwargs: None
    sys.modules["bloobcat.scheduler"] = scheduler_mod

    remna_client_mod = types.ModuleType("bloobcat.routes.remnawave.client")

    class RemnaWaveClient:
        def __init__(self, *args, **kwargs):
            self.users = self

        async def get_user_hwid_devices(self, *args, **kwargs):
            return []

        async def update_user(self, *args, **kwargs):
            return {"response": True}

        async def close(self):
            return None

    remna_client_mod.RemnaWaveClient = RemnaWaveClient
    sys.modules["bloobcat.routes.remnawave.client"] = remna_client_mod
    return originals


def restore_stubs(originals: dict[str, object]) -> None:
    for key, value in originals.items():
        if value is _MISSING:
            sys.modules.pop(key, None)
            if "." in key:
                parent_name, child_name = key.rsplit(".", 1)
                parent = sys.modules.get(parent_name)
                current = getattr(parent, child_name, None) if parent else None
                if getattr(current, "__name__", None) == key:
                    delattr(parent, child_name)
        else:
            sys.modules[key] = value
            if "." in key:
                parent_name, child_name = key.rsplit(".", 1)
                parent = sys.modules.get(parent_name)
                if parent is not None:
                    setattr(parent, child_name, value)


@pytest_asyncio.fixture(autouse=True)
async def db():
    originals = install_stubs()
    register_sqlite_datetime_compat()
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": [
                        "bloobcat.db.users",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.family_members",
                        "bloobcat.db.family_invites",
                        "bloobcat.db.user_devices",
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
        restore_stubs(originals)


class _FakeRemnaWaveClient:
    def __init__(self):
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self.deleted: list[str] = []
        self.closed = False
        self.users = self
        self.tools = self

    async def create_user(self, **kwargs):
        self.created.append(kwargs)
        return {"response": {"uuid": "00000000-0000-0000-0000-00000000d001"}}

    async def update_user(self, uuid, **kwargs):
        self.updated.append((str(uuid), kwargs))
        return {"response": {"uuid": str(uuid)}}

    async def delete_user(self, uuid):
        self.deleted.append(str(uuid))
        return {"response": True}

    async def get_user_by_uuid(self, uuid):
        return {"response": {"subscriptionUrl": f"https://sub.example/{uuid}"}}

    async def encrypt_happ_crypto_link(self, url):
        return f"happ://crypto/{url.rsplit('/', 1)[-1]}"

    async def close(self):
        self.closed = True


async def _create_user(
    user_id: int,
    *,
    hwid_limit: int = 2,
    enabled: bool | None = True,
    subscription_active: bool = True,
):
    from bloobcat.db.users import Users

    return await Users.create(
        id=user_id,
        username=f"user{user_id}",
        full_name=f"User {user_id}",
        is_registered=True,
        is_subscribed=subscription_active,
        expired_at=date.today() + timedelta(days=30) if subscription_active else None,
        hwid_limit=hwid_limit,
        device_per_user_enabled=enabled,
        remnawave_uuid=f"00000000-0000-0000-0000-{user_id:012d}",
    )


def test_user_device_per_user_flag_uses_user_override(monkeypatch):
    from bloobcat.db.users import Users
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "device_per_user_enabled", False, raising=False)
    assert Users(id=1, full_name="A", device_per_user_enabled=True).is_device_per_user_enabled() is True
    assert Users(id=2, full_name="B", device_per_user_enabled=False).is_device_per_user_enabled() is False
    assert Users(id=3, full_name="C", device_per_user_enabled=None).is_device_per_user_enabled() is False

    monkeypatch.setattr(app_settings, "device_per_user_enabled", True, raising=False)
    assert Users(id=4, full_name="D", device_per_user_enabled=None).is_device_per_user_enabled() is True


@pytest.mark.asyncio
async def test_limit_state_counts_legacy_and_device_users(monkeypatch):
    from bloobcat.db.user_devices import DeviceKind, UserDevice
    from bloobcat.services import device_service

    user = await _create_user(101, hwid_limit=3, subscription_active=False)
    await UserDevice.create(user=user, kind=DeviceKind.DEVICE_USER, remnawave_uuid="00000000-0000-0000-0000-000000000101")

    async def fake_legacy_items(_legacy_user):
        return [{"hwid": "legacy-1"}]

    monkeypatch.setattr(device_service, "_list_legacy_hwid_items", fake_legacy_items)

    context = await device_service.resolve_device_inventory_context(user)
    state = await device_service.device_limit_state_for_context(context)

    assert state["effective_limit"] == 3
    assert state["legacy_hwid_count"] == 1
    assert state["device_user_count"] == 1
    assert state["used_total"] == 2
    assert state["available_slots"] == 1
    assert state["can_add"] is False
    assert state["blocked_reason"] == "subscription_expired"


@pytest.mark.asyncio
async def test_create_device_user_creates_dedicated_remnawave_user(monkeypatch):
    from bloobcat.db.user_devices import DeviceKind, UserDevice
    from bloobcat.services import device_service

    user = await _create_user(102, hwid_limit=2, subscription_active=False)
    fake_client = _FakeRemnaWaveClient()
    monkeypatch.setattr(device_service, "_rw_client", lambda: fake_client)

    async def fake_legacy_items(_legacy_user):
        return []

    monkeypatch.setattr(device_service, "_list_legacy_hwid_items", fake_legacy_items)

    device = await device_service.create_device_user(user, name="MacBook")

    assert device.kind == DeviceKind.DEVICE_USER
    assert device.device_name == "MacBook"
    assert str(device.remnawave_uuid) == "00000000-0000-0000-0000-00000000d001"
    assert await UserDevice.filter(user_id=user.id).count() == 1
    assert fake_client.created[0]["username"].startswith("VECTRA_")
    assert fake_client.created[0]["hwid_device_limit"] == 1
    assert fake_client.updated[-1][1]["hwidDeviceLimit"] == 1
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_enabling_device_per_user_syncs_derived_legacy_limit(monkeypatch):
    from bloobcat.services import device_service

    user = await _create_user(103, hwid_limit=3, enabled=False)
    fake_client = _FakeRemnaWaveClient()
    monkeypatch.setattr(device_service, "_rw_client", lambda: fake_client)

    async def fake_legacy_items(_legacy_user):
        return []

    monkeypatch.setattr(device_service, "_list_legacy_hwid_items", fake_legacy_items)

    user.device_per_user_enabled = True
    await user.save()

    assert fake_client.updated[-1][0] == str(user.remnawave_uuid)
    assert fake_client.updated[-1][1]["hwidDeviceLimit"] == 3
    assert "expireAt" in fake_client.updated[-1][1]


@pytest.mark.asyncio
async def test_family_member_context_uses_allocated_devices_and_cascades(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.user_devices import DeviceKind, UserDevice
    from bloobcat.services import device_service

    owner = await _create_user(201, hwid_limit=10)
    member_user = await _create_user(202, hwid_limit=1, enabled=False)
    membership = await FamilyMembers.create(
        owner=owner,
        member=member_user,
        allocated_devices=2,
        status="active",
    )
    device = await UserDevice.create(
        user=owner,
        family_member=membership,
        kind=DeviceKind.DEVICE_USER,
        remnawave_uuid="00000000-0000-0000-0000-000000000202",
    )
    fake_client = _FakeRemnaWaveClient()
    monkeypatch.setattr(device_service, "_rw_client", lambda: fake_client)

    async def fake_legacy_items(_legacy_user):
        return []

    monkeypatch.setattr(device_service, "_list_legacy_hwid_items", fake_legacy_items)

    context = await device_service.resolve_device_inventory_context(member_user)
    state = await device_service.device_limit_state_for_context(context)

    assert context.owner.id == owner.id
    assert context.family_member.id == membership.id
    assert device_service.is_device_per_user_enabled_for_context(member_user, context) is True
    assert state["effective_limit"] == 2
    assert state["device_user_count"] == 1

    member_user.temp_setup_token = "member-temp-token"
    member_user.temp_setup_expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    member_user.temp_setup_device_id = device.id
    await member_user.save(update_fields=["temp_setup_token", "temp_setup_expires_at", "temp_setup_device_id"])
    device.hwid = "bound-member-hwid"
    await device.save(update_fields=["hwid"])
    assert await device_service.invalidate_temp_link_if_bound(device) is True
    await member_user.refresh_from_db()
    assert member_user.temp_setup_token is None
    assert member_user.temp_setup_expires_at is None
    assert member_user.temp_setup_device_id is None

    deleted = await device_service.cascade_delete_family_member_devices(membership)

    assert deleted == 1
    assert await UserDevice.filter(id=device.id).count() == 0
    assert fake_client.deleted == ["00000000-0000-0000-0000-000000000202"]


@pytest.mark.asyncio
async def test_device_hwid_collision_is_reported_without_status_failure(monkeypatch):
    from bloobcat.db.user_devices import DeviceKind, UserDevice
    from bloobcat.services import device_service

    user = await _create_user(203, hwid_limit=3)
    existing = await UserDevice.create(
        user=user,
        kind=DeviceKind.DEVICE_USER,
        remnawave_uuid="00000000-0000-0000-0000-000000000203",
        hwid="same-device-hwid",
    )
    pending = await UserDevice.create(
        user=user,
        kind=DeviceKind.DEVICE_USER,
        remnawave_uuid="00000000-0000-0000-0000-000000000204",
    )

    async def fake_hwid_info(_device):
        return {"hwid": "same-device-hwid", "platform": "android"}

    monkeypatch.setattr(
        device_service,
        "fetch_first_hwid_device_info",
        fake_hwid_info,
    )

    assert await device_service.sync_device_hwid_from_remnawave(pending) is True
    await pending.refresh_from_db()
    collisions = await device_service.find_device_collisions(pending)

    assert pending.hwid == "same-device-hwid"
    assert pending.platform == "android"
    assert [collision.id for collision in collisions] == [existing.id]


def test_temp_setup_expiry_helper():
    from bloobcat.services.temp_setup_links import (
        get_temp_setup_expires_at,
        is_temp_setup_token_expired,
    )

    now = datetime(2026, 5, 5, 9, 0, tzinfo=timezone.utc)
    expires_at = get_temp_setup_expires_at(now)

    assert is_temp_setup_token_expired(expires_at, now + timedelta(minutes=14)) is False
    assert is_temp_setup_token_expired(expires_at, now + timedelta(minutes=15)) is True
