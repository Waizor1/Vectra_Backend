"""Quote-building for combined subscription upgrade bundles.

Computes the extra cost for simultaneously upgrading three subscription axes:
- device limit (`hwid_limit`),
- LTE GB quota (`lte_gb_total`),
- subscription period (extra days on top of current `expired_at`).

The actual money-moving / write-effects live in `bloobcat/routes/user.py` —
this module only produces a deterministic, side-effect-free quote dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Any

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.tariff import Tariffs
from bloobcat.services.subscription_limits import (
    lte_default_max_gb,
    subscription_devices_max,
)


# Hard cap on device topups regardless of tariff: matches the global
# `subscription_devices_max()` (default 30) — we don't allow building a quote
# that exceeds it even if a tariff snapshot accidentally permits more.
DEVICES_TOPUP_HARD_CAP_FALLBACK = 30
# Fallback LTE cap when the snapshot/tariff has no `lte_max_gb` (default 500).
LTE_TOPUP_FALLBACK_TOTAL_MAX_GB = 500
# Cannot extend a subscription more than 365 days past `today` — keeps quotes
# from growing unbounded and matches the plan contract.
MAX_TOTAL_DAYS_AHEAD = 365


@dataclass
class UpgradeBundleQuote:
    device_delta: int
    lte_delta_gb: int
    extra_days: int
    device_extra_cost_rub: int
    lte_extra_cost_rub: int
    period_extra_cost_rub: int
    total_extra_cost_rub: int
    applies_progressive_discount: bool
    daily_rate: float
    validation_errors: list[str]

    def to_response_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # FastAPI serializes via Pydantic; this is a hand-built dict so it
        # round-trips through `JSONResponse` regardless of pydantic version.
        return data


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def compute_daily_rate(active_tariff: ActiveTariffs | dict[str, Any]) -> float:
    """Average rubles-per-day for an active tariff snapshot.

    Formula: `price / (months * 30)`. We deliberately use the fixed-length
    month convention (30 days) rather than calendar months because the
    snapshot doesn't carry `subscription_started_at` and the extra-days
    quote should be predictable regardless of when the period started.

    `ActiveTariffs.residual_day_fraction` is intentionally NOT used here:
    it captures fractional-day residuals from device-count conversions
    (see `PATCH /user/active_tariff`), which is a different concern.

    Returns `0.0` for any degenerate input (zero/None price or months) so
    that downstream callers can safely multiply by it without producing
    negative or NaN costs.
    """
    if isinstance(active_tariff, dict):
        price = _to_int(active_tariff.get("price"))
        months = _to_int(active_tariff.get("months"))
    else:
        price = _to_int(getattr(active_tariff, "price", 0))
        months = _to_int(getattr(active_tariff, "months", 0))

    if price <= 0 or months <= 0:
        return 0.0
    rate = price / float(months * 30)
    return max(0.0, rate)


def _compute_progressive_full_price(
    active_tariff: ActiveTariffs,
    original_tariff: Tariffs | None,
    target_device_count: int,
) -> tuple[int, float]:
    """Recompute the snapshot full-period price for `target_device_count` seats.

    Mirrors the math in `bloobcat.routes.user._compute_active_tariff_full_price`
    (kept local here so the service has no cyclic import on the route module).
    Returns `(price_rub, multiplier)`.
    """
    getcontext().prec = 28
    if original_tariff is not None:
        base_price = original_tariff.base_price
        multiplier = (
            active_tariff.progressive_multiplier
            or original_tariff.progressive_multiplier
        )
    else:
        multiplier = active_tariff.progressive_multiplier or 0.9
        n = max(1, _to_int(getattr(active_tariff, "hwid_limit", 1), 1))
        if n == 1:
            base_price = _to_int(active_tariff.price, 0)
        else:
            denom = 1 - multiplier
            geom_sum = (1 - (multiplier**n)) / denom if denom != 0 else n
            base_price = (
                _to_int(active_tariff.price, 0) / geom_sum
                if geom_sum > 0
                else _to_int(active_tariff.price, 0)
            )

    mult_dec = Decimal(str(multiplier))
    base_dec = Decimal(str(base_price))
    if target_device_count <= 1:
        total_dec = base_dec
    else:
        total_dec = base_dec
        for k in range(2, target_device_count + 1):
            total_dec += base_dec * (mult_dec ** (k - 1))
    price_rub = int(total_dec.to_integral_value(rounding=ROUND_HALF_UP))
    return price_rub, float(multiplier)


async def build_upgrade_bundle_quote(
    *,
    user_id: int,
    user_balance: int,
    user_expired_at: date | None,
    user_hwid_limit: int | None,
    active_tariff: ActiveTariffs,
    target_devices: int,
    target_lte_gb: int,
    target_extra_days: int,
    today: date | None = None,
) -> UpgradeBundleQuote:
    """Pure quote builder. Reads the original Tariffs row only for caps and
    for the price-difference math; writes nothing.

    Validation errors are accumulated in `quote.validation_errors`; callers
    can still inspect partial deltas (e.g., only the device delta failed)
    and decide whether to return a 400 or a partial preview.
    """
    today = today or date.today()
    errors: list[str] = []

    # ----- read current state --------------------------------------------
    current_devices = max(
        1, _to_int(user_hwid_limit, 0) or _to_int(getattr(active_tariff, "hwid_limit", 1), 1)
    )
    current_lte_gb_total = _to_int(getattr(active_tariff, "lte_gb_total", 0), 0)

    target_devices = _to_int(target_devices, current_devices)
    target_lte_gb = _to_int(target_lte_gb, current_lte_gb_total)
    target_extra_days = _to_int(target_extra_days, 0)

    # ----- look up tariff caps --------------------------------------------
    original_tariff = await Tariffs.filter(
        name=getattr(active_tariff, "name", None),
        months=getattr(active_tariff, "months", None),
    ).first()

    devices_cap = subscription_devices_max()
    raw_family_cap = (
        _to_int(getattr(original_tariff, "devices_limit_family", devices_cap), devices_cap)
        if original_tariff is not None
        else devices_cap
    )
    devices_cap = max(1, min(devices_cap, raw_family_cap or devices_cap))
    if devices_cap <= 0:
        devices_cap = DEVICES_TOPUP_HARD_CAP_FALLBACK

    lte_cap = (
        _to_int(getattr(original_tariff, "lte_max_gb", None), lte_default_max_gb())
        if original_tariff is not None
        else lte_default_max_gb()
    )
    if lte_cap <= 0:
        lte_cap = LTE_TOPUP_FALLBACK_TOTAL_MAX_GB

    # ----- compute deltas -------------------------------------------------
    device_delta = 0
    if target_devices < current_devices:
        errors.append("target_devices_below_current")
    else:
        device_delta = target_devices - current_devices

    lte_delta_gb = 0
    if target_lte_gb < current_lte_gb_total:
        errors.append("target_lte_below_current")
    else:
        lte_delta_gb = target_lte_gb - current_lte_gb_total

    if target_extra_days < 0:
        errors.append("extra_days_negative")
        target_extra_days = 0

    # ----- validate caps --------------------------------------------------
    if target_devices > devices_cap:
        errors.append("target_devices_exceeds_cap")
        device_delta = 0  # don't price an impossible upgrade
    if target_lte_gb > lte_cap:
        errors.append("target_lte_exceeds_cap")
        lte_delta_gb = 0

    days_remaining_now = 0
    if user_expired_at is not None:
        days_remaining_now = max(0, (user_expired_at - today).days)
    max_extra_days_allowed = max(0, MAX_TOTAL_DAYS_AHEAD - days_remaining_now)
    if target_extra_days > max_extra_days_allowed:
        errors.append("extra_days_exceeds_year")
        target_extra_days = 0

    if device_delta == 0 and lte_delta_gb == 0 and target_extra_days == 0:
        errors.append("nothing_to_upgrade")

    # ----- price each axis ------------------------------------------------
    # Devices: reuse the same prorate logic as `_apply_devices_topup_effect`
    # (see bloobcat/routes/payment.py:3374 and the upstream computation in
    # bloobcat/routes/user.py:1500-1524). Formula:
    #   extra_cost = (new_full_price - current_price) * days_remaining / total_days_full
    device_extra_cost_rub = 0
    applies_progressive_discount = False
    if device_delta > 0:
        new_full_price, multiplier = _compute_progressive_full_price(
            active_tariff, original_tariff, target_devices
        )
        applies_progressive_discount = (
            multiplier > 0 and multiplier < 1.0 and target_devices > 1
        )
        from bloobcat.utils.dates import add_months_safe

        active_months = _to_int(getattr(active_tariff, "months", 1), 1)
        target_date_full = add_months_safe(today, active_months)
        total_days_full = max(1, (target_date_full - today).days)

        full_price_delta = max(
            0, int(new_full_price) - _to_int(getattr(active_tariff, "price", 0), 0)
        )
        if days_remaining_now > 0 and full_price_delta > 0:
            getcontext().prec = 28
            extra_cost_dec = (
                Decimal(full_price_delta)
                * Decimal(days_remaining_now)
                / Decimal(total_days_full)
            )
            device_extra_cost_rub = int(
                extra_cost_dec.to_integral_value(rounding=ROUND_HALF_UP)
            )
        else:
            device_extra_cost_rub = 0

    # LTE: linear in the price-per-gb snapshot stored on the active tariff.
    lte_extra_cost_rub = 0
    if lte_delta_gb > 0:
        lte_price_per_gb = _to_float(
            getattr(active_tariff, "lte_price_per_gb", 0.0), 0.0
        )
        if lte_price_per_gb <= 0:
            errors.append("lte_unavailable_for_tariff")
            lte_delta_gb = 0
        else:
            lte_extra_cost_rub = int(
                Decimal(str(lte_delta_gb * lte_price_per_gb)).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            )

    daily_rate = compute_daily_rate(active_tariff)
    period_extra_cost_rub = 0
    if target_extra_days > 0:
        if daily_rate <= 0:
            errors.append("daily_rate_unavailable")
        else:
            period_extra_cost_rub = int(
                Decimal(str(daily_rate * target_extra_days)).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            )

    total_extra_cost_rub = max(
        0, device_extra_cost_rub + lte_extra_cost_rub + period_extra_cost_rub
    )

    return UpgradeBundleQuote(
        device_delta=int(device_delta),
        lte_delta_gb=int(lte_delta_gb),
        extra_days=int(target_extra_days),
        device_extra_cost_rub=int(device_extra_cost_rub),
        lte_extra_cost_rub=int(lte_extra_cost_rub),
        period_extra_cost_rub=int(period_extra_cost_rub),
        total_extra_cost_rub=int(total_extra_cost_rub),
        applies_progressive_discount=bool(applies_progressive_discount),
        daily_rate=float(daily_rate),
        validation_errors=errors,
    )
