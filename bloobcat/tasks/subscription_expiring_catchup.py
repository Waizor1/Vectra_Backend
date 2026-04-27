import asyncio
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.db.notifications import NotificationMarks
from bloobcat.bot.notifications.subscription.expiration import notify_expiring_subscription
from bloobcat.logger import get_logger
from bloobcat.settings import payment_settings

MOSCOW = ZoneInfo("Europe/Moscow")
logger = get_logger("tasks.subscription_expiring_catchup")


def _in_window(now_msk: datetime, target: time, hours: int = 1) -> bool:
    start = datetime.combine(now_msk.date(), target, tzinfo=MOSCOW)
    end = start + timedelta(hours=hours)
    return start <= now_msk <= end


async def _send_for_target_days(offset_days: int, now_msk: datetime) -> int:
    """Send expiring reminders for users whose expired_at == today + offset_days.

    Uses NotificationMarks with keys like '3d:YYYY-MM-DD'.
    """
    target_date = (now_msk.date() + timedelta(days=offset_days))
    key_prefix = f"{offset_days}d:{target_date}"
    filters = {
        "is_subscribed": True,
        "expired_at": target_date,
    }
    # only users without active auto-renewal; when global auto-renewal is disabled,
    # existing YooKassa renew_id values are kept for rollback but must not suppress reminders.
    if str(getattr(payment_settings, "auto_renewal_mode", "") or "").strip().lower() == "yookassa":
        filters["renew_id__isnull"] = True
    users = await Users.filter(
        **filters,
    )
    sent = 0
    for user in users:
        exists = await NotificationMarks.filter(
            user_id=user.id, type="subscription_expiring", key=key_prefix
        ).exists()
        if exists:
            continue
        try:
            await notify_expiring_subscription(user)
            await NotificationMarks.create(
                user_id=user.id,
                type="subscription_expiring",
                key=key_prefix,
            )
            sent += 1
        except Exception as e:
            logger.error(f"Failed subscription expiring notify for user {user.id}: {e}")
    return sent


async def subscription_expiring_catchup_once(now_msk: datetime | None = None) -> int:
    if now_msk is None:
        now_msk = datetime.now(MOSCOW)
    total_sent = 0
    # 3d/2d at midnight window
    if _in_window(now_msk, time(0, 0), hours=1):
        total_sent += await _send_for_target_days(3, now_msk)
        total_sent += await _send_for_target_days(2, now_msk)
    # 1d at noon window
    if _in_window(now_msk, time(12, 0), hours=1):
        total_sent += await _send_for_target_days(1, now_msk)
    if total_sent:
        logger.info(f"Subscription expiring catch-up sent: {total_sent}")
    return total_sent


async def run_subscription_expiring_catchup_scheduler(interval_seconds: int = 600):
    logger.info(
        f"Starting subscription expiring catch-up scheduler (interval: {interval_seconds}s)"
    )
    while True:
        try:
            await subscription_expiring_catchup_once()
        except Exception as e:
            logger.error(f"Error in subscription expiring catch-up scheduler: {e}")
        await asyncio.sleep(interval_seconds)

