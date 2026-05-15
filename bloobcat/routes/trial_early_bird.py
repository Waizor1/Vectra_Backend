"""FastAPI router for the Trial Early-Bird discount UX endpoints.

The frontend uses ``GET /trial/early-bird-discount/state`` to render the
banner countdown ("Оформи со скидкой −50% — осталось X дней"). The
discount itself is auto-applied at checkout via the existing
``PersonalDiscount.get_best_active_for_user`` lookup; no separate redeem
endpoint is needed.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from bloobcat.funcs.validate import validate
from bloobcat.services.trial_early_bird import get_trial_early_bird_state_payload


router = APIRouter(prefix="/trial", tags=["trial"])


class TrialEarlyBirdStateResponse(BaseModel):
    """Discount payload for FE. Uses camelCase to match the convention shared
    with /reverse-trial/state and /referrals/golden/payouts (see
    ReverseTrialDiscount, GoldenPeriodPayoutRow)."""

    active: bool
    percent: int
    expiresAtMs: Optional[int] = None
    used: bool


def _service_payload_to_response(payload: dict) -> TrialEarlyBirdStateResponse:
    """Adapt the service-side snake_case payload to the camelCase API response."""
    return TrialEarlyBirdStateResponse(
        active=bool(payload.get("active")),
        percent=int(payload.get("percent") or 0),
        expiresAtMs=payload.get("expires_at_ms"),
        used=bool(payload.get("used")),
    )


@router.get("/early-bird-discount/state", response_model=TrialEarlyBirdStateResponse)
async def get_state(user=Depends(validate)) -> TrialEarlyBirdStateResponse:
    payload = await get_trial_early_bird_state_payload(user)
    return _service_payload_to_response(payload)
