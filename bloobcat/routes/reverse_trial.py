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
    """Discount payload for FE. Uses camelCase to match the convention shared
    with /referrals/status payloads (see ReferralStatusResponse, GoldenPeriodPayload)."""

    available: bool
    percent: int
    expiresAtMs: Optional[int] = None
    used: bool


class ReverseTrialStateResponse(BaseModel):
    status: str
    grantedAtMs: Optional[int] = None
    expiresAtMs: Optional[int] = None
    daysRemaining: int
    tariffName: Optional[str] = None
    discount: ReverseTrialDiscount


class ReverseTrialRedeemResponse(BaseModel):
    applicable: bool
    discountId: Optional[int] = None
    percent: Optional[int] = None


_STATUS_SERVICE_TO_API: dict[str, str] = {
    "none": "absent",
    "active": "active",
    "expired": "expired",
    "converted_to_paid": "converted",
    "cancelled": "cancelled",
}


def _service_payload_to_response(payload: dict) -> ReverseTrialStateResponse:
    """Adapt the service-side snake_case payload to the camelCase API response.

    Also normalises ``status`` values that the service uses internally to the
    set the FE expects (``absent`` instead of ``none``, ``converted`` instead
    of ``converted_to_paid``)."""
    discount_in = payload.get("discount") or {}
    raw_status = str(payload.get("status") or "none")
    api_status = _STATUS_SERVICE_TO_API.get(raw_status, raw_status)
    return ReverseTrialStateResponse(
        status=api_status,
        grantedAtMs=payload.get("granted_at_ms"),
        expiresAtMs=payload.get("expires_at_ms"),
        daysRemaining=int(payload.get("days_remaining") or 0),
        tariffName=payload.get("tariff_name"),
        discount=ReverseTrialDiscount(
            available=bool(discount_in.get("available")),
            percent=int(discount_in.get("percent") or 0),
            expiresAtMs=discount_in.get("expires_at_ms"),
            used=bool(discount_in.get("used")),
        ),
    )


def _service_redeem_to_response(result: dict) -> ReverseTrialRedeemResponse:
    return ReverseTrialRedeemResponse(
        applicable=bool(result.get("applicable")),
        discountId=result.get("discount_id"),
        percent=result.get("percent"),
    )


@router.get("/state", response_model=ReverseTrialStateResponse)
async def get_state(user=Depends(validate)) -> ReverseTrialStateResponse:
    payload = await get_reverse_trial_state_payload(user)
    return _service_payload_to_response(payload)


@router.post("/redeem-discount", response_model=ReverseTrialRedeemResponse)
async def redeem_discount(user=Depends(validate)) -> ReverseTrialRedeemResponse:
    result = await redeem_reverse_trial_discount(user)
    return _service_redeem_to_response(result)
