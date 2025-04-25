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
