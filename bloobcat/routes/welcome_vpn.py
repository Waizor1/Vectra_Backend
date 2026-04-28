from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.happ_crypto import normalize_happ_crypto_link
from bloobcat.routes.user import remnawave_client

logger = get_logger("routes.welcome_vpn")

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
WELCOME_ALIAS = "welcome-agent"
WELCOME_SUBSCRIPTION_TITLE = "Vectra Connect | Временная"
WELCOME_SERVER_REMARK = "🇷🇺 RU 1дн | Регистрация в Vectra Connect"
WELCOME_BOT_URL = "https://t.me/VectraConnect_bot"
WELCOME_SUPPORT_URL = "https://t.me/VectraConnect_support_bot"
WELCOME_ROTATION_MODE = "recreate"

router = APIRouter(tags=["welcome-vpn"])


class WelcomeVpnResponse(BaseModel):
    featureEnabled: bool
    alias: str = WELCOME_ALIAS
    subscriptionTitle: str = WELCOME_SUBSCRIPTION_TITLE
    serverRemark: str = WELCOME_SERVER_REMARK
    subscriptionUrl: str | None = None
    botUrl: str = WELCOME_BOT_URL
    supportUrl: str = WELCOME_SUPPORT_URL
    announce: str
    activeUntilLabel: str
    rotatedAt: str | None = None
    rotationMode: str = WELCOME_ROTATION_MODE
    unavailableReason: str | None = Field(default=None, exclude=True)


def next_moscow_midnight_label(now: datetime | None = None) -> str:
    """Returns the next daily welcome-agent rotation date as dd.MM in Moscow time."""
    current = now.astimezone(MOSCOW_TZ) if now else datetime.now(MOSCOW_TZ)
    next_day = current.date() + timedelta(days=1)
    return next_day.strftime("%d.%m")


def build_welcome_announce(active_until_label: str) -> str:
    return (
        "Временный доступ помогает продолжить настройку Vectra Connect.\n"
        "Установите Happ, добавьте подписку и откройте нашего бота.\n"
        f"Доступ обновляется каждый день и активен до {active_until_label} по Москве."
    )


def _extract_remnawave_user(raw_response: dict[str, Any]) -> dict[str, Any]:
    response = raw_response.get("response")
    if isinstance(response, dict):
        user = response.get("user")
        if isinstance(user, dict):
            return user
        return response
    return raw_response


async def _extract_happ_subscription_url(user_data: dict[str, Any]) -> str:
    happ_payload = user_data.get("happ")
    if isinstance(happ_payload, dict):
        crypto_link = normalize_happ_crypto_link(happ_payload.get("cryptoLink") or "")
        if crypto_link:
            return crypto_link

    raw_subscription_url = str(user_data.get("subscriptionUrl") or "").strip()
    if raw_subscription_url:
        return normalize_happ_crypto_link(
            await remnawave_client.tools.encrypt_happ_crypto_link(raw_subscription_url)
        )

    raise ValueError("welcome-agent subscription URL not found")


async def build_welcome_vpn_response(now: datetime | None = None) -> WelcomeVpnResponse:
    active_until_label = next_moscow_midnight_label(now)
    base_payload = {
        "activeUntilLabel": active_until_label,
        "announce": build_welcome_announce(active_until_label),
    }

    try:
        raw_user = await remnawave_client.users.get_user_by_username(WELCOME_ALIAS)
        user_data = _extract_remnawave_user(raw_user)
        subscription_url = await _extract_happ_subscription_url(user_data)

        return WelcomeVpnResponse(
            featureEnabled=True,
            subscriptionUrl=subscription_url,
            rotatedAt=str(user_data.get("rotatedAt") or "").strip() or None,
            **base_payload,
        )
    except Exception as exc:
        logger.error("Failed to resolve welcome VPN subscription: {}", exc, exc_info=True)
        return WelcomeVpnResponse(
            featureEnabled=False,
            subscriptionUrl=None,
            unavailableReason="subscription_unavailable",
            **base_payload,
        )


@router.get("/welcome-vpn", response_model=WelcomeVpnResponse)
async def get_welcome_vpn(response: Response) -> WelcomeVpnResponse:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return await build_welcome_vpn_response()
