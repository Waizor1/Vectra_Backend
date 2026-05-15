"""Admin endpoints for the tvpn-golden-period Directus module.

All endpoints reuse the existing admin-integration token guard so the
extension authenticates with the same `X-Admin-Integration-Token` header
used by the rest of the panel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from bloobcat.db.golden_period import (
    GoldenPeriod,
    GoldenPeriodConfig,
    GoldenPeriodPayout,
)
from bloobcat.routes.admin_integration import require_admin_integration_token
from bloobcat.services.golden_period import get_active_golden_period_config
from bloobcat.services.golden_period_clawback import reinstate_payout

router = APIRouter(
    prefix="/admin/golden-period",
    tags=["admin", "golden-period"],
)


class GoldenConfigResponse(BaseModel):
    id: int
    slug: str
    is_enabled: bool
    default_cap: int
    payout_amount_rub: int
    eligibility_min_active_days: int
    window_hours: int
    clawback_window_days: int
    message_templates: Dict[str, Any]
    signal_thresholds: Dict[str, Any]


class GoldenConfigPatch(BaseModel):
    is_enabled: Optional[bool] = None
    default_cap: Optional[int] = Field(default=None, ge=1, le=10000)
    payout_amount_rub: Optional[int] = Field(default=None, ge=1, le=100000)
    eligibility_min_active_days: Optional[int] = Field(default=None, ge=1, le=365)
    window_hours: Optional[int] = Field(default=None, ge=1, le=24 * 30)
    clawback_window_days: Optional[int] = Field(default=None, ge=1, le=365)
    message_templates: Optional[Dict[str, Any]] = None
    signal_thresholds: Optional[Dict[str, Any]] = None


def _config_to_response(config: GoldenPeriodConfig) -> GoldenConfigResponse:
    return GoldenConfigResponse(
        id=int(config.id),
        slug=str(config.slug),
        is_enabled=bool(config.is_enabled),
        default_cap=int(config.default_cap or 0),
        payout_amount_rub=int(config.payout_amount_rub or 0),
        eligibility_min_active_days=int(config.eligibility_min_active_days or 0),
        window_hours=int(config.window_hours or 0),
        clawback_window_days=int(config.clawback_window_days or 0),
        message_templates=dict(config.message_templates or {}),
        signal_thresholds=dict(config.signal_thresholds or {}),
    )


@router.get(
    "/config",
    response_model=GoldenConfigResponse,
    dependencies=[Depends(require_admin_integration_token)],
)
async def get_config() -> GoldenConfigResponse:
    config = await get_active_golden_period_config()
    return _config_to_response(config)


@router.patch(
    "/config",
    response_model=GoldenConfigResponse,
    dependencies=[Depends(require_admin_integration_token)],
)
async def patch_config(payload: GoldenConfigPatch) -> GoldenConfigResponse:
    config = await get_active_golden_period_config()
    updates: dict[str, Any] = {}
    for field in (
        "is_enabled",
        "default_cap",
        "payout_amount_rub",
        "eligibility_min_active_days",
        "window_hours",
        "clawback_window_days",
    ):
        value = getattr(payload, field)
        if value is not None:
            updates[field] = value
    if payload.message_templates is not None:
        updates["message_templates"] = payload.message_templates
    if payload.signal_thresholds is not None:
        updates["signal_thresholds"] = payload.signal_thresholds
    if updates:
        await GoldenPeriodConfig.filter(id=int(config.id)).update(**updates)
    refreshed = await GoldenPeriodConfig.get(id=int(config.id))
    return _config_to_response(refreshed)


class GoldenDashboardResponse(BaseModel):
    range_days: int
    active_periods_count: int
    total_paid_rub_period: int
    payouts_count_period: int
    clawback_rate: float
    top_referrers: list[dict]


def _parse_range(range_str: str) -> int:
    raw = (range_str or "").strip().lower()
    if raw.endswith("d") and raw[:-1].isdigit():
        return max(1, min(int(raw[:-1]), 365))
    if raw.isdigit():
        return max(1, min(int(raw), 365))
    return 7


@router.get(
    "/dashboard",
    response_model=GoldenDashboardResponse,
    dependencies=[Depends(require_admin_integration_token)],
)
async def dashboard(
    range: str = Query("7d", description="Window suffix, e.g. 7d, 30d"),
) -> GoldenDashboardResponse:
    range_days = _parse_range(range)
    cutoff = datetime.now(timezone.utc) - timedelta(days=range_days)

    active_count = await GoldenPeriod.filter(status="active").count()
    payouts = await GoldenPeriodPayout.filter(paid_at__gte=cutoff)
    payouts_count = len(payouts)
    total_paid = sum(int(p.amount_rub or 0) for p in payouts if str(p.status) != "clawed_back")
    clawed = sum(1 for p in payouts if str(p.status) == "clawed_back")
    clawback_rate = (clawed / payouts_count) if payouts_count > 0 else 0.0

    # Top referrers in window by total amount paid (excluding clawed_back).
    by_referrer: dict[int, int] = {}
    for p in payouts:
        if str(p.status) == "clawed_back":
            continue
        by_referrer[int(p.referrer_user_id)] = by_referrer.get(
            int(p.referrer_user_id), 0
        ) + int(p.amount_rub or 0)
    top_sorted = sorted(by_referrer.items(), key=lambda kv: kv[1], reverse=True)[:10]
    top = [{"user_id": uid, "total_paid_rub": amount} for uid, amount in top_sorted]

    return GoldenDashboardResponse(
        range_days=range_days,
        active_periods_count=int(active_count),
        total_paid_rub_period=int(total_paid),
        payouts_count_period=int(payouts_count),
        clawback_rate=round(float(clawback_rate), 4),
        top_referrers=top,
    )


class ReinstateResponse(BaseModel):
    reinstated: bool


@router.post(
    "/payouts/{payout_id}/reinstate",
    response_model=ReinstateResponse,
    dependencies=[Depends(require_admin_integration_token)],
)
async def reinstate(payout_id: int) -> ReinstateResponse:
    ok = await reinstate_payout(int(payout_id))
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payout not found or not in clawed_back state",
        )
    return ReinstateResponse(reinstated=True)
