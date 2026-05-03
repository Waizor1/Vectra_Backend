from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.services.email_preferences import verify_unsubscribe_token

logger = get_logger("routes.email")

router = APIRouter(prefix="/email", tags=["email"])


async def _resolve_user_from_token(token: str) -> Users:
    user_id = verify_unsubscribe_token(token)
    if user_id is None:
        raise HTTPException(status_code=400, detail="invalid_unsubscribe_token")
    user = await Users.get_or_none(id=user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return user


@router.get("/unsubscribe/status")
async def unsubscribe_status(token: str = Query(...)):
    user = await _resolve_user_from_token(token)
    return {"email_notifications_enabled": user.email_notifications_enabled}


@router.post("/unsubscribe")
async def unsubscribe(token: str = Query(...)):
    user = await _resolve_user_from_token(token)
    if not user.email_notifications_enabled:
        return {"status": "already_unsubscribed"}
    user.email_notifications_enabled = False
    await user.save(update_fields=["email_notifications_enabled"])
    logger.info("User {} unsubscribed from email notifications", user.id)
    return {"status": "unsubscribed"}


@router.post("/unsubscribe/one-click")
async def unsubscribe_one_click(token: str = Query(...)):
    return await unsubscribe(token=token)
