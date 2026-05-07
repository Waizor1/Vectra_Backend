"""One-off reconcile for service-growth analytics.

Re-runs `collect_service_growth_analytics_once` with an explicit
`reconcile_days` window so historical rows in `analytics_service_daily`
and `analytics_trial_daily` are re-computed and overwritten with the
current collector logic (e.g. after a fix that changes how rows are
filtered or aggregated).

Usage on a production host:

    docker compose exec bloobcat \
        python scripts/reconcile_service_growth_analytics.py --days 14

Idempotent: rows are upserted by `(day, product)` / `day` keys.
Will hit RemnaWave to re-fetch usage for the chosen window, so
plan accordingly on large windows.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from tortoise import Tortoise

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.logger import get_logger  # noqa: E402
from bloobcat.tasks.service_growth_analytics import (  # noqa: E402
    collect_service_growth_analytics_once,
)

logger = get_logger("scripts.reconcile_service_growth_analytics")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Force-reconcile service-growth analytics over a fixed window.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Number of days to reconcile (default: 14).",
    )
    parser.add_argument(
        "--no-alerts",
        action="store_true",
        help="Suppress Telegram admin alerts for newly created risk flags.",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    days = max(1, int(args.days))

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        result = await collect_service_growth_analytics_once(
            reconcile_days=days,
            send_alerts=not args.no_alerts,
        )
    finally:
        await Tortoise.close_connections()

    logger.info("Reconcile result: {}", result)
    print(result)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
