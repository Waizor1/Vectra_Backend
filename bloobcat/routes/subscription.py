from datetime import date, datetime, time, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bloobcat.db.users import Users, normalize_date
from bloobcat.db.tariff import Tariffs
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.funcs.validate import validate
from bloobcat.routes.payment import pay
from bloobcat.logger import get_logger

logger = get_logger("routes.subscription")

router = APIRouter(prefix="/subscription", tags=["subscription"])


class SubscriptionStatusResponse(BaseModel):
    isActive: bool
    endAtMs: int | None
    devicesLimit: int
    isFamilyEligible: bool


class SubscriptionPlanResponse(BaseModel):
    id: str
    title: str
    months: int
    devicesLimit: int
    priceRub: int
    perMonthText: str | None = None
    discountText: str | None = None
    badge: str | None = None


class SubscriptionPurchaseRequest(BaseModel):
    planId: str


def _compute_devices_limit(user: Users) -> int:
    devices_limit = 1
    if user.active_tariff_id:
        # fallback to active tariff limit
        # if not found, keep default
        pass
    return devices_limit


async def _resolve_devices_limit(user: Users) -> int:
    devices_limit = 1
    if user.active_tariff_id:
        tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if tariff and tariff.hwid_limit:
            devices_limit = int(tariff.hwid_limit)
    if user.hwid_limit is not None:
        devices_limit = int(user.hwid_limit)
    return max(1, devices_limit)


def _end_at_ms(expired_at: date | None) -> int | None:
    if not expired_at:
        return None
    dt = datetime.combine(expired_at, time.max, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _plan_id_for_months(months: int, family: bool = False) -> str:
    if months == 1:
        return "1month"
    if months == 3:
        return "3months"
    if months == 6:
        return "6months"
    if months == 12 and family:
        return "12months_family"
    if months == 12:
        return "12months_promo"
    return f"{months}months"


async def _build_plans() -> List[SubscriptionPlanResponse]:
    tariffs = await Tariffs.all().order_by("order")
    by_months: Dict[int, Tariffs] = {int(t.months): t for t in tariffs}
    plans: List[SubscriptionPlanResponse] = []

    def add_plan(months: int, device_count: int, badge: str | None = None):
        tariff = by_months.get(months)
        if not tariff:
            return
        price = int(tariff.calculate_price(device_count))
        per_month = int(round(price / months)) if months > 0 else price
        plan_id = _plan_id_for_months(months, family=(device_count == 10 and months == 12))
        plans.append(
            SubscriptionPlanResponse(
                id=plan_id,
                title=f"{months} {'месяц' if months == 1 else 'месяцев'}",
                months=months,
                devicesLimit=device_count,
                priceRub=price,
                perMonthText=f"≈ {per_month} ₽/мес",
                badge=badge,
            )
        )

    add_plan(1, 3)
    add_plan(3, 3)
    add_plan(6, 3)
    add_plan(12, 3, badge="выгодно")
    add_plan(12, 10, badge="семейная")
    return plans


@router.get("/status", response_model=SubscriptionStatusResponse)
async def get_status(user: Users = Depends(validate)) -> SubscriptionStatusResponse:
    end_at_ms = _end_at_ms(normalize_date(user.expired_at))
    is_active = bool(end_at_ms and end_at_ms > int(datetime.now(timezone.utc).timestamp() * 1000))
    devices_limit = await _resolve_devices_limit(user)
    is_family_eligible = bool(is_active and devices_limit >= 10)
    return SubscriptionStatusResponse(
        isActive=is_active,
        endAtMs=end_at_ms,
        devicesLimit=devices_limit,
        isFamilyEligible=is_family_eligible,
    )


@router.get("/plans", response_model=List[SubscriptionPlanResponse])
async def get_plans() -> List[SubscriptionPlanResponse]:
    return await _build_plans()


@router.post("/purchase", response_model=SubscriptionStatusResponse)
async def purchase(payload: SubscriptionPurchaseRequest, user: Users = Depends(validate)) -> SubscriptionStatusResponse:
    plan_id = payload.planId
    plan_map = {
        "1month": (1, 3),
        "3months": (3, 3),
        "6months": (6, 3),
        "12months_promo": (12, 3),
        "12months_family": (12, 10),
    }
    if plan_id not in plan_map:
        raise HTTPException(status_code=400, detail="Unknown planId")
    months, device_count = plan_map[plan_id]

    tariff = await Tariffs.filter(months=months).first()
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    if not user.email:
        raise HTTPException(status_code=400, detail="Email is required for purchase")

    result = await pay(
        tariff_id=tariff.id,
        email=user.email,
        device_count=device_count,
        lte_gb=0,
        user=user,
    )
    if isinstance(result, dict) and result.get("redirect_to"):
        raise HTTPException(status_code=409, detail={"code": "PAYMENT_REQUIRED", "redirect_to": result.get("redirect_to")})

    return await get_status(user)


@router.post("/cancel-renewal")
async def cancel_renewal(user: Users = Depends(validate)) -> Dict[str, Any]:
    if user.renew_id:
        user.renew_id = None
        await user.save(update_fields=["renew_id"])
    return {"ok": True}
