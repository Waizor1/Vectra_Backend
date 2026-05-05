from __future__ import annotations

import time
from typing import Any

from tortoise import Tortoise

from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

logger = get_logger("services.trial_lte")

TRIAL_LTE_SETTINGS_CACHE_TTL_SECONDS = 15.0
_trial_lte_limit_cache: tuple[float, float] | None = None


def normalize_trial_lte_limit_gb(value: Any, *, fallback: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(0.0, parsed)


def default_trial_lte_limit_gb() -> float:
    return normalize_trial_lte_limit_gb(
        getattr(app_settings, "trial_lte_limit_gb", 1.0),
        fallback=1.0,
    )


def clear_trial_lte_limit_cache() -> None:
    global _trial_lte_limit_cache
    _trial_lte_limit_cache = None


async def read_trial_lte_limit_gb() -> float:
    """
    Runtime trial LTE quota in GB.

    Priority:
    1. Directus singleton tvpn_admin_settings.trial_lte_limit_gb when present.
    2. TRIAL_LTE_LIMIT_GB / AppSettings default.

    This intentionally fails open to the env/default value so missing Directus
    schema during deploy cannot break trial creation or the limiter.
    """

    global _trial_lte_limit_cache
    now = time.monotonic()
    if _trial_lte_limit_cache is not None:
        cached_at, cached_value = _trial_lte_limit_cache
        if now - cached_at < TRIAL_LTE_SETTINGS_CACHE_TTL_SECONDS:
            return cached_value

    fallback = default_trial_lte_limit_gb()
    value = fallback
    try:
        conn = Tortoise.get_connection("default")
        rows = await conn.execute_query_dict(
            """
            SELECT trial_lte_limit_gb
            FROM tvpn_admin_settings
            LIMIT 1
            """
        )
        if rows:
            raw_value = rows[0].get("trial_lte_limit_gb")
            if raw_value is not None:
                value = normalize_trial_lte_limit_gb(raw_value, fallback=fallback)
    except Exception as exc:
        logger.debug("Trial LTE settings unavailable, using fallback: {}", exc)

    _trial_lte_limit_cache = (now, value)
    return value
