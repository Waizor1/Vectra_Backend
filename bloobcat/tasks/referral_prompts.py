import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.db.notifications import NotificationMarks
from bloobcat.bot.notifications.general.referral import on_referral_prompt
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

MOSCOW = ZoneInfo("Europe/Moscow")
logger = get_logger("tasks.referral_prompts")


async def _should_send_today(now_msk: datetime) -> bool:
    # Отправляем около 18:00 МСК; допускаем окно в 1 час
    target = time(18, 0)
    start = datetime.combine(now_msk.date(), target, tzinfo=MOSCOW)
    end = start + timedelta(hours=1)
    return start <= now_msk <= end


async def _send_referral_prompt_if_due(user: Users, now_msk: datetime) -> bool:
    if not user.is_registered or not user.is_subscribed or user.referrals > 0:
        return False

    reg_date = user.registration_date.replace(tzinfo=ZoneInfo("UTC")).astimezone(MOSCOW).date()
    days_since = (now_msk.date() - reg_date).days

    if days_since < 7:
        return False

    # 7 и 14 дней — по одному разу
    for d in (7, 14):
        if days_since >= d:
            exists = await NotificationMarks.filter(user_id=user.id, type="referral_prompt", key=f"{d}d").exists()
            if not exists and _should_send_today(now_msk):
                await on_referral_prompt(user, d)
                await NotificationMarks.create(user_id=user.id, type="referral_prompt", key=f"{d}d")
                logger.debug(f"Referral prompt {d}d sent to user {user.id}")
                return True

    # 30 дней и далее — каждые 30 дней бесконечно
    if days_since >= 30:
        sent_30d_count = await NotificationMarks.filter(user_id=user.id, type="referral_prompt", key="30d").count()
        expected_30d_count = ((days_since - 30) // 30) + 1
        if sent_30d_count < expected_30d_count and _should_send_today(now_msk):
            await on_referral_prompt(user, 30)
            await NotificationMarks.create(user_id=user.id, type="referral_prompt", key="30d")
            logger.debug(f"Referral prompt 30d sent to user {user.id} (#{sent_30d_count + 1})")
            return True

    return False


async def run_referral_prompts_scheduler(interval_seconds: int = 600):
    """Периодический батч реферальных уведомлений (7d, 14d, 30d бесконечно).

    По умолчанию каждые 10 минут. Требует таблицу `notification_marks`.
    """
    if hasattr(app_settings, "referral_prompts_enabled") and not app_settings.referral_prompts_enabled:
        logger.info("Referral prompts scheduler disabled")
        return

    logger.info(
        f"Starting referral prompts scheduler (interval: {interval_seconds}s)"
    )
    while True:
        try:
            now_msk = datetime.now(MOSCOW)
            # Берём только релевантных пользователей
            users = await Users.filter(is_registered=True, is_subscribed=True, referrals=0)
            processed = 0
            sent = 0
            for user in users:
                processed += 1
                try:
                    if await _send_referral_prompt_if_due(user, now_msk):
                        sent += 1
                except Exception as e:
                    logger.error(f"Failed referral prompt for user {user.id}: {e}")
            if sent > 0:
                logger.info(f"Referral prompts sent: {sent} of {processed}")
        except Exception as e:
            logger.error(f"Error in referral prompts scheduler: {e}")
        await asyncio.sleep(interval_seconds)


