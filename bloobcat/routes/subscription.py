from datetime import date, datetime, time, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bloobcat.db.users import Users, normalize_date
from bloobcat.db.tariff import Tariffs
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.funcs.validate import validate
from bloobcat.routes.payment import pay
from bloobcat.settings import app_settings, payment_settings
from bloobcat.logger import get_logger

logger = get_logger("routes.subscription")

router = APIRouter(prefix="/subscription", tags=["subscription"])


def _auto_renewal_enabled_for_user(user: Users) -> bool:
    return bool(user.renew_id) and str(
        getattr(payment_settings, "auto_renewal_mode", "") or ""
    ).strip().lower() == "yookassa"


class SubscriptionStatusResponse(BaseModel):
    isActive: bool
    endAtMs: int | None
    devicesLimit: int
    isFamilyEligible: bool
    autoRenewEnabled: bool


class SubscriptionPlanResponse(BaseModel):
    id: str
    tariffId: int
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
    tariffs = await Tariffs.filter(is_active=True).order_by("order")
    for tariff in tariffs:
        await tariff.sync_effective_pricing_fields()
    by_months: Dict[int, Tariffs] = {int(t.months): t for t in tariffs}
    plans: List[SubscriptionPlanResponse] = []

    # Reference price for discount calculation: 1-month plan for default device count.
    one_month_tariff = by_months.get(1)
    one_month_price_ref: int | None = None
    if one_month_tariff:
        one_month_default_devices = max(1, int(one_month_tariff.devices_limit_default or 3))
        one_month_price_ref = int(one_month_tariff.calculate_price(one_month_default_devices))

    def add_plan(months: int, device_count: int, badge: str | None = None, family: bool = False):
        tariff = by_months.get(months)
        if not tariff:
            return
        if device_count < 1:
            return
        price = int(tariff.calculate_price(device_count))
        per_month = int(round(price / months)) if months > 0 else price
        plan_id = _plan_id_for_months(months, family=family)

        discount_text: str | None = None
        default_devices = max(1, int(tariff.devices_limit_default or 3))
        if (
            months > 1
            and device_count == default_devices
            and one_month_price_ref
            and one_month_price_ref > 0
        ):
            full_price = one_month_price_ref * months
            if full_price > 0:
                pct = round((1 - price / full_price) * 100)
                if 0 < pct < 100:
                    discount_text = f"\u2212{pct}%"

        plans.append(
            SubscriptionPlanResponse(
                id=plan_id,
                tariffId=int(tariff.id),
                title=f"{months} {'месяц' if months == 1 else 'месяцев'}",
                months=months,
                devicesLimit=device_count,
                priceRub=price,
                perMonthText=f"≈ {per_month} ₽/мес",
                discountText=discount_text,
                badge=badge,
            )
        )

    def base_limit(months: int, default: int = 3) -> int:
        tariff = by_months.get(months)
        if not tariff:
            return default
        return max(1, int(tariff.devices_limit_default or default))

    add_plan(1, base_limit(1))
    add_plan(3, base_limit(3))
    add_plan(6, base_limit(6))
    add_plan(12, base_limit(12), badge="выгодно")

    # Family is a 12-month variant with a higher device limit on the same tariff.
    family_tariff = by_months.get(12)
    if family_tariff and bool(getattr(family_tariff, "family_plan_enabled", True)):
        family_limit = max(1, int(family_tariff.devices_limit_family or 10))
        default_limit_12 = max(1, int(family_tariff.devices_limit_default or 3))
        if family_limit > default_limit_12:
            add_plan(12, family_limit, badge="семейная", family=True)
    return plans


@router.get("/status", response_model=SubscriptionStatusResponse)
async def get_status(user: Users = Depends(validate)) -> SubscriptionStatusResponse:
    end_at_ms = _end_at_ms(normalize_date(user.expired_at))
    is_active = bool(end_at_ms and end_at_ms > int(datetime.now(timezone.utc).timestamp() * 1000))
    devices_limit = await _resolve_devices_limit(user)
    family_limit = max(1, int(getattr(app_settings, "family_devices_limit", 10) or 10))
    is_family_eligible = bool(is_active and devices_limit >= family_limit)
    return SubscriptionStatusResponse(
        isActive=is_active,
        endAtMs=end_at_ms,
        devicesLimit=devices_limit,
        isFamilyEligible=is_family_eligible,
        autoRenewEnabled=_auto_renewal_enabled_for_user(user),
    )


@router.get("/plans", response_model=List[SubscriptionPlanResponse])
async def get_plans() -> List[SubscriptionPlanResponse]:
    return await _build_plans()


@router.post("/purchase", response_model=SubscriptionStatusResponse)
async def purchase(payload: SubscriptionPurchaseRequest, user: Users = Depends(validate)) -> SubscriptionStatusResponse:
    plan_id = payload.planId
    tariffs = await Tariffs.filter(is_active=True).all()
    for tariff in tariffs:
        await tariff.sync_effective_pricing_fields()
    by_months: Dict[int, Tariffs] = {int(t.months): t for t in tariffs}
    family_tariff = by_months.get(12)
    family_enabled = bool(getattr(family_tariff, "family_plan_enabled", True)) if family_tariff else False
    plan_map = {
        "1month": (1, max(1, int((by_months.get(1).devices_limit_default if by_months.get(1) else 3) or 3))),
        "3months": (3, max(1, int((by_months.get(3).devices_limit_default if by_months.get(3) else 3) or 3))),
        "6months": (6, max(1, int((by_months.get(6).devices_limit_default if by_months.get(6) else 3) or 3))),
        "12months_promo": (12, max(1, int((by_months.get(12).devices_limit_default if by_months.get(12) else 3) or 3))),
    }
    if family_enabled:
        plan_map["12months_family"] = (
            12,
            max(1, int((by_months.get(12).devices_limit_family if by_months.get(12) else 10) or 10)),
        )
    if plan_id not in plan_map:
        raise HTTPException(status_code=400, detail="Unknown planId")
    months, device_count = plan_map[plan_id]

    tariff = await Tariffs.filter(months=months, is_active=True).first()
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
    was_already_cancelled = not bool(user.renew_id)
    if user.renew_id:
        user.renew_id = None
        await user.save(update_fields=["renew_id"])
    return {
        "ok": True,
        "autoRenewEnabled": False,
        "wasAlreadyCancelled": was_already_cancelled,
    }
