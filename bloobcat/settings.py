from dotenv import load_dotenv
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import os

load_dotenv()


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    token: SecretStr
    webhook_secret: str
    webapp_url: str
    miniapp_url: str 


class YookassaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YOOKASSA_")

    shop_id: str
    secret_key: SecretStr
    webhook_secret: str


class RemnaWaveSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REMNAWAVE_")

    url: str
    token: SecretStr
    # UUID внутреннего сквада по умолчанию (для автоподключения у новых пользователей)
    # Ожидается переменная окружения REMNAWAVE_DEFAULT_INTERNAL_SQUAD_UUID
    default_internal_squad_uuid: str | None = None


class ScriptSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCRIPT_")

    db: SecretStr
    dev: bool
    api_url: str


class AdminSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADMIN_")

    telegram_id: int
    login: SecretStr
    password: SecretStr

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.telegram_id:
            raise ValueError("ADMIN_TELEGRAM_ID must be set in environment variables")


telegram_settings = TelegramSettings()
yookassa_settings = YookassaSettings()
remnawave_settings = RemnaWaveSettings()
script_settings = ScriptSettings()
admin_settings = AdminSettings()
test_mode = os.getenv("TESTMODE", "false").strip().lower() in ("true", "1", "yes")

# New settings class for application-specific configurations
class AppSettings(BaseSettings):
    trial_days: int = 10  # Default to 10 days, will be overridden by TRIAL_DAYS from .env
    
    # Настройки для блокированных пользователей
    blocked_user_cleanup_days: int = 7  # Через сколько дней удалять заблокированных пользователей
    blocked_user_max_failed_attempts: int = 5  # Максимальное количество неудачных попыток до блокировки
    cleanup_blocked_users_enabled: bool = True  # Включить/выключить автоочистку заблокированных пользователей
    cleanup_blocked_users_interval_hours: int = 24  # Интервал очистки в часах
    # model_config = SettingsConfigDict(env_prefix="APP_") # If we want APP_TRIAL_DAYS
    # No env_prefix means it will look for TRIAL_DAYS directly.

    # Колесо призов
    prize_wheel_spin_bonus_price: int = 50  # Стоимость одной крутки при оплате бонусным балансом (₽)

app_settings = AppSettings()

# Promo settings
class PromoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROMO_")

    # Секрет для HMAC промокодов; делаем необязательным, чтобы не падать при отсутствии
    hmac_secret: SecretStr | None = None

promo_settings = PromoSettings()


class CaptainUserLookupSettings(BaseSettings):
    """Настройки HTTPS-сервиса Captain User Lookup."""

    api_key: SecretStr = SecretStr("change-me")
    allowlist_domains: list[str] | str | None = None

    @field_validator("allowlist_domains", mode="before")
    @classmethod
    def parse_allowlist(cls, value):
        if not value:
            return []
        if isinstance(value, str):
            cleaned = [item.strip().lower() for item in value.split(",") if item.strip()]
            return cleaned
        if isinstance(value, (list, tuple, set)):
            cleaned = [str(item).strip().lower() for item in value if str(item).strip()]
            return cleaned
        return value


captain_lookup_settings = CaptainUserLookupSettings()
