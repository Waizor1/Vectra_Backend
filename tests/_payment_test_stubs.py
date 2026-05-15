import sys
import types
from pathlib import Path
from typing import Callable


def install_stubs() -> Callable[[], None]:
    """РџРѕРґРјРµРЅСЏРµС‚ РІРЅРµС€РЅРёРµ Р·Р°РІРёСЃРёРјРѕСЃС‚Рё (YooKassa, СѓРІРµРґРѕРјР»РµРЅРёСЏ, RemnaWave, scheduler)."""
    module_missing = object()

    project_root = Path(__file__).resolve().parents[1]
    added_project_root_to_syspath = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        added_project_root_to_syspath = True

    tracked_modules = [
        "yookassa",
        "yookassa.domain",
        "yookassa.domain.notification",
        "bloobcat.routes",
        "bloobcat.routes.remnawave",
        "bloobcat.bot",
        "bloobcat.bot.notifications",
        "bloobcat.bot.notifications.subscription",
        "bloobcat.bot.notifications.general",
        "bloobcat.bot.notifications.trial",
        "bloobcat.logger",
        "bloobcat.bot.notifications.admin",
        "bloobcat.bot.notifications.subscription.renewal",
        "bloobcat.bot.notifications.general.referral",
        "bloobcat.bot.notifications.prize_wheel",
        "bloobcat.bot.bot",
        "bloobcat.bot.keyboard",
        "bloobcat.bot.error_handler",
        "bloobcat.bot.notifications.trial.granted",
        "bloobcat.routes.remnawave.client",
        "bloobcat.routes.remnawave.hwid_utils",
        "bloobcat.routes.remnawave.lte_utils",
        "bloobcat.scheduler",
        "bloobcat.routes.payment",
    ]
    saved_modules = {
        name: sys.modules.get(name, module_missing) for name in tracked_modules
    }

    import pydantic.networks as pydantic_networks
    import tortoise.contrib.pydantic as tortoise_pydantic_pkg
    import tortoise.contrib.pydantic.creator as tortoise_pydantic_creator

    saved_import_email_validator = pydantic_networks.import_email_validator
    saved_validate_email = pydantic_networks.validate_email
    saved_pydantic_model_creator_pkg = tortoise_pydantic_pkg.pydantic_model_creator
    saved_pydantic_model_creator_creator = (
        tortoise_pydantic_creator.pydantic_model_creator
    )

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

        @staticmethod
        def find_one(*args, **kwargs):
            _ = args, kwargs
            return types.SimpleNamespace(
                id="test_payment_id",
                amount=types.SimpleNamespace(value="1.00"),
                status="pending",
                metadata={},
                payment_method=None,
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

    def _noop_import_email_validator() -> None:
        return None

    def _validate_email(value: str, /, *args, **kwargs):
        _ = args, kwargs
        return "", value

    pydantic_networks.import_email_validator = _noop_import_email_validator
    pydantic_networks.validate_email = _validate_email

    # pydantic>=2.12 compatibility: bypass tortoise pydantic schema generation in tests.
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

    async def notify_family_membership_event(*args, **kwargs):
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
    admin_notif.notify_family_membership_event = notify_family_membership_event
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
        mod.notify_family_membership_event = notify_family_membership_event
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
        async def session_close(self):
            return None

    bot_mod.get_bot_username = get_bot_username
    bot_mod.bot = DummyBot()
    sys.modules["bloobcat.bot.bot"] = bot_mod

    keyboard_mod = types.ModuleType("bloobcat.bot.keyboard")

    async def webapp_inline_button(text: str, url: str | None = None):
        return {"button": text, "url": url}

    keyboard_mod.webapp_inline_button = webapp_inline_button
    sys.modules["bloobcat.bot.keyboard"] = keyboard_mod

    error_handler_mod = types.ModuleType("bloobcat.bot.error_handler")

    async def handle_telegram_forbidden_error(*args, **kwargs):
        return True

    async def handle_telegram_bad_request(*args, **kwargs):
        return True

    async def reset_user_failed_count(*args, **kwargs):
        return True

    def _coerce_user_id(value, *, caller="stub"):
        """Stub mirror of bloobcat.bot.error_handler._coerce_user_id.
        Real version is in bloobcat/bot/error_handler.py — this just keeps the
        test isolation pattern from breaking when other tests import it."""
        if isinstance(value, int):
            return value
        fallback_id = getattr(value, "id", None)
        if isinstance(fallback_id, int):
            return fallback_id
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    error_handler_mod.handle_telegram_forbidden_error = handle_telegram_forbidden_error
    error_handler_mod.handle_telegram_bad_request = handle_telegram_bad_request
    error_handler_mod.reset_user_failed_count = reset_user_failed_count
    error_handler_mod._coerce_user_id = _coerce_user_id
    sys.modules["bloobcat.bot.error_handler"] = error_handler_mod
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

            @staticmethod
            async def get_user_usage_by_range(*args, **kwargs):
                _ = args, kwargs
                return {"response": []}

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

    def restore_stubs() -> None:
        if added_project_root_to_syspath:
            try:
                sys.path.remove(str(project_root))
            except ValueError:
                pass
        pydantic_networks.import_email_validator = saved_import_email_validator
        pydantic_networks.validate_email = saved_validate_email
        tortoise_pydantic_pkg.pydantic_model_creator = saved_pydantic_model_creator_pkg
        tortoise_pydantic_creator.pydantic_model_creator = (
            saved_pydantic_model_creator_creator
        )
        for module_name, previous_module in saved_modules.items():
            if previous_module is module_missing:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous_module

    return restore_stubs
