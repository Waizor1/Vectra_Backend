from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from bloobcat.db.segment_campaigns import SegmentCampaign
from bloobcat.db.tariff import Tariffs
from bloobcat.services.discounts import apply_personal_discount
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
    campaign_discount_rub: int = 0
    campaign_discount_percent: int = 0
    campaign_slug: str | None = None
    campaign_id: int | None = None

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
            "campaignDiscountRub": self.campaign_discount_rub,
            "campaignDiscountPercent": self.campaign_discount_percent,
            "campaignSlug": self.campaign_slug,
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
            "quote_campaign_discount_rub": int(self.campaign_discount_rub),
            "quote_campaign_discount_percent": int(self.campaign_discount_percent),
            "quote_campaign_slug": self.campaign_slug,
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


def _campaign_applies_to_months(
    campaign: SegmentCampaign | None, months: int
) -> bool:
    """Признак того, что кампания распространяется на тариф этой длительности."""
    if campaign is None:
        return False
    applies = list(getattr(campaign, "applies_to_months", None) or [])
    if not applies:
        return True
    try:
        return int(months) in {int(value) for value in applies}
    except (TypeError, ValueError):
        return False


async def build_subscription_quote(
    *,
    tariff: Tariffs,
    user_id: int,
    device_count: Any = 1,
    lte_gb: Any = 0,
    one_month_reference_tariff: Tariffs | None = None,
    campaign: SegmentCampaign | None = None,
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
    personal_price_raw, personal_discount_id, personal_percent_raw = (
        await apply_personal_discount(int(user_id), subscription_price, months)
    )
    personal_price = max(0, int(personal_price_raw))
    personal_rub = max(0, int(subscription_price) - int(personal_price))
    personal_percent = (
        max(0, int(personal_percent_raw or 0)) if personal_rub > 0 else 0
    )

    candidate_campaign_percent = 0
    candidate_campaign_rub = 0
    candidate_campaign_slug: str | None = None
    candidate_campaign_id: int | None = None
    if _campaign_applies_to_months(campaign, months) and subscription_price > 0:
        raw_percent = int(getattr(campaign, "discount_percent", 0) or 0)
        if raw_percent > 0:
            candidate_campaign_percent = max(0, min(99, raw_percent))
            candidate_campaign_rub = int(
                round(subscription_price * (candidate_campaign_percent / 100.0))
            )
            candidate_campaign_slug = getattr(campaign, "slug", None)
            candidate_campaign_id = getattr(campaign, "id", None)

    # Пользователь получает лучшую из применимых скидок: персональную или
    # сегментную кампанию. Складывать их нельзя — это может уйти в минус
    # маржу и обнулить цену. Кампания не «сжигает» персональную скидку,
    # поэтому при выигрыше кампании discount_id обнуляем; при проигрыше
    # обнуляем поля campaign_*, чтобы фронт не показывал бейдж "Акция"
    # рядом с уже применённой персональной скидкой.
    if candidate_campaign_rub > personal_rub:
        discounted_price = max(
            0, int(subscription_price) - int(candidate_campaign_rub)
        )
        discount_rub = int(candidate_campaign_rub)
        discount_percent = int(candidate_campaign_percent)
        discount_id_value: int | None = None
        campaign_rub = int(candidate_campaign_rub)
        campaign_percent = int(candidate_campaign_percent)
        campaign_slug = candidate_campaign_slug
        campaign_id = candidate_campaign_id
    else:
        discounted_price = personal_price
        discount_rub = personal_rub
        discount_percent = personal_percent
        discount_id_value = (
            int(personal_discount_id) if personal_discount_id is not None else None
        )
        campaign_rub = 0
        campaign_percent = 0
        campaign_slug = None
        campaign_id = None

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
        discount_id=discount_id_value,
        campaign_discount_rub=int(campaign_rub),
        campaign_discount_percent=int(campaign_percent),
        campaign_slug=campaign_slug,
        campaign_id=campaign_id,
    )


async def build_duration_offer(
    *,
    tariff: Tariffs,
    user_id: int,
    one_month_reference_tariff: Tariffs | None = None,
    campaign: SegmentCampaign | None = None,
) -> dict[str, Any]:
    quote = await build_subscription_quote(
        tariff=tariff,
        user_id=user_id,
        device_count=tariff_default_devices(tariff),
        lte_gb=0,
        one_month_reference_tariff=one_month_reference_tariff,
        campaign=campaign,
    )
    months = int(quote.months)
    title = f"{months} месяц" if months == 1 else f"{months} месяцев"
    validation = quote_validation(tariff)
    badge = getattr(tariff, "storefront_badge", None) or ("выгодно" if months == 12 else None)
    has_personal_discount = (
        quote.discount_rub > 0 and quote.campaign_discount_rub == 0
    )
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
        "personalDiscountPercent": quote.discount_percent if has_personal_discount else None,
        "campaignDiscountRub": int(quote.campaign_discount_rub) if quote.campaign_discount_rub > 0 else None,
        "campaignDiscountPercent": int(quote.campaign_discount_percent) if quote.campaign_discount_rub > 0 else None,
        "campaignSlug": quote.campaign_slug,
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
