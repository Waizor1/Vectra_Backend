from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from aiogram.utils.web_app import safe_parse_webapp_init_data

from bloobcat.db.users import Users
from bloobcat.funcs.auth_tokens import create_access_token
from bloobcat.settings import telegram_settings
from bloobcat.logger import get_logger

logger = get_logger("routes.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramAuthRequest(BaseModel):
    initData: str


class TelegramAuthResponse(BaseModel):
    accessToken: str
    expiresIn: int


@router.post("/telegram", response_model=TelegramAuthResponse)
async def auth_telegram(payload: TelegramAuthRequest) -> TelegramAuthResponse:
    init_data = (payload.initData or "").strip()
    if not init_data:
        raise HTTPException(status_code=400, detail="Missing initData")

    try:
        user_data = safe_parse_webapp_init_data(
            telegram_settings.token.get_secret_value(), init_data
        )
    except Exception as exc:
        logger.warning(f"Invalid initData for /auth/telegram: {exc}")
        raise HTTPException(status_code=403, detail="Invalid initData")

    if not user_data or not user_data.user:
        raise HTTPException(status_code=403, detail="Invalid user data")

    db_user = await Users.get_user(telegram_user=user_data.user, referred_by=0, utm=None)
    if not db_user:
        raise HTTPException(status_code=500, detail="User not found in database")

    token, ttl_seconds = create_access_token(db_user.id)
    return TelegramAuthResponse(accessToken=token, expiresIn=ttl_seconds)
