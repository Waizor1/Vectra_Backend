"""
Insert-only tariff seed for production deploys.

Why:
- ensure required default tariffs exist after deploy;
- allow operators to edit tariffs in admin without deploy overwrites;
- avoid destructive TRUNCATE on existing databases.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from tortoise import Tortoise

# Ensure project root is importable when running as a script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM
from bloobcat.db.tariff import Tariffs
from bloobcat.logger import get_logger

logger = get_logger("scripts.seed_tariffs")


LTE_PRICE_PER_GB = 1.5


TARIFFS: list[dict[str, Any]] = [
    {
        "name": "1 month",
        "months": 1,
        "base_price": 290,
        "progressive_multiplier": 0.9,
        "order": 1,
        "is_active": True,
        "devices_limit_default": 1,
        "devices_limit_family": 30,
        "family_plan_enabled": False,
        "final_price_default": 290,
        "final_price_family": None,
        "lte_enabled": True,
        "lte_price_per_gb": LTE_PRICE_PER_GB,
        "lte_min_gb": 0,
        "lte_max_gb": 500,
        "lte_step_gb": 1,
    },
    {
        "name": "3 months",
        "months": 3,
        "base_price": 749,
        "progressive_multiplier": 0.88,
        "order": 2,
        "is_active": True,
        "devices_limit_default": 1,
        "devices_limit_family": 30,
        "family_plan_enabled": False,
        "final_price_default": 749,
        "final_price_family": None,
        "lte_enabled": True,
        "lte_price_per_gb": LTE_PRICE_PER_GB,
        "lte_min_gb": 0,
        "lte_max_gb": 500,
        "lte_step_gb": 1,
    },
    {
        "name": "6 months",
        "months": 6,
        "base_price": 1290,
        "progressive_multiplier": 0.86,
        "order": 3,
        "is_active": True,
        "devices_limit_default": 1,
        "devices_limit_family": 30,
        "family_plan_enabled": False,
        "final_price_default": 1290,
        "final_price_family": None,
        "lte_enabled": True,
        "lte_price_per_gb": LTE_PRICE_PER_GB,
        "lte_min_gb": 0,
        "lte_max_gb": 500,
        "lte_step_gb": 1,
    },
    {
        "name": "12 months",
        "months": 12,
        "base_price": 2190,
        "progressive_multiplier": 0.82,
        "order": 4,
        "is_active": True,
        "devices_limit_default": 1,
        "devices_limit_family": 30,
        "family_plan_enabled": False,
        "final_price_default": 2190,
        "final_price_family": None,
        "lte_enabled": True,
        "lte_price_per_gb": LTE_PRICE_PER_GB,
        "lte_min_gb": 0,
        "lte_max_gb": 500,
        "lte_step_gb": 1,
        "storefront_badge": "выгодно",
    },
]



async def _ensure_tariff_exists(data: dict[str, Any]) -> str:
    # Logical key for insert-only sync.
    obj = (
        await Tariffs.filter(name=data["name"], months=data["months"])
        .order_by("id")
        .first()
    )
    if not obj:
        await Tariffs.create(**data)
        logger.info(f"Created tariff {data['name']} ({data['months']}m)")
        return "created"

    logger.info(f"Skipped existing tariff {data['name']} ({data['months']}m) [insert-only]")
    return "skipped"


async def run() -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        logger.info("Tariff seed mode: insert-only")
        created = 0
        skipped = 0

        for tariff in TARIFFS:
            status = await _ensure_tariff_exists(tariff)
            if status == "created":
                created += 1
            else:
                skipped += 1

        logger.info(
            f"Tariff seed done (insert-only): created={created}, skipped_existing={skipped}"
        )
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(run())
