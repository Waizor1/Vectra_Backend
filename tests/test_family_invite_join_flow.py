import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pydantic.networks as pydantic_networks
import pytest
import pytest_asyncio
import tortoise.contrib.pydantic as tortoise_pydantic_pkg
import tortoise.contrib.pydantic.creator as tortoise_pydantic_creator
from fastapi import HTTPException
from tortoise import Tortoise

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests._sqlite_datetime_compat import register_sqlite_datetime_compat


def install_stubs() -> None:
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

    validate_mod = types.ModuleType("bloobcat.funcs.validate")

    async def validate(*args, **kwargs):
        _ = args, kwargs
        return None

    validate_mod.validate = validate
    sys.modules["bloobcat.funcs.validate"] = validate_mod

    admin_mod = types.ModuleType("bloobcat.bot.notifications.admin")

    async def send_admin_message(*args, **kwargs):
        return None

    async def notify_family_membership_event(*args, **kwargs):
        return None

    async def on_activated_bot(*args, **kwargs):
        return None

    admin_mod.send_admin_message = send_admin_message
    admin_mod.notify_family_membership_event = notify_family_membership_event
    admin_mod.on_activated_bot = on_activated_bot
    sys.modules["bloobcat.bot.notifications.admin"] = admin_mod

    trial_granted_mod = types.ModuleType("bloobcat.bot.notifications.trial.granted")

    async def notify_trial_granted(*args, **kwargs):
        return None

    trial_granted_mod.notify_trial_granted = notify_trial_granted
    sys.modules["bloobcat.bot.notifications.trial.granted"] = trial_granted_mod

    family_events_mod = types.ModuleType("bloobcat.bot.notifications.family.events")

    async def _noop(*args, **kwargs):
        return None

    family_events_mod.notify_family_member_joined = _noop
    family_events_mod.notify_family_member_limit_updated = _noop
    family_events_mod.notify_family_member_removed = _noop
    family_events_mod.notify_family_owner_invites_blocked = _noop
    family_events_mod.notify_family_owner_invites_unblocked = _noop
    family_events_mod.notify_family_owner_invite_revoked = _noop
    family_events_mod.notify_family_owner_member_joined = _noop
    sys.modules["bloobcat.bot.notifications.family.events"] = family_events_mod

    def _noop_import_email_validator() -> None:
        return None

    def _validate_email(value: str, /, *args, **kwargs):
        _ = args, kwargs
        return "", value

    pydantic_networks.import_email_validator = _noop_import_email_validator
    pydantic_networks.validate_email = _validate_email

    def _compat_pydantic_model_creator(*args, **kwargs):
        _ = args, kwargs

        class _CompatSerialized:
            def __init__(self, obj):
                self._obj = obj

            def model_dump(self, mode: str = "python"):
                _ = mode
                return {
                    field_name: getattr(self._obj, field_name, None)
                    for field_name in getattr(self._obj._meta, "db_fields", [])
                }

        class _CompatModel:
            @classmethod
            async def from_tortoise_orm(cls, obj):
                return _CompatSerialized(obj)

        return _CompatModel

    tortoise_pydantic_pkg.pydantic_model_creator = _compat_pydantic_model_creator
    tortoise_pydantic_creator.pydantic_model_creator = _compat_pydantic_model_creator

    remna_client_mod = types.ModuleType("bloobcat.routes.remnawave.client")

    class RemnaWaveClient:
        def __init__(self, *args, **kwargs):
            pass

        class users:  # type: ignore[no-redef]
            @staticmethod
            async def get_user_hwid_devices(*args, **kwargs):
                return {}

            @staticmethod
            async def update_user(*args, **kwargs):
                return None

        async def close(self):
            return None

    remna_client_mod.RemnaWaveClient = RemnaWaveClient
    sys.modules["bloobcat.routes.remnawave.client"] = remna_client_mod

    hwid_utils_mod = types.ModuleType("bloobcat.routes.remnawave.hwid_utils")

    def count_active_devices(*args, **kwargs):
        _ = args, kwargs
        return 0

    hwid_utils_mod.count_active_devices = count_active_devices
    sys.modules["bloobcat.routes.remnawave.hwid_utils"] = hwid_utils_mod


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
                        "bloobcat.db.family_members",
                        "bloobcat.db.family_invites",
                        "bloobcat.db.family_audit_logs",
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


async def _create_owner(*, user_id: int, hwid_limit: int = 10):
    from bloobcat.db.users import Users

    return await Users.create(
        id=user_id,
        username=f"owner{user_id}",
        full_name=f"Owner {user_id}",
        is_registered=True,
        is_subscribed=True,
        expired_at=date.today() + timedelta(days=30),
        hwid_limit=hwid_limit,
        remnawave_uuid=f"00000000-0000-0000-0000-{user_id:012d}",
    )


async def _create_member(*, user_id: int, hwid_limit: int = 1):
    from bloobcat.db.users import Users

    return await Users.create(
        id=user_id,
        username=f"member{user_id}",
        full_name=f"Member {user_id}",
        is_registered=True,
        is_subscribed=True,
        expired_at=date.today() + timedelta(days=5),
        hwid_limit=hwid_limit,
        remnawave_uuid=f"10000000-0000-0000-0000-{user_id:012d}",
    )


def _stub_family_sync(monkeypatch, family_module) -> None:
    async def _set_user_limit(user, limit):
        user.hwid_limit = int(limit)
        await user.save(update_fields=["hwid_limit"])

    async def _noop(*args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(
        family_module,
        "_sync_user_hwid_limit",
        _set_user_limit,
        raising=False,
    )
    monkeypatch.setattr(
        family_module,
        "_sync_owner_effective_remnawave_limit",
        _noop,
        raising=False,
    )


@pytest.mark.asyncio
async def test_preview_marks_self_invite(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.routes import family_invites as family_module

    owner = await _create_owner(user_id=9101)
    token = "self-invite-token"
    await FamilyInvites.create(
        owner=owner,
        allocated_devices=3,
        token_hash=family_module._hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        max_uses=1,
        used_count=0,
    )

    async def fake_connected_devices(_user):
        return 2

    monkeypatch.setattr(
        family_module,
        "_get_user_connected_devices_count",
        fake_connected_devices,
    )

    preview = await family_module.preview_invite(token=token, user=owner)

    assert preview.join_mode == "self_invite"
    assert preview.owner.id == owner.id
    assert preview.current_family_owner is not None
    assert preview.current_family_owner.id == owner.id
    assert preview.current_connected_devices == 2


@pytest.mark.asyncio
async def test_preview_marks_owner_blocked(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.routes import family_invites as family_module

    invite_owner = await _create_owner(user_id=9201)
    current_owner = await _create_owner(user_id=9202)
    token = "owner-block-token"
    await FamilyInvites.create(
        owner=invite_owner,
        allocated_devices=2,
        token_hash=family_module._hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        max_uses=1,
        used_count=0,
    )

    async def fake_connected_devices(_user):
        return 1

    monkeypatch.setattr(
        family_module,
        "_get_user_connected_devices_count",
        fake_connected_devices,
    )

    preview = await family_module.preview_invite(token=token, user=current_owner)

    assert preview.join_mode == "owner_blocked"
    assert preview.current_family_owner is not None
    assert preview.current_family_owner.id == current_owner.id

    with pytest.raises(HTTPException) as exc_info:
        await family_module.accept_invite(token=token, user=current_owner)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Family owner cannot join another family"


@pytest.mark.asyncio
async def test_preview_requires_cleanup_for_family_switch(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.routes import family_invites as family_module

    old_owner = await _create_owner(user_id=9301)
    new_owner = await _create_owner(user_id=9302)
    member = await _create_member(user_id=9303)
    token = "switch-cleanup-token"
    await FamilyMembers.create(
        owner=old_owner,
        member=member,
        allocated_devices=5,
        status="active",
    )
    await FamilyInvites.create(
        owner=new_owner,
        allocated_devices=1,
        token_hash=family_module._hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        max_uses=1,
        used_count=0,
    )

    async def fake_connected_devices(_user):
        return 3

    monkeypatch.setattr(
        family_module,
        "_get_user_connected_devices_count",
        fake_connected_devices,
    )

    preview = await family_module.preview_invite(token=token, user=member)

    assert preview.join_mode == "switch_family_cleanup_required"
    assert preview.devices_to_remove == 2
    assert preview.current_family_owner is not None
    assert preview.current_family_owner.id == old_owner.id
    assert preview.current_family_allocated_devices == 5


@pytest.mark.asyncio
async def test_accept_switch_moves_member_to_new_family(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.routes import family_invites as family_module

    _stub_family_sync(monkeypatch, family_module)

    old_owner = await _create_owner(user_id=9401)
    new_owner = await _create_owner(user_id=9402)
    member = await _create_member(user_id=9403, hwid_limit=5)
    token = "switch-success-token"
    await FamilyMembers.create(
        owner=old_owner,
        member=member,
        allocated_devices=5,
        status="active",
    )
    await FamilyInvites.create(
        owner=new_owner,
        allocated_devices=2,
        token_hash=family_module._hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        max_uses=1,
        used_count=0,
    )

    async def fake_connected_devices(_user):
        return 2

    monkeypatch.setattr(
        family_module,
        "_get_user_connected_devices_count",
        fake_connected_devices,
    )

    result = await family_module.accept_invite(token=token, user=member)

    await member.refresh_from_db()
    invite = await FamilyInvites.get(token_hash=family_module._hash_token(token))
    new_membership = await FamilyMembers.get(owner_id=new_owner.id, member_id=member.id)

    assert result["ok"] is True
    assert await FamilyMembers.filter(owner_id=old_owner.id, member_id=member.id).count() == 0
    assert new_membership.allocated_devices == 2
    assert new_membership.status == "active"
    assert member.hwid_limit == 2
    assert invite.used_count == 1


@pytest.mark.asyncio
async def test_accept_switch_rejects_when_cleanup_is_required(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.routes import family_invites as family_module

    _stub_family_sync(monkeypatch, family_module)

    old_owner = await _create_owner(user_id=9501)
    new_owner = await _create_owner(user_id=9502)
    member = await _create_member(user_id=9503, hwid_limit=4)
    token = "switch-reject-token"
    await FamilyMembers.create(
        owner=old_owner,
        member=member,
        allocated_devices=4,
        status="active",
    )
    await FamilyInvites.create(
        owner=new_owner,
        allocated_devices=1,
        token_hash=family_module._hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        max_uses=1,
        used_count=0,
    )

    async def fake_connected_devices(_user):
        return 3

    monkeypatch.setattr(
        family_module,
        "_get_user_connected_devices_count",
        fake_connected_devices,
    )

    with pytest.raises(HTTPException) as exc_info:
        await family_module.accept_invite(token=token, user=member)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Device cleanup required"
    assert await FamilyMembers.filter(owner_id=old_owner.id, member_id=member.id).count() == 1
    assert await FamilyMembers.filter(owner_id=new_owner.id, member_id=member.id).count() == 0


@pytest.mark.asyncio
async def test_accept_rejects_self_invite_direct_call(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.routes import family_invites as family_module

    _stub_family_sync(monkeypatch, family_module)

    owner = await _create_owner(user_id=9601)
    token = "self-invite-direct"
    await FamilyInvites.create(
        owner=owner,
        allocated_devices=2,
        token_hash=family_module._hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        max_uses=1,
        used_count=0,
    )

    async def fake_connected_devices(_user):
        return 1

    monkeypatch.setattr(
        family_module,
        "_get_user_connected_devices_count",
        fake_connected_devices,
    )

    with pytest.raises(HTTPException) as exc_info:
        await family_module.accept_invite(token=token, user=owner)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Owner cannot accept own invite"


@pytest.mark.asyncio
async def test_owner_with_30_devices_can_create_and_accept_invite_above_legacy_10(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.routes import family_invites as family_module

    _stub_family_sync(monkeypatch, family_module)

    owner = await _create_owner(user_id=9701, hwid_limit=30)
    member = await _create_member(user_id=9702, hwid_limit=1)

    created = await family_module.create_invite(
        family_module.InviteCreateRequest(allocated_devices=12),
        user=owner,
    )
    assert created["token"]

    invite = await FamilyInvites.get(owner_id=owner.id)
    assert invite.allocated_devices == 12

    async def fake_connected_devices(_user):
        return 0

    monkeypatch.setattr(
        family_module,
        "_get_user_connected_devices_count",
        fake_connected_devices,
    )

    result = await family_module.accept_invite(token=created["token"], user=member)

    await member.refresh_from_db()
    membership = await FamilyMembers.get(owner_id=owner.id, member_id=member.id)
    assert result["ok"] is True
    assert membership.allocated_devices == 12
    assert member.hwid_limit == 12
