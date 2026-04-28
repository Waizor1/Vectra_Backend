import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.bot.notifications.subscription.renewal import (
    notify_subscription_cancelled_after_failures,
)
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings, payment_settings
from bloobcat.tasks.quiet_hours import (
    PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
    SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
    ensure_notification_mark,
    is_quiet_hours,
)

logger = get_logger("tasks.cleanup_missed_cancellations")
MOSCOW = ZoneInfo("Europe/Moscow")


async def cleanup_missed_cancellations_once() -> int:
    """Find and cancel subscriptions where auto-renewal failed and cancellation was missed.

    Returns number of cancelled users.
    """
    if payment_settings.auto_renewal_mode != "yookassa":
        logger.info("Skipping missed cancellation cleanup: auto-renewal disabled")
        return 0

    logger.info("Running cleanup for missed subscription cancellations...")
    now = datetime.now(MOSCOW)

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
            notification_key = str(user.expired_at)
            if user.is_blocked:
                logger.info(
                    f"Subscription cancelled for blocked user {user.id} without notification"
                )
            elif is_quiet_hours(now):
                await ensure_notification_mark(
                    user_id=user.id,
                    mark_type=PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
                    key=notification_key,
                )
            else:
                sent_ok = await notify_subscription_cancelled_after_failures(user)
                if sent_ok:
                    await ensure_notification_mark(
                        user_id=user.id,
                        mark_type=SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
                        key=notification_key,
                    )
                else:
                    refreshed_user = await Users.get_or_none(id=user.id)
                    if refreshed_user and not refreshed_user.is_blocked:
                        await ensure_notification_mark(
                            user_id=user.id,
                            mark_type=PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
                            key=notification_key,
                        )
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
