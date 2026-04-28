from datetime import date, datetime, time, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from bloobcat.db.users import Users, normalize_date
from bloobcat.db.tariff import Tariffs
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.funcs.validate import validate
from bloobcat.routes.payment import pay
from bloobcat.bot.notifications.admin import (
    cancel_subscription,
    notify_frozen_base_activation,
    notify_frozen_family_activation,
)
from bloobcat.bot.notifications.subscription.renewal import (
    notify_frozen_base_activation_success,
    notify_frozen_family_activation_success,
)
from bloobcat.settings import app_settings, payment_settings
from bloobcat.logger import get_logger
from bloobcat.services.subscription_limits import family_devices_threshold
from bloobcat.services.tariff_quote import (
    build_duration_offer,
    build_subscription_quote,
    quote_public_dict,
    tariff_default_devices,
)
from bloobcat.services.subscription_overlay import (
    FrozenBaseActivationError,
    FrozenFamilyActivationError,
    activate_frozen_family_with_current_freeze,
    activate_frozen_base_with_current_freeze,
    get_overlay_payload,
    resume_frozen_base_if_due,
)

logger = get_logger("routes.subscription")

router = APIRouter(prefix="/subscription", tags=["subscription"])


class SubscriptionStatusResponse(BaseModel):
    isActive: bool
    endAtMs: int | None
    devicesLimit: int
    isFamilyEligible: bool
    autoRenewEnabled: bool
    has_frozen_base: bool | None = None
    base_remaining_days: int | None = None
    base_hwid_limit: int | None = None
    base_resume_at: str | None = None
    will_restore_base_after_family: bool | None = None
    has_frozen_family: bool | None = None
    frozen_family_remaining_days: int | None = None
    frozen_family_hwid_limit: int | None = None
    frozen_family_resume_at: str | None = None
    reverse_migration_available_at: str | None = None
    reverse_migration_retry_after_seconds: int | None = None
    active_kind: str | None = None


class SubscriptionPlanResponse(BaseModel):
    id: str
    tariffId: int
    title: str
    months: int
    devicesLimit: int
    priceRub: int
    priceFromRub: int | None = None
    priceFromText: str | None = None
    originalPriceRub: int | None = None
    personalDiscountPercent: int | None = None
    perMonthText: str | None = None
    discountText: str | None = None
    discountPercent: int | None = None
    badge: str | None = None
    hint: str | None = None
    devicesMin: int = 1
    devicesMax: int = 30
    defaultDevices: int = 1
    familyThreshold: int = 2
    lteEnabled: bool = False
    lteAvailable: bool = False
    ltePricePerGb: float = 0
    lteMinGb: int = 0
    lteMaxGb: int = 0
    lteStepGb: int = 1
    tariffKind: str = "base"
    tariffType: str = "base"
    familyVariant: bool = False


class SubscriptionQuoteRequest(BaseModel):
    tariff_id: int = Field(alias="tariffId")
    device_count: int = Field(default=1, alias="deviceCount")
    lte_gb: int = Field(default=0, alias="lteGb")
    promo_code: str | None = Field(default=None, alias="promoCode")
    client_context: dict[str, Any] | None = Field(default=None, alias="clientContext")

    model_config = {"populate_by_name": True}


class SubscriptionPurchaseRequest(BaseModel):
    planId: str


class ActivateFrozenBaseResponse(BaseModel):
    ok: bool
    switched_until: str
    frozen_current_days: int
    activated_frozen_base_days: int


class ActivateFrozenFamilyResponse(BaseModel):
    ok: bool
    switched_until: str
    frozen_current_days: int
    activated_frozen_family_days: int


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
    return f"{months}months"


async def _notify_frozen_base_activation_success(
    user: Users, result: dict[str, Any]
) -> None:
    try:
        await notify_frozen_base_activation_success(
            user,
            switched_until=str(result["switched_until"]),
            frozen_current_days=int(result["frozen_current_days"]),
            activated_frozen_base_days=int(result["activated_frozen_base_days"]),
        )
    except Exception as exc:
        logger.error("Failed to notify user about frozen base activation: %s", exc)

    try:
        await notify_frozen_base_activation(
            user,
            switched_until=str(result["switched_until"]),
            frozen_current_days=int(result["frozen_current_days"]),
            activated_frozen_base_days=int(result["activated_frozen_base_days"]),
        )
    except Exception as exc:
        logger.error("Failed to notify admin about frozen base activation: %s", exc)


async def _notify_frozen_family_activation_success(
    user: Users, result: dict[str, Any]
) -> None:
    try:
        await notify_frozen_family_activation_success(
            user,
            switched_until=str(result["switched_until"]),
            frozen_current_days=int(result["frozen_current_days"]),
            activated_frozen_family_days=int(result["activated_frozen_family_days"]),
        )
    except Exception as exc:
        logger.error("Failed to notify user about frozen family activation: %s", exc)

    try:
        await notify_frozen_family_activation(
            user,
            switched_until=str(result["switched_until"]),
            frozen_current_days=int(result["frozen_current_days"]),
            activated_frozen_family_days=int(result["activated_frozen_family_days"]),
        )
    except Exception as exc:
        logger.error("Failed to notify admin about frozen family activation: %s", exc)


async def _build_plans(user: Users) -> List[SubscriptionPlanResponse]:
    tariffs = await Tariffs.filter(is_active=True).order_by("order", "months")
    by_months: Dict[int, Tariffs] = {}
    for tariff in tariffs:
        months = int(getattr(tariff, "months", 0) or 0)
        if months <= 0 or months in by_months:
            continue
        by_months[months] = tariff

    one_month_tariff = by_months.get(1)
    offers: List[SubscriptionPlanResponse] = []
    for months in (1, 3, 6, 12):
        tariff = by_months.get(months)
        if not tariff:
            continue
        offer = await build_duration_offer(
            tariff=tariff,
            user_id=int(user.id),
            one_month_reference_tariff=one_month_tariff if months > 1 else None,
        )
        offers.append(SubscriptionPlanResponse(**offer))
    return offers


@router.get("/status", response_model=SubscriptionStatusResponse)
async def get_status(user: Users = Depends(validate)) -> SubscriptionStatusResponse:
    resumed = await resume_frozen_base_if_due(user)
    if resumed:
        user = await Users.get(id=user.id)
    end_at_ms = _end_at_ms(normalize_date(user.expired_at))
    is_active = bool(
        end_at_ms and end_at_ms > int(datetime.now(timezone.utc).timestamp() * 1000)
    )
    devices_limit = await _resolve_devices_limit(user)
    is_family_eligible = bool(is_active and devices_limit >= family_devices_threshold())
    overlay = await get_overlay_payload(user)
    return SubscriptionStatusResponse(
        isActive=is_active,
        endAtMs=end_at_ms,
        devicesLimit=devices_limit,
        isFamilyEligible=is_family_eligible,
        autoRenewEnabled=(
            bool(user.renew_id) and payment_settings.auto_renewal_mode == "yookassa"
        ),
        has_frozen_base=overlay.get("has_frozen_base"),
        base_remaining_days=overlay.get("base_remaining_days"),
        base_hwid_limit=overlay.get("base_hwid_limit"),
        base_resume_at=overlay.get("base_resume_at"),
        will_restore_base_after_family=overlay.get("will_restore_base_after_family"),
        has_frozen_family=overlay.get("has_frozen_family"),
        frozen_family_remaining_days=overlay.get("frozen_family_remaining_days"),
        frozen_family_hwid_limit=overlay.get("frozen_family_hwid_limit"),
        frozen_family_resume_at=overlay.get("frozen_family_resume_at"),
        reverse_migration_available_at=overlay.get("reverse_migration_available_at"),
        reverse_migration_retry_after_seconds=overlay.get(
            "reverse_migration_retry_after_seconds"
        ),
        active_kind=overlay.get("active_kind"),
    )


@router.get("/plans", response_model=List[SubscriptionPlanResponse])
async def get_plans(user: Users = Depends(validate)) -> List[SubscriptionPlanResponse]:
    return await _build_plans(user)


@router.post("/quote")
async def quote_subscription(
    payload: SubscriptionQuoteRequest, user: Users = Depends(validate)
) -> Dict[str, Any]:
    tariff = await Tariffs.get_or_none(id=int(payload.tariff_id), is_active=True)
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    one_month_tariff = await Tariffs.filter(months=1, is_active=True).order_by("order").first()
    quote = await build_subscription_quote(
        tariff=tariff,
        user_id=int(user.id),
        device_count=payload.device_count,
        lte_gb=payload.lte_gb,
        one_month_reference_tariff=one_month_tariff if int(tariff.months or 0) > 1 else None,
    )
    data = quote_public_dict(quote, tariff)
    data["copy"] = "Стоимость обновлена и будет проверена перед оплатой"
    return data


@router.post("/purchase", response_model=SubscriptionStatusResponse)
async def purchase(
    payload: SubscriptionPurchaseRequest, user: Users = Depends(validate)
) -> SubscriptionStatusResponse:
    plan_id = payload.planId
    tariffs = await Tariffs.filter(is_active=True).all()
    by_months: Dict[int, Tariffs] = {int(t.months): t for t in tariffs}
    plan_month_aliases = {
        "1month": 1,
        "3months": 3,
        "6months": 6,
        "12months": 12,
        "12months_promo": 12,  # backward compatible legacy alias
    }
    plan_map: Dict[str, tuple[int, int]] = {}
    for pid, months in plan_month_aliases.items():
        tariff = by_months.get(months)
        if tariff:
            plan_map[pid] = (months, tariff_default_devices(tariff))
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
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PAYMENT_REQUIRED",
                "redirect_to": result.get("redirect_to"),
            },
        )

    return await get_status(user)


@router.post("/frozen-base/activate", response_model=ActivateFrozenBaseResponse)
async def activate_frozen_base(
    user: Users = Depends(validate),
) -> ActivateFrozenBaseResponse:
    try:
        result = await activate_frozen_base_with_current_freeze(user)
    except FrozenBaseActivationError as exc:
        raise HTTPException(
            status_code=409, detail={"code": exc.code, "message": exc.message}
        ) from exc

    await _notify_frozen_base_activation_success(user, result)

    return ActivateFrozenBaseResponse(
        ok=True,
        switched_until=str(result["switched_until"]),
        frozen_current_days=int(result["frozen_current_days"]),
        activated_frozen_base_days=int(result["activated_frozen_base_days"]),
    )


@router.post("/frozen-family/activate", response_model=ActivateFrozenFamilyResponse)
async def activate_frozen_family(
    user: Users = Depends(validate),
) -> ActivateFrozenFamilyResponse:
    try:
        result = await activate_frozen_family_with_current_freeze(user)
    except FrozenFamilyActivationError as exc:
        detail: Dict[str, Any] = {
            "code": exc.code,
            "message": exc.message,
        }
        if exc.retry_after_seconds is not None:
            detail["retry_after_seconds"] = int(exc.retry_after_seconds)
        if exc.reverse_migration_available_at is not None:
            detail["reverse_migration_available_at"] = (
                exc.reverse_migration_available_at
            )
        raise HTTPException(status_code=409, detail=detail) from exc

    await _notify_frozen_family_activation_success(user, result)

    return ActivateFrozenFamilyResponse(
        ok=True,
        switched_until=str(result["switched_until"]),
        frozen_current_days=int(result["frozen_current_days"]),
        activated_frozen_family_days=int(result["activated_frozen_family_days"]),
    )


@router.post("/cancel-renewal")
async def cancel_renewal(user: Users = Depends(validate)) -> Dict[str, Any]:
    was_already_cancelled = not bool(user.renew_id)
    if user.renew_id:
        user.renew_id = None
        await user.save(update_fields=["renew_id"])
        try:
            await cancel_subscription(
                user,
                reason="Пользователь отключил автопродление через /subscription/cancel-renewal",
            )
        except Exception as exc:
            logger.error("Failed to notify admin about cancel-renewal: %s", exc)
    return {
        "ok": True,
        "autoRenewEnabled": False,
        "wasAlreadyCancelled": was_already_cancelled,
    }
