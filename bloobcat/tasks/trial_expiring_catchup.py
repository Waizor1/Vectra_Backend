import asyncio
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.db.notifications import NotificationMarks
from bloobcat.bot.notifications.trial.expiring import notify_expiring_trial
from bloobcat.logger import get_logger

MOSCOW = ZoneInfo("Europe/Moscow")
logger = get_logger("tasks.trial_expiring_catchup")


def _in_noon_window(now_msk: datetime) -> bool:
    target = time(12, 0)
    start = datetime.combine(now_msk.date(), target, tzinfo=MOSCOW)
    end = start + timedelta(hours=1)
    return start <= now_msk <= end


async def trial_expiring_catchup_once(now_msk: datetime | None = None) -> int:
    """Send trial 'expiring tomorrow at 12:00' notifications missed by per-user tasks.

    Returns count of sent notifications.
    """
    if now_msk is None:
        now_msk = datetime.now(MOSCOW)

    if not _in_noon_window(now_msk):
        return 0

    tomorrow = (now_msk + timedelta(days=1)).date()

    users = await Users.filter(is_trial=True, is_blocked=False, expired_at=tomorrow)
    sent = 0
    for user in users:
        # Idempotency: mark per (user, expired_at)
        mark_exists = await NotificationMarks.filter(
            user_id=user.id, type="trial_expiring", key=str(tomorrow)
        ).exists()
        if mark_exists:
            continue
        try:
            sent_ok = await notify_expiring_trial(user)
            if sent_ok:
                await NotificationMarks.create(
                    user_id=user.id, type="trial_expiring", key=str(tomorrow)
                )
                sent += 1
        except Exception as e:
            logger.error(f"Failed to send trial expiring to user {user.id}: {e}")

    if sent:
        logger.info(f"Trial expiring catch-up sent: {sent}")
    return sent


async def run_trial_expiring_catchup_scheduler(interval_seconds: int = 600):
    logger.info(
        f"Starting trial expiring catch-up scheduler (interval: {interval_seconds}s)"
    )
    while True:
        try:
            await trial_expiring_catchup_once()
        except Exception as e:
            logger.error(f"Error in trial expiring catch-up scheduler: {e}")
        await asyncio.sleep(interval_seconds)

