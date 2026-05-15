"""Quote-building for combined subscription upgrade bundles.

Computes the extra cost for simultaneously upgrading three subscription axes:
- device limit (`hwid_limit`),
- LTE GB quota (`lte_gb_total`),
- subscription period (extra days on top of current `expired_at`).

The actual money-moving / write-effects live in `bloobcat/routes/user.py` —
this module only produces a deterministic, side-effect-free quote dataclass.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from datetime import date
from decimal import Decimal, ROUND_DOWN, getcontext
from typing import Any

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.tariff import Tariffs
from bloobcat.services.subscription_limits import subscription_devices_max
from bloobcat.utils.dates import add_months_safe

logger = logging.getLogger(__name__)


def _upgrade_fresh_mode_enabled() -> bool:
    """Return True when Phase 2 fresh-equivalent pricing is active.

    Reads UPGRADE_PRICING_FRESH_MODE env var at call time so the flag can be
    toggled without restarting the process (instant rollback via env update).
    Default: enabled (true). Disable with "0", "false", "no", or "off".
    """
    return os.environ.get("UPGRADE_PRICING_FRESH_MODE", "true").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# Hard cap on device topups regardless of tariff: matches the global
# `subscription_devices_max()` (default 30) — we don't allow building a quote
# that exceeds it even if a tariff snapshot accidentally permits more.
DEVICES_TOPUP_HARD_CAP_FALLBACK = 30
# Absolute LTE cap for upgrade top-ups. Tariff-level `lte_max_gb` is a soft
# UX recommendation; for top-up flow we deliberately allow up to this hard
# cap so a user who exhausted their tariff quota can ALWAYS pay for more.
# Business principle: «человек платит — получает услугу», do not block
# revenue at an arbitrary tariff line. RemnaWave already unblocks LTE
# traffic when `lte_gb_total > lte_gb_used` regardless of tariff line.
LTE_TOPUP_FALLBACK_TOTAL_MAX_GB = 10000
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
    # Live price-per-GB the user will actually pay for the NEW GB at this
    # moment (taken from `Tariffs` not the active_tariff snapshot). Frontend
    # uses this for the per-unit hint so UI matches what backend actually
    # charges — avoiding the "UI says 1.5, charged 2" confusion.
    lte_price_per_gb: float
    # Bug 3 (BE v4): how many ₽ the user saved relative to the linear
    # (no-progressive-discount) device price, prorated over the SAME
    # `(remaining + extra) / total` window that `device_extra_cost_rub`
    # uses. 0 when device_delta == 0 or multiplier >= 1.0.
    device_discount_rub: int
    # Tariff-level discount percentage on the FULL period price (not the
    # prorated window). It represents the structural saving baked into the
    # progressive ladder of the chosen tariff — independent of how many days
    # are left on the current subscription. The frontend renders this as the
    # «−10%» label next to the prorated `device_discount_rub` amount; the
    # two intentionally describe different facets (rate vs. money) and are
    # NOT required to multiply back to each other.
    device_discount_percent: float
    validation_errors: list[str]
    # Phase 1 SHADOW fields — new formula result, user is NOT charged yet.
    # total_extra_cost_rub stays on the legacy delta path.
    # FE may display "Выгода vs новая покупка" once Phase 2 flips.
    fresh_equivalent_total_rub: int = 0
    refund_rub: int = 0
    optimal_sku_months: int = 0
    optimal_sku_id: int | None = None
    pricing_mode: str = "delta_legacy_shadow"

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


def compute_total_period_days(months: int, anchor: date | None = None) -> int:
    """Exact number of days in a calendar-aware `months`-period from `anchor`.

    Mirrors the convention used everywhere else in the codebase for
    subscription time math:
      - `bloobcat.db.users.extend_subscription` (users.py:1550-1571)
      - `bloobcat.routes.payment.pay` (payment.py:4019-4020) where
        `target_date = add_months_safe(current_date, months); days = (target_date - current_date).days`
      - `bloobcat.routes.payment._apply_devices_topup_effect` (payment.py:3943-4000)

    Returns the **actual** calendar distance — e.g. `compute_total_period_days(12)`
    from 2026-01-15 returns 365 (Jan 15 2026 → Jan 15 2027), not 360.

    The previous fixed-30-day convention systematically overcharged users
    (≈5d on a year-long tariff, ≈1d on a monthly). Switching to calendar-
    aware days is a price decrease in the user's favor and matches what
    `pay()` would compute if the same period were purchased fresh.

    `anchor` defaults to `date.today()`. Use an explicit anchor for tests
    or any place that needs determinism across midnights.
    """
    if months <= 0:
        return 0
    today = anchor or date.today()
    target = add_months_safe(today, months)
    return max(0, (target - today).days)


def _compute_period_extra_cost(
    price: int,
    months: int,
    extra_days: int,
    anchor: date | None = None,
) -> int:
    """Compute the prorated extra cost for `extra_days` based on a calendar-
    aware month convention (see `compute_total_period_days`). All math runs
    in `Decimal` so we never observe float rounding drift.

    We round DOWN (truncate toward zero) on purpose: the upgrade-bundle
    pricing principle is "never charge fractional rubles the user did not
    consent to" — symmetric with the device and LTE components which also
    round down (Fix B). Always round in the customer's favor.

    Returns 0 for any degenerate input (zero/negative price, months, or days).
    """
    if price <= 0 or months <= 0 or extra_days <= 0:
        return 0
    total_days = compute_total_period_days(months, anchor=anchor)
    if total_days <= 0:
        return 0
    cost = Decimal(int(price)) * Decimal(int(extra_days)) / Decimal(total_days)
    return int(cost.to_integral_value(rounding=ROUND_DOWN))


def compute_daily_rate(active_tariff: ActiveTariffs | dict[str, Any]) -> float:
    """Average rubles-per-day for an active tariff snapshot.

    Uses the calendar-aware day count (`compute_total_period_days(months)`)
    so the per-day price equals what `pay()` would compute if the same
    period were purchased fresh today. This is the same convention used by
    `_apply_devices_topup_effect` and `extend_subscription`.

    Note: the result is sensitive to `date.today()` (next month's length
    differs from this month's). Callers that need determinism should
    snapshot the value and use `compute_total_period_days(months, anchor=...)`
    directly.

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
    total_days = compute_total_period_days(months)
    if total_days <= 0:
        return 0.0
    return max(0.0, float(Decimal(price) / Decimal(total_days)))


def _compute_progressive_full_price(
    active_tariff: ActiveTariffs,
    original_tariff: Tariffs | None,
    target_device_count: int,
) -> tuple[int, float]:
    """Compute the full-period price for `target_device_count` seats.

    LIVE pricing semantics: when `original_tariff` is present, delegate to
    `original_tariff.calculate_price()` — the exact same path the regular
    tariff constructor uses (see `bloobcat/services/tariff_quote.py:227`).
    This guarantees that:
      `upgrade_quote.full_price_delta(N→M) == build_subscription_quote(M) − build_subscription_quote(N)`

    Returns `(price_rub, multiplier)`.

    Replaces the previous «live base + snapshot multiplier» asymmetry which
    quoted absurd prices when admin-edited live pricing diverged from the
    user's snapshot (e.g. ~600₽/device on annual when live multiplier had
    been lowered since the user's purchase). Cohort protection by snapshot
    multiplier is deliberately removed — the user now pays today's rate for
    new seats, identical to what a fresh purchase would cost.

    The snapshot-only path is preserved for the exotic edge where Directus
    has no matching tariff at all (admin retired every line), so callers
    can still build a quote without crashing.
    """
    getcontext().prec = 28

    if original_tariff is not None:
        # Live path — single source of truth for device pricing across the
        # whole product (purchase / upgrade / webhook snapshot update).
        price = int(original_tariff.calculate_price(int(target_device_count)))
        live_multiplier = getattr(original_tariff, "progressive_multiplier", None)
        if live_multiplier is None:
            live_multiplier = 0.9
        return price, float(live_multiplier)

    # Tariff deleted from Directus: reconstruct from the snapshot so the
    # endpoint still produces a (best-effort) quote rather than 500ing.
    multiplier = getattr(active_tariff, "progressive_multiplier", None)
    if multiplier is None:
        logger.warning(
            "active_tariff.progressive_multiplier missing AND no live tariff "
            "available — falling back to default 0.9 (active_tariff_id=%s)",
            getattr(active_tariff, "id", None),
        )
        multiplier = 0.9

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
    price_rub = int(total_dec.to_integral_value(rounding=ROUND_DOWN))
    return price_rub, float(multiplier)


async def _pick_optimal_sku(
    active_tariff: ActiveTariffs,
    target_total_days: int,
    today: date,
) -> tuple["Tariffs | None", int]:
    """Smallest SKU whose natural period covers target_total_days.

    Filters Tariffs(lte_enabled=True, is_active=True), ordered by months/order/id.
    If none cover target_total_days, picks the longest available.
    Returns (sku, sku_natural_days) or (None, 0) when no SKUs exist.
    """
    skus = await Tariffs.filter(lte_enabled=True, is_active=True).order_by(
        "months", "order", "id"
    ).all()
    if not skus:
        return None, 0

    covering = [s for s in skus if compute_total_period_days(s.months, anchor=today) >= target_total_days]
    sku = covering[0] if covering else skus[-1]
    return sku, compute_total_period_days(sku.months, anchor=today)


def _compute_fresh_equivalent(
    sku: "Tariffs",
    target_devices: int,
    target_lte_gb: int,
    target_total_days: int,
    today: date,
) -> tuple[int, int, int]:
    """Returns (total_rub, devices_share_rub, lte_share_rub).

    devices_share = sku.calculate_price(target_devices) × target_total_days / sku_natural_days
    lte_share = target_lte_gb × sku.lte_price_per_gb if lte_enabled, else 0.
    All Decimal, ROUND_DOWN.
    """
    getcontext().prec = 28
    sku_natural_days = compute_total_period_days(sku.months, anchor=today)
    if sku_natural_days <= 0 or target_total_days <= 0:
        return 0, 0, 0

    device_price = int(sku.calculate_price(int(target_devices)))
    devices_share_dec = (
        Decimal(device_price) * Decimal(target_total_days) / Decimal(sku_natural_days)
    )
    devices_share = int(devices_share_dec.to_integral_value(rounding=ROUND_DOWN))

    lte_share = 0
    lte_enabled = getattr(sku, "lte_enabled", False)
    if lte_enabled and target_lte_gb > 0:
        lte_price = _to_float(getattr(sku, "lte_price_per_gb", 0.0), 0.0)
        if lte_price > 0:
            lte_share = int(
                Decimal(str(target_lte_gb * lte_price)).to_integral_value(rounding=ROUND_DOWN)
            )

    return devices_share + lte_share, devices_share, lte_share


def _compute_refund(
    active_tariff: ActiveTariffs,
    days_remaining: int,
    today: date,
) -> int:
    """Prorated refund: price × days_remaining / total_days_full.

    total_days_full = compute_total_period_days(active_tariff.months, anchor=today).
    Decimal, ROUND_DOWN. Returns 0 for any degenerate input.
    """
    if days_remaining <= 0:
        return 0
    price = _to_int(getattr(active_tariff, "price", 0), 0)
    months = _to_int(getattr(active_tariff, "months", 0), 0)
    if price <= 0 or months <= 0:
        return 0
    total_days = compute_total_period_days(months, anchor=today)
    if total_days <= 0:
        return 0
    getcontext().prec = 28
    refund_dec = Decimal(price) * Decimal(days_remaining) / Decimal(total_days)
    return int(refund_dec.to_integral_value(rounding=ROUND_DOWN))


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
    # Bug 1 (BE v4): when admin renames a tariff in Directus, the strict
    # (name, months) lookup misses and we fall through to the historical
    # snapshot — meaning a user who paid at the old 2 ₽/GB rate keeps
    # seeing 2 ₽ even after admin dropped the live price to 1.5 ₽. Broaden
    # the fallback: first try (name, months); if that misses, try any live
    # tariff matching only `months`; finally any tariff with `lte_enabled`
    # so the user always sees Directus's current LTE pricing.
    # Each lookup is `order_by("order", "id")` to make the choice deterministic
    # across PostgreSQL `vacuum/analyze` cycles. Without explicit ordering the
    # SQL standard does NOT guarantee row ordering, and `.first()` could swap
    # the matched row arbitrarily between queries — leading to a user seeing a
    # different tariff's `lte_price_per_gb` between two refreshes. Stable
    # ordering also matches how Directus list views surface tariffs to admins.
    original_tariff = await Tariffs.filter(
        name=getattr(active_tariff, "name", None),
        months=getattr(active_tariff, "months", None),
    ).order_by("order", "id").first()
    if original_tariff is None:
        active_months_for_lookup = getattr(active_tariff, "months", None)
        if active_months_for_lookup is not None:
            original_tariff = await Tariffs.filter(
                months=active_months_for_lookup
            ).order_by("order", "id").first()
            if original_tariff is not None:
                logger.warning(
                    "upgrade_bundle: original_tariff fallback on months-only — "
                    "active_tariff name=%r missing in Directus, picked tariff id=%s name=%r "
                    "(LTE pricing may diverge from user's purchase if multipliers differ)",
                    getattr(active_tariff, "name", None),
                    getattr(original_tariff, "id", None),
                    getattr(original_tariff, "name", None),
                )
    if original_tariff is None:
        original_tariff = await Tariffs.filter(
            lte_enabled=True
        ).order_by("order", "id").first()
        if original_tariff is not None:
            logger.warning(
                "upgrade_bundle: original_tariff wide-fallback to ANY lte_enabled tariff "
                "(active_tariff months=%r,name=%r not found by either lookup) — using id=%s name=%r months=%r",
                getattr(active_tariff, "months", None),
                getattr(active_tariff, "name", None),
                getattr(original_tariff, "id", None),
                getattr(original_tariff, "name", None),
                getattr(original_tariff, "months", None),
            )

    devices_cap = subscription_devices_max()
    raw_family_cap = (
        _to_int(getattr(original_tariff, "devices_limit_family", devices_cap), devices_cap)
        if original_tariff is not None
        else devices_cap
    )
    devices_cap = max(1, min(devices_cap, raw_family_cap or devices_cap))
    if devices_cap <= 0:
        devices_cap = DEVICES_TOPUP_HARD_CAP_FALLBACK

    # Absolute hard cap for top-ups — deliberately ignore `tariff.lte_max_gb`
    # so a user at 500/500 (their tariff line) can still pay for additional
    # GB up to the absolute system limit. Tariff line is informational only.
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
    # Devices: symmetric LIVE pricing — full_price_delta is the delta between
    # `calculate_price(target)` and `calculate_price(current)` on the live
    # tariff, exactly matching `build_subscription_quote` (tariff_quote.py:227).
    # Then prorated over `(days_remaining + extra_days) / total_days_full`.
    # The previous formula compared a live-base × snapshot-mult `new_full_price`
    # against the raw `active_tariff.price` snapshot — that asymmetry quoted
    # absurd prices (e.g. ~600₽/device on annual) when live and snapshot
    # multipliers diverged.
    device_extra_cost_rub = 0
    device_discount_rub = 0
    device_discount_percent = 0.0
    applies_progressive_discount = False
    if device_delta > 0:
        new_full_price, multiplier = _compute_progressive_full_price(
            active_tariff, original_tariff, target_devices
        )
        current_full_price, _ = _compute_progressive_full_price(
            active_tariff, original_tariff, current_devices
        )
        applies_progressive_discount = (
            multiplier > 0 and multiplier < 1.0 and target_devices > 1
        )

        # Calendar-aware day count `pay()` uses (replaces fixed `months * 30`
        # which understated by ~5d/year on annual and overcharged accordingly).
        active_months = _to_int(getattr(active_tariff, "months", 1), 1)
        total_days_full = max(1, compute_total_period_days(active_months, anchor=today))

        # When a user buys +1 device AND +N extra days in one bundle, the
        # new device serves for the FULL window (current remaining + extra),
        # not just current. Collapses to `days_remaining_now` when extra=0.
        total_days_for_device = days_remaining_now + max(0, target_extra_days)

        full_price_delta = max(0, int(new_full_price) - int(current_full_price))
        if total_days_for_device > 0 and full_price_delta > 0:
            getcontext().prec = 28
            extra_cost_dec = (
                Decimal(full_price_delta)
                * Decimal(total_days_for_device)
                / Decimal(total_days_full)
            )
            # ROUND_DOWN — keeps the device component in the customer's
            # favor and symmetric with LTE and period axes.
            device_extra_cost_rub = int(
                extra_cost_dec.to_integral_value(rounding=ROUND_DOWN)
            )

        # Progressive-discount breakdown — savings on the ADDED devices only.
        # Linear baseline = `base_price × device_delta` (what those new seats
        # would cost without progressive decay). Prorated over the same
        # window as device_extra_cost_rub so the «−N% прогрессивная скидка»
        # sub-row is consistent with the parent «+N устройств» row.
        if applies_progressive_discount:
            if original_tariff is not None:
                base_price = _to_int(
                    getattr(original_tariff, "base_price", 0), 0
                )
            else:
                n = max(
                    1, _to_int(getattr(active_tariff, "hwid_limit", 1), 1)
                )
                snapshot_price = _to_int(getattr(active_tariff, "price", 0), 0)
                if n == 1:
                    base_price = snapshot_price
                else:
                    denom = 1 - multiplier
                    geom_sum = (
                        (1 - (multiplier**n)) / denom if denom != 0 else n
                    )
                    base_price = int(
                        snapshot_price / geom_sum if geom_sum > 0 else snapshot_price
                    )
            linear_delta_cost = int(base_price) * int(device_delta)
            full_discount = max(0, linear_delta_cost - full_price_delta)
            if total_days_for_device > 0 and full_discount > 0:
                getcontext().prec = 28
                discount_dec = (
                    Decimal(full_discount)
                    * Decimal(total_days_for_device)
                    / Decimal(total_days_full)
                )
                device_discount_rub = int(
                    discount_dec.to_integral_value(rounding=ROUND_DOWN)
                )
            if linear_delta_cost > 0:
                device_discount_percent = round(
                    100.0 * full_discount / linear_delta_cost, 1
                )

    # LTE: linear in the LIVE price-per-gb from Directus (`Tariffs`). Reason:
    # user pays today's price for the NEW GB they're buying right now —
    # consistent with `pay()` for fresh purchases.
    #
    # Bug 1 (BE v4): we no longer write a new snapshot to
    # `active_tariff.lte_price_per_gb` on apply (see user.py / payment.py
    # changes). The snapshot is preserved as the original purchase price for
    # audit. The fallback when no live tariff exists at all is to read that
    # snapshot — exotic edge only, since the `original_tariff` lookup above
    # already widens to "any live tariff with lte_enabled".
    snapshot_lte_price_per_gb = _to_float(
        getattr(active_tariff, "lte_price_per_gb", 0.0), 0.0
    )
    if original_tariff is not None:
        live_lte_price_per_gb = _to_float(
            getattr(original_tariff, "lte_price_per_gb", 0.0), 0.0
        )
        effective_lte_price_per_gb = live_lte_price_per_gb
    else:
        effective_lte_price_per_gb = snapshot_lte_price_per_gb

    lte_extra_cost_rub = 0
    if lte_delta_gb > 0:
        if effective_lte_price_per_gb <= 0:
            errors.append("lte_unavailable_for_tariff")
            lte_delta_gb = 0
        else:
            # Fix B — ROUND_DOWN, symmetric with period & device axes.
            lte_extra_cost_rub = int(
                Decimal(str(lte_delta_gb * effective_lte_price_per_gb)).to_integral_value(
                    rounding=ROUND_DOWN
                )
            )

    daily_rate = compute_daily_rate(active_tariff)
    period_extra_cost_rub = 0
    if target_extra_days > 0:
        # Compute directly from (price, months, extra_days) in Decimal so the
        # rounded daily_rate float does not propagate compounding error into
        # the prorated period cost. `daily_rate` is kept for UI display only.
        price_for_period = _to_int(getattr(active_tariff, "price", 0), 0)
        months_for_period = _to_int(getattr(active_tariff, "months", 0), 0)
        period_extra_cost_rub = _compute_period_extra_cost(
            price_for_period, months_for_period, target_extra_days, anchor=today
        )
        # MINOR 4: only flag `daily_rate_unavailable` when the period extension
        # is genuinely unpriceable (no daily rate to extrapolate from). A zero
        # `period_extra_cost_rub` with a positive `daily_rate` just means the
        # truncation rounded a tiny fraction to 0 — that's not an error and
        # must not leak into `validation_errors` (which gates `is_actionable`).
        if daily_rate <= 0:
            errors.append("daily_rate_unavailable")

    total_extra_cost_rub = max(
        0, device_extra_cost_rub + lte_extra_cost_rub + period_extra_cost_rub
    )

    # ----- Phase 2: fresh-equivalent pricing + optional flip ----------------
    # When UPGRADE_PRICING_FRESH_MODE=true (default), the user pays
    # max(0, fresh_equivalent - refund) instead of the legacy delta sum.
    # The flag can be toggled at runtime for instant rollback.
    _fresh_mode_on = _upgrade_fresh_mode_enabled()

    target_total_days = max(0, days_remaining_now + max(0, target_extra_days))
    shadow_sku, _shadow_sku_days = await _pick_optimal_sku(active_tariff, target_total_days, today)
    shadow_fresh_total = 0
    _shadow_devices_share = 0
    shadow_lte_share = 0
    shadow_refund = 0
    shadow_sku_months = 0
    shadow_sku_id: int | None = None
    if shadow_sku is not None and target_total_days > 0:
        shadow_fresh_total, _shadow_devices_share, shadow_lte_share = _compute_fresh_equivalent(
            shadow_sku, target_devices, target_lte_gb, target_total_days, today
        )
        shadow_refund = _compute_refund(active_tariff, days_remaining_now, today)
        shadow_sku_months = int(getattr(shadow_sku, "months", 0) or 0)
        shadow_sku_id = getattr(shadow_sku, "id", None)

    # Guard: only flip pricing when the quote has at least one upgrade axis.
    # Zero-delta quotes must remain at 0 regardless of the flag.
    has_any_upgrade = device_delta > 0 or lte_delta_gb > 0 or target_extra_days > 0

    pricing_mode_str = "delta_legacy_shadow"
    legacy_total = total_extra_cost_rub

    if _fresh_mode_on and has_any_upgrade and shadow_sku is not None and target_total_days > 0:
        # Flip: user pays fresh_equivalent - refund instead of legacy delta.
        new_total = max(0, int(shadow_fresh_total) - int(shadow_refund))

        # Re-allocate axis breakdown so axes sum to new_total.
        new_lte = int(shadow_lte_share) if lte_delta_gb > 0 else 0
        new_lte = min(new_lte, new_total)
        non_lte_total = max(0, new_total - new_lte)

        has_devices = device_delta > 0
        has_period = target_extra_days > 0
        if has_devices and has_period:
            # Split non-LTE proportionally to legacy device/period ratio.
            legacy_dev = max(0, int(device_extra_cost_rub))
            legacy_per = max(0, int(period_extra_cost_rub))
            legacy_split = legacy_dev + legacy_per
            if legacy_split > 0:
                getcontext().prec = 28
                new_dev = int(
                    (
                        Decimal(non_lte_total) * Decimal(legacy_dev) / Decimal(legacy_split)
                    ).to_integral_value(rounding=ROUND_DOWN)
                )
                new_per = max(0, non_lte_total - new_dev)
            else:
                new_dev = non_lte_total // 2
                new_per = non_lte_total - new_dev
        elif has_devices:
            new_dev = non_lte_total
            new_per = 0
        elif has_period:
            new_dev = 0
            new_per = non_lte_total
        else:
            new_dev = 0
            new_per = 0

        device_extra_cost_rub = new_dev
        period_extra_cost_rub = new_per
        lte_extra_cost_rub = new_lte
        total_extra_cost_rub = new_total
        pricing_mode_str = "fresh_minus_refund"
        # Progressive discount concept doesn't apply in fresh-equivalent mode —
        # the SKU already bakes in the right multiplier via fresh pricing.
        device_discount_rub = 0
        device_discount_percent = 0.0
        applies_progressive_discount = False

    elif _fresh_mode_on and has_any_upgrade and shadow_sku is None:
        # Fresh mode on but no SKU found — fall back to legacy total.
        pricing_mode_str = "snapshot_fallback"

    logger.info(
        "upgrade_bundle_shadow user_id=%s pricing_mode=%s legacy_total=%d "
        "charged_total=%d shadow_fresh_total=%d shadow_refund=%d shadow_diff=%d "
        "shadow_sku_months=%d target_devices=%d target_lte_gb=%d "
        "target_total_days=%d active_months=%d",
        user_id,
        pricing_mode_str,
        int(legacy_total),
        int(total_extra_cost_rub),
        int(shadow_fresh_total),
        int(shadow_refund),
        int(legacy_total - max(0, shadow_fresh_total - shadow_refund)),
        int(shadow_sku_months),
        int(target_devices),
        int(target_lte_gb),
        int(target_total_days),
        int(getattr(active_tariff, "months", 0) or 0),
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
        lte_price_per_gb=float(effective_lte_price_per_gb),
        device_discount_rub=int(device_discount_rub),
        device_discount_percent=float(device_discount_percent),
        validation_errors=errors,
        fresh_equivalent_total_rub=int(shadow_fresh_total),
        refund_rub=int(shadow_refund),
        optimal_sku_months=int(shadow_sku_months),
        optimal_sku_id=shadow_sku_id,
        pricing_mode=pricing_mode_str,
    )
