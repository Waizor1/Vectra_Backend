from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from aiogram.utils.web_app import safe_parse_webapp_init_data

from bloobcat.db.users import Users
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.funcs.auth_tokens import create_access_token
from bloobcat.settings import telegram_settings
from bloobcat.logger import get_logger
from tortoise.expressions import F

logger = get_logger("routes.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


class TelegramAuthRequest(BaseModel):
    initData: str
    # Optional: forwarded start payload (e.g. from bot /start -> Mini App URL ?start=...).
    startParam: str | None = None


class TelegramAuthResponse(BaseModel):
    accessToken: str
    expiresIn: int
    is_registered: bool = False


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

    # Determine effective start param:
    # - Telegram deep links populate initDataUnsafe.start_param
    # - When we open Mini App from bot /start, we forward it via ?start=... and send here
    start_param = (payload.startParam or "").strip() or (getattr(user_data, "start_param", None) or "").strip()

    referred_by = 0
    utm = None

    # Combined UTM and numeric referral (format: <utm>-<referrer_id>)
    if start_param:
        param = start_param
        if "-" in param:
            utm_part, ref_part = param.rsplit("-", 1)
            if ref_part.isdigit():
                referred_by = int(ref_part)
                utm = utm_part
            else:
                utm = param
        elif param.startswith("family_"):
            # Ignore family links in auth context.
            pass
        elif param.startswith("qr_"):
            utm = param
            token = param[3:]
            qr: PartnerQr | None = None
            # Try to resolve as UUID (hex or canonical) first.
            try:
                qr_uuid = uuid.UUID(token) if len(token) != 32 else uuid.UUID(hex=token)
                qr = await PartnerQr.get_or_none(id=qr_uuid)
            except Exception:
                qr = None
            if not qr:
                qr = await PartnerQr.get_or_none(slug=token)
            if qr:
                referred_by = int(qr.owner_id)
                # Count a "view" on each app open via this QR token.
                try:
                    await PartnerQr.filter(id=qr.id).update(views_count=F("views_count") + 1)
                except Exception as e_views:
                    logger.warning(f"Failed to update partner QR views for {qr.id}: {e_views}")
        elif param.isdigit():
            referred_by = int(param)
        else:
            utm = param

    db_user = await Users.get_user(telegram_user=user_data.user, referred_by=referred_by, utm=utm)
    if not db_user:
        raise HTTPException(status_code=500, detail="User not found in database")

    token, ttl_seconds = create_access_token(db_user.id)
    return TelegramAuthResponse(
        accessToken=token,
        expiresIn=ttl_seconds,
        is_registered=bool(db_user.is_registered),
    )
