from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets

TEMP_SETUP_LINK_TTL = timedelta(minutes=15)
TEMP_SETUP_TOKEN_BYTES = 32


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def generate_temp_setup_token() -> str:
    return secrets.token_urlsafe(TEMP_SETUP_TOKEN_BYTES)


def get_temp_setup_expires_at(now: datetime | None = None) -> datetime:
    current_time = ensure_utc(now) if now else utc_now()
    return current_time + TEMP_SETUP_LINK_TTL


def is_temp_setup_token_expired(expires_at: datetime | None, now: datetime | None = None) -> bool:
    if expires_at is None:
        return True
    current_time = ensure_utc(now) if now else utc_now()
    return current_time >= ensure_utc(expires_at)


def build_temp_setup_link_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/setup/temp/{token}"


def build_temp_setup_public_payload(
    *,
    subscription_url: str | None,
    subscription_url_error: str | None,
    devices_count: int,
    devices_limit: int,
    expires_at: datetime,
    now: datetime | None = None,
) -> dict[str, object]:
    current_time = ensure_utc(now) if now else utc_now()
    normalized_expires_at = ensure_utc(expires_at)
    ttl_seconds = max(0, int((normalized_expires_at - current_time).total_seconds()))
    return {
        "subscription_url": subscription_url,
        "subscription_url_error": subscription_url_error,
        "devices_count": int(devices_count),
        "devices_limit": int(devices_limit),
        "expires_at": normalized_expires_at.isoformat(),
        "server_now": current_time.isoformat(),
        "ttl_seconds": ttl_seconds,
    }
