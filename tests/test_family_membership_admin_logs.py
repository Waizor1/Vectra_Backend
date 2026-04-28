from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from tortoise import Tortoise
import pydantic.networks as pydantic_networks
import tortoise.contrib.pydantic as tortoise_pydantic_pkg
import tortoise.contrib.pydantic.creator as tortoise_pydantic_creator

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
        expired_at=date.today() + timedelta(days=30),
        hwid_limit=hwid_limit,
        remnawave_uuid=f"00000000-0000-0000-0000-{user_id:012d}",
    )


async def _create_member(*, user_id: int):
    from bloobcat.db.users import Users

    return await Users.create(
        id=user_id,
        username=f"member{user_id}",
        full_name=f"Member {user_id}",
        is_registered=True,
        remnawave_uuid=f"10000000-0000-0000-0000-{user_id:012d}",
    )


def _stub_family_sync(monkeypatch, family_module) -> None:
    async def _noop(*args, **kwargs):
        _ = args, kwargs
        return None

    monkeypatch.setattr(
        family_module,
        "_sync_user_hwid_limit",
        _noop,
        raising=False,
    )
    monkeypatch.setattr(
        family_module,
        "_sync_owner_effective_remnawave_limit",
        _noop,
        raising=False,
    )


@pytest.mark.asyncio
async def test_accept_invite_emits_member_added_admin_log(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.routes import family_invites as family_module

    _stub_family_sync(monkeypatch, family_module)

    owner = await _create_owner(user_id=9911)
    member = await _create_member(user_id=9912)
    token = "join-token-9911"
    await FamilyInvites.create(
        owner=owner,
        allocated_devices=2,
        token_hash=family_module._hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        max_uses=1,
        used_count=0,
    )

    events: list[str] = []

    async def fake_admin_event(*args, **kwargs):
        events.append(kwargs["event"])

    monkeypatch.setattr(
        family_module,
        "notify_family_membership_event",
        fake_admin_event,
        raising=False,
    )

    result = await family_module.accept_invite(token=token, user=member)

    assert result["ok"] is True
    assert events == ["member_added"]


@pytest.mark.asyncio
async def test_accept_invite_emits_member_reactivated_admin_log(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.routes import family_invites as family_module

    _stub_family_sync(monkeypatch, family_module)

    owner = await _create_owner(user_id=9921)
    member = await _create_member(user_id=9922)
    token = "join-token-9921"
    await FamilyMembers.create(
        owner=owner,
        member=member,
        allocated_devices=0,
        status="disabled",
    )
    await FamilyInvites.create(
        owner=owner,
        allocated_devices=3,
        token_hash=family_module._hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        max_uses=1,
        used_count=0,
    )

    events: list[str] = []

    async def fake_admin_event(*args, **kwargs):
        events.append(kwargs["event"])

    monkeypatch.setattr(
        family_module,
        "notify_family_membership_event",
        fake_admin_event,
        raising=False,
    )

    result = await family_module.accept_invite(token=token, user=member)

    assert result["ok"] is True
    assert events == ["member_reactivated"]


@pytest.mark.asyncio
async def test_update_and_delete_member_emit_admin_logs(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.routes import family_invites as family_module

    _stub_family_sync(monkeypatch, family_module)

    owner = await _create_owner(user_id=9931)
    member_user = await _create_member(user_id=9932)
    member = await FamilyMembers.create(
        owner=owner,
        member=member_user,
        allocated_devices=2,
        status="active",
    )

    events: list[str] = []

    async def fake_admin_event(*args, **kwargs):
        events.append(kwargs["event"])

    monkeypatch.setattr(
        family_module,
        "notify_family_membership_event",
        fake_admin_event,
        raising=False,
    )

    updated = await family_module.update_member(
        member_id=str(member.id),
        payload=family_module.MemberLimitPatch(allocated_devices=4),
        user=owner,
    )
    deleted = await family_module.delete_member(member_id=str(member.id), user=owner)

    assert updated["ok"] is True
    assert deleted["ok"] is True
    assert events == ["member_limit_updated", "member_deleted"]


@pytest.mark.asyncio
async def test_create_invite_rejects_when_owner_connected_devices_exceed_capacity(
    monkeypatch,
):
    from fastapi import HTTPException

    from bloobcat.routes import family_invites as family_module
    from bloobcat.routes import family_quota as family_quota_module

    _stub_family_sync(monkeypatch, family_module)

    owner = await _create_owner(user_id=9936)

    async def fake_owner_connected_devices(_owner):
        return 2

    monkeypatch.setattr(
        family_quota_module,
        "get_owner_connected_devices_count",
        fake_owner_connected_devices,
    )

    with pytest.raises(HTTPException) as exc_info:
        await family_module.create_invite(
            payload=family_module.InviteCreateRequest(allocated_devices=9),
            user=owner,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Family allocation limit exceeded"


@pytest.mark.asyncio
async def test_revoke_invite_frees_reserved_family_quota(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.routes import family_invites as family_module
    from bloobcat.routes import family_quota as family_quota_module

    _stub_family_sync(monkeypatch, family_module)

    owner = await _create_owner(user_id=9937)
    invite = await FamilyInvites.create(
        owner=owner,
        allocated_devices=3,
        token_hash="invite-token-9937",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        max_uses=1,
        used_count=0,
    )

    async def fake_owner_connected_devices(_owner):
        return 2

    monkeypatch.setattr(
        family_quota_module,
        "get_owner_connected_devices_count",
        fake_owner_connected_devices,
    )

    before_revoke = await family_quota_module.build_family_quota_snapshot(owner)
    result = await family_module.revoke_invite(invite_id=str(invite.id), user=owner)
    after_revoke = await family_quota_module.build_family_quota_snapshot(owner)
    invite = await FamilyInvites.get(id=invite.id)

    assert result["ok"] is True
    assert invite.revoked_at is not None
    assert before_revoke.invite_reserved_devices == 3
    assert before_revoke.owner_quota_limit == 7
    assert before_revoke.available_devices == 5
    assert after_revoke.invite_reserved_devices == 0
    assert after_revoke.owner_quota_limit == 10
    assert after_revoke.available_devices == 8


@pytest.mark.asyncio
async def test_leave_family_emits_member_left_admin_log(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.routes import family_invites as family_module

    _stub_family_sync(monkeypatch, family_module)

    owner = await _create_owner(user_id=9941)
    member_user = await _create_member(user_id=9942)
    await FamilyMembers.create(
        owner=owner,
        member=member_user,
        allocated_devices=2,
        status="active",
    )

    events: list[str] = []

    async def fake_admin_event(*args, **kwargs):
        events.append(kwargs["event"])

    monkeypatch.setattr(
        family_module,
        "notify_family_membership_event",
        fake_admin_event,
        raising=False,
    )

    result = await family_module.leave_family(user=member_user)

    assert result["ok"] is True
    assert events == ["member_left"]
