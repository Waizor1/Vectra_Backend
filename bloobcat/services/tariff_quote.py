from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from bloobcat.db.tariff import Tariffs
from bloobcat.db.users import Users
from bloobcat.services.discounts import apply_personal_discount
from bloobcat.services.segment_campaigns import select_active_campaign
from bloobcat.services.subscription_limits import (
    family_devices_threshold,
    lte_default_max_gb,
    lte_default_step_gb,
    subscription_devices_max,
    subscription_devices_min,
    tariff_kind_for_device_count,
)


def _round_rub(value: float) -> int:
    return int(round(float(value or 0)))


def _apply_percent_to_price(base_price: int, percent: int) -> int:
    if percent <= 0:
        return max(0, int(base_price))
    discount_value = int(round(int(base_price) * (int(percent) / 100.0)))
    return max(0, int(base_price) - discount_value)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def tariff_devices_max(tariff: Tariffs | None = None) -> int:
    global_max = subscription_devices_max()
    if tariff is None:
        return global_max
    raw = getattr(tariff, "devices_max", None)
    if raw is None:
        raw = getattr(tariff, "devices_limit_family", None)
    parsed = _safe_int(raw, global_max)
    return max(subscription_devices_min(), min(global_max, parsed))


def tariff_lte_max_gb(tariff: Tariffs | None = None) -> int:
    raw = getattr(tariff, "lte_max_gb", None) if tariff is not None else None
    return max(0, _safe_int(raw, lte_default_max_gb()))


def tariff_lte_step_gb(tariff: Tariffs | None = None) -> int:
    raw = getattr(tariff, "lte_step_gb", None) if tariff is not None else None
    return max(1, _safe_int(raw, lte_default_step_gb()))


def tariff_lte_min_gb(tariff: Tariffs | None = None) -> int:
    raw = getattr(tariff, "lte_min_gb", None) if tariff is not None else None
    return max(0, _safe_int(raw, 0))


def tariff_default_devices(tariff: Tariffs | None = None) -> int:
    raw = getattr(tariff, "devices_limit_default", None) if tariff is not None else None
    default_devices = _safe_int(raw, 1)
    return max(subscription_devices_min(), min(tariff_devices_max(tariff), default_devices))


def validate_device_count_for_tariff(tariff: Tariffs, device_count: Any) -> int:
    try:
        normalized = int(device_count)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректное количество устройств")
    min_devices = subscription_devices_min()
    max_devices = tariff_devices_max(tariff)
    if normalized < min_devices or normalized > max_devices:
        raise HTTPException(
            status_code=400,
            detail=f"Количество устройств должно быть от {min_devices} до {max_devices}",
        )
    return normalized


def validate_lte_gb_for_tariff(tariff: Tariffs, lte_gb: Any) -> int:
    try:
        normalized = int(lte_gb or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректное значение LTE лимита")
    if normalized < 0:
        raise HTTPException(status_code=400, detail="Некорректное значение LTE лимита")
    if normalized == 0:
        return 0
    if not bool(getattr(tariff, "lte_enabled", False)):
        raise HTTPException(status_code=400, detail="LTE недоступен для выбранного тарифа")
    min_gb = tariff_lte_min_gb(tariff)
    max_gb = tariff_lte_max_gb(tariff)
    step_gb = tariff_lte_step_gb(tariff)
    if normalized < min_gb or normalized > max_gb:
        raise HTTPException(status_code=400, detail=f"LTE должен быть от {min_gb} до {max_gb} ГБ")
    if normalized % step_gb != 0:
        raise HTTPException(status_code=400, detail=f"LTE должен быть кратен {step_gb} ГБ")
    return normalized


@dataclass(frozen=True, slots=True)
class SubscriptionQuote:
    tariff_id: int
    months: int
    device_count: int
    tariff_kind: str
    lte_gb: int
    subscription_price_rub: int
    discounted_subscription_price_rub: int
    device_discount_rub: int
    device_discount_percent: int
    discount_rub: int
    discount_percent: int
    duration_discount_percent: int
    duration_discount_rub: int
    total_discount_rub: int
    lte_price_rub: int
    total_price_rub: int
    per_month_text: str
    lte_price_per_gb: float
    discount_id: int | None
    discount_source: str | None = None
    discount_campaign_slug: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "tariffId": self.tariff_id,
            "months": self.months,
            "deviceCount": self.device_count,
            "tariffKind": self.tariff_kind,
            "lteGb": self.lte_gb,
            "subscriptionPriceRub": self.subscription_price_rub,
            "discountedSubscriptionPriceRub": self.discounted_subscription_price_rub,
            "deviceDiscountRub": self.device_discount_rub,
            "deviceDiscountPercent": self.device_discount_percent,
            "discountRub": self.discount_rub,
            "discountPercent": self.discount_percent,
            "durationDiscountPercent": self.duration_discount_percent,
            "durationDiscountRub": self.duration_discount_rub,
            "totalDiscountRub": self.total_discount_rub,
            "ltePriceRub": self.lte_price_rub,
            "totalPriceRub": self.total_price_rub,
            "perMonthText": self.per_month_text,
            "discountSource": self.discount_source,
            "discountCampaignSlug": self.discount_campaign_slug,
        }

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "quote_subscription_price": int(self.subscription_price_rub),
            "quote_discounted_subscription_price": int(self.discounted_subscription_price_rub),
            "quote_device_discount_rub": int(self.device_discount_rub),
            "quote_device_discount_percent": int(self.device_discount_percent),
            "quote_discount_rub": int(self.discount_rub),
            "quote_discount_percent": int(self.discount_percent),
            "quote_duration_discount_percent": int(self.duration_discount_percent),
            "quote_duration_discount_rub": int(self.duration_discount_rub),
            "quote_total_discount_rub": int(self.total_discount_rub),
            "quote_lte_price": int(self.lte_price_rub),
            "quote_total_price": int(self.total_price_rub),
            "quote_per_month_text": self.per_month_text,
            "quote_discount_source": self.discount_source,
            "quote_discount_campaign_slug": self.discount_campaign_slug,
        }


def quote_validation(tariff: Tariffs) -> dict[str, Any]:
    return {
        "devicesMin": subscription_devices_min(),
        "devicesMax": tariff_devices_max(tariff),
        "defaultDevices": tariff_default_devices(tariff),
        "familyThreshold": family_devices_threshold(),
        "lteEnabled": bool(getattr(tariff, "lte_enabled", False)),
        "ltePricePerGb": float(getattr(tariff, "lte_price_per_gb", 0) or 0),
        "lteMinGb": tariff_lte_min_gb(tariff),
        "lteMaxGb": tariff_lte_max_gb(tariff),
        "lteStepGb": tariff_lte_step_gb(tariff),
    }


def quote_public_dict(quote: SubscriptionQuote, tariff: Tariffs) -> dict[str, Any]:
    data = quote.public_dict()
    data["validation"] = quote_validation(tariff)
    data["limits"] = quote_validation(tariff)
    return data


async def build_subscription_quote(
    *,
    tariff: Tariffs,
    user_id: int,
    device_count: Any = 1,
    lte_gb: Any = 0,
    one_month_reference_tariff: Tariffs | None = None,
) -> SubscriptionQuote:
    await tariff.sync_effective_pricing_fields()
    normalized_device_count = validate_device_count_for_tariff(tariff, device_count)
    normalized_lte_gb = validate_lte_gb_for_tariff(tariff, lte_gb)

    months = max(1, int(getattr(tariff, "months", 1) or 1))
    subscription_price = int(tariff.calculate_price(normalized_device_count))
    single_device_price = int(tariff.calculate_price(1))
    full_device_price = max(0, int(single_device_price) * int(normalized_device_count))
    device_discount_rub = max(0, int(full_device_price) - int(subscription_price))
    device_discount_percent = (
        max(0, min(99, round(device_discount_rub / full_device_price * 100)))
        if full_device_price > 0 and device_discount_rub > 0
        else 0
    )
    (
        personal_discounted_price,
        discount_id,
        personal_discount_percent,
    ) = await apply_personal_discount(
        int(user_id),
        subscription_price,
        months,
    )
    discounted_price_raw = int(personal_discounted_price)
    discount_percent_raw = int(personal_discount_percent or 0)
    discount_source = "personal_discount" if discount_id is not None else None
    discount_campaign_slug: str | None = None

    user = await Users.get_or_none(id=int(user_id))
    campaign = await select_active_campaign(user, months=months) if user else None
    campaign_discount_percent = (
        max(0, min(90, int(getattr(campaign, "discount_percent", 0) or 0)))
        if campaign
        else 0
    )
    if campaign_discount_percent > 0:
        campaign_discounted_price = _apply_percent_to_price(
            subscription_price, campaign_discount_percent
        )
        # Segment campaigns should not consume an existing personal discount.
        # If the campaign is at least as good as the personal discount, use the
        # virtual campaign discount and keep the personal discount for later.
        if int(campaign_discounted_price) <= int(discounted_price_raw):
            discounted_price_raw = int(campaign_discounted_price)
            discount_id = None
            discount_percent_raw = int(campaign_discount_percent)
            discount_source = "segment_campaign"
            discount_campaign_slug = str(getattr(campaign, "slug", "") or "") or None

    discounted_price = max(0, int(discounted_price_raw))
    discount_rub = max(0, int(subscription_price) - int(discounted_price))
    discount_percent = max(0, int(discount_percent_raw or 0)) if discount_rub > 0 else 0
    if discount_rub <= 0:
        discount_source = None
        discount_campaign_slug = None

    lte_price_per_gb = (
        float(getattr(tariff, "lte_price_per_gb", 0) or 0.0)
        if bool(getattr(tariff, "lte_enabled", False))
        else 0.0
    )
    lte_price = _round_rub(normalized_lte_gb * lte_price_per_gb)
    total_price = int(discounted_price) + int(lte_price)
    per_month = int(round(total_price / months)) if months > 0 else total_price

    duration_discount_percent = 0
    duration_discount_rub = 0
    if one_month_reference_tariff is not None and months > 1:
        try:
            await one_month_reference_tariff.sync_effective_pricing_fields()
            ref = int(one_month_reference_tariff.calculate_price(normalized_device_count)) * months
            if ref > subscription_price > 0:
                duration_discount_rub = max(0, int(ref) - int(subscription_price))
                duration_discount_percent = max(0, min(99, round(duration_discount_rub / ref * 100)))
        except Exception:
            duration_discount_percent = 0
            duration_discount_rub = 0
    total_discount_rub = int(device_discount_rub) + int(duration_discount_rub) + int(discount_rub)

    return SubscriptionQuote(
        tariff_id=int(tariff.id),
        months=months,
        device_count=normalized_device_count,
        tariff_kind=tariff_kind_for_device_count(normalized_device_count),
        lte_gb=normalized_lte_gb,
        subscription_price_rub=int(subscription_price),
        discounted_subscription_price_rub=int(discounted_price),
        device_discount_rub=int(device_discount_rub),
        device_discount_percent=int(device_discount_percent),
        discount_rub=int(discount_rub),
        discount_percent=int(discount_percent),
        duration_discount_percent=int(duration_discount_percent),
        duration_discount_rub=int(duration_discount_rub),
        total_discount_rub=int(total_discount_rub),
        lte_price_rub=int(lte_price),
        total_price_rub=int(total_price),
        per_month_text=f"≈ {per_month} ₽/мес",
        lte_price_per_gb=float(lte_price_per_gb),
        discount_id=int(discount_id) if discount_id is not None else None,
        discount_source=discount_source,
        discount_campaign_slug=discount_campaign_slug,
    )


async def build_duration_offer(
    *,
    tariff: Tariffs,
    user_id: int,
    one_month_reference_tariff: Tariffs | None = None,
) -> dict[str, Any]:
    quote = await build_subscription_quote(
        tariff=tariff,
        user_id=user_id,
        device_count=tariff_default_devices(tariff),
        lte_gb=0,
        one_month_reference_tariff=one_month_reference_tariff,
    )
    months = int(quote.months)
    title = f"{months} месяц" if months == 1 else f"{months} месяцев"
    validation = quote_validation(tariff)
    badge = getattr(tariff, "storefront_badge", None) or ("выгодно" if months == 12 else None)
    return {
        "id": "1month" if months == 1 else f"{months}months",
        "tariffId": int(tariff.id),
        "title": title,
        "name": getattr(tariff, "name", title),
        "months": months,
        "devicesLimit": validation["defaultDevices"],
        "deviceCount": validation["defaultDevices"],
        "priceRub": int(quote.total_price_rub),
        "priceFromRub": int(quote.total_price_rub),
        "priceFromText": f"от {int(quote.total_price_rub)} ₽",
        "originalPriceRub": int(quote.subscription_price_rub) if quote.discount_rub > 0 else None,
        "personalDiscountPercent": quote.discount_percent if quote.discount_rub > 0 else None,
        "perMonthText": quote.per_month_text,
        "discountText": f"−{quote.duration_discount_percent}%" if quote.duration_discount_percent > 0 else None,
        "discountPercent": quote.duration_discount_percent,
        "badge": badge,
        "hint": getattr(tariff, "storefront_hint", None),
        "lteAvailable": validation["lteEnabled"],
        "lteEnabled": validation["lteEnabled"],
        "ltePricePerGb": validation["ltePricePerGb"],
        "lteMinGb": validation["lteMinGb"],
        "lteMaxGb": validation["lteMaxGb"],
        "lteStepGb": validation["lteStepGb"],
        "devicesMin": validation["devicesMin"],
        "devicesMax": validation["devicesMax"],
        "defaultDevices": validation["defaultDevices"],
        "familyThreshold": validation["familyThreshold"],
        "tariffKind": "base",
        "tariffType": "base",
        "familyVariant": False,
    }
