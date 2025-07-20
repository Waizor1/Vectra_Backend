from dotenv import load_dotenv
from pydantic import SecretStr
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

app_settings = AppSettings()
