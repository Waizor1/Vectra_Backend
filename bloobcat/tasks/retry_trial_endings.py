import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.bot.notifications.trial.end import notify_trial_ended
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings
from bloobcat.tasks.quiet_hours import (
    PENDING_TRIAL_ENDED_MARK_TYPE,
    ensure_notification_mark,
    is_quiet_hours,
)

logger = get_logger("tasks.retry_trial_endings")
MOSCOW = ZoneInfo("Europe/Moscow")


async def retry_missed_trial_endings_once() -> int:
    """Single pass to end trials that should have ended but were missed.

    Returns ended_count.
    """
    logger.debug("Starting missed trial endings check")
    users = await Users.filter(is_trial=True, expired_at__lte=date.today())
    now = datetime.now(MOSCOW)

    ended_count = 0
    for user in users:
        try:
            logger.info(f"Retrying trial end for user {user.id}")
            notification_key = str(user.expired_at)
            user.is_trial = False
            await user.save()
            if user.is_blocked:
                logger.info(f"Trial ended for blocked user {user.id} without notification")
            elif is_quiet_hours(now):
                await ensure_notification_mark(
                    user_id=user.id,
                    mark_type=PENDING_TRIAL_ENDED_MARK_TYPE,
                    key=notification_key,
                )
            else:
                sent_ok = await notify_trial_ended(user)
                if sent_ok:
                    await ensure_notification_mark(
                        user_id=user.id,
                        mark_type="trial_ended",
                        key=notification_key,
                    )
                else:
                    refreshed_user = await Users.get_or_none(id=user.id)
                    if refreshed_user and not refreshed_user.is_blocked:
                        await ensure_notification_mark(
                            user_id=user.id,
                            mark_type=PENDING_TRIAL_ENDED_MARK_TYPE,
                            key=notification_key,
                        )
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
