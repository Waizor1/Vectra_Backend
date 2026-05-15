"""FastAPI router for the reverse-trial UX endpoints.

The frontend uses ``GET /reverse-trial/state`` to render the countdown banner
and the post-downgrade discount modal. ``POST /reverse-trial/redeem-discount``
is called from that modal when the user clicks "use my -50 % discount" so the
backend stops nagging on subsequent loads.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from bloobcat.funcs.validate import validate
from bloobcat.services.reverse_trial import (
    get_reverse_trial_state_payload,
    redeem_reverse_trial_discount,
)


router = APIRouter(prefix="/reverse-trial", tags=["reverse-trial"])


class ReverseTrialDiscount(BaseModel):
    available: bool
    percent: int
    expires_at_ms: Optional[int] = None
    used: bool


class ReverseTrialStateResponse(BaseModel):
    status: str
    granted_at_ms: Optional[int] = None
    expires_at_ms: Optional[int] = None
    days_remaining: int
    tariff_name: Optional[str] = None
    discount: ReverseTrialDiscount


class ReverseTrialRedeemResponse(BaseModel):
    applicable: bool
    discount_id: Optional[int] = None
    percent: Optional[int] = None


@router.get("/state", response_model=ReverseTrialStateResponse)
async def get_state(user=Depends(validate)) -> ReverseTrialStateResponse:
    payload = await get_reverse_trial_state_payload(user)
    return ReverseTrialStateResponse(**payload)


@router.post("/redeem-discount", response_model=ReverseTrialRedeemResponse)
async def redeem_discount(user=Depends(validate)) -> ReverseTrialRedeemResponse:
    result = await redeem_reverse_trial_discount(user)
    return ReverseTrialRedeemResponse(**result)
