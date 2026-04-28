import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.bot.notifications.trial.no_trial import notify_no_trial_taken
from bloobcat.db.notifications import NotificationMarks
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings
from bloobcat.tasks.quiet_hours import is_quiet_hours

MOSCOW = ZoneInfo("Europe/Moscow")
logger = get_logger("tasks.retry_trial_notifications")


async def retry_send_missed_trial_notifications_once() -> tuple[int, int]:
    """Single pass to retry sending missed trial notifications (2h/24h).

    Returns (processed_count, sent_count).
    """
    logger.debug("Starting missed trial notifications check")

    # Только для пользователей с активным триалом, без коннектов и без платной подписки
    users = await Users.filter(
        is_trial=True,
        is_blocked=False,
        connected_at=None,
        is_subscribed=False,
    )

    processed_count = 0
    sent_count = 0

    now = datetime.now(MOSCOW)
    if is_quiet_hours(now):
        logger.debug("Skipping retry of missed trial notifications during quiet hours")
        return processed_count, sent_count
    for user in users:
        processed_count += 1

        # Skip users who have subscribed since assignment
        if user.is_subscribed:
            continue

        # Skip users who made a payment
        from bloobcat.db.payments import ProcessedPayments
        if await ProcessedPayments.filter(user_id=user.id, status="succeeded").exists():
            continue

        reg_dt = user.registration_date.replace(tzinfo=ZoneInfo("UTC")).astimezone(MOSCOW)
        hours_since_reg = (now - reg_dt).total_seconds() / 3600

        if hours_since_reg >= 2:
            mark_2h = await NotificationMarks.filter(user_id=user.id, type="trial_no_sub", key="2h").exists()
            if not mark_2h:
                logger.debug(
                    f"Sending missed 2h notification to user {user.id}, registration {hours_since_reg:.1f}h ago"
                )
                sent_ok = await notify_no_trial_taken(user, 2)
                if sent_ok:
                    await NotificationMarks.create(
                        user_id=user.id, type="trial_no_sub", key="2h"
                    )
                    sent_count += 1

        if hours_since_reg >= 24:
            mark_24h = await NotificationMarks.filter(user_id=user.id, type="trial_no_sub", key="24h").exists()
            if not mark_24h:
                logger.debug(
                    f"Sending missed 24h notification to user {user.id}, registration {hours_since_reg:.1f}h ago"
                )
                sent_ok = await notify_no_trial_taken(user, 24)
                if sent_ok:
                    await NotificationMarks.create(
                        user_id=user.id, type="trial_no_sub", key="24h"
                    )
                    sent_count += 1

    logger.debug(
        f"Finished missed trial notifications: processed {processed_count}, sent {sent_count}"
    )
    return processed_count, sent_count


async def run_retry_trial_notifications_scheduler(interval_seconds: int = 900):
    """Periodic scheduler loop for retrying trial notifications.

    Defaults to 15 minutes interval.
    Respects optional app_settings flag `retry_trial_notifications_enabled` if present.
    """
    if hasattr(app_settings, "retry_trial_notifications_enabled") and not app_settings.retry_trial_notifications_enabled:
        logger.info("Retry trial notifications scheduler disabled")
        return

    logger.info(
        f"Starting retry trial notifications scheduler (interval: {interval_seconds}s)"
    )
    while True:
        try:
            await retry_send_missed_trial_notifications_once()
        except Exception as e:
            logger.error(f"Error in retry trial notifications scheduler: {e}")
        await asyncio.sleep(interval_seconds)
