import json
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from tortoise import Tortoise


def install_stubs() -> None:
    yk_module = types.ModuleType('yookassa')

    class Payment:
        @staticmethod
        def create(*args, **kwargs):
            return None

    yk_module.Payment = Payment
    sys.modules['yookassa'] = yk_module

    routes_pkg = types.ModuleType('bloobcat.routes')
    routes_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / 'bloobcat' / 'routes')]
    remnawave_pkg = types.ModuleType('bloobcat.routes.remnawave')
    remnawave_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / 'bloobcat' / 'routes' / 'remnawave')]
    sys.modules['bloobcat.routes'] = routes_pkg
    sys.modules['bloobcat.routes.remnawave'] = remnawave_pkg

    bot_pkg = types.ModuleType('bloobcat.bot')
    bot_pkg.__path__ = []
    notifications_pkg = types.ModuleType('bloobcat.bot.notifications')
    notifications_pkg.__path__ = []
    trial_notifications_pkg = types.ModuleType('bloobcat.bot.notifications.trial')
    trial_notifications_pkg.__path__ = []
    sys.modules['bloobcat.bot'] = bot_pkg
    sys.modules['bloobcat.bot.notifications'] = notifications_pkg
    sys.modules['bloobcat.bot.notifications.trial'] = trial_notifications_pkg

    logger_mod = types.ModuleType('bloobcat.logger')

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
    sys.modules['bloobcat.logger'] = logger_mod

    # Avoid hard dependency on email-validator package for route module import.
    import pydantic.networks as pydantic_networks

    def _noop_import_email_validator() -> None:
        return None

    def _validate_email(value: str, /, *args, **kwargs):
        _ = args, kwargs
        return '', value

    pydantic_networks.import_email_validator = _noop_import_email_validator
    pydantic_networks.validate_email = _validate_email

    admin_notif = types.ModuleType('bloobcat.bot.notifications.admin')

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
    sys.modules['bloobcat.bot.notifications.admin'] = admin_notif

    bot_mod = types.ModuleType('bloobcat.bot.bot')

    async def get_bot_username():
        return 'TriadVPN_bot'

    bot_mod.get_bot_username = get_bot_username
    sys.modules['bloobcat.bot.bot'] = bot_mod

    trial_granted_mod = types.ModuleType('bloobcat.bot.notifications.trial.granted')

    async def notify_trial_granted(*args, **kwargs):
        return None

    trial_granted_mod.notify_trial_granted = notify_trial_granted
    sys.modules['bloobcat.bot.notifications.trial.granted'] = trial_granted_mod

    remna_client_mod = types.ModuleType('bloobcat.routes.remnawave.client')

    class RemnaWaveClient:
        def __init__(self, *args, **kwargs):
            pass

        class users:  # type: ignore[no-redef]
            @staticmethod
            async def get_subscription_url(*args, **kwargs):
                return 'https://example.test/subscription'

            @staticmethod
            async def get_user_hwid_devices(*args, **kwargs):
                return {}

            @staticmethod
            async def update_user(*args, **kwargs):
                return None

        async def close(self):
            return None

    remna_client_mod.RemnaWaveClient = RemnaWaveClient
    sys.modules['bloobcat.routes.remnawave.client'] = remna_client_mod

    hwid_utils_mod = types.ModuleType('bloobcat.routes.remnawave.hwid_utils')

    async def cleanup_user_hwid_devices(*args, **kwargs):
        return None

    def count_active_devices(*args, **kwargs):
        return 0

    hwid_utils_mod.cleanup_user_hwid_devices = cleanup_user_hwid_devices
    hwid_utils_mod.count_active_devices = count_active_devices
    sys.modules['bloobcat.routes.remnawave.hwid_utils'] = hwid_utils_mod

    scheduler_mod = types.ModuleType('bloobcat.scheduler')

    async def schedule_user_tasks(*args, **kwargs):
        return None

    scheduler_mod.schedule_user_tasks = schedule_user_tasks
    sys.modules['bloobcat.scheduler'] = scheduler_mod


@pytest.fixture(scope='module', autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
    await Tortoise.init(
        config={
            'connections': {'default': 'sqlite://:memory:'},
            'apps': {
                'models': {
                    'models': [
                        'bloobcat.db.users',
                        'bloobcat.db.active_tariff',
                        'bloobcat.db.family_members',
                        'bloobcat.db.family_invites',
                    ],
                    'default_connection': 'default',
                }
            },
        }
    )

    from bloobcat.db.users import Users

    Users._meta.fk_fields.discard('active_tariff')
    users_active_tariff_fk = Users._meta.fields_map.get('active_tariff')
    if users_active_tariff_fk is not None:
        users_active_tariff_fk.reference = False
        users_active_tariff_fk.db_constraint = False

    from tortoise.backends.sqlite.schema_generator import SqliteSchemaGenerator

    client = Tortoise.get_connection('default')
    generator = SqliteSchemaGenerator(client)
    models_to_create = []
    try:
        maybe_models = generator._get_models_to_create(models_to_create)
        if maybe_models is not None:
            models_to_create = maybe_models
    except TypeError:
        models_to_create = generator._get_models_to_create()
    tables = [generator._get_table_sql(model, safe=True) for model in models_to_create]
    creation_sql = '\n'.join(
        [t['table_creation_string'] for t in tables]
        + [m for t in tables for m in t['m2m_tables']]
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
    from bloobcat.routes.user import check
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, 'family_devices_limit', 12, raising=False)

    owner = await Users.create(
        id=2001,
        username='owner',
        full_name='Owner',
        is_registered=True,
        expired_at=date.today() + timedelta(days=30),
        is_subscribed=True,
        hwid_limit=12,
        remnawave_uuid='00000000-0000-0000-0000-000000000001',
    )
    member_a = await Users.create(id=2002, username='m1', full_name='Member 1', is_registered=True)
    member_b = await Users.create(id=2003, username='m2', full_name='Member 2', is_registered=True)

    await FamilyMembers.create(owner=owner, member=member_a, allocated_devices=3, status='active')
    await FamilyMembers.create(owner=owner, member=member_b, allocated_devices=2, status='active')

    await FamilyInvites.create(
        owner=owner,
        allocated_devices=1,
        token_hash='token_active',
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        max_uses=1,
        used_count=0,
    )
    await FamilyInvites.create(
        owner=owner,
        allocated_devices=1,
        token_hash='token_revoked',
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        max_uses=1,
        used_count=0,
        revoked_at=datetime.now(timezone.utc),
    )

    response = await check(user=owner)
    payload = json.loads(response.body.decode('utf-8'))

    assert payload['family_entitled'] is True
    assert payload['subscription_context'] == 'family_owner'
    assert payload['devices_limit'] == 7
    assert payload['family_owner'] == {
        'is_owner': True,
        'family_devices_total': 12,
        'allocated_devices_total': 5,
        'owner_remaining_devices': 7,
        'active_members_count': 2,
        'active_invites_count': 1,
    }


@pytest.mark.asyncio
async def test_user_owner_remaining_devices_can_be_zero(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.users import Users
    from bloobcat.routes.user import check
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, 'family_devices_limit', 10, raising=False)

    owner = await Users.create(
        id=2051,
        username='owner_zero',
        full_name='Owner Zero',
        is_registered=True,
        expired_at=date.today() + timedelta(days=30),
        is_subscribed=True,
        hwid_limit=10,
        remnawave_uuid='00000000-0000-0000-0000-000000000051',
    )

    for idx in range(10):
        member = await Users.create(
            id=2060 + idx,
            username=f'zero_m{idx}',
            full_name=f'Zero Member {idx}',
            is_registered=True,
        )
        await FamilyMembers.create(
            owner=owner,
            member=member,
            allocated_devices=1,
            status='active',
        )

    response = await check(user=owner)
    payload = json.loads(response.body.decode('utf-8'))

    assert payload['family_entitled'] is True
    assert payload['subscription_context'] == 'family_owner'
    assert payload['devices_limit'] == 0
    assert payload['family_owner'] == {
        'is_owner': True,
        'family_devices_total': 10,
        'allocated_devices_total': 10,
        'owner_remaining_devices': 0,
        'active_members_count': 10,
        'active_invites_count': 0,
    }


@pytest.mark.asyncio
async def test_user_family_entitled_for_member_and_non_family(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.users import Users
    from bloobcat.routes.user import check
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, 'family_devices_limit', 12, raising=False)

    owner = await Users.create(
        id=2101,
        username='owner2',
        full_name='Owner 2',
        is_registered=True,
        expired_at=date.today() + timedelta(days=20),
        is_subscribed=True,
        hwid_limit=12,
        remnawave_uuid='00000000-0000-0000-0000-000000000011',
    )
    member = await Users.create(
        id=2102,
        username='member2',
        full_name='Member 2',
        is_registered=True,
        expired_at=date.today() + timedelta(days=2),
        is_subscribed=True,
        hwid_limit=1,
        remnawave_uuid='00000000-0000-0000-0000-000000000012',
    )
    regular_user = await Users.create(
        id=2103,
        username='regular',
        full_name='Regular',
        is_registered=True,
        expired_at=date.today() + timedelta(days=10),
        is_subscribed=True,
        hwid_limit=3,
        remnawave_uuid='00000000-0000-0000-0000-000000000013',
    )

    await FamilyMembers.create(owner=owner, member=member, allocated_devices=2, status='active')

    member_response = await check(user=member)
    member_payload = json.loads(member_response.body.decode('utf-8'))
    assert member_payload['family_entitled'] is True
    assert member_payload['subscription_context'] == 'family_member'
    assert member_payload['devices_limit'] == 2
    assert member_payload['family_owner'] is None

    regular_response = await check(user=regular_user)
    regular_payload = json.loads(regular_response.body.decode('utf-8'))
    assert regular_payload['family_entitled'] is False
    assert regular_payload['subscription_context'] == 'personal'
    assert regular_payload['family_owner'] is None
