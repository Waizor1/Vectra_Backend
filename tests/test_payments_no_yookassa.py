import asyncio
import importlib
import sys
import types
from pathlib import Path
import pytest
import pytest_asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tortoise import Tortoise

try:
    from tests._sqlite_datetime_compat import register_sqlite_datetime_compat
except ModuleNotFoundError:  # pragma: no cover - root/workdir import compatibility
    from _sqlite_datetime_compat import register_sqlite_datetime_compat


register_sqlite_datetime_compat()


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
                confirmation=types.SimpleNamespace(
                    confirmation_url="https://example.com"
                ),
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
            self.object = types.SimpleNamespace(
                id="notif_id",
                amount=types.SimpleNamespace(value="1.00"),
                status="succeeded",
                metadata={},
            )

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
    routes_pkg.__path__ = [
        str(Path(__file__).resolve().parents[1] / "bloobcat" / "routes")
    ]
    remnawave_pkg = types.ModuleType("bloobcat.routes.remnawave")
    remnawave_pkg.__path__ = [
        str(Path(__file__).resolve().parents[1] / "bloobcat" / "routes" / "remnawave")
    ]
    sys.modules["bloobcat.routes"] = routes_pkg
    sys.modules["bloobcat.routes.remnawave"] = remnawave_pkg

    notifications_root = Path(__file__).resolve().parents[1] / "bloobcat" / "bot" / "notifications"
    subscription_notifications_root = notifications_root / "subscription"
    general_notifications_root = notifications_root / "general"
    trial_notifications_root = notifications_root / "trial"

    # Package placeholders to avoid importing the whole bot package tree.
    bot_pkg = types.ModuleType("bloobcat.bot")
    bot_pkg.__path__ = []
    notifications_pkg = types.ModuleType("bloobcat.bot.notifications")
    notifications_pkg.__path__ = [str(notifications_root)]
    sub_notifications_pkg = types.ModuleType("bloobcat.bot.notifications.subscription")
    sub_notifications_pkg.__path__ = [str(subscription_notifications_root)]
    gen_notifications_pkg = types.ModuleType("bloobcat.bot.notifications.general")
    gen_notifications_pkg.__path__ = [str(general_notifications_root)]
    trial_notifications_pkg = types.ModuleType("bloobcat.bot.notifications.trial")
    trial_notifications_pkg.__path__ = [str(trial_notifications_root)]
    sys.modules["bloobcat.bot"] = bot_pkg
    sys.modules["bloobcat.bot.notifications"] = notifications_pkg
    sys.modules["bloobcat.bot.notifications.subscription"] = sub_notifications_pkg
    sys.modules["bloobcat.bot.notifications.general"] = gen_notifications_pkg
    sys.modules["bloobcat.bot.notifications.trial"] = trial_notifications_pkg

    keyboard_mod = types.ModuleType("bloobcat.bot.keyboard")

    async def webapp_inline_button(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    keyboard_mod.webapp_inline_button = webapp_inline_button
    sys.modules["bloobcat.bot.keyboard"] = keyboard_mod

    error_handler_mod = types.ModuleType("bloobcat.bot.error_handler")

    async def handle_telegram_forbidden_error(*args, **kwargs):
        return None

    async def handle_telegram_bad_request(*args, **kwargs):
        return None

    async def reset_user_failed_count(*args, **kwargs):
        return None

    error_handler_mod.handle_telegram_forbidden_error = handle_telegram_forbidden_error
    error_handler_mod.handle_telegram_bad_request = handle_telegram_bad_request
    error_handler_mod.reset_user_failed_count = reset_user_failed_count
    sys.modules["bloobcat.bot.error_handler"] = error_handler_mod

    localization_mod = types.ModuleType("bloobcat.bot.notifications.localization")

    def get_user_locale(*args, **kwargs):
        return "ru"

    localization_mod.get_user_locale = get_user_locale
    sys.modules["bloobcat.bot.notifications.localization"] = localization_mod

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

    # pydantic>=2.12 compatibility: bypass tortoise pydantic schema generation in tests.
    import tortoise.contrib.pydantic as tortoise_pydantic_pkg
    import tortoise.contrib.pydantic.creator as tortoise_pydantic_creator

    def _compat_pydantic_model_creator(*args, **kwargs):
        _ = args, kwargs

        class _CompatModel:
            @classmethod
            async def from_tortoise_orm(cls, obj):
                return obj

        return _CompatModel

    tortoise_pydantic_pkg.pydantic_model_creator = _compat_pydantic_model_creator
    tortoise_pydantic_creator.pydantic_model_creator = _compat_pydantic_model_creator

    # notifications stubs
    admin_notif = types.ModuleType("bloobcat.bot.notifications.admin")

    async def on_payment(*args, **kwargs):
        return None

    async def cancel_subscription(*args, **kwargs):
        return None

    async def notify_manual_payment_canceled(*args, **kwargs):
        return None

    async def notify_frozen_base_activation(*args, **kwargs):
        return None

    async def notify_frozen_family_activation(*args, **kwargs):
        return None

    async def notify_frozen_base_auto_resumed_admin(*args, **kwargs):
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
    admin_notif.notify_manual_payment_canceled = notify_manual_payment_canceled
    admin_notif.notify_frozen_base_activation = notify_frozen_base_activation
    admin_notif.notify_frozen_family_activation = notify_frozen_family_activation
    admin_notif.notify_frozen_base_auto_resumed_admin = (
        notify_frozen_base_auto_resumed_admin
    )
    admin_notif.on_activated_bot = on_activated_bot
    admin_notif.notify_lte_topup = notify_lte_topup
    admin_notif.notify_active_tariff_change = notify_active_tariff_change
    admin_notif.send_admin_message = send_admin_message
    sys.modules["bloobcat.bot.notifications.admin"] = admin_notif
    if "bloobcat.bot.notifications.admin" in sys.modules:
        mod = sys.modules["bloobcat.bot.notifications.admin"]
        mod.on_payment = on_payment
        mod.cancel_subscription = cancel_subscription
        mod.notify_manual_payment_canceled = notify_manual_payment_canceled
        mod.notify_frozen_base_activation = notify_frozen_base_activation
        mod.notify_frozen_family_activation = notify_frozen_family_activation
        mod.notify_frozen_base_auto_resumed_admin = (
            notify_frozen_base_auto_resumed_admin
        )
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

    async def notify_frozen_base_activation_success(*args, **kwargs):
        return None

    async def notify_frozen_family_activation_success(*args, **kwargs):
        return None

    async def notify_frozen_base_auto_resumed_success(*args, **kwargs):
        return None

    sub_notif.notify_auto_renewal_success_balance = notify_auto_renewal_success_balance
    sub_notif.notify_auto_renewal_failure = notify_auto_renewal_failure
    sub_notif.notify_renewal_success_yookassa = notify_renewal_success_yookassa
    sub_notif.notify_family_purchase_success_yookassa = (
        notify_family_purchase_success_yookassa
    )
    sub_notif.notify_payment_canceled_yookassa = notify_payment_canceled_yookassa
    sub_notif.notify_frozen_base_activation_success = (
        notify_frozen_base_activation_success
    )
    sub_notif.notify_frozen_family_activation_success = (
        notify_frozen_family_activation_success
    )
    sub_notif.notify_frozen_base_auto_resumed_success = (
        notify_frozen_base_auto_resumed_success
    )
    sys.modules["bloobcat.bot.notifications.subscription.renewal"] = sub_notif
    if "bloobcat.bot.notifications.subscription.renewal" in sys.modules:
        mod = sys.modules["bloobcat.bot.notifications.subscription.renewal"]
        mod.notify_auto_renewal_success_balance = notify_auto_renewal_success_balance
        mod.notify_auto_renewal_failure = notify_auto_renewal_failure
        mod.notify_renewal_success_yookassa = notify_renewal_success_yookassa
        mod.notify_family_purchase_success_yookassa = (
            notify_family_purchase_success_yookassa
        )
        mod.notify_payment_canceled_yookassa = notify_payment_canceled_yookassa
        mod.notify_frozen_base_activation_success = (
            notify_frozen_base_activation_success
        )
        mod.notify_frozen_family_activation_success = (
            notify_frozen_family_activation_success
        )
        mod.notify_frozen_base_auto_resumed_success = (
            notify_frozen_base_auto_resumed_success
        )

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
        return "VectraConnect_bot"

    class DummyBot:
        async def send_message(self, *args, **kwargs):
            return None

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


@pytest.fixture(autouse=True)
def _use_yookassa_provider_for_legacy_payment_tests(monkeypatch, _install_stubs_once):
    from pydantic import SecretStr

    from bloobcat.routes import payment as payment_module

    monkeypatch.setattr(payment_module.payment_settings, "provider", "yookassa")
    monkeypatch.setattr(payment_module.payment_settings, "auto_renewal_mode", "yookassa")
    monkeypatch.setattr(payment_module.yookassa_settings, "shop_id", "test-shop")
    monkeypatch.setattr(payment_module.yookassa_settings, "secret_key", SecretStr("test-secret"))
    monkeypatch.setattr(payment_module.yookassa_settings, "webhook_secret", "test-webhook-secret")
    payment_module._configure_yookassa_if_available()


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
                        "bloobcat.db.subscription_freezes",
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
    user = await Users.create(
        id=123,
        username="test",
        full_name="Test User",
        balance=10_000,
        is_registered=True,
    )
    tariff = await Tariffs.create(
        id=1,
        name="РњРµСЃСЏС†",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
    )
    device_count = 2
    base_price_no_discount = tariff.calculate_price(device_count)

    # РџРµСЂСЃРѕРЅР°Р»СЊРЅР°СЏ СЃРєРёРґРєР° 20% (СЂР°Р·РѕРІР°СЏ)
    await PersonalDiscount.create(
        user_id=user.id, percent=20, is_permanent=False, remaining_uses=1
    )

    # Act вЂ” РїСЂСЏРјРѕР№ РІС‹Р·РѕРІ РІРµС‚РєРё РѕРїР»Р°С‚С‹ СЃ Р±Р°Р»Р°РЅСЃР°
    result = await pay(
        tariff_id=tariff.id,
        email="test@example.com",
        device_count=device_count,
        user=user,
    )

    # Assert
    assert result["status"] == "success"

    # РџРµСЂРµС‡РёС‚С‹РІР°РµРј РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ Рё Р°РєС‚РёРІРЅС‹Р№ С‚Р°СЂРёС„
    user = await Users.get(id=user.id)
    assert user.active_tariff_id is not None
    at = await ActiveTariffs.get(id=user.active_tariff_id)
    assert (
        at.price == base_price_no_discount
    )  # РІР°Р¶РЅРѕ: Р±РµР· РїРµСЂСЃРѕРЅР°Р»СЊРЅРѕР№ СЃРєРёРґРєРё
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
async def test_pay_partial_uses_client_request_id_as_yookassa_idempotence_key(
    monkeypatch,
):
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    import bloobcat.routes.payment as payment_module
    from bloobcat.routes.payment import pay

    user = await Users.create(
        id=1300,
        username="manual-partial",
        full_name="Manual Partial User",
        balance=0,
        is_registered=True,
    )
    tariff = await Tariffs.create(
        id=1301,
        name="manual-partial-plan",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=10,
        is_active=True,
        devices_limit_default=3,
        devices_limit_family=10,
    )

    captured: dict[str, object] = {}

    def _fake_payment_create(payment_data, idempotence_key):
        captured["payment_data"] = payment_data
        captured["idempotence_key"] = idempotence_key
        return types.SimpleNamespace(
            id="manual-partial-1300",
            amount=types.SimpleNamespace(value=str(payment_data["amount"]["value"])),
            status="pending",
            confirmation=types.SimpleNamespace(
                confirmation_url="https://example.com/manual-partial"
            ),
        )

    payment_global = pay.__globals__.get("Payment")
    if payment_global is not None:
        monkeypatch.setattr(payment_global, "create", _fake_payment_create)
    monkeypatch.setattr(payment_module.Payment, "create", _fake_payment_create)

    result = await pay(
        tariff_id=tariff.id,
        email="manual-partial@example.com",
        device_count=3,
        client_request_id="miniapp-pay-1300",
        user=user,
    )

    assert result["payment_id"] == "manual-partial-1300"
    assert result["redirect_to"] == "https://example.com/manual-partial"
    assert captured["idempotence_key"] == "miniapp-pay-1300"
    metadata = dict(captured["payment_data"]["metadata"])  # type: ignore[index]
    assert metadata["client_request_id"] == "miniapp-pay-1300"
    assert int(metadata["tariff_id"]) == int(tariff.id)
    assert int(metadata["device_count"]) == 3
    assert int(metadata["quote_subscription_price"]) == int(metadata["base_full_price"])
    assert int(metadata["quote_total_price"]) == int(metadata["discounted_price"]) + int(metadata["lte_cost"])


@pytest.mark.asyncio
async def test_pay_balance_retry_after_post_debit_crash_reuses_same_debit(monkeypatch):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    import bloobcat.routes.payment as payment_module
    from bloobcat.routes.payment import _build_balance_payment_id, pay

    user = await Users.create(
        id=1310,
        username="balance-retry-1310",
        full_name="Balance Retry User",
        balance=1000,
        is_registered=True,
        hwid_limit=1,
    )
    tariff = await Tariffs.create(
        id=1311,
        name="balance-retry-plan",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=11,
        is_active=True,
        devices_limit_default=3,
        devices_limit_family=10,
    )

    client_request_id = "balance-crash-1310"
    payment_id = _build_balance_payment_id(
        user_id=int(user.id),
        client_request_id=client_request_id,
    )

    original_mark_payment_effect_success = payment_module._mark_payment_effect_success
    crash_state = {"armed": True, "calls": 0}

    async def _crash_once_post_debit(*args, **kwargs):
        crash_state["calls"] += 1
        if crash_state["armed"]:
            crash_state["armed"] = False
            raise RuntimeError("simulated post-debit crash")
        return await original_mark_payment_effect_success(*args, **kwargs)

    monkeypatch.setattr(
        payment_module,
        "_mark_payment_effect_success",
        _crash_once_post_debit,
        raising=False,
    )
    monkeypatch.setitem(
        pay.__globals__,
        "_mark_payment_effect_success",
        _crash_once_post_debit,
    )

    with pytest.raises(RuntimeError, match="simulated post-debit crash"):
        await pay(
            tariff_id=tariff.id,
            email="balance-retry-1310@example.com",
            device_count=1,
            client_request_id=client_request_id,
            user=user,
        )

    user_after_crash = await Users.get(id=user.id)
    assert int(float(user_after_crash.balance)) == 0

    row_after_crash = await ProcessedPayments.get(payment_id=payment_id)
    assert row_after_crash.effect_applied is False
    assert row_after_crash.processing_state == "failed"
    assert int(float(row_after_crash.amount_from_balance)) == 1000
    assert "RuntimeError:simulated post-debit crash" in str(
        row_after_crash.last_error or ""
    )
    assert crash_state["calls"] == 1

    retry_result = await pay(
        tariff_id=tariff.id,
        email="balance-retry-1310@example.com",
        device_count=1,
        client_request_id=client_request_id,
        user=user_after_crash,
    )

    assert retry_result["status"] == "success"

    user_after_retry = await Users.get(id=user.id)
    assert int(float(user_after_retry.balance)) == 0
    assert user_after_retry.expired_at is not None

    row_after_retry = await ProcessedPayments.get(payment_id=payment_id)
    assert row_after_retry.effect_applied is True
    assert row_after_retry.processing_state == "applied"
    assert crash_state["calls"] == 2


@pytest.mark.asyncio
async def test_create_auto_payment_from_balance_no_double_discount():
    from bloobcat.db.users import Users
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.routes.payment import create_auto_payment

    # Arrange: РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ СѓР¶Рµ РёРјРµРµС‚ Р°РєС‚РёРІРЅС‹Р№ С‚Р°СЂРёС„ (РєР°Рє РїРѕСЃР»Рµ РїСЂРµРґС‹РґСѓС‰РµРіРѕ С‚РµСЃС‚Р°)
    user = await Users.create(
        id=456,
        username="auto",
        full_name="Auto User",
        balance=10_000,
        is_registered=True,
    )
    # РђРєС‚РёРІРЅС‹Р№ С‚Р°СЂРёС„ СЃРѕ В«СЃРЅРёРјРєРѕРјВ» С†РµРЅС‹ Р‘Р•Р— РїРµСЂСЃРѕРЅР°Р»СЊРЅРѕР№ СЃРєРёРґРєРё
    at = await ActiveTariffs.create(
        user=user,
        name="РњРµСЃСЏС†",
        months=1,
        price=2000,
        hwid_limit=2,
        progressive_multiplier=0.9,
    )
    user.active_tariff_id = at.id
    await user.save()

    # РџРµСЂСЃРѕРЅР°Р»СЊРЅР°СЏ СЃРєРёРґРєР° 50% (СЂР°Р·РѕРІР°СЏ)
    await PersonalDiscount.create(
        user_id=user.id, percent=50, is_permanent=False, remaining_uses=1
    )

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
async def test_fallback_success_consumes_discount_once():
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_succeeded_payment_fallback

    user = await Users.create(
        id=124,
        username="fallback-success",
        full_name="Fallback Success User",
        balance=0,
        is_registered=True,
        hwid_limit=1,
    )
    tariff = await Tariffs.create(
        id=2,
        name="fallback-1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=2,
        is_active=True,
        devices_limit_default=3,
        devices_limit_family=10,
    )
    discount = await PersonalDiscount.create(
        user_id=user.id,
        percent=20,
        is_permanent=False,
        remaining_uses=1,
    )

    yk_payment = types.SimpleNamespace(
        id="fallback-discount-success-124",
        amount=types.SimpleNamespace(value="800.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 1,
        "tariff_id": tariff.id,
        "device_count": 1,
        "amount_from_balance": 0,
        "discount_id": discount.id,
        "discount_percent": 20,
        "discounted_price": 800,
        "base_full_price": 1000,
        "lte_gb": 0,
    }

    applied = await _apply_succeeded_payment_fallback(yk_payment, user, metadata)

    assert applied is True
    discount_after = await PersonalDiscount.get(id=discount.id)
    assert int(discount_after.remaining_uses or 0) == 0

    processed = await ProcessedPayments.get(payment_id="fallback-discount-success-124")
    assert processed.effect_applied is True
    assert processed.processing_state == "applied"


@pytest.mark.asyncio
async def test_fallback_late_failure_does_not_consume_discount(monkeypatch):
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import _apply_succeeded_payment_fallback

    user = await Users.create(
        id=125,
        username="fallback-failure",
        full_name="Fallback Failure User",
        balance=0,
        is_registered=True,
        hwid_limit=1,
    )
    tariff = await Tariffs.create(
        id=3,
        name="fallback-1m-failure",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=3,
        is_active=True,
        devices_limit_default=3,
        devices_limit_family=10,
    )
    discount = await PersonalDiscount.create(
        user_id=user.id,
        percent=20,
        is_permanent=False,
        remaining_uses=1,
    )

    original_save = Users.save
    save_calls = {"count": 0}

    async def _fail_on_first_user_save(self, *args, **kwargs):
        if int(self.id) == int(user.id) and save_calls["count"] == 0:
            save_calls["count"] += 1
            raise RuntimeError("simulated late failure")
        return await original_save(self, *args, **kwargs)

    monkeypatch.setattr(Users, "save", _fail_on_first_user_save)

    yk_payment = types.SimpleNamespace(
        id="fallback-discount-failure-125",
        amount=types.SimpleNamespace(value="800.00"),
        payment_method=None,
    )
    metadata = {
        "user_id": user.id,
        "month": 1,
        "tariff_id": tariff.id,
        "device_count": 1,
        "amount_from_balance": 0,
        "discount_id": discount.id,
        "discount_percent": 20,
        "discounted_price": 800,
        "base_full_price": 1000,
        "lte_gb": 0,
    }

    with pytest.raises(RuntimeError, match="simulated late failure"):
        await _apply_succeeded_payment_fallback(yk_payment, user, metadata)

    discount_after = await PersonalDiscount.get(id=discount.id)
    assert int(discount_after.remaining_uses or 0) == 1

    processed = await ProcessedPayments.get(payment_id="fallback-discount-failure-125")
    assert processed.effect_applied is False
    assert processed.processing_state == "processing"


@pytest.mark.asyncio
async def test_create_auto_payment_partial_contains_tariff_id_device_count_and_kind(
    monkeypatch,
):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    import bloobcat.routes.payment as payment_module
    from bloobcat.routes.payment import create_auto_payment

    user = await Users.create(
        id=1301,
        username="auto-partial",
        full_name="Auto Partial User",
        email="auto-partial@example.com",
        balance=10,
        is_registered=True,
        renew_id="renew_method_1301",
        hwid_limit=3,
    )
    tariff = await Tariffs.create(
        id=1302,
        name="base-1m-auto",
        months=1,
        base_price=2000,
        progressive_multiplier=0.9,
        order=10,
        is_active=True,
        devices_limit_default=3,
        devices_limit_family=10,
    )
    active_tariff = await ActiveTariffs.create(
        user=user,
        name=tariff.name,
        months=1,
        price=tariff.calculate_price(3),
        hwid_limit=3,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active_tariff.id
    await user.save(update_fields=["active_tariff_id"])

    captured: dict[str, object] = {}

    def _fake_payment_create(payment_data, idempotence_key):
        captured["payment_data"] = payment_data
        captured["idempotence_key"] = idempotence_key
        return types.SimpleNamespace(
            id="auto-partial-1301",
            amount=types.SimpleNamespace(value=str(payment_data["amount"]["value"])),
            status="pending",
            confirmation=types.SimpleNamespace(
                confirmation_url="https://example.com/auto-partial"
            ),
        )

    payment_global = create_auto_payment.__globals__.get("Payment")
    if payment_global is not None:
        monkeypatch.setattr(payment_global, "create", _fake_payment_create)
    monkeypatch.setattr(payment_module.Payment, "create", _fake_payment_create)

    ok = await create_auto_payment(user)
    assert ok is True
    assert "payment_data" in captured

    metadata = dict(captured["payment_data"]["metadata"])  # type: ignore[index]
    assert metadata["is_auto"] is True
    assert int(metadata["device_count"]) == 3
    assert metadata["tariff_kind"] == "family"
    assert int(metadata["tariff_id"]) == int(tariff.id)


@pytest.mark.asyncio
async def test_create_auto_payment_balance_base_overlay_refreshes_frozen_snapshot(
    monkeypatch,
):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    import bloobcat.routes.payment as payment_module
    from bloobcat.routes.payment import create_auto_payment

    today = date.today()
    family_expired_at = today + timedelta(days=40)

    user = await Users.create(
        id=1306,
        username="auto-balance-overlay",
        full_name="Auto Balance Overlay",
        balance=10_000,
        is_registered=True,
        expired_at=family_expired_at,
        hwid_limit=10,
    )
    active_tariff = await ActiveTariffs.create(
        user=user,
        name="base-1m-1d",
        months=1,
        price=1000,
        hwid_limit=1,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active_tariff.id
    await user.save(update_fields=["active_tariff_id"])

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=5,
        base_expires_at_snapshot=today + timedelta(days=5),
        family_expires_at=family_expired_at,
        base_tariff_name="trial",
        base_tariff_months=1,
        base_tariff_price=0,
        base_hwid_limit=1,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.0,
        base_residual_day_fraction=0.0,
    )

    replay_calls: list[dict[str, object]] = []

    async def _noop(*args, **kwargs):
        _ = args, kwargs
        return None

    async def _unexpected_direct_notify(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("legacy direct payment notification path should not run")

    async def _capture_replay(**kwargs):
        replay_calls.append(kwargs)
        return None

    monkeypatch.setattr(payment_module, "_award_partner_cashback", _noop, raising=False)
    monkeypatch.setattr(
        payment_module, "on_payment", _unexpected_direct_notify, raising=False
    )
    monkeypatch.setattr(
        payment_module,
        "notify_auto_renewal_success_balance",
        _unexpected_direct_notify,
        raising=False,
    )
    monkeypatch.setattr(
        payment_module,
        "_replay_payment_notifications_if_needed",
        _capture_replay,
        raising=False,
    )
    monkeypatch.setitem(
        create_auto_payment.__globals__,
        "_replay_payment_notifications_if_needed",
        _capture_replay,
    )
    monkeypatch.setattr(payment_module, "notify_spin_awarded", _noop, raising=False)

    ok = await create_auto_payment(user)

    assert ok is True
    assert len(replay_calls) == 1

    user_after = await Users.get(id=user.id)
    freeze_after = await SubscriptionFreezes.get(id=freeze.id)
    replay_call = replay_calls[0]
    context = await payment_module._build_payment_notification_context(
        user=user_after,
        days=int(replay_call["days"]),
        amount_external=float(replay_call["amount_external"]),
        amount_from_balance=float(replay_call["amount_from_balance"]),
        device_count=int(replay_call["device_count"]),
        months=int(replay_call["months"]),
        is_auto_payment=bool(replay_call["is_auto_payment"]),
        discount_percent=replay_call["discount_percent"],
        old_expired_at=replay_call["old_expired_at"],
        new_expired_at=replay_call["new_expired_at"],
        lte_gb_total=int(replay_call["lte_gb_total"]),
        method=str(replay_call["method"]),
    )

    assert user_after.expired_at == family_expired_at
    assert replay_call["method"] == "balance_auto"
    assert bool(replay_call["is_auto_payment"]) is True
    assert context.migration_direction == "family_to_base"
    assert int(freeze_after.base_remaining_days or 0) > 5
    assert freeze_after.base_tariff_name == "base-1m-1d"
    assert int(freeze_after.base_tariff_months or 0) == 1
    assert int(freeze_after.base_tariff_price or 0) == 1000
    assert int(freeze_after.base_hwid_limit or 0) == 1
    assert float(freeze_after.base_progressive_multiplier or 0.0) == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_payment_status_entitlements_ready_stays_false_for_early_success(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import get_payment_status
    from bloobcat.routes import payment as payment_module

    payment_id = "early-success-not-ready-1303"
    user = await Users.create(
        id=1303, username="u1303", full_name="User 1303", is_registered=True
    )

    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=100,
        amount_external=100,
        amount_from_balance=0,
        status="pending",
        processing_state="pending",
        effect_applied=False,
    )

    fake_payment = types.SimpleNamespace(
        id=payment_id,
        status="succeeded",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        metadata={"user_id": str(user.id)},
    )

    async def _fake_apply_succeeded_payment_fallback(*_args, **_kwargs):
        return False

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )
    monkeypatch.setattr(
        payment_module,
        "_apply_succeeded_payment_fallback",
        _fake_apply_succeeded_payment_fallback,
        raising=False,
    )

    result = await get_payment_status(payment_id, user=user)

    assert result["is_paid"] is True
    assert result["processed"] is True
    assert result["processed_status"] == "pending"
    assert result["entitlements_ready"] is False


@pytest.mark.asyncio
async def test_payment_status_succeeded_includes_overlay_snapshot(monkeypatch):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_module
    from bloobcat.routes.payment import get_payment_status

    payment_id = "status-overlay-snapshot-1304"
    today = date.today()
    user = await Users.create(
        id=1304,
        username="u1304",
        full_name="User 1304",
        is_registered=True,
        expired_at=today + timedelta(days=90),
        is_subscribed=True,
        hwid_limit=3,
    )

    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=14,
        family_expires_at=today + timedelta(days=30),
        base_hwid_limit=3,
    )

    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=100,
        amount_external=100,
        amount_from_balance=0,
        status="succeeded",
        processing_state="done",
        effect_applied=True,
    )

    fake_payment = types.SimpleNamespace(
        id=payment_id,
        status="succeeded",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        metadata={"user_id": str(user.id)},
    )

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )

    result = await get_payment_status(payment_id, user=user)

    assert result["is_paid"] is True
    assert result["processed"] is True
    assert result["processed_status"] == "succeeded"
    assert result["entitlements_ready"] is True
    assert isinstance(result.get("overlay_snapshot"), dict)
    assert result["overlay_snapshot"]["has_frozen_base"] is True
    assert result["overlay_snapshot"]["active_kind"] == "family"
    assert result["overlay_snapshot"]["base_remaining_days"] == 14
    assert result["overlay_snapshot"]["base_hwid_limit"] == 3


@pytest.mark.asyncio
async def test_payment_status_succeeded_reports_frozen_overlay_for_paid_unregistered_family_owner(
    monkeypatch,
):
    from bloobcat.db.payments import ProcessedPayments
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_module
    from bloobcat.routes.payment import get_payment_status

    payment_id = "status-overlay-unregistered-1305"
    today = date.today()
    user = await Users.create(
        id=1305,
        username="u1305",
        full_name="User 1305",
        is_registered=False,
        expired_at=today + timedelta(days=90),
        is_subscribed=True,
        hwid_limit=10,
    )

    await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=14,
        family_expires_at=today + timedelta(days=30),
        base_hwid_limit=3,
    )

    await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=user.id,
        amount=100,
        amount_external=100,
        amount_from_balance=0,
        status="succeeded",
        processing_state="done",
        effect_applied=True,
    )

    fake_payment = types.SimpleNamespace(
        id=payment_id,
        status="succeeded",
        amount=types.SimpleNamespace(value="100.00", currency="RUB"),
        metadata={"user_id": str(user.id)},
    )

    monkeypatch.setattr(
        payment_module.Payment,
        "find_one",
        staticmethod(lambda _pid: fake_payment),
        raising=False,
    )

    result = await get_payment_status(payment_id, user=user)

    assert result["is_paid"] is True
    assert result["processed"] is True
    assert result["processed_status"] == "succeeded"
    assert result["entitlements_ready"] is True
    assert isinstance(result.get("overlay_snapshot"), dict)
    assert result["overlay_snapshot"]["has_frozen_base"] is True
    assert result["overlay_snapshot"]["active_kind"] == "family"
    assert result["overlay_snapshot"]["will_restore_base_after_family"] is True
    assert result["overlay_snapshot"]["base_remaining_days"] == 14
    assert result["overlay_snapshot"]["base_hwid_limit"] == 3


@pytest.mark.asyncio
async def test_pay_from_balance_base_to_family_uses_migration_notification_context(
    monkeypatch,
):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes import payment as payment_module
    from bloobcat.routes.payment import pay

    today = date.today()
    user = await Users.create(
        id=1311,
        username="u1311",
        full_name="User 1311",
        email="u1311@example.com",
        balance=100_000,
        is_registered=True,
        expired_at=today + timedelta(days=20),
        hwid_limit=3,
    )
    active_tariff = await ActiveTariffs.create(
        user=user,
        name="base-1m",
        months=1,
        price=1000,
        hwid_limit=3,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active_tariff.id
    await user.save(update_fields=["active_tariff_id"])

    family_tariff = await Tariffs.create(
        id=1311,
        name="family_12m",
        months=12,
        base_price=4490,
        progressive_multiplier=0.9,
        order=1,
        devices_limit_default=3,
        devices_limit_family=10,
    )

    replay_calls: list[dict[str, object]] = []

    async def _noop(*args, **kwargs):
        _ = args, kwargs
        return None

    async def fake_replay(**kwargs):
        replay_calls.append(kwargs)
        return None

    monkeypatch.setattr(payment_module, "_award_partner_cashback", _noop, raising=False)
    monkeypatch.setattr(
        payment_module,
        "_replay_payment_notifications_if_needed",
        fake_replay,
        raising=False,
    )

    result = await pay(
        tariff_id=family_tariff.id,
        email=user.email,
        device_count=10,
        lte_gb=0,
        user=user,
        client_request_id="balance-base-to-family-1311",
    )

    assert isinstance(result, dict)
    assert len(replay_calls) == 1
    replay_call = replay_calls[0]
    assert replay_call["method"] == "balance"
    assert int(replay_call["device_count"]) == 10
    assert int(replay_call["months"]) == 12

    user_after = await Users.get(id=user.id)
    context = await payment_module._build_payment_notification_context(
        user=user_after,
        days=int(replay_call["days"]),
        amount_external=float(replay_call["amount_external"]),
        amount_from_balance=float(replay_call["amount_from_balance"]),
        device_count=int(replay_call["device_count"]),
        months=int(replay_call["months"]),
        is_auto_payment=bool(replay_call["is_auto_payment"]),
        discount_percent=replay_call["discount_percent"],
        old_expired_at=replay_call["old_expired_at"],
        new_expired_at=replay_call["new_expired_at"],
        lte_gb_total=int(replay_call["lte_gb_total"]),
        method=str(replay_call["method"]),
    )
    assert context.migration_direction == "base_to_family"

    freeze = await SubscriptionFreezes.get(user_id=user.id, is_active=True)
    assert freeze.freeze_reason == "family_overlay"


@pytest.mark.asyncio
async def test_discount_respects_min_max_months():
    from bloobcat.db.users import Users
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.routes.payment import pay

    user = await Users.create(
        id=789,
        username="months",
        full_name="Month User",
        balance=10_000,
        is_registered=True,
    )

    tariff_1m = await Tariffs.create(
        id=11, name="1m", months=1, base_price=1000, progressive_multiplier=0.9, order=1
    )
    tariff_6m = await Tariffs.create(
        id=12, name="6m", months=6, base_price=3000, progressive_multiplier=0.9, order=2
    )
    tariff_12m = await Tariffs.create(
        id=13,
        name="12m",
        months=12,
        base_price=4000,
        progressive_multiplier=0.9,
        order=3,
    )

    discount = await PersonalDiscount.create(
        user_id=user.id,
        percent=30,
        is_permanent=False,
        remaining_uses=2,
        min_months=3,
        max_months=6,
    )

    # 1 РјРµСЃСЏС† вЂ” СЃРєРёРґРєР° РЅРµ РґРѕР»Р¶РЅР° РїСЂРёРјРµРЅРёС‚СЊСЃСЏ
    result_1m = await pay(
        tariff_id=tariff_1m.id, email="m1@example.com", device_count=1, user=user
    )
    assert result_1m["status"] == "success"
    user = await Users.get(id=user.id)
    assert (
        int(user.balance) == 9_000
    )  # 10_000 - 1000, СЃРєРёРґРєР° РЅРµ РїСЂРёРјРµРЅРµРЅР°
    discount = await PersonalDiscount.get(id=discount.id)
    assert int(discount.remaining_uses or 0) == 2

    # 6 РјРµСЃСЏС†РµРІ вЂ” СЃРєРёРґРєР° РїСЂРёРјРµРЅСЏРµС‚СЃСЏ (РїРѕРїР°РґР°РµС‚ РІ РґРёР°РїР°Р·РѕРЅ)
    result_6m = await pay(
        tariff_id=tariff_6m.id, email="m6@example.com", device_count=1, user=user
    )
    assert result_6m["status"] == "success"
    user = await Users.get(id=user.id)
    assert int(user.balance) == 6_900  # 9_000 - (3000 * 0.7)
    discount = await PersonalDiscount.get(id=discount.id)
    assert int(discount.remaining_uses or 0) == 1


@pytest.mark.asyncio
async def test_referrer_family_owner_bonus_does_not_extend_subscription_and_uses_family_limit_setting(
    monkeypatch,
):
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
    assert int(owner_after.referral_bonus_days_total or 0) == 0

    reward = await ReferralRewards.get(
        referred_user_id=referred.id, kind="first_payment"
    )
    assert int(reward.referrer_bonus_days or 0) == 0
    assert reward.applied_to_subscription is False


@pytest.mark.asyncio
async def test_friend_bonus_does_not_extend_for_family_member_with_expired_personal_subscription(
    monkeypatch,
):
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
    assert int(referrer_after.referral_bonus_days_total or 0) == 0


@pytest.mark.asyncio
async def test_pay_full_balance_family_creates_freeze_without_base_carryover():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import pay
    from bloobcat.utils.dates import add_months_safe

    today = date.today()
    user = await Users.create(
        id=1201,
        username="family-balance",
        full_name="Family Balance",
        balance=1_000_000,
        is_registered=True,
        expired_at=today + timedelta(days=30),
        hwid_limit=3,
    )

    base_active = await ActiveTariffs.create(
        user=user,
        name="base-3m",
        months=3,
        price=2190,
        hwid_limit=3,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = base_active.id
    await user.save(update_fields=["active_tariff_id"])

    family_tariff = await Tariffs.create(
        id=1202,
        name="family-12m",
        months=12,
        base_price=1000,
        progressive_multiplier=0.9,
        order=1,
        devices_limit_default=3,
        devices_limit_family=10,
    )

    result = await pay(
        tariff_id=family_tariff.id,
        email="family@example.com",
        device_count=10,
        lte_gb=0,
        user=user,
    )
    assert result["status"] == "success"

    user_after = await Users.get(id=user.id)
    freeze = await SubscriptionFreezes.get_or_none(
        user_id=user.id, is_active=True, resume_applied=False
    )
    assert freeze is not None
    assert int(freeze.base_remaining_days or 0) >= 29

    expected_family_days = (add_months_safe(today, 12) - today).days
    assert user_after.expired_at == today + timedelta(days=expected_family_days)


@pytest.mark.asyncio
async def test_pay_base_during_active_family_overlay_updates_frozen_base_not_family_expiry():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import pay
    from bloobcat.utils.dates import add_months_safe

    today = date.today()
    family_expired_at = today + timedelta(
        days=(add_months_safe(today, 12) - today).days
    )
    base_purchase_days = (add_months_safe(today, 1) - today).days

    user = await Users.create(
        id=1203,
        username="family-overlay-base-topup",
        full_name="Family Overlay Base Topup",
        email="family-overlay@example.com",
        balance=1_000_000,
        is_registered=True,
        expired_at=family_expired_at,
        hwid_limit=10,
    )

    family_active = await ActiveTariffs.create(
        user=user,
        name="family-12m-active",
        months=12,
        price=4490,
        hwid_limit=10,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = family_active.id
    await user.save(update_fields=["active_tariff_id"])

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=20,
        base_expires_at_snapshot=today + timedelta(days=20),
        family_expires_at=family_expired_at,
        base_tariff_name="base-1m",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    base_tariff = await Tariffs.create(
        id=1204,
        name="base-1m",
        months=1,
        base_price=1000,
        progressive_multiplier=0.9,
        order=2,
        devices_limit_default=1,
        devices_limit_family=30,
    )

    result = await pay(
        tariff_id=base_tariff.id,
        email="family-overlay@example.com",
        device_count=1,
        lte_gb=0,
        user=user,
    )
    assert result["status"] == "success"

    user_after = await Users.get(id=user.id)
    freeze_after = await SubscriptionFreezes.get(id=freeze.id)
    family_active_after = await ActiveTariffs.get_or_none(id=family_active.id)

    assert user_after.expired_at == family_expired_at
    assert int(freeze_after.base_remaining_days or 0) == 20 + int(base_purchase_days)
    assert str(user_after.active_tariff_id) == str(family_active.id)
    assert int(user_after.hwid_limit or 0) == 10
    assert family_active_after is not None
    assert int(family_active_after.hwid_limit or 0) == 10


@pytest.mark.asyncio
async def test_pay_family_renewal_during_active_overlay_is_additive_and_not_shortened():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.subscription_freezes import SubscriptionFreezes
    from bloobcat.db.tariff import Tariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import pay
    from bloobcat.utils.dates import add_months_safe

    today = date.today()
    existing_family_expired_at = today + timedelta(days=120)
    purchased_days = (add_months_safe(today, 12) - today).days

    user = await Users.create(
        id=1205,
        username="family-overlay-renew",
        full_name="Family Overlay Renew",
        email="family-overlay-renew@example.com",
        balance=1_000_000,
        is_registered=True,
        expired_at=existing_family_expired_at,
        hwid_limit=10,
    )

    family_active = await ActiveTariffs.create(
        user=user,
        name="family-12m-active",
        months=12,
        price=4490,
        hwid_limit=10,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = family_active.id
    await user.save(update_fields=["active_tariff_id"])

    freeze = await SubscriptionFreezes.create(
        user_id=user.id,
        freeze_reason="family_overlay",
        is_active=True,
        resume_applied=False,
        base_remaining_days=20,
        base_expires_at_snapshot=today + timedelta(days=20),
        family_expires_at=existing_family_expired_at,
        base_tariff_name="base-1m",
        base_tariff_months=1,
        base_tariff_price=1000,
        base_hwid_limit=3,
        base_lte_gb_total=0,
        base_lte_gb_used=0.0,
        base_lte_price_per_gb=0.0,
        base_progressive_multiplier=0.9,
        base_residual_day_fraction=0.0,
    )

    family_tariff = await Tariffs.create(
        id=1206,
        name="family-12m",
        months=12,
        base_price=4490,
        progressive_multiplier=0.9,
        order=3,
        devices_limit_default=3,
        devices_limit_family=10,
    )

    result = await pay(
        tariff_id=family_tariff.id,
        email="family-overlay-renew@example.com",
        device_count=10,
        lte_gb=0,
        user=user,
    )
    assert result["status"] == "success"

    user_after = await Users.get(id=user.id)
    freeze_after = await SubscriptionFreezes.get(id=freeze.id)
    expected_expiry = existing_family_expired_at + timedelta(days=purchased_days)

    assert user_after.expired_at == expected_expiry
    assert user_after.expired_at >= existing_family_expired_at
    assert freeze_after.family_expires_at == expected_expiry


@pytest.mark.asyncio
async def test_build_auto_payment_preview_external_only():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import build_auto_payment_preview

    user = await Users.create(
        id=1901,
        username="preview-external",
        full_name="Preview External",
        balance=0,
        is_registered=True,
        hwid_limit=3,
    )
    active_tariff = await ActiveTariffs.create(
        user=user,
        name="preview-external-plan",
        months=1,
        price=2190,
        hwid_limit=3,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active_tariff.id
    await user.save(update_fields=["active_tariff_id"])

    preview = await build_auto_payment_preview(user)

    assert preview is not None
    assert preview.total_amount == 2190.0
    assert preview.amount_external == 2190.0
    assert preview.amount_from_balance == 0.0
    assert preview.device_count == 3


@pytest.mark.asyncio
async def test_build_auto_payment_preview_balance_only():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import build_auto_payment_preview

    user = await Users.create(
        id=1902,
        username="preview-balance",
        full_name="Preview Balance",
        balance=3000,
        is_registered=True,
        hwid_limit=2,
    )
    active_tariff = await ActiveTariffs.create(
        user=user,
        name="preview-balance-plan",
        months=1,
        price=2190,
        hwid_limit=2,
        lte_gb_total=0,
        lte_gb_used=0.0,
        lte_price_per_gb=0.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active_tariff.id
    await user.save(update_fields=["active_tariff_id"])

    preview = await build_auto_payment_preview(user)

    assert preview is not None
    assert preview.total_amount == 2190.0
    assert preview.amount_external == 0.0
    assert preview.amount_from_balance == 2190.0
    assert preview.device_count == 2


@pytest.mark.asyncio
async def test_build_auto_payment_preview_mixed_quote_includes_discount_and_lte():
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users
    from bloobcat.routes.payment import build_auto_payment_preview

    user = await Users.create(
        id=1903,
        username="preview-mixed",
        full_name="Preview Mixed",
        balance=500,
        is_registered=True,
        hwid_limit=3,
    )
    active_tariff = await ActiveTariffs.create(
        user=user,
        name="preview-mixed-plan",
        months=1,
        price=2000,
        hwid_limit=3,
        lte_gb_total=10,
        lte_gb_used=0.0,
        lte_price_per_gb=19.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active_tariff.id
    await user.save(update_fields=["active_tariff_id"])
    await PersonalDiscount.create(
        user_id=user.id,
        percent=10,
        is_permanent=False,
        remaining_uses=1,
    )

    preview = await build_auto_payment_preview(user)

    assert preview is not None
    assert preview.discount_percent == 10
    assert preview.lte_cost == 190
    assert preview.total_amount == 1990.0
    assert preview.amount_external == 1490.0
    assert preview.amount_from_balance == 500.0
    assert preview.device_count == 3


@pytest.mark.asyncio
async def test_create_auto_payment_uses_same_quote_as_preview(monkeypatch):
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.users import Users
    import bloobcat.routes.payment as payment_module
    from bloobcat.routes.payment import build_auto_payment_preview, create_auto_payment

    user = await Users.create(
        id=1904,
        username="preview-consistency",
        full_name="Preview Consistency",
        balance=500,
        email="preview-consistency@example.com",
        renew_id="renew-preview-1904",
        is_registered=True,
        hwid_limit=3,
    )
    active_tariff = await ActiveTariffs.create(
        user=user,
        name="preview-consistency-plan",
        months=1,
        price=2000,
        hwid_limit=3,
        lte_gb_total=10,
        lte_gb_used=0.0,
        lte_price_per_gb=19.0,
        progressive_multiplier=0.9,
        residual_day_fraction=0.0,
    )
    user.active_tariff_id = active_tariff.id
    await user.save(update_fields=["active_tariff_id"])
    await PersonalDiscount.create(
        user_id=user.id,
        percent=10,
        is_permanent=False,
        remaining_uses=1,
    )

    preview = await build_auto_payment_preview(user)
    assert preview is not None

    captured: dict[str, object] = {}

    def fake_payment_create(payment_data, _idempotence_key):
        captured["payment_data"] = payment_data
        return types.SimpleNamespace(
            id="auto-preview-1904",
            amount=types.SimpleNamespace(value=str(payment_data["amount"]["value"])),
            status="pending",
            confirmation=types.SimpleNamespace(
                confirmation_url="https://example.com/auto-preview-1904"
            ),
        )

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    payment_global = create_auto_payment.__globals__.get("Payment")
    if payment_global is not None:
        monkeypatch.setattr(payment_global, "create", fake_payment_create)
    monkeypatch.setattr(payment_module.Payment, "create", fake_payment_create)
    monkeypatch.setattr(payment_module.asyncio, "to_thread", fake_to_thread)

    ok = await create_auto_payment(user)

    assert ok is True
    payment_data = captured["payment_data"]
    metadata = dict(payment_data["metadata"])  # type: ignore[index]
    assert float(payment_data["amount"]["value"]) == preview.amount_external  # type: ignore[index]
    assert float(metadata["amount_from_balance"]) == preview.amount_from_balance
    assert int(metadata["base_full_price"]) == preview.base_full_price
    assert float(metadata["discounted_price"]) == preview.discounted_price
    assert int(metadata["discount_percent"]) == int(preview.discount_percent or 0)
    assert int(metadata["lte_cost"]) == preview.lte_cost
    assert int(metadata["device_count"]) == preview.device_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("quote_kwargs", "expected_parts"),
    [
        (
            {
                "total_amount": 2190,
                "amount_external": 2190,
                "amount_from_balance": 0,
            },
            [
                "сегодня ночью будет выполнена попытка автопродления подписки",
                "К списанию: 2190₽",
            ],
        ),
        (
            {
                "total_amount": 2190,
                "amount_external": 1690,
                "amount_from_balance": 500,
            },
            [
                "сегодня ночью будет выполнена попытка автопродления подписки",
                "Всего: 2190₽",
                "С баланса: 500₽",
                "Через YooKassa: 1690₽",
            ],
        ),
        (
            {
                "total_amount": 2190,
                "amount_external": 0,
                "amount_from_balance": 2190,
            },
            [
                "сегодня ночью будет выполнено автопродление подписки",
                "С баланса будет списано: 2190₽",
            ],
        ),
    ],
)
async def test_notify_auto_payment_formats_explicit_quote_variants(
    monkeypatch,
    quote_kwargs,
    expected_parts,
):
    sys.modules.pop("bloobcat.bot.notifications.subscription.expiration", None)
    module = importlib.import_module("bloobcat.bot.notifications.subscription.expiration")

    captured: dict[str, object] = {}

    class CaptureBot:
        async def send_message(self, user_id, text, reply_markup=None):
            captured["user_id"] = user_id
            captured["text"] = text
            captured["reply_markup"] = reply_markup
            return None

    async def fake_button(label, path=None):
        return {"label": label, "path": path}

    monkeypatch.setattr(module, "bot", CaptureBot())
    monkeypatch.setattr(module, "webapp_inline_button", fake_button)

    user = types.SimpleNamespace(
        id=1905,
        full_name="Vasily",
        expired_at=date.today() + timedelta(days=1),
    )

    await module.notify_auto_payment(
        user,
        charge_date=date.today() + timedelta(days=1),
        **quote_kwargs,
    )

    assert captured["user_id"] == 1905
    assert captured["reply_markup"] == {
        "label": "Управление подпиской",
        "path": "/subscription",
    }
    for expected in expected_parts:
        assert expected in str(captured["text"])


def _make_capture_bot(captured: dict[str, object]):
    class CaptureBot:
        async def send_message(
            self,
            user_id,
            text,
            reply_markup=None,
            parse_mode=None,
        ):
            captured["user_id"] = user_id
            captured["text"] = text
            captured["reply_markup"] = reply_markup
            captured["parse_mode"] = parse_mode
            return None

    return CaptureBot()


async def _fake_button(label, path=None):
    return {"label": label, "path": path}


def test_build_rescue_link_paragraph_localizes_ru_and_en():
    sys.modules.pop("bloobcat.bot.notifications.rescue_link", None)
    module = importlib.import_module("bloobcat.bot.notifications.rescue_link")

    ru_text = module.build_rescue_link_paragraph("ru")
    en_text = module.build_rescue_link_paragraph("en")

    assert "Если не получается открыть бота" in ru_text
    assert "https://app.vectra-pro.net" in ru_text
    assert "If the bot is hard to open" in en_text
    assert "https://app.vectra-pro.net" in en_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("days", "expected_part"),
    [
        (7, "подписка истекает через 7 дней"),
        (3, "подписка истекает через 3 дня"),
        (1, "подписка истекает завтра"),
    ],
)
async def test_notify_expiring_subscription_appends_rescue_link(
    monkeypatch,
    days,
    expected_part,
):
    sys.modules.pop("bloobcat.bot.notifications.subscription.expiration", None)
    module = importlib.import_module("bloobcat.bot.notifications.subscription.expiration")

    captured: dict[str, object] = {}
    monkeypatch.setattr(module, "bot", _make_capture_bot(captured))
    monkeypatch.setattr(module, "webapp_inline_button", _fake_button)

    user = types.SimpleNamespace(
        id=1910 + days,
        full_name="Vasily",
        expired_at=datetime.now(ZoneInfo("Europe/Moscow")).date() + timedelta(days=days),
    )

    await module.notify_expiring_subscription(user)

    assert expected_part in str(captured["text"])
    assert "Если не получается открыть бота" in str(captured["text"])
    assert "https://app.vectra-pro.net" in str(captured["text"])
    assert captured["reply_markup"] == {"label": "Продлить", "path": "/pay"}


@pytest.mark.asyncio
async def test_notify_subscription_expired_appends_localized_rescue_link(monkeypatch):
    sys.modules.pop("bloobcat.bot.notifications.subscription.key", None)
    module = importlib.import_module("bloobcat.bot.notifications.subscription.key")

    captured: dict[str, object] = {}
    monkeypatch.setattr(module, "bot", _make_capture_bot(captured))
    monkeypatch.setattr(module, "webapp_inline_button", _fake_button)
    monkeypatch.setattr(module, "get_user_locale", lambda _user: "en")

    user = types.SimpleNamespace(id=1920, full_name="Alice")

    await module.on_disabled(user)

    assert "your subscription has expired" in str(captured["text"])
    assert "If the bot is hard to open" in str(captured["text"])
    assert "https://app.vectra-pro.net" in str(captured["text"])
    assert captured["reply_markup"] == {"label": "Renew Now", "path": "/pay"}


@pytest.mark.asyncio
async def test_notify_winback_offer_uses_plain_text_for_markdown_sensitive_name(
    monkeypatch,
):
    sys.modules.pop("bloobcat.bot.notifications.winback.discount_offer", None)
    module = importlib.import_module("bloobcat.bot.notifications.winback.discount_offer")

    captured: dict[str, object] = {}
    monkeypatch.setattr(module, "bot", _make_capture_bot(captured))
    monkeypatch.setattr(module, "webapp_inline_button", _fake_button)
    monkeypatch.setattr(module, "get_user_locale", lambda _user: "en")

    user = types.SimpleNamespace(
        id=1925,
        full_name="Alice_[promo]*`user`",
    )

    delivered = await module.notify_winback_discount_offer(
        user,
        percent=25,
        expires_at=date(2026, 4, 30),
    )

    assert delivered is True
    assert captured["parse_mode"] is None
    assert "Alice_[promo]*`user`" in str(captured["text"])
    assert "25%" in str(captured["text"])
    assert "**25%**" not in str(captured["text"])
    assert captured["reply_markup"] == {"label": "Get Discount", "path": "/pay"}


@pytest.mark.asyncio
async def test_notify_expiring_trial_appends_rescue_link(monkeypatch):
    sys.modules.pop("bloobcat.bot.notifications.trial.expiring", None)
    module = importlib.import_module("bloobcat.bot.notifications.trial.expiring")

    captured: dict[str, object] = {}
    monkeypatch.setattr(module, "bot", _make_capture_bot(captured))
    monkeypatch.setattr(module, "webapp_inline_button", _fake_button)

    user = types.SimpleNamespace(
        id=1930,
        full_name="Vasily",
        expired_at=datetime.now(ZoneInfo("Europe/Moscow")).date() + timedelta(days=1),
    )

    await module.notify_expiring_trial(user)

    assert "пробный доступ закончится сегодня ночью" in str(captured["text"])
    assert "Если не получается открыть бота" in str(captured["text"])
    assert "https://app.vectra-pro.net" in str(captured["text"])
    assert captured["reply_markup"] == {"label": "Продлить доступ", "path": "/pay"}


@pytest.mark.asyncio
async def test_notify_trial_ended_appends_localized_rescue_link(monkeypatch):
    sys.modules.pop("bloobcat.bot.notifications.trial.end", None)
    module = importlib.import_module("bloobcat.bot.notifications.trial.end")

    captured: dict[str, object] = {}
    monkeypatch.setattr(module, "bot", _make_capture_bot(captured))
    monkeypatch.setattr(module, "webapp_inline_button", _fake_button)
    monkeypatch.setattr(module, "get_user_locale", lambda _user: "en")

    user = types.SimpleNamespace(id=1940, full_name="Alice")

    await module.notify_trial_ended(user)

    assert "Your trial period has ended" in str(captured["text"])
    assert "If the bot is hard to open" in str(captured["text"])
    assert "https://app.vectra-pro.net" in str(captured["text"])
    assert captured["reply_markup"] == {"label": "Renew Now", "path": "/pay"}


@pytest.mark.asyncio
async def test_notify_trial_three_days_left_keeps_html_parse_mode_and_rescue_link(
    monkeypatch,
):
    sys.modules.pop("bloobcat.bot.notifications.trial.pre_expiring_3d", None)
    module = importlib.import_module("bloobcat.bot.notifications.trial.pre_expiring_3d")

    captured: dict[str, object] = {}
    monkeypatch.setattr(module, "bot", _make_capture_bot(captured))
    monkeypatch.setattr(module, "webapp_inline_button", _fake_button)

    async def fake_get_devices_count(_user):
        return 2

    monkeypatch.setattr(module, "_get_devices_count", fake_get_devices_count)

    user = types.SimpleNamespace(id=1950, full_name="Vasily")

    await module.notify_trial_three_days_left(user)

    assert "до окончания пробного периода: <b>1 день</b>" in str(captured["text"])
    assert "Если не получается открыть бота" in str(captured["text"])
    assert "https://app.vectra-pro.net" in str(captured["text"])
    assert captured["reply_markup"] == {"label": "Выбрать тариф", "path": "/pay"}
    assert captured["parse_mode"] == "HTML"
