import json
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


register_sqlite_datetime_compat()

_ORIGINAL_SUBSCRIPTION_OVERLAY_MODULE = sys.modules.get(
    "bloobcat.services.subscription_overlay"
)
_ORIGINAL_ROUTES_MODULE = sys.modules.get("bloobcat.routes")
_ORIGINAL_REMNAWAVE_ROUTES_MODULE = sys.modules.get("bloobcat.routes.remnawave")
_ORIGINAL_REMNAWAVE_CLIENT_MODULE = sys.modules.get("bloobcat.routes.remnawave.client")
_ORIGINAL_REMNAWAVE_HWID_UTILS_MODULE = sys.modules.get(
    "bloobcat.routes.remnawave.hwid_utils"
)
_ORIGINAL_FAMILY_QUOTA_MODULE = sys.modules.get("bloobcat.routes.family_quota")
_ORIGINAL_BLOOBCAT_ROUTES_ATTR = getattr(sys.modules.get("bloobcat"), "routes", None)


def install_stubs() -> None:
    yk_module = types.ModuleType("yookassa")

    class Payment:
        @staticmethod
        def create(*args, **kwargs):
            return None

    yk_module.Payment = Payment
    sys.modules["yookassa"] = yk_module

    routes_pkg = types.ModuleType("bloobcat.routes")
    routes_pkg.__path__ = [
        str(Path(__file__).resolve().parents[1] / "bloobcat" / "routes")
    ]
    remnawave_pkg = types.ModuleType("bloobcat.routes.remnawave")
    remnawave_pkg.__path__ = [
        str(Path(__file__).resolve().parents[1] / "bloobcat" / "routes" / "remnawave")
    ]
    sys.modules["bloobcat.routes"] = routes_pkg
    sys.modules["bloobcat.routes.remnawave"] = remnawave_pkg
    bloobcat_pkg = sys.modules.get("bloobcat")
    if bloobcat_pkg is not None:
        setattr(bloobcat_pkg, "routes", routes_pkg)
    setattr(routes_pkg, "remnawave", remnawave_pkg)

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

    def get_logger(name: str):
        _ = name
        return DummyLogger()

    def get_payment_logger():
        return DummyLogger()

    logger_mod.get_logger = get_logger
    logger_mod.get_payment_logger = get_payment_logger
    logger_mod.configured_logger = DummyLogger()
    sys.modules["bloobcat.logger"] = logger_mod

    # Avoid hard dependency on email-validator package for route module import.
    import pydantic.networks as pydantic_networks

    def _noop_import_email_validator() -> None:
        return None

    def _validate_email(value: str, /, *args, **kwargs):
        _ = args, kwargs
        return "", value

    pydantic_networks.import_email_validator = _noop_import_email_validator
    pydantic_networks.validate_email = _validate_email

    # pydantic>=2.12 compatibility: bypass tortoise pydantic schema generation in tests.
    import tortoise.contrib.pydantic as tortoise_pydantic_pkg
    import tortoise.contrib.pydantic.creator as tortoise_pydantic_creator

    def _compat_pydantic_model_creator(*args, **kwargs):
        _ = args, kwargs

        class _CompatSerialized:
            def __init__(self, obj):
                self._obj = obj

            def model_dump(self, mode: str = "python"):
                _ = mode
                import uuid

                payload = {}
                for field_name in getattr(self._obj._meta, "db_fields", []):
                    value = getattr(self._obj, field_name, None)
                    if isinstance(value, (datetime, date)):
                        payload[field_name] = value.isoformat()
                    elif isinstance(value, uuid.UUID):
                        payload[field_name] = str(value)
                    else:
                        payload[field_name] = value
                return payload

        class _CompatModel:
            @classmethod
            async def from_tortoise_orm(cls, obj):
                return _CompatSerialized(obj)

        return _CompatModel

    tortoise_pydantic_pkg.pydantic_model_creator = _compat_pydantic_model_creator
    tortoise_pydantic_creator.pydantic_model_creator = _compat_pydantic_model_creator

    admin_notif = types.ModuleType("bloobcat.bot.notifications.admin")

    async def cancel_subscription(*args, **kwargs):
        return None

    async def on_activated_bot(*args, **kwargs):
        return None

    async def notify_active_tariff_change(*args, **kwargs):
        return None

    async def notify_lte_topup(*args, **kwargs):
        return None

    admin_notif.cancel_subscription = cancel_subscription
    admin_notif.on_activated_bot = on_activated_bot
    admin_notif.notify_active_tariff_change = notify_active_tariff_change
    admin_notif.notify_lte_topup = notify_lte_topup
    sys.modules["bloobcat.bot.notifications.admin"] = admin_notif

    bot_mod = types.ModuleType("bloobcat.bot.bot")

    async def get_bot_username():
        return "VectraConnect_bot"

    bot_mod.get_bot_username = get_bot_username
    sys.modules["bloobcat.bot.bot"] = bot_mod

    trial_granted_mod = types.ModuleType("bloobcat.bot.notifications.trial.granted")

    async def notify_trial_granted(*args, **kwargs):
        return None

    trial_granted_mod.notify_trial_granted = notify_trial_granted
    sys.modules["bloobcat.bot.notifications.trial.granted"] = trial_granted_mod

    remna_client_mod = types.ModuleType("bloobcat.routes.remnawave.client")

    class RemnaWaveClient:
        def __init__(self, *args, **kwargs):
            pass

        class users:  # type: ignore[no-redef]
            @staticmethod
            async def get_subscription_url(*args, **kwargs):
                return "https://example.test/subscription"

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
    setattr(remnawave_pkg, "client", remna_client_mod)

    hwid_utils_mod = types.ModuleType("bloobcat.routes.remnawave.hwid_utils")

    async def cleanup_user_hwid_devices(*args, **kwargs):
        return None

    def count_active_devices(*args, **kwargs):
        return 0

    hwid_utils_mod.cleanup_user_hwid_devices = cleanup_user_hwid_devices
    hwid_utils_mod.count_active_devices = count_active_devices
    sys.modules["bloobcat.routes.remnawave.hwid_utils"] = hwid_utils_mod
    setattr(remnawave_pkg, "hwid_utils", hwid_utils_mod)

    family_quota_mod = types.ModuleType("bloobcat.routes.family_quota")

    async def build_family_quota_snapshot(
        owner,
        *,
        owner_connected_devices=None,
        owner_base_devices_limit=None,
        now=None,
    ):
        from tortoise.expressions import F, Q

        from bloobcat.db.family_invites import FamilyInvites
        from bloobcat.db.family_members import FamilyMembers
        from bloobcat.settings import app_settings

        effective_now = now or datetime.now(timezone.utc)
        active_members = await FamilyMembers.filter(
            owner_id=owner.id,
            status="active",
            allocated_devices__gt=0,
        )
        active_invites = (
            await FamilyInvites.filter(owner_id=owner.id, revoked_at=None)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=effective_now))
            .filter(used_count__lt=F("max_uses"))
        )
        member_allocated_devices = sum(
            int(member.allocated_devices or 0) for member in active_members
        )
        invite_reserved_devices = sum(
            int(invite.allocated_devices or 0) for invite in active_invites
        )
        family_limit = max(
            int(app_settings.family_devices_limit),
            int(owner_base_devices_limit or getattr(owner, "hwid_limit", None) or 1),
        )
        owner_quota_limit = max(
            0,
            family_limit - member_allocated_devices - invite_reserved_devices,
        )
        return types.SimpleNamespace(
            family_limit=family_limit,
            owner_base_devices_limit=int(
                owner_base_devices_limit or getattr(owner, "hwid_limit", None) or 1
            ),
            owner_connected_devices=int(owner_connected_devices or 0),
            member_allocated_devices=member_allocated_devices,
            invite_reserved_devices=invite_reserved_devices,
            reserved_devices=(
                int(owner_connected_devices or 0)
                + member_allocated_devices
                + invite_reserved_devices
            ),
            available_devices=max(
                0,
                family_limit
                - int(owner_connected_devices or 0)
                - member_allocated_devices
                - invite_reserved_devices,
            ),
            owner_quota_limit=owner_quota_limit,
            active_members_count=len(active_members),
            active_invites_count=len(active_invites),
        )

    family_quota_mod.build_family_quota_snapshot = build_family_quota_snapshot
    sys.modules["bloobcat.routes.family_quota"] = family_quota_mod
    setattr(routes_pkg, "family_quota", family_quota_mod)

    scheduler_mod = types.ModuleType("bloobcat.scheduler")

    async def schedule_user_tasks(*args, **kwargs):
        return None

    scheduler_mod.schedule_user_tasks = schedule_user_tasks
    sys.modules["bloobcat.scheduler"] = scheduler_mod

    subscription_overlay_mod = types.ModuleType(
        "bloobcat.services.subscription_overlay"
    )

    async def get_overlay_payload(*args, **kwargs):
        _ = kwargs
        from bloobcat.db.subscription_freezes import SubscriptionFreezes

        user = args[0]
        freeze = (
            await SubscriptionFreezes.filter(user_id=int(user.id), is_active=True)
            .order_by("-id")
            .first()
        )
        if freeze is None:
            return {}
        freeze_reason = str(getattr(freeze, "freeze_reason", "") or "").lower()
        is_family_overlay = freeze_reason == "family_overlay"
        return {
            "has_frozen_base": is_family_overlay,
            "base_remaining_days": getattr(freeze, "base_remaining_days", None),
            "base_hwid_limit": getattr(freeze, "base_hwid_limit", None),
            "base_resume_at": None,
            "will_restore_base_after_family": is_family_overlay,
            "active_kind": "family" if is_family_overlay else "base",
        }

    async def resume_frozen_base_if_due(*args, **kwargs):
        _ = args, kwargs
        return False

    subscription_overlay_mod.get_overlay_payload = get_overlay_payload
    subscription_overlay_mod.resume_frozen_base_if_due = resume_frozen_base_if_due
    sys.modules["bloobcat.services.subscription_overlay"] = subscription_overlay_mod

    # Ensure route module is re-imported with test stubs instead of a
    # previously imported real RemnaWave client from other test modules.
    if "bloobcat.routes.user" in sys.modules:
        del sys.modules["bloobcat.routes.user"]
    if hasattr(routes_pkg, "user"):
        delattr(routes_pkg, "user")


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    yield
    if _ORIGINAL_SUBSCRIPTION_OVERLAY_MODULE is not None:
        sys.modules["bloobcat.services.subscription_overlay"] = (
            _ORIGINAL_SUBSCRIPTION_OVERLAY_MODULE
        )
    else:
        sys.modules.pop("bloobcat.services.subscription_overlay", None)
    sys.modules.pop("bloobcat.routes.user", None)
    if _ORIGINAL_ROUTES_MODULE is not None:
        sys.modules["bloobcat.routes"] = _ORIGINAL_ROUTES_MODULE
    else:
        sys.modules.pop("bloobcat.routes", None)
    if _ORIGINAL_REMNAWAVE_ROUTES_MODULE is not None:
        sys.modules["bloobcat.routes.remnawave"] = _ORIGINAL_REMNAWAVE_ROUTES_MODULE
    else:
        sys.modules.pop("bloobcat.routes.remnawave", None)
    if _ORIGINAL_REMNAWAVE_CLIENT_MODULE is not None:
        sys.modules["bloobcat.routes.remnawave.client"] = (
            _ORIGINAL_REMNAWAVE_CLIENT_MODULE
        )
    else:
        sys.modules.pop("bloobcat.routes.remnawave.client", None)
    if _ORIGINAL_REMNAWAVE_HWID_UTILS_MODULE is not None:
        sys.modules["bloobcat.routes.remnawave.hwid_utils"] = (
            _ORIGINAL_REMNAWAVE_HWID_UTILS_MODULE
        )
    else:
        sys.modules.pop("bloobcat.routes.remnawave.hwid_utils", None)
    if _ORIGINAL_FAMILY_QUOTA_MODULE is not None:
        sys.modules["bloobcat.routes.family_quota"] = _ORIGINAL_FAMILY_QUOTA_MODULE
    else:
        sys.modules.pop("bloobcat.routes.family_quota", None)
    bloobcat_pkg = sys.modules.get("bloobcat")
    if bloobcat_pkg is not None:
        if _ORIGINAL_BLOOBCAT_ROUTES_ATTR is not None:
            setattr(bloobcat_pkg, "routes", _ORIGINAL_BLOOBCAT_ROUTES_ATTR)
        elif hasattr(bloobcat_pkg, "routes"):
            delattr(bloobcat_pkg, "routes")


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
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
    creation_sql = "\n".join(
        [t["table_creation_string"] for t in tables]
        + [m for t in tables for m in t["m2m_tables"]]
    )
    await generator.generate_from_string(creation_sql)

    try:
        yield
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_user_returns_family_owner_summary(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.users import Users
    from bloobcat.routes import user as user_module
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "family_devices_limit", 12, raising=False)
    monkeypatch.setattr(user_module, "count_active_devices", lambda *_args, **_kwargs: 2)

    owner = await Users.create(
        id=2001,
        username="owner",
        full_name="Owner",
        is_registered=True,
        expired_at=date.today() + timedelta(days=30),
        is_subscribed=True,
        hwid_limit=12,
        remnawave_uuid="00000000-0000-0000-0000-000000000001",
    )
    member_a = await Users.create(
        id=2002, username="m1", full_name="Member 1", is_registered=True
    )
    member_b = await Users.create(
        id=2003, username="m2", full_name="Member 2", is_registered=True
    )

    await FamilyMembers.create(
        owner=owner, member=member_a, allocated_devices=3, status="active"
    )
    await FamilyMembers.create(
        owner=owner, member=member_b, allocated_devices=2, status="active"
    )

    await FamilyInvites.create(
        owner=owner,
        allocated_devices=1,
        token_hash="token_active",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        max_uses=1,
        used_count=0,
    )
    await FamilyInvites.create(
        owner=owner,
        allocated_devices=1,
        token_hash="token_revoked",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        max_uses=1,
        used_count=0,
        revoked_at=datetime.now(timezone.utc),
    )

    response = await user_module.check(user=owner)
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["family_entitled"] is True
    assert payload["subscription_context"] == "family_owner"
    assert payload["devices_count"] == 2
    assert payload["devices_limit"] == 6
    assert payload["family_owner"] == {
        "is_owner": True,
        "family_devices_total": 12,
        "allocated_devices_total": 5,
        "active_invites_devices_total": 1,
        "owner_remaining_devices": 6,
        "active_members_count": 2,
        "active_invites_count": 1,
    }


@pytest.mark.asyncio
async def test_user_owner_remaining_devices_can_be_zero(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.users import Users
    from bloobcat.routes import user as user_module
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "family_devices_limit", 10, raising=False)

    owner = await Users.create(
        id=2051,
        username="owner_zero",
        full_name="Owner Zero",
        is_registered=True,
        expired_at=date.today() + timedelta(days=30),
        is_subscribed=True,
        hwid_limit=10,
        remnawave_uuid="00000000-0000-0000-0000-000000000051",
    )

    for idx in range(10):
        member = await Users.create(
            id=2060 + idx,
            username=f"zero_m{idx}",
            full_name=f"Zero Member {idx}",
            is_registered=True,
        )
        await FamilyMembers.create(
            owner=owner,
            member=member,
            allocated_devices=1,
            status="active",
        )

    response = await user_module.check(user=owner)
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["family_entitled"] is True
    assert payload["subscription_context"] == "family_owner"
    assert payload["devices_limit"] == 0
    assert payload["family_owner"] == {
        "is_owner": True,
        "family_devices_total": 10,
        "allocated_devices_total": 10,
        "active_invites_devices_total": 0,
        "owner_remaining_devices": 0,
        "active_members_count": 10,
        "active_invites_count": 0,
    }


@pytest.mark.asyncio
async def test_user_owner_devices_limit_ignores_owner_connected_devices(monkeypatch):
    from bloobcat.db.family_invites import FamilyInvites
    from bloobcat.db.users import Users
    from bloobcat.routes import user as user_module
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "family_devices_limit", 10, raising=False)
    monkeypatch.setattr(user_module, "count_active_devices", lambda *_args, **_kwargs: 2)

    owner = await Users.create(
        id=2071,
        username="owner_connected",
        full_name="Owner Connected",
        is_registered=True,
        expired_at=date.today() + timedelta(days=30),
        is_subscribed=True,
        hwid_limit=10,
        remnawave_uuid="00000000-0000-0000-0000-000000000071",
    )

    await FamilyInvites.create(
        owner=owner,
        allocated_devices=3,
        token_hash="token_connected",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        max_uses=1,
        used_count=0,
    )

    response = await user_module.check(user=owner)
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["devices_count"] == 2
    assert payload["devices_limit"] == 7
    assert payload["family_owner"]["allocated_devices_total"] == 0
    assert payload["family_owner"]["active_invites_devices_total"] == 3
    assert payload["family_owner"]["owner_remaining_devices"] == 7


@pytest.mark.asyncio
async def test_user_family_entitled_for_member_and_non_family(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.users import Users
    from bloobcat.routes.user import check
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "family_devices_limit", 12, raising=False)

    owner = await Users.create(
        id=2101,
        username="owner2",
        full_name="Owner 2",
        is_registered=True,
        expired_at=date.today() + timedelta(days=20),
        is_subscribed=True,
        hwid_limit=12,
        remnawave_uuid="00000000-0000-0000-0000-000000000011",
    )
    member = await Users.create(
        id=2102,
        username="member2",
        full_name="Member 2",
        is_registered=True,
        expired_at=date.today() + timedelta(days=2),
        is_subscribed=True,
        hwid_limit=1,
        remnawave_uuid="00000000-0000-0000-0000-000000000012",
    )
    regular_user = await Users.create(
        id=2103,
        username="regular",
        full_name="Regular",
        is_registered=True,
        expired_at=date.today() + timedelta(days=10),
        is_subscribed=True,
        hwid_limit=1,
        remnawave_uuid="00000000-0000-0000-0000-000000000013",
    )

    await FamilyMembers.create(
        owner=owner, member=member, allocated_devices=2, status="active"
    )

    member_response = await check(user=member)
    member_payload = json.loads(member_response.body.decode("utf-8"))
    assert member_payload["family_entitled"] is True
    assert member_payload["subscription_context"] == "family_member"
    assert member_payload["devices_limit"] == 2
    assert member_payload["family_owner"] is None

    regular_response = await check(user=regular_user)
    regular_payload = json.loads(regular_response.body.decode("utf-8"))
    assert regular_payload["family_entitled"] is False
    assert regular_payload["subscription_context"] == "personal"
    assert regular_payload["family_owner"] is None


@pytest.mark.asyncio
async def test_member_entitlement_follows_owner_overlay_state(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.routes.user import check
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "family_devices_limit", 10, raising=False)

    owner = await Users.create(
        id=2201,
        username="overlay_owner",
        full_name="Overlay Owner",
        is_registered=True,
        expired_at=date.today() + timedelta(days=20),
        is_subscribed=True,
        hwid_limit=1,
        remnawave_uuid="00000000-0000-0000-0000-000000000021",
    )
    member = await Users.create(
        id=2202,
        username="overlay_member",
        full_name="Overlay Member",
        is_registered=True,
        remnawave_uuid="00000000-0000-0000-0000-000000000022",
    )
    await FamilyMembers.create(
        owner=owner, member=member, allocated_devices=2, status="active"
    )

    freeze = await SubscriptionFreezes.create(
        user_id=owner.id,
        freeze_reason="base_overlay",
        is_active=True,
        base_remaining_days=120,
        family_expires_at=date.today() + timedelta(days=20),
        base_hwid_limit=10,
    )

    base_overlay_response = await check(user=member)
    base_overlay_payload = json.loads(base_overlay_response.body.decode("utf-8"))
    assert base_overlay_payload["family_entitled"] is False
    assert base_overlay_payload["family_member"]["family_is_active"] is False

    freeze.freeze_reason = "family_overlay"
    freeze.base_remaining_days = 10
    await freeze.save(update_fields=["freeze_reason", "base_remaining_days"])

    family_overlay_response = await check(user=member)
    family_overlay_payload = json.loads(family_overlay_response.body.decode("utf-8"))
    assert family_overlay_payload["family_entitled"] is True
    assert family_overlay_payload["family_member"]["family_is_active"] is True


@pytest.mark.asyncio
async def test_user_check_reports_frozen_base_for_paid_unregistered_family_owner():
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.routes.user import check

    today = date.today()
    user = await Users.create(
        id=2203,
        username="overlay_owner_unregistered",
        full_name="Overlay Owner Unregistered",
        is_registered=False,
        expired_at=today + timedelta(days=30),
        is_subscribed=True,
        hwid_limit=10,
        remnawave_uuid="00000000-0000-0000-0000-000000000023",
    )

    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=12,
        base_expires_at_snapshot=today + timedelta(days=12),
        family_expires_at=today + timedelta(days=30),
        base_hwid_limit=3,
    )

    response = await check(user=user)
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["has_frozen_base"] is True
    assert payload["active_kind"] == "family"
    assert payload["will_restore_base_after_family"] is True
    assert int(payload["base_remaining_days"]) == 12
    assert int(payload["base_hwid_limit"]) == 3
    assert payload["is_registered"] is False
    assert payload["has_completed_onboarding"] is True


@pytest.mark.asyncio
async def test_subscription_url_state_uses_public_pending_message_for_missing_remnawave_uuid():
    from bloobcat.routes import user as user_module

    state = await user_module._resolve_subscription_url_state(
        types.SimpleNamespace(
            id=3301,
            remnawave_uuid=None,
            expired_at=date.today() + timedelta(days=30),
        ),
        source="test",
    )

    assert state["subscription_url"] is None
    assert state["subscription_url_status"] == "pending"
    assert state["subscription_url_error_code"] == "account_initializing"
    assert state["subscription_url_error"] == (
        "Аккаунт ещё настраивается. Обычно это занимает несколько секунд."
    )
    assert "Failed to initialize" not in str(state["subscription_url_error"])


@pytest.mark.asyncio
async def test_subscription_url_state_returns_needs_purchase_for_expired_user(monkeypatch):
    from bloobcat.routes import user as user_module

    sentinel_calls = {"count": 0}

    async def _should_not_be_called(_user):
        sentinel_calls["count"] += 1
        return "should-not-reach"

    monkeypatch.setattr(
        user_module.remnawave_client.users,
        "get_subscription_url",
        _should_not_be_called,
    )

    state = await user_module._resolve_subscription_url_state(
        types.SimpleNamespace(
            id=3303,
            remnawave_uuid="00000000-0000-0000-0000-000000003303",
            expired_at=date.today() - timedelta(days=1),
        ),
        source="test",
    )

    assert state["subscription_url"] is None
    assert state["subscription_url_status"] == "needs_purchase"
    assert state["subscription_url_error_code"] == "no_active_subscription"
    assert state["subscription_url_error"] == (
        "Триал истёк. Оформите тариф, чтобы получить ключ подключения."
    )
    assert sentinel_calls["count"] == 0


@pytest.mark.asyncio
async def test_subscription_url_state_needs_purchase_when_expired_at_missing(monkeypatch):
    from bloobcat.routes import user as user_module

    async def _should_not_be_called(_user):
        raise AssertionError("RemnaWave should not be called when subscription is missing")

    monkeypatch.setattr(
        user_module.remnawave_client.users,
        "get_subscription_url",
        _should_not_be_called,
    )

    state = await user_module._resolve_subscription_url_state(
        types.SimpleNamespace(
            id=3304,
            remnawave_uuid="00000000-0000-0000-0000-000000003304",
            expired_at=None,
        ),
        source="test",
    )

    assert state["subscription_url_status"] == "needs_purchase"
    assert state["subscription_url_error_code"] == "no_active_subscription"


@pytest.mark.asyncio
async def test_subscription_url_state_uses_explicit_effective_expired_at_for_family_member(
    monkeypatch,
):
    from bloobcat.routes import user as user_module

    async def _ready_subscription_url(_user):
        return "https://example.invalid/family-key"

    monkeypatch.setattr(
        user_module.remnawave_client.users,
        "get_subscription_url",
        _ready_subscription_url,
    )

    # Member with own trial expired, but owner has an active plan in the future.
    state = await user_module._resolve_subscription_url_state(
        types.SimpleNamespace(
            id=3305,
            remnawave_uuid="00000000-0000-0000-0000-000000003305",
            expired_at=date.today() - timedelta(days=5),
        ),
        source="test",
        effective_expired_at=date.today() + timedelta(days=20),
    )

    assert state["subscription_url"] == "https://example.invalid/family-key"
    assert state["subscription_url_status"] == "ready"
    assert state["subscription_url_error"] is None
    assert state["subscription_url_error_code"] is None


@pytest.mark.asyncio
async def test_trial_user_response_includes_lte_usage_balance(monkeypatch):
    from bloobcat.db.users import Users
    from bloobcat.routes import user as user_module
    from bloobcat.routes.user import check

    async def fake_trial_lte_limit() -> float:
        return 1.0

    async def fake_usage_by_range(user_uuid: str, start: str, end: str):
        assert user_uuid == "00000000-0000-0000-0000-000000000031"
        assert start == "2026-05-05T21:00:00.000Z"
        assert end.endswith(".000Z")
        return {
            "response": [
                {"nodeName": "VLESS TCP REALITY RU-LTE CHTF", "total": 0.25 * user_module.BYTES_IN_GB},
                {"nodeName": "regular vpn", "total": 10 * user_module.BYTES_IN_GB},
            ]
        }

    monkeypatch.setattr(user_module, "read_trial_lte_limit_gb", fake_trial_lte_limit)
    monkeypatch.setattr(user_module.remnawave_settings, "lte_node_marker", "CHTF", raising=False)
    monkeypatch.setattr(
        user_module.remnawave_client.users,
        "get_user_usage_by_range",
        fake_usage_by_range,
        raising=False,
    )

    trial = await Users.create(
        id=2301,
        username="trial_lte",
        full_name="Trial LTE",
        is_registered=True,
        is_trial=True,
        is_subscribed=True,
        created_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        trial_started_at=datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc),
        expired_at=date.today() + timedelta(days=10),
        remnawave_uuid="00000000-0000-0000-0000-000000000031",
    )

    response = await check(user=trial)
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["active_tariff"] is None
    assert payload["trial_lte_gb_total"] == pytest.approx(1.0)
    assert payload["trial_lte_gb_used"] == pytest.approx(0.25)
    assert payload["trial_lte_gb_remaining"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_subscription_url_state_hides_backend_exception_text(monkeypatch):
    from bloobcat.routes import user as user_module

    async def _raise_subscription_error(_user):
        raise RuntimeError("Failed to get subscription URL: upstream exploded")

    monkeypatch.setattr(
        user_module.remnawave_client.users,
        "get_subscription_url",
        _raise_subscription_error,
    )

    state = await user_module._resolve_subscription_url_state(
        types.SimpleNamespace(
            id=3302,
            remnawave_uuid="00000000-0000-0000-0000-000000003302",
            expired_at=date.today() + timedelta(days=30),
        ),
        source="test",
    )

    assert state["subscription_url"] is None
    assert state["subscription_url_status"] == "error"
    assert state["subscription_url_error_code"] == "subscription_url_unavailable"
    assert state["subscription_url_error"] == (
        "Не удалось получить ключ подключения. Обновите экран или попробуйте позже."
    )
    assert "upstream exploded" not in str(state["subscription_url_error"])
