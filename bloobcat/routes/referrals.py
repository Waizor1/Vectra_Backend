from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.bot.bot import get_bot_username
from bloobcat.logger import get_logger

logger = get_logger("routes.referrals")

router = APIRouter(prefix="/referrals", tags=["referrals"])


class ReferralStatusResponse(BaseModel):
    referralLink: str
    friendsCount: int
    totalBonusDays: int
    level: int


def _calc_level(count: int) -> int:
    if count <= 0:
        return 0
    if 1 <= count <= 4:
        return 1
    if 5 <= count <= 9:
        return 2
    return 3


@router.get("/status", response_model=ReferralStatusResponse)
async def get_status(user: Users = Depends(validate)) -> ReferralStatusResponse:
    # Keep consistent with partner dashboard counters:
    # count all invited users by referred_by, regardless of activation stage.
    friends_count = await Users.filter(referred_by=user.id).count()
    level = _calc_level(friends_count)
    # Total earned referral bonus days (not money).
    # This value is accumulated when a referred user makes their first payment.
    total_bonus = int(getattr(user, "referral_bonus_days_total", 0) or 0)
    try:
        bot_name = await get_bot_username()
    except Exception:
        bot_name = "TriadVPN_bot"
    referral_link = f"https://t.me/{bot_name}?start={user.id}"
    return ReferralStatusResponse(
        referralLink=referral_link,
        friendsCount=friends_count,
        totalBonusDays=total_bonus,
        level=level,
    )


@router.post("/invite")
async def log_invite(user: Users = Depends(validate)) -> Dict[str, Any]:
    logger.info(f"Referral invite created by user {user.id}")
    return {"ok": True}
