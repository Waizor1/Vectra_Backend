import os
from typing import Annotated
from ipaddress import ip_address
from urllib.parse import urlparse
from uuid import UUID

from dotenv import load_dotenv
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

load_dotenv()


_STRATA_REMNAWAVE_DOMAIN = "stratavpn.com"
_UNSAFE_SECRET_PLACEHOLDERS = {
    "",
    "change-me",
    "changeme",
    "replace-me",
    "replace_me",
    "secret",
    "password",
    "your-secret",
    "your_secret",
}
_UNSAFE_SECRET_MARKERS = ("dev-only", "please-rotate", "example", "dummy")


def _test_mode_from_env() -> bool:
    return os.getenv("TESTMODE", "false").strip().lower() in {"true", "1", "yes", "on"}


def validate_runtime_secret(
    name: str,
    value: SecretStr | str | None,
    *,
    min_length: int = 32,
) -> str:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value or "")
    normalized = raw.strip()
    if _test_mode_from_env():
        return normalized
    lowered = normalized.lower()
    if (
        lowered in _UNSAFE_SECRET_PLACEHOLDERS
        or any(marker in lowered for marker in _UNSAFE_SECRET_MARKERS)
        or len(normalized) < min_length
    ):
        raise ValueError(f"{name} must be a non-placeholder secret with at least {min_length} characters")
    return normalized


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    token: SecretStr
    webhook_secret: str
    webhook_enabled: bool = True
    delete_webhook_on_shutdown: bool = False
    username: str | None = None
    webapp_url: str
    miniapp_url: str
    logs_channel: int | None = None
    api_fallback_ips: Annotated[list[str], NoDecode] = []
    # Optional: where to return the user after YooKassa redirect flow.
    # Useful when payment is opened in an external browser and we want to jump back to Telegram.
    payment_return_url: str | None = None

    @field_validator("logs_channel", mode="before")
    @classmethod
    def parse_logs_channel(cls, value):
        if value in (None, ""):
            return None
        return int(value)

    @field_validator("api_fallback_ips", mode="before")
    @classmethod
    def parse_api_fallback_ips(cls, value):
        if not value:
            return []
        if isinstance(value, str):
            raw_values = value.split(",")
        elif isinstance(value, (list, tuple, set)):
            raw_values = value
        else:
            return value

        parsed: list[str] = []
        for raw in raw_values:
            text = str(raw).strip()
            if not text:
                continue
            parsed.append(str(ip_address(text)))
        return parsed


class YookassaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YOOKASSA_")

    shop_id: str | None = None
    secret_key: SecretStr | None = None
    webhook_secret: str | None = None

    @field_validator("shop_id", "webhook_secret", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class PaymentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAYMENT_")

    provider: str = "platega"
    auto_renewal_mode: str = "disabled"

    @field_validator("provider", "auto_renewal_mode", mode="before")
    @classmethod
    def normalize_mode(cls, value):
        return str(value or "").strip().lower()

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value):
        if value not in {"yookassa", "platega"}:
            raise ValueError("PAYMENT_PROVIDER must be one of: yookassa, platega")
        return value

    @field_validator("auto_renewal_mode")
    @classmethod
    def validate_auto_renewal_mode(cls, value):
        if value not in {"yookassa", "disabled"}:
            raise ValueError(
                "PAYMENT_AUTO_RENEWAL_MODE must be one of: yookassa, disabled"
            )
        return value


class PlategaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLATEGA_")

    merchant_id: str | None = None
    secret_key: SecretStr | None = None
    base_url: str = "https://app.platega.io"
    payment_method: int | None = None

    @field_validator("merchant_id", "base_url", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("payment_method", mode="before")
    @classmethod
    def normalize_payment_method(cls, value):
        if value in (None, ""):
            return None
        return int(value)


class RemnaWaveSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REMNAWAVE_")

    url: str
    token: SecretStr
    # UUID внутреннего сквада по умолчанию (для автоподключения у новых пользователей)
    # Ожидается переменная окружения REMNAWAVE_DEFAULT_INTERNAL_SQUAD_UUID
    default_internal_squad_uuid: str | None = None
    # UUID внешнего сквада по умолчанию (для сегментации новых пользователей)
    # Ожидается переменная окружения REMNAWAVE_DEFAULT_EXTERNAL_SQUAD_UUID
    default_external_squad_uuid: str | None = None
    # UUID LTE-сквада (внутренний), для выдачи доступа к LTE нодам
    lte_internal_squad_uuid: str | None = None
    # Маркер LTE-нод в названии (например, CHTF)
    lte_node_marker: str = "CHTF"

    @field_validator("url")
    @classmethod
    def validate_vectra_owned_url(cls, value):
        text = str(value or "").strip().rstrip("/")
        if not text:
            raise ValueError(
                "REMNAWAVE_URL must point to Vectra's own RemnaWave panel"
            )

        parsed = urlparse(text)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            raise ValueError("REMNAWAVE_URL must include a hostname")

        if hostname == _STRATA_REMNAWAVE_DOMAIN or hostname.endswith(
            f".{_STRATA_REMNAWAVE_DOMAIN}"
        ):
            raise ValueError(
                "REMNAWAVE_URL points to Strata RemnaWave; configure "
                "Vectra's own RemnaWave panel instead"
            )

        return text

    @field_validator(
        "default_internal_squad_uuid",
        "default_external_squad_uuid",
        "lte_internal_squad_uuid",
        mode="before",
    )
    @classmethod
    def normalize_optional_uuid(cls, value):
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not text:
            return None
        return str(UUID(text))


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
payment_settings = PaymentSettings()
platega_settings = PlategaSettings()
remnawave_settings = RemnaWaveSettings()
script_settings = ScriptSettings()
admin_settings = AdminSettings()
test_mode = os.getenv("TESTMODE", "false").strip().lower() in ("true", "1", "yes")


class AdminIntegrationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADMIN_INTEGRATION_")

    token: SecretStr | None = None


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_")

    jwt_secret: SecretStr = SecretStr("change-me")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 86400
    jwt_leeway_seconds: int = 30

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        validate_runtime_secret("AUTH_JWT_SECRET", self.jwt_secret)


class CORSSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORS_")

    allow_origins: str | list[str] | None = None
    allow_origin_regex: str | None = None
    strict_allowlist: bool = True
    allow_loopback_http: bool = False

    @field_validator("allow_origins", mode="before")
    @classmethod
    def parse_allow_origins(cls, value):
        if not value:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return value

    @field_validator("allow_origin_regex", mode="before")
    @classmethod
    def parse_allow_origin_regex(cls, value):
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


# New settings class for application-specific configurations
class AppSettings(BaseSettings):
    trial_days: int = (
        3  # Default to 3 days, will be overridden by TRIAL_DAYS from .env
    )

    # Настройки для блокированных пользователей
    blocked_user_cleanup_days: int = (
        7  # Через сколько дней удалять заблокированных пользователей
    )
    blocked_user_max_failed_attempts: int = (
        5  # Максимальное количество неудачных попыток до блокировки
    )
    cleanup_blocked_users_enabled: bool = (
        True  # Включить/выключить автоочистку заблокированных пользователей
    )
    cleanup_blocked_users_interval_hours: int = 24  # Интервал очистки в часах
    # model_config = SettingsConfigDict(env_prefix="APP_") # If we want APP_TRIAL_DAYS
    # No env_prefix means it will look for TRIAL_DAYS directly.

    # Колесо призов
    prize_wheel_spin_bonus_price: int = (
        50  # Стоимость одной крутки при оплате бонусным балансом (₽)
    )
    devices_decrease_limit: int = (
        1  # Сколько раз можно уменьшить лимит устройств за период
    )
    family_devices_limit: int = 10  # Базовый лимит устройств family-подписки
    family_devices_threshold: int = 2
    subscription_devices_max: int = 30
    lte_default_price_per_gb: float = 1.5
    lte_default_max_gb: int = 500
    lte_default_step_gb: int = 1
    # Family alerts and anomaly protection
    family_alerts_enabled: bool = True
    family_alerts_webhook_url: str | None = None
    family_alerts_webhook_timeout_seconds: int = 5
    family_anomaly_block_threshold: int = 3
    family_anomaly_block_window_hours: int = 6
    family_anomaly_block_duration_minutes: int = 60


app_settings = AppSettings()


# Promo settings
class PromoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROMO_")

    # Секрет для HMAC промокодов; делаем необязательным, чтобы не падать при отсутствии
    hmac_secret: SecretStr | None = None


promo_settings = PromoSettings()


admin_integration_settings = AdminIntegrationSettings()
auth_settings = AuthSettings()
cors_settings = CORSSettings()


class WebAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    web_auth_enabled: bool = False
    password_auth_enabled: bool = False
    oauth_google_enabled: bool = False
    oauth_apple_enabled: bool = False
    oauth_yandex_enabled: bool = False
    oauth_telegram_enabled: bool = False


class OAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OAUTH_")

    public_base_url: str | None = None
    frontend_app_url: str = "https://app.vectra-pro.net"
    enabled_providers: Annotated[list[str], NoDecode] = ["google", "apple", "yandex", "telegram"]
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    apple_client_id: str | None = None
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    apple_private_key: SecretStr | None = None
    # Optional pre-generated Apple client secret for environments where
    # private-key signing is handled by the secret manager.
    apple_client_secret: SecretStr | None = None
    yandex_client_id: str | None = None
    yandex_client_secret: SecretStr | None = None
    telegram_client_id: str | None = None
    telegram_client_secret: SecretStr | None = None

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def parse_enabled_providers(cls, value):
        if not value:
            return []
        if isinstance(value, str):
            raw_values = value.split(",")
        elif isinstance(value, (list, tuple, set)):
            raw_values = value
        else:
            return value
        allowed = {"google", "apple", "yandex", "telegram"}
        return [
            item.strip().lower()
            for item in raw_values
            if str(item).strip().lower() in allowed
        ]


class SMTPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMTP_")

    host: str | None = None
    port: int = 465
    username: str | None = None
    password: SecretStr | None = None
    from_email: str | None = None
    from_name: str = "Vectra Connect"
    use_tls: bool = True


class ResendSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RESEND_")

    api_key: SecretStr | None = None
    from_email: str | None = None
    from_name: str = "Vectra Connect"
    base_url: str = "https://api.resend.com"
    timeout_seconds: float = 10.0


web_auth_settings = WebAuthSettings()
oauth_settings = OAuthSettings()
smtp_settings = SMTPSettings()
resend_settings = ResendSettings()


class LocalDevAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOCAL_DEV_AUTH_")

    enabled: bool = False
    allowed_telegram_ids: list[int] | str | None = None

    @field_validator("allowed_telegram_ids", mode="before")
    @classmethod
    def parse_allowed_telegram_ids(cls, value):
        if not value:
            return []
        if isinstance(value, str):
            raw_values = value.split(",")
        elif isinstance(value, int):
            raw_values = [value]
        elif isinstance(value, (list, tuple, set)):
            raw_values = value
        else:
            return value
        parsed: list[int] = []
        for raw in raw_values:
            text = str(raw).strip()
            if not text:
                continue
            parsed.append(int(text))
        return parsed


local_dev_auth_settings = LocalDevAuthSettings()


class CaptainUserLookupSettings(BaseSettings):
    """Настройки HTTPS-сервиса Captain User Lookup."""

    api_key: SecretStr = SecretStr("change-me")
    allowlist_domains: list[str] | str | None = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        validate_runtime_secret("API_KEY", self.api_key)

    @field_validator("allowlist_domains", mode="before")
    @classmethod
    def parse_allowlist(cls, value):
        if not value:
            return []
        if isinstance(value, str):
            cleaned = [
                item.strip().lower() for item in value.split(",") if item.strip()
            ]
            return cleaned
        if isinstance(value, (list, tuple, set)):
            cleaned = [str(item).strip().lower() for item in value if str(item).strip()]
            return cleaned
        return value


captain_lookup_settings = CaptainUserLookupSettings()
