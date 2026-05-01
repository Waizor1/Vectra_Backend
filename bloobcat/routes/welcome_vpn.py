from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

WELCOME_ALIAS = "browser-entry"
WELCOME_SUBSCRIPTION_TITLE = "Vectra Connect | Вход через браузер"
WELCOME_SERVER_REMARK = "Временный доступ отключён"
WELCOME_BOT_URL = "https://t.me/VectraConnect_bot"
WELCOME_SUPPORT_URL = "https://t.me/VectraConnect_support_bot"
WELCOME_ROTATION_MODE = "disabled"
WELCOME_UNAVAILABLE_REASON = "browser_entry_available"

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


def build_welcome_announce() -> str:
    return (
        "Временный доступ отключён для Vectra Connect. "
        "Используйте вход через браузер: он заменяет временную Happ-подписку для первичного подключения."
    )


async def build_welcome_vpn_response() -> WelcomeVpnResponse:
    """Return a stable disabled contract for stale clients without touching RemnaWave."""
    return WelcomeVpnResponse(
        featureEnabled=False,
        subscriptionUrl=None,
        announce=build_welcome_announce(),
        activeUntilLabel="",
        rotatedAt=None,
        unavailableReason=WELCOME_UNAVAILABLE_REASON,
    )


@router.get("/welcome-vpn", response_model=WelcomeVpnResponse)
async def get_welcome_vpn(response: Response) -> WelcomeVpnResponse:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return await build_welcome_vpn_response()
