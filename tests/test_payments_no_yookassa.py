import asyncio
import sys
import types
import pytest
import pytest_asyncio
from datetime import date

from tortoise import Tortoise


def install_stubs() -> None:
    """Подменяет внешние зависимости (YooKassa, уведомления, RemnaWave, scheduler)."""
    # yookassa stubs
    yk_module = types.ModuleType("yookassa")

    class Configuration:
        account_id = None
        secret_key = None

    class Payment:
        @staticmethod
        def create(*args, **kwargs):
            # Возвращаем заглушку, чтобы код, который ожидает confirmation.confirmation_url, не падал
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

    # notifications stubs
    admin_notif = types.ModuleType("bloobcat.bot.notifications.admin")

    async def on_payment(*args, **kwargs):
        return None

    async def cancel_subscription(*args, **kwargs):
        return None

    async def on_activated_bot(*args, **kwargs):
        return None

    admin_notif.on_payment = on_payment
    admin_notif.cancel_subscription = cancel_subscription
    admin_notif.on_activated_bot = on_activated_bot
    sys.modules["bloobcat.bot.notifications.admin"] = admin_notif

    sub_notif = types.ModuleType("bloobcat.bot.notifications.subscription.renewal")

    async def notify_auto_renewal_success_balance(*args, **kwargs):
        return None

    async def notify_auto_renewal_failure(*args, **kwargs):
        return None

    async def notify_renewal_success_yookassa(*args, **kwargs):
        return None

    sub_notif.notify_auto_renewal_success_balance = notify_auto_renewal_success_balance
    sub_notif.notify_auto_renewal_failure = notify_auto_renewal_failure
    sub_notif.notify_renewal_success_yookassa = notify_renewal_success_yookassa
    sys.modules["bloobcat.bot.notifications.subscription.renewal"] = sub_notif

    gen_notif = types.ModuleType("bloobcat.bot.notifications.general.referral")

    async def on_referral_payment(*args, **kwargs):
        return None

    gen_notif.on_referral_payment = on_referral_payment
    sys.modules["bloobcat.bot.notifications.general.referral"] = gen_notif

    # Create package placeholder for bloobcat.bot to avoid executing real __init__
    bot_pkg = types.ModuleType("bloobcat.bot")
    bot_pkg.__path__ = []  # mark as package
    sys.modules["bloobcat.bot"] = bot_pkg

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

    hwid_utils_mod.cleanup_user_hwid_devices = cleanup_user_hwid_devices
    sys.modules["bloobcat.routes.remnawave.hwid_utils"] = hwid_utils_mod

    # Scheduler stub (используется внутри Users.save/extend_subscription)
    scheduler_mod = types.ModuleType("bloobcat.scheduler")

    async def schedule_user_tasks(*args, **kwargs):
        return None

    scheduler_mod.schedule_user_tasks = schedule_user_tasks
    sys.modules["bloobcat.scheduler"] = scheduler_mod


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    install_stubs()
    return None


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
    # Инициализируем тестовую SQLite БД в памяти
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": [
                        "bloobcat.db.users",
                        "bloobcat.db.tariff",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.payments",
                        "bloobcat.db.discounts",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )
    await Tortoise.generate_schemas()
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
    tariff = await Tariffs.create(id=1, name="Месяц", months=1, base_price=1000, progressive_multiplier=0.9, order=1)
    device_count = 2
    base_price_no_discount = tariff.calculate_price(device_count)

    # Персональная скидка 20% (разовая)
    await PersonalDiscount.create(user_id=user.id, percent=20, is_permanent=False, remaining_uses=1)

    # Act — прямой вызов ветки оплаты с баланса
    result = await pay(tariff_id=tariff.id, email="test@example.com", device_count=device_count, user=user)

    # Assert
    assert result["status"] == "success"

    # Перечитываем пользователя и активный тариф
    user = await Users.get(id=user.id)
    assert user.active_tariff_id is not None
    at = await ActiveTariffs.get(id=user.active_tariff_id)
    assert at.price == base_price_no_discount  # важно: без персональной скидки
    assert at.hwid_limit == device_count
    # Скидка списалась
    d = await PersonalDiscount.get_or_none(user_id=user.id)
    assert d is not None
    assert int(d.remaining_uses or 0) == 0
    # Платёж записан по факту списанной суммы (со скидкой)
    pp = await ProcessedPayments.get_or_none(user_id=user.id)
    assert pp is not None
    assert pp.status == "succeeded"
    # Подписка продлена
    assert user.expired_at is not None
    assert user.expired_at >= date.today()


@pytest.mark.asyncio
async def test_create_auto_payment_from_balance_no_double_discount():
    from bloobcat.db.users import Users
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.routes.payment import create_auto_payment

    # Arrange: пользователь уже имеет активный тариф (как после предыдущего теста)
    user = await Users.create(id=456, username="auto", full_name="Auto User", balance=10_000, is_registered=True)
    # Активный тариф со «снимком» цены БЕЗ персональной скидки
    at = await ActiveTariffs.create(user=user, name="Месяц", months=1, price=2000, hwid_limit=2, progressive_multiplier=0.9)
    user.active_tariff_id = at.id
    await user.save()

    # Персональная скидка 50% (разовая)
    await PersonalDiscount.create(user_id=user.id, percent=50, is_permanent=False, remaining_uses=1)

    # Act — баланс >= цене после скидки, платёж целиком с баланса
    ok = await create_auto_payment(user)

    # Assert
    assert ok is True
    # Скидка должна быть списана ровно 1 раз
    d = await PersonalDiscount.get_or_none(user_id=user.id)
    assert d is not None
    assert int(d.remaining_uses or 0) == 0
    # Подписка продлена
    user = await Users.get(id=user.id)
    assert user.expired_at is not None
    assert user.expired_at >= date.today()


