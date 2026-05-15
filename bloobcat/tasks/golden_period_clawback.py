"""6-hour Golden Period clawback scanner.

For every payout still in `optimistic` state inside the configured
clawback window, recompute fraud signals via
`detect_golden_overlap_signals` and call `clawback_payout` if the
detector says we should. Payouts older than the window flip to
`confirmed` so the next pass skips them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from bloobcat.db.golden_period import GoldenPeriodPayout
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.services.cashback_review import detect_golden_overlap_signals
from bloobcat.services.golden_period import get_active_golden_period_config
from bloobcat.services.golden_period_clawback import (
    clawback_payout,
    confirm_payouts_past_clawback_window,
)
from bloobcat.settings import app_settings

logger = get_logger("tasks.golden_period_clawback")

TICK_INTERVAL_SECONDS = 6 * 60 * 60  # every 6 hours
CHUNK_SIZE = 200


async def run_golden_period_clawback_once() -> dict:
    """Single clawback pass. Returns counters: scanned / clawed_back / confirmed / alerted."""
    config = await get_active_golden_period_config()
    if not config.is_enabled:
        return {"scanned": 0, "clawed_back": 0, "confirmed": 0, "alerted": False}

    confirmed = await confirm_payouts_past_clawback_window()

    window_days = max(1, int(config.clawback_window_days or 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    candidates = (
        await GoldenPeriodPayout.filter(
            status="optimistic", paid_at__gte=cutoff
        ).order_by("-paid_at").limit(CHUNK_SIZE)
    )

    scanned = 0
    clawed_back = 0
    clawed_back_total_rub = 0
    thresholds = dict(config.signal_thresholds or {})

    for payout in candidates:
        scanned += 1
        try:
            referrer = await Users.get_or_none(id=int(payout.referrer_user_id))
            referred = await Users.get_or_none(id=int(payout.referred_user_id))
            if referrer is None or referred is None:
                continue
            signals = await detect_golden_overlap_signals(
                referrer, referred, thresholds=thresholds
            )
            if signals.get("should_clawback"):
                ok = await clawback_payout(payout, signals)
                if ok:
                    clawed_back += 1
                    clawed_back_total_rub += int(payout.amount_rub or 0)
        except Exception:
            logger.exception(
                "Golden Period clawback evaluation failed payout=%s", payout.id
            )

    alerted = False
    threshold_rub = int(
        getattr(app_settings, "golden_period_admin_alert_threshold_rub", 5000)
        or 5000
    )
    if clawed_back_total_rub >= threshold_rub:
        try:
            from bloobcat.bot.notifications.admin import send_admin_message

            await send_admin_message(
                "Golden Period clawback alert: "
                f"{clawed_back} payouts reversed in this pass, "
                f"{clawed_back_total_rub}₽ recovered "
                f"(threshold {threshold_rub}₽)."
            )
            alerted = True
        except Exception:
            logger.exception(
                "Golden Period admin alert dispatch failed"
            )

    if scanned or confirmed:
        logger.info(
            "Golden Period clawback pass: scanned=%s clawed_back=%s confirmed=%s alerted=%s",
            scanned,
            clawed_back,
            confirmed,
            alerted,
        )
    return {
        "scanned": scanned,
        "clawed_back": clawed_back,
        "confirmed": confirmed,
        "alerted": alerted,
    }


async def run_golden_period_clawback_scheduler() -> None:
    """Long-running scheduler that runs every 6 hours."""
    logger.info("Golden Period clawback scheduler started")
    while True:
        try:
            await run_golden_period_clawback_once()
        except Exception:
            logger.exception("Golden Period clawback scheduler tick failed")
        await asyncio.sleep(TICK_INTERVAL_SECONDS)
