import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.db.notifications import NotificationMarks
from bloobcat.bot.notifications.trial.extended import notify_trial_extended
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

MOSCOW = ZoneInfo("Europe/Moscow")
logger = get_logger("tasks.retry_trial_extension_notifications")


async def retry_trial_extension_notifications_once() -> tuple[int, int]:
    """Send missing 'trial extended' notifications where extension was applied but not notified.

    Returns (processed_users, notified_count).
    """
    logger.debug("Starting retry for missing trial extension notifications")

    users = await Users.filter(is_trial=True, is_subscribed=False)
    processed = 0
    notified = 0
    now = datetime.now(MOSCOW)
    _ = now  # quiet linter about unused

    for user in users:
        processed += 1

        # Skip users who connected after assignment (they shouldn't get extension flow messages)
        if user.connected_at is not None:
            continue

        try:
            # Find applied extension marks without a corresponding notified mark
            applied_marks = await NotificationMarks.filter(user_id=user.id, type="trial_extension_applied")
            for mark in applied_marks:
                exists_notified = await NotificationMarks.filter(
                    user_id=user.id, type="trial_extension_notified", key=mark.key
                ).exists()
                if exists_notified:
                    continue

                # Attempt to send notification now
                extension_days = app_settings.trial_days // 2
                try:
                    sent_ok = await notify_trial_extended(user, extension_days)
                except Exception as e:
                    logger.error(f"[{user.id}] Error while sending delayed trial extension notification: {e}")
                    sent_ok = False

                if sent_ok:
                    try:
                        await NotificationMarks.create(user_id=user.id, type="trial_extension_notified", key=mark.key)
                    except Exception as e:
                        logger.warning(f"[{user.id}] Failed to create trial_extension_notified mark (key={mark.key}): {e}")
                    notified += 1

        except Exception as e:
            logger.error(f"[{user.id}] Failed during retry of trial extension notifications: {e}")

    logger.debug(f"Finished retry for missing trial extension notifications: processed {processed}, notified {notified}")
    return processed, notified


async def run_retry_trial_extension_notifications_scheduler(interval_seconds: int = 900):
    """Periodic loop to retry missing 'trial extended' notifications.

    Defaults to 15 minutes interval.
    Respects optional app_settings flag `retry_trial_extension_notifications_enabled` if present.
    """
    if hasattr(app_settings, "retry_trial_extension_notifications_enabled") and not app_settings.retry_trial_extension_notifications_enabled:
        logger.info("Retry trial extension notifications scheduler disabled")
        return

    logger.info(
        f"Starting retry trial extension notifications scheduler (interval: {interval_seconds}s)"
    )
    while True:
        try:
            await retry_trial_extension_notifications_once()
        except Exception as e:
            logger.error(f"Error in retry trial extension notifications scheduler: {e}")
        await asyncio.sleep(interval_seconds)


