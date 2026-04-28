from __future__ import annotations

from typing import Any

from bloobcat.settings import app_settings


FAMILY_DEVICES_THRESHOLD = 2
SUBSCRIPTION_DEVICES_DEFAULT_MAX = 30
LTE_DEFAULT_MAX_GB = 500
LTE_DEFAULT_STEP_GB = 1
LTE_DEFAULT_PRICE_PER_GB = 1.5


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def family_devices_threshold() -> int:
    """Minimum purchased devices that enables family semantics.

    Tariff Builder v1 defines family by capacity, not by a separate product:
    1 device is personal/base, 2+ devices are family capacity.
    """
    return max(
        FAMILY_DEVICES_THRESHOLD,
        _safe_int(getattr(app_settings, "family_devices_threshold", FAMILY_DEVICES_THRESHOLD), FAMILY_DEVICES_THRESHOLD),
    )


def subscription_devices_min() -> int:
    return 1


def subscription_devices_max() -> int:
    return max(
        subscription_devices_min(),
        _safe_int(
            getattr(app_settings, "subscription_devices_max", SUBSCRIPTION_DEVICES_DEFAULT_MAX),
            SUBSCRIPTION_DEVICES_DEFAULT_MAX,
        ),
    )


def lte_default_max_gb() -> int:
    return max(0, _safe_int(getattr(app_settings, "lte_default_max_gb", LTE_DEFAULT_MAX_GB), LTE_DEFAULT_MAX_GB))


def lte_default_step_gb() -> int:
    return max(1, _safe_int(getattr(app_settings, "lte_default_step_gb", LTE_DEFAULT_STEP_GB), LTE_DEFAULT_STEP_GB))


def lte_default_price_per_gb() -> float:
    try:
        value = float(getattr(app_settings, "lte_default_price_per_gb", LTE_DEFAULT_PRICE_PER_GB))
    except (TypeError, ValueError):
        value = LTE_DEFAULT_PRICE_PER_GB
    return max(0.0, value)


def tariff_kind_for_device_count(device_count: int) -> str:
    return "family" if int(device_count or 1) >= family_devices_threshold() else "base"


def is_family_device_count(device_count: int | None) -> bool:
    return int(device_count or 1) >= family_devices_threshold()
