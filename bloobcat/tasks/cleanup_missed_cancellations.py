import asyncio
from datetime import date

from bloobcat.db.users import Users
from bloobcat.bot.notifications.subscription.renewal import (
    notify_subscription_cancelled_after_failures,
)
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

logger = get_logger("tasks.cleanup_missed_cancellations")


async def cleanup_missed_cancellations_once() -> int:
    """Find and cancel subscriptions where auto-renewal failed and cancellation was missed.

    Returns number of cancelled users.
    """
    logger.info("Running cleanup for missed subscription cancellations...")

    users_to_cancel = await Users.filter(
        is_subscribed=True,
        renew_id__not_isnull=True,
        expired_at__lte=date.today(),
    )

    cancelled_count = 0
    for user in users_to_cancel:
        try:
            logger.warning(
                f"Found missed cancellation for user {user.id} (expired at {user.expired_at}). Cancelling now."
            )
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            await notify_subscription_cancelled_after_failures(user)
            cancelled_count += 1
        except Exception as e:
            logger.error(f"Failed to cleanup missed cancellation for user {user.id}: {e}")

    logger.info(f"Finished cleanup for missed cancellations. Cancelled: {cancelled_count} users.")
    return cancelled_count


async def run_cleanup_missed_cancellations_scheduler(interval_seconds: int = 3600):
    """Periodic scheduler loop for missed cancellations cleanup.

    Defaults to 1 hour interval. Can be toggled with `cleanup_missed_cancellations_enabled`.
    """
    if hasattr(app_settings, "cleanup_missed_cancellations_enabled") and not app_settings.cleanup_missed_cancellations_enabled:
        logger.info("Cleanup missed cancellations scheduler disabled")
        return

    logger.info(
        f"Starting cleanup missed cancellations scheduler (interval: {interval_seconds}s)"
    )
    while True:
        try:
            await cleanup_missed_cancellations_once()
        except Exception as e:
            logger.error(f"Error in cleanup missed cancellations scheduler: {e}")
        await asyncio.sleep(interval_seconds)


