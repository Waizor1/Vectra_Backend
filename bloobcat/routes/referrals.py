from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.logger import get_logger
from bloobcat.services.referral_gamification import (
    build_referral_status,
    open_referral_chest,
)

logger = get_logger("routes.referrals")

router = APIRouter(prefix="/referrals", tags=["referrals"])


class ReferralLevelInfo(BaseModel):
    key: Literal["start", "bronze", "silver", "gold", "platinum", "diamond"] | str
    name: str
    threshold: int
    cashbackPercent: int


class ReferralNextLevelInfo(ReferralLevelInfo):
    friendsLeft: int


class ReferralLevelRow(ReferralLevelInfo):
    chestRewardLabel: str
    reached: bool


class ReferralPendingChest(BaseModel):
    id: int
    levelKey: str
    levelName: str
    title: str


class ReferralRewardHistoryItem(BaseModel):
    type: Literal["cashback", "chest"]
    title: str
    valueLabel: str
    createdAt: str


class ReferralStatusResponse(BaseModel):
    referralLink: str
    friendsCount: int
    invitedCount: int
    paidFriendsCount: int
    totalCashbackRub: int
    availableBalanceRub: int
    currentLevel: ReferralLevelInfo
    nextLevel: ReferralNextLevelInfo | None
    levels: list[ReferralLevelRow]
    pendingChests: list[ReferralPendingChest]
    lastRewards: list[ReferralRewardHistoryItem]
    totalBonusDays: int
    # Legacy numeric level for older clients. New UI uses currentLevel instead.
    level: int


class ReferralChestRewardResponse(BaseModel):
    id: int
    levelKey: str
    levelName: str
    type: Literal["balance", "discount_percent"] | str
    value: int
    valueLabel: str
    title: str


class ReferralChestOpenResponse(BaseModel):
    reward: ReferralChestRewardResponse
    status: ReferralStatusResponse


@router.get("/status", response_model=ReferralStatusResponse)
async def get_status(user: Users = Depends(validate)) -> ReferralStatusResponse:
    return ReferralStatusResponse(**(await build_referral_status(user)))


@router.post("/chests/{chest_id}/open", response_model=ReferralChestOpenResponse)
async def open_chest(
    chest_id: int,
    user: Users = Depends(validate),
) -> ReferralChestOpenResponse:
    reward = await open_referral_chest(user=user, chest_id=int(chest_id))
    if reward is None:
        raise HTTPException(status_code=404, detail="Referral chest not found or already opened")
    status = await build_referral_status(user, ensure_chests=True)
    return ReferralChestOpenResponse(
        reward=ReferralChestRewardResponse(**reward),
        status=ReferralStatusResponse(**status),
    )


@router.post("/invite")
async def log_invite(user: Users = Depends(validate)) -> Dict[str, Any]:
    logger.info(f"Referral invite created by user {user.id}")
    return {"ok": True}
