"""User-facing endpoints for the Golden Period UX."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.services.golden_period import (
    list_user_payouts,
    mark_period_seen,
)


router = APIRouter(prefix="/referrals/golden", tags=["referrals", "golden-period"])


class GoldenPeriodPayoutRow(BaseModel):
    id: int
    displayName: str
    amountRub: int
    status: str
    paidAtMs: Optional[int] = None
    clawedBackAtMs: Optional[int] = None
    clawbackReason: Optional[str] = None


class GoldenPeriodPayoutsResponse(BaseModel):
    payouts: List[GoldenPeriodPayoutRow]


class GoldenPeriodSeenResponse(BaseModel):
    updated: bool


@router.get("/payouts", response_model=GoldenPeriodPayoutsResponse)
async def list_payouts(
    limit: int = Query(20, ge=1, le=100),
    user: Users = Depends(validate),
) -> GoldenPeriodPayoutsResponse:
    rows = await list_user_payouts(user, limit=int(limit))
    return GoldenPeriodPayoutsResponse(
        payouts=[GoldenPeriodPayoutRow(**row) for row in rows]
    )


@router.post("/seen", response_model=GoldenPeriodSeenResponse)
async def mark_seen(
    user: Users = Depends(validate),
) -> GoldenPeriodSeenResponse:
    updated = await mark_period_seen(user)
    return GoldenPeriodSeenResponse(updated=bool(updated))
