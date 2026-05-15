"""Daily 12:00 MSK pre-warning scheduler for active reverse trials.

Looks at every state expiring in the [now+23h, now+25h] window and sends a
single pre-expiry notification. Idempotency is enforced through the
``pre_warning_sent_at`` column on ``ReverseTrialState`` so reruns within the
same window do not double-notify.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from bloobcat.db.reverse_trial import ReverseTrialState
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("tasks.reverse_trial_pre_warning")
MOSCOW = ZoneInfo("Europe/Moscow")

WINDOW_LOWER_HOURS = 23
WINDOW_UPPER_HOURS = 25
CHUNK_SIZE = 50
INTER_CHUNK_PAUSE_SECONDS = 0.1


async def run_reverse_trial_pre_warning_once() -> int:
    """Send the day-before reminder to every state inside the 23-25h window
    that has not been warned yet. Returns the number of notifications sent.
    """
    now_utc = datetime.now(timezone.utc)
    lower = now_utc + timedelta(hours=WINDOW_LOWER_HOURS)
    upper = now_utc + timedelta(hours=WINDOW_UPPER_HOURS)

    sent = 0
    offset = 0
    while True:
        chunk = await (
            ReverseTrialState.filter(
                status="active",
                expires_at__gte=lower,
                expires_at__lte=upper,
                pre_warning_sent_at__isnull=True,
            )
            .offset(offset)
            .limit(CHUNK_SIZE)
        )
        if not chunk:
            break

        for state in chunk:
            try:
                user = await Users.get_or_none(id=state.user_id)
                if user is None:
                    continue
                # Late import: notifications module pulls in aiogram, which
                # is heavier than necessary for any caller that just wants
                # the scheduler entry point.
                from bloobcat.bot.notifications.reverse_trial.pre_expiry import (
                    notify_reverse_trial_pre_expiry,
                )

                await notify_reverse_trial_pre_expiry(user, state)
                state.pre_warning_sent_at = datetime.now(timezone.utc)
                await state.save(update_fields=["pre_warning_sent_at"])
                sent += 1
            except Exception:
                logger.exception(
                    "Reverse-trial pre-warning failed for state=%s user=%s",
                    state.id,
                    state.user_id,
                )

        if len(chunk) < CHUNK_SIZE:
            break
        offset += CHUNK_SIZE
        await asyncio.sleep(INTER_CHUNK_PAUSE_SECONDS)

    if sent:
        logger.info("Reverse-trial pre-warning pass sent %s notifications", sent)
    return sent


def _next_daily_run_time() -> datetime:
    now_msk = datetime.now(MOSCOW)
    target = datetime.combine(now_msk.date(), time(12, 0)).replace(tzinfo=MOSCOW)
    if target <= now_msk:
        target += timedelta(days=1)
    return target


async def run_reverse_trial_pre_warning_scheduler() -> None:
    logger.info("Reverse-trial pre-warning scheduler started")
    while True:
        next_run = _next_daily_run_time()
        delay = (next_run - datetime.now(MOSCOW)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await run_reverse_trial_pre_warning_once()
        except Exception:
            logger.exception("Reverse-trial pre-warning scheduler tick failed")
