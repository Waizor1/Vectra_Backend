import asyncio
import sys
import types
from pathlib import Path
import pytest
import pytest_asyncio
from datetime import date, timedelta

from tortoise import Tortoise


def install_stubs() -> None:
    """РџРѕРґРјРµРЅСЏРµС‚ РІРЅРµС€РЅРёРµ Р·Р°РІРёСЃРёРјРѕСЃС‚Рё (YooKassa, СѓРІРµРґРѕРјР»РµРЅРёСЏ, RemnaWave, scheduler)."""
    # yookassa stubs
    yk_module = types.ModuleType("yookassa")

    class Configuration:
        account_id = None
        secret_key = None

    class Payment:
        @staticmethod
        def create(*args, **kwargs):
            # Р’РѕР·РІСЂР°С‰Р°РµРј Р·Р°РіР»СѓС€РєСѓ, С‡С‚РѕР±С‹ РєРѕРґ, РєРѕС‚РѕСЂС‹Р№ РѕР¶РёРґР°РµС‚ confirmation.confirmation_url, РЅРµ РїР°РґР°Р»
            return types.SimpleNamespace(
                id="test_payment_id",
                amount=types.SimpleNamespace(value="1.00"),
                status="pending",
                confirmation=types.SimpleNamespace(confirmation_url="https://example.com")
            )

    class Webhook:
        pass

    yk_module.Configuration = Configuration
    yk_module.Payment = Payment
    yk_module.Webhook = Webhook

    # yookassa.domain.notification stubs
    yk_domain = types.ModuleType("yookassa.domain")
    yk_notification = types.ModuleType("yookassa.domain.notification")

    class WebhookNotification:
        def __init__(self, body, headers):
            self.event = "test"
            self.object = types.SimpleNamespace(id="notif_id", amount=types.SimpleNamespace(value="1.00"), status="succeeded", metadata={})

    class WebhookNotificationEventType:
        PAYMENT_SUCCEEDED = "payment.succeeded"
        PAYMENT_CANCELED = "payment.canceled"
        REFUND_SUCCEEDED = "refund.succeeded"

    yk_notification.WebhookNotification = WebhookNotification
    yk_notification.WebhookNotificationEventType = WebhookNotificationEventType

    sys.modules["yookassa"] = yk_module
    sys.modules["yookassa.domain"] = yk_domain
    sys.modules["yookassa.domain.notification"] = yk_notification

    # Prevent importing bloobcat.routes.__init__ (it pulls many unrelated routers).
    routes_pkg = types.ModuleType("bloobcat.routes")
    routes_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "bloobcat" / "routes")]
    remnawave_pkg = types.ModuleType("bloobcat.routes.remnawave")
    remnawave_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / "bloobcat" / "routes" / "remnawave")]
    sys.modules["bloobcat.routes"] = routes_pkg
    sys.modules["bloobcat.routes.remnawave"] = remnawave_pkg

    # Package placeholders to avoid importing real bot package tree.
    bot_pkg = types.ModuleType("bloobcat.bot")
    bot_pkg.__path__ = []
    notifications_pkg = types.ModuleType("bloobcat.bot.notifications")
    notifications_pkg.__path__ = []
    sub_notifications_pkg = types.ModuleType("bloobcat.bot.notifications.subscription")
    sub_notifications_pkg.__path__ = []
    gen_notifications_pkg = types.ModuleType("bloobcat.bot.notifications.general")
    gen_notifications_pkg.__path__ = []
    trial_notifications_pkg = types.ModuleType("bloobcat.bot.notifications.trial")
    trial_notifications_pkg.__path__ = []
    sys.modules["bloobcat.bot"] = bot_pkg
    sys.modules["bloobcat.bot.notifications"] = notifications_pkg
    sys.modules["bloobcat.bot.notifications.subscription"] = sub_notifications_pkg
    sys.modules["bloobcat.bot.notifications.general"] = gen_notifications_pkg
    sys.modules["bloobcat.bot.notifications.trial"] = trial_notifications_pkg

    # Logger stub (real module requires loguru, which is not needed in tests).
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

    # Avoid hard dependency on email-validator package when route modules are imported.
    import pydantic.networks as pydantic_networks

    def _noop_import_email_validator() -> None:
        return None

    def _validate_email(value: str, /, *args, **kwargs):
        _ = args, kwargs
        return "", value

    pydantic_networks.import_email_validator = _noop_import_email_validator
    pydantic_networks.validate_email = _validate_email

    # notifications stubs
    admin_notif = types.ModuleType("bloobcat.bot.notifications.admin")

    async def on_payment(*args, **kwargs):
        return None

    async def cancel_subscription(*args, **kwargs):
        return None

    async def on_activated_bot(*args, **kwargs):
        return None

    async def notify_lte_topup(*args, **kwargs):
        return None

    async def notify_active_tariff_change(*args, **kwargs):
        return None

    async def send_admin_message(*args, **kwargs):
        return None

    admin_notif.on_payment = on_payment
    admin_notif.cancel_subscription = cancel_subscription
    admin_notif.on_activated_bot = on_activated_bot
    admin_notif.notify_lte_topup = notify_lte_topup
    admin_notif.notify_active_tariff_change = notify_active_tariff_change
    admin_notif.send_admin_message = send_admin_message
    sys.modules["bloobcat.bot.notifications.admin"] = admin_notif
    if "bloobcat.bot.notifications.admin" in sys.modules:
        mod = sys.modules["bloobcat.bot.notifications.admin"]
        mod.on_payment = on_payment
        mod.cancel_subscription = cancel_subscription
        mod.on_activated_bot = on_activated_bot
        mod.notify_lte_topup = notify_lte_topup
        mod.notify_active_tariff_change = notify_active_tariff_change
        mod.send_admin_message = send_admin_message

    sub_notif = types.ModuleType("bloobcat.bot.notifications.subscription.renewal")

    async def notify_auto_renewal_success_balance(*args, **kwargs):
        return None

    async def notify_auto_renewal_failure(*args, **kwargs):
        return None

    async def notify_renewal_success_yookassa(*args, **kwargs):
        return None

    async def notify_family_purchase_success_yookassa(*args, **kwargs):
        return None

    async def notify_payment_canceled_yookassa(*args, **kwargs):
        return None

    sub_notif.notify_auto_renewal_success_balance = notify_auto_renewal_success_balance
    sub_notif.notify_auto_renewal_failure = notify_auto_renewal_failure
    sub_notif.notify_renewal_success_yookassa = notify_renewal_success_yookassa
    sub_notif.notify_family_purchase_success_yookassa = notify_family_purchase_success_yookassa
    sub_notif.notify_payment_canceled_yookassa = notify_payment_canceled_yookassa
    sys.modules["bloobcat.bot.notifications.subscription.renewal"] = sub_notif
    if "bloobcat.bot.notifications.subscription.renewal" in sys.modules:
        mod = sys.modules["bloobcat.bot.notifications.subscription.renewal"]
        mod.notify_auto_renewal_success_balance = notify_auto_renewal_success_balance
        mod.notify_auto_renewal_failure = notify_auto_renewal_failure
        mod.notify_renewal_success_yookassa = notify_renewal_success_yookassa
        mod.notify_family_purchase_success_yookassa = notify_family_purchase_success_yookassa
        mod.notify_payment_canceled_yookassa = notify_payment_canceled_yookassa

    gen_notif = types.ModuleType("bloobcat.bot.notifications.general.referral")

    async def on_referral_payment(*args, **kwargs):
        return None

    async def on_referral_friend_bonus(*args, **kwargs):
        return None

    gen_notif.on_referral_payment = on_referral_payment
    gen_notif.on_referral_friend_bonus = on_referral_friend_bonus
    sys.modules["bloobcat.bot.notifications.general.referral"] = gen_notif

    prize_wheel_notif = types.ModuleType("bloobcat.bot.notifications.prize_wheel")

    async def notify_spin_awarded(*args, **kwargs):
        return None

    prize_wheel_notif.notify_spin_awarded = notify_spin_awarded
    sys.modules["bloobcat.bot.notifications.prize_wheel"] = prize_wheel_notif

    bot_mod = types.ModuleType("bloobcat.bot.bot")

    async def get_bot_username():
        return "BloobCatBot"

    class DummyBot:
        async def session_close(self):
            return None

    bot_mod.get_bot_username = get_bot_username
    bot_mod.bot = DummyBot()
    sys.modules["bloobcat.bot.bot"] = bot_mod
    # Trial granted notification stub
    trial_granted_mod = types.ModuleType("bloobcat.bot.notifications.trial.granted")

    async def notify_trial_granted(*args, **kwargs):
        return None

    trial_granted_mod.notify_trial_granted = notify_trial_granted
    sys.modules["bloobcat.bot.notifications.trial.granted"] = trial_granted_mod

    # RemnaWave stubs
    remna_client_mod = types.ModuleType("bloobcat.routes.remnawave.client")

    class RemnaWaveClient:
        def __init__(self, *args, **kwargs):
            pass

        class users:  # type: ignore[no-redef]
            @staticmethod
            async def update_user(*args, **kwargs):
                return None

        async def close(self):
            return None

    remna_client_mod.RemnaWaveClient = RemnaWaveClient
    sys.modules["bloobcat.routes.remnawave.client"] = remna_client_mod

    hwid_utils_mod = types.ModuleType("bloobcat.routes.remnawave.hwid_utils")

    async def cleanup_user_hwid_devices(*args, **kwargs):
        return None

    def count_active_devices(*args, **kwargs):
        return 0

    hwid_utils_mod.cleanup_user_hwid_devices = cleanup_user_hwid_devices
    hwid_utils_mod.count_active_devices = count_active_devices
    sys.modules["bloobcat.routes.remnawave.hwid_utils"] = hwid_utils_mod

    lte_utils_mod = types.ModuleType("bloobcat.routes.remnawave.lte_utils")

    async def set_lte_squad_status(*args, **kwargs):
        return None

    lte_utils_mod.set_lte_squad_status = set_lte_squad_status
    sys.modules["bloobcat.routes.remnawave.lte_utils"] = lte_utils_mod

    # Scheduler stub (РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РІРЅСѓС‚СЂРё Users.save/extend_subscription)
    scheduler_mod = types.ModuleType("bloobcat.scheduler")

    async def schedule_user_tasks(*args, **kwargs):
        return None

    scheduler_mod.schedule_user_tasks = schedule_user_tasks
    sys.modules["bloobcat.scheduler"] = scheduler_mod

    # РЎР±СЂР°СЃС‹РІР°РµРј РєСЌС€ РјРѕРґСѓР»РµР№, С‡С‚РѕР±С‹ payment РёРјРїРѕСЂС‚РёСЂРѕРІР°Р»СЃСЏ СЃРѕ СЃС‚Р°Р±Р°РјРё
    for mod_name in [
        "bloobcat.routes.payment",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
    # РРЅРёС†РёР°Р»РёР·РёСЂСѓРµРј С‚РµСЃС‚РѕРІСѓСЋ SQLite Р‘Р” РІ РїР°РјСЏС‚Рё
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
                        "bloobcat.db.notifications",
                        "bloobcat.db.referral_rewards",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )
    # Р’ SQLite СЃС…РµРјР° РЅРµ СЃРѕР·РґР°С‘С‚СЃСЏ РёР·-Р·Р° С†РёРєР»РёС‡РµСЃРєРёС… FK (Users <-> ActiveTariffs).
    # Р”Р»СЏ С‚РµСЃС‚РѕРІ СѓР±РёСЂР°РµРј FK-СЃСЃС‹Р»РєСѓ Users -> ActiveTariffs РёР· РјРµС‚Р°РґР°РЅРЅС‹С….
    from bloobcat.db.users import Users

    Users._meta.fk_fields.discard("active_tariff")
    users_active_tariff_fk = Users._meta.fields_map.get("active_tariff")
    if users_active_tariff_fk is not None:
        users_active_tariff_fk.reference = False
        users_active_tariff_fk.db_constraint = False

    # Р“РµРЅРµСЂРёСЂСѓРµРј СЃС…РµРјСѓ Р±РµР· РїСЂРѕРІРµСЂРєРё РЅР° С†РёРєР»РёС‡РµСЃРєРёРµ СЃСЃС‹Р»РєРё.
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
async def test_pay_from_balance_sets_active_tariff_without_discount_and_consumes_discount():
    from bloobcat.db.users import Users
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.routes.payment import pay

    # Arrange
    user = await Users.create(id=123, username="test", full_name="Test User", balance=10_000, is_registered=True)
    tariff = await Tariffs.create(id=1, name="РњРµСЃСЏС†", months=1, base_price=1000, progressive_multiplier=0.9, order=1)
    device_count = 2
    base_price_no_discount = tariff.calculate_price(device_count)

    # РџРµСЂСЃРѕРЅР°Р»СЊРЅР°СЏ СЃРєРёРґРєР° 20% (СЂР°Р·РѕРІР°СЏ)
    await PersonalDiscount.create(user_id=user.id, percent=20, is_permanent=False, remaining_uses=1)

    # Act вЂ” РїСЂСЏРјРѕР№ РІС‹Р·РѕРІ РІРµС‚РєРё РѕРїР»Р°С‚С‹ СЃ Р±Р°Р»Р°РЅСЃР°
    result = await pay(tariff_id=tariff.id, email="test@example.com", device_count=device_count, user=user)

    # Assert
    assert result["status"] == "success"

    # РџРµСЂРµС‡РёС‚С‹РІР°РµРј РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ Рё Р°РєС‚РёРІРЅС‹Р№ С‚Р°СЂРёС„
    user = await Users.get(id=user.id)
    assert user.active_tariff_id is not None
    at = await ActiveTariffs.get(id=user.active_tariff_id)
    assert at.price == base_price_no_discount  # РІР°Р¶РЅРѕ: Р±РµР· РїРµСЂСЃРѕРЅР°Р»СЊРЅРѕР№ СЃРєРёРґРєРё
    assert at.hwid_limit == device_count
    # РЎРєРёРґРєР° СЃРїРёСЃР°Р»Р°СЃСЊ
    d = await PersonalDiscount.get_or_none(user_id=user.id)
    assert d is not None
    assert int(d.remaining_uses or 0) == 0
    # РџР»Р°С‚С‘Р¶ Р·Р°РїРёСЃР°РЅ РїРѕ С„Р°РєС‚Сѓ СЃРїРёСЃР°РЅРЅРѕР№ СЃСѓРјРјС‹ (СЃРѕ СЃРєРёРґРєРѕР№)
    pp = await ProcessedPayments.get_or_none(user_id=user.id)
    assert pp is not None
    assert pp.status == "succeeded"
    # РџРѕРґРїРёСЃРєР° РїСЂРѕРґР»РµРЅР°
    assert user.expired_at is not None
    assert user.expired_at >= date.today()


@pytest.mark.asyncio
async def test_create_auto_payment_from_balance_no_double_discount():
    from bloobcat.db.users import Users
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.routes.payment import create_auto_payment

    # Arrange: РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ СѓР¶Рµ РёРјРµРµС‚ Р°РєС‚РёРІРЅС‹Р№ С‚Р°СЂРёС„ (РєР°Рє РїРѕСЃР»Рµ РїСЂРµРґС‹РґСѓС‰РµРіРѕ С‚РµСЃС‚Р°)
    user = await Users.create(id=456, username="auto", full_name="Auto User", balance=10_000, is_registered=True)
    # РђРєС‚РёРІРЅС‹Р№ С‚Р°СЂРёС„ СЃРѕ В«СЃРЅРёРјРєРѕРјВ» С†РµРЅС‹ Р‘Р•Р— РїРµСЂСЃРѕРЅР°Р»СЊРЅРѕР№ СЃРєРёРґРєРё
    at = await ActiveTariffs.create(user=user, name="РњРµСЃСЏС†", months=1, price=2000, hwid_limit=2, progressive_multiplier=0.9)
    user.active_tariff_id = at.id
    await user.save()

    # РџРµСЂСЃРѕРЅР°Р»СЊРЅР°СЏ СЃРєРёРґРєР° 50% (СЂР°Р·РѕРІР°СЏ)
    await PersonalDiscount.create(user_id=user.id, percent=50, is_permanent=False, remaining_uses=1)

    # Act вЂ” Р±Р°Р»Р°РЅСЃ >= С†РµРЅРµ РїРѕСЃР»Рµ СЃРєРёРґРєРё, РїР»Р°С‚С‘Р¶ С†РµР»РёРєРѕРј СЃ Р±Р°Р»Р°РЅСЃР°
    ok = await create_auto_payment(user)

    # Assert
    assert ok is True
    # РЎРєРёРґРєР° РґРѕР»Р¶РЅР° Р±С‹С‚СЊ СЃРїРёСЃР°РЅР° СЂРѕРІРЅРѕ 1 СЂР°Р·
    d = await PersonalDiscount.get_or_none(user_id=user.id)
    assert d is not None
    assert int(d.remaining_uses or 0) == 0
    # РџРѕРґРїРёСЃРєР° РїСЂРѕРґР»РµРЅР°
    user = await Users.get(id=user.id)
    assert user.expired_at is not None
    assert user.expired_at >= date.today()


@pytest.mark.asyncio
async def test_discount_respects_min_max_months():
    from bloobcat.db.users import Users
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.routes.payment import pay

    user = await Users.create(id=789, username="months", full_name="Month User", balance=10_000, is_registered=True)

    tariff_1m = await Tariffs.create(id=11, name="1m", months=1, base_price=1000, progressive_multiplier=0.9, order=1)
    tariff_6m = await Tariffs.create(id=12, name="6m", months=6, base_price=3000, progressive_multiplier=0.9, order=2)
    tariff_12m = await Tariffs.create(id=13, name="12m", months=12, base_price=4000, progressive_multiplier=0.9, order=3)

    discount = await PersonalDiscount.create(
        user_id=user.id,
        percent=30,
        is_permanent=False,
        remaining_uses=2,
        min_months=3,
        max_months=6,
    )

    # 1 РјРµСЃСЏС† вЂ” СЃРєРёРґРєР° РЅРµ РґРѕР»Р¶РЅР° РїСЂРёРјРµРЅРёС‚СЊСЃСЏ
    result_1m = await pay(tariff_id=tariff_1m.id, email="m1@example.com", device_count=1, user=user)
    assert result_1m["status"] == "success"
    user = await Users.get(id=user.id)
    assert int(user.balance) == 9_000  # 10_000 - 1000, СЃРєРёРґРєР° РЅРµ РїСЂРёРјРµРЅРµРЅР°
    discount = await PersonalDiscount.get(id=discount.id)
    assert int(discount.remaining_uses or 0) == 2

    # 6 РјРµСЃСЏС†РµРІ вЂ” СЃРєРёРґРєР° РїСЂРёРјРµРЅСЏРµС‚СЃСЏ (РїРѕРїР°РґР°РµС‚ РІ РґРёР°РїР°Р·РѕРЅ)
    result_6m = await pay(tariff_id=tariff_6m.id, email="m6@example.com", device_count=1, user=user)
    assert result_6m["status"] == "success"
    user = await Users.get(id=user.id)
    assert int(user.balance) == 6_900  # 9_000 - (3000 * 0.7)
    discount = await PersonalDiscount.get(id=discount.id)
    assert int(discount.remaining_uses or 0) == 1


@pytest.mark.asyncio
async def test_referrer_family_owner_bonus_does_not_extend_subscription_and_uses_family_limit_setting(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.referral_rewards import ReferralRewards
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_referral_first_payment_reward
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "family_devices_limit", 12, raising=False)

    today = date.today()
    owner_expired_at = today + timedelta(days=40)
    owner = await Users.create(
        id=1001,
        username="owner12",
        full_name="Owner Family",
        is_registered=True,
        expired_at=owner_expired_at,
        is_subscribed=True,
        hwid_limit=12,
    )
    referred = await Users.create(
        id=1002,
        username="friend",
        full_name="Friend User",
        is_registered=True,
        expired_at=today + timedelta(days=10),
        referred_by=owner.id,
    )
    owner_member = await Users.create(
        id=1003,
        username="member",
        full_name="Family Member",
        is_registered=True,
        expired_at=today + timedelta(days=5),
    )
    await FamilyMembers.create(
        owner=owner,
        member=owner_member,
        allocated_devices=3,
        status="active",
    )

    res = await _apply_referral_first_payment_reward(
        referred_user_id=int(referred.id),
        payment_id="ref-test-owner-family",
        amount_rub=1000,
        months=1,
        device_count=1,
    )

    assert res["applied"] is True
    assert res["applied_to_subscription"] is False

    owner_after = await Users.get(id=owner.id)
    assert owner_after.expired_at == owner_expired_at
    assert int(owner_after.referral_bonus_days_total or 0) == 7

    reward = await ReferralRewards.get(referred_user_id=referred.id, kind="first_payment")
    assert int(reward.referrer_bonus_days or 0) == 7
    assert reward.applied_to_subscription is False


@pytest.mark.asyncio
async def test_friend_bonus_does_not_extend_for_family_member_with_expired_personal_subscription(monkeypatch):
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_referral_first_payment_reward
    from bloobcat.settings import app_settings

    monkeypatch.setattr(app_settings, "family_devices_limit", 12, raising=False)

    today = date.today()
    owner = await Users.create(
        id=1101,
        username="owner",
        full_name="Owner",
        is_registered=True,
        expired_at=today + timedelta(days=30),
        is_subscribed=True,
        hwid_limit=12,
    )
    referrer = await Users.create(
        id=1102,
        username="referrer",
        full_name="Referrer",
        is_registered=True,
        expired_at=today + timedelta(days=7),
        is_subscribed=True,
    )
    member_expired_at = today - timedelta(days=2)
    member = await Users.create(
        id=1103,
        username="member",
        full_name="Family Member",
        is_registered=True,
        expired_at=member_expired_at,
        referred_by=referrer.id,
    )
    await FamilyMembers.create(
        owner=owner,
        member=member,
        allocated_devices=2,
        status="active",
    )

    res = await _apply_referral_first_payment_reward(
        referred_user_id=int(member.id),
        payment_id="ref-test-member-family",
        amount_rub=1000,
        months=1,
        device_count=1,
    )

    assert res["applied"] is True
    assert res["friend_applied_to_subscription"] is False

    member_after = await Users.get(id=member.id)
    assert member_after.expired_at == member_expired_at
    assert member_after.referral_first_payment_rewarded is True

    referrer_after = await Users.get(id=referrer.id)
    assert int(referrer_after.referral_bonus_days_total or 0) == 7
