import asyncio
from datetime import date

from bloobcat.db.users import Users
from bloobcat.bot.notifications.trial.end import notify_trial_ended
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

logger = get_logger("tasks.retry_trial_endings")


async def retry_missed_trial_endings_once() -> int:
    """Single pass to end trials that should have ended but were missed.

    Returns ended_count.
    """
    logger.debug("Starting missed trial endings check")
    users = await Users.filter(is_trial=True, expired_at__lte=date.today())

    ended_count = 0
    for user in users:
        try:
            logger.info(f"Retrying trial end for user {user.id}")
            await notify_trial_ended(user)
            user.is_trial = False
            await user.save()
            ended_count += 1
        except Exception as e:
            logger.error(f"Failed to end trial for user {user.id}: {e}")

    logger.debug(f"Finished missed trial endings check. Ended: {ended_count}")
    return ended_count


async def run_retry_trial_endings_scheduler(interval_seconds: int = 3600):
    """Periodic scheduler loop for retrying missed trial endings.

    Defaults to 1 hour interval.
    Respects optional app_settings flag `retry_trial_endings_enabled` if present.
    """
    if hasattr(app_settings, "retry_trial_endings_enabled") and not app_settings.retry_trial_endings_enabled:
        logger.info("Retry trial endings scheduler disabled")
        return

    logger.info(
        f"Starting retry trial endings scheduler (interval: {interval_seconds}s)"
    )
    while True:
        try:
            await retry_missed_trial_endings_once()
        except Exception as e:
            logger.error(f"Error in retry trial endings scheduler: {e}")
        await asyncio.sleep(interval_seconds)


