import asyncio
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

MOSCOW = ZoneInfo("Europe/Moscow")
logger = get_logger("tasks.retry_trial_extensions")


async def retry_missed_trial_extensions_once() -> tuple[int, int]:
    """Single pass to retry trial extensions for users if the extension time has already passed.

    Returns (processed_count, extended_count).
    """
    logger.debug("Starting missed trial extensions check")
    users = await Users.filter(is_trial=True, connected_at=None, is_subscribed=False)
    processed = 0
    extended_count = 0
    now_dt = datetime.now(MOSCOW)

    for user in users:
        processed += 1
        exp_dt = datetime.combine(user.expired_at, time.min).replace(tzinfo=MOSCOW)
        ext_eta = exp_dt - timedelta(days=2)
        if ext_eta <= now_dt:
            try:
                # Late import to avoid circular dependency at module import time
                from bloobcat.scheduler import _exec_extend_trial  # noqa: WPS433
                await _exec_extend_trial(user.id, user.expired_at)
                extended_count += 1
            except Exception as e:
                logger.error(f"Failed to extend (missed) trial for user {user.id}: {e}")

    logger.debug(f"Finished missed trial extensions: processed {processed}, extended {extended_count}")
    return processed, extended_count


async def run_retry_trial_extensions_scheduler(interval_seconds: int = 1800):
    """Periodic scheduler loop for retrying missed trial extensions.

    Defaults to 30 minutes interval.
    Respects optional app_settings flag `retry_trial_extensions_enabled` if present.
    """
    if hasattr(app_settings, "retry_trial_extensions_enabled") and not app_settings.retry_trial_extensions_enabled:
        logger.info("Retry trial extensions scheduler disabled")
        return

    logger.info(
        f"Starting retry trial extensions scheduler (interval: {interval_seconds}s)"
    )
    while True:
        try:
            await retry_missed_trial_extensions_once()
        except Exception as e:
            logger.error(f"Error in retry trial extensions scheduler: {e}")
        await asyncio.sleep(interval_seconds)


