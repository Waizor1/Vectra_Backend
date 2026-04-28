import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import json

from bloobcat.db.users import Users
from bloobcat.db.notifications import NotificationMarks
from bloobcat.bot.notifications.general.referral import on_referral_prompt
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

MOSCOW = ZoneInfo("Europe/Moscow")
logger = get_logger("tasks.referral_prompts")


def _should_send_today(now_msk: datetime) -> bool:
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

    should_send_now = _should_send_today(now_msk)
    # region agent log
    try:
        with open("/Users/urijgurov/Documents/resilcio/documents/PROJECTS/Разработки/Vectra/VectraConnectbot/.cursor/debug.log", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "sessionId": "debug-session",
                "runId": "pre-fix",
                "hypothesisId": "A",
                "location": "bloobcat/tasks/referral_prompts.py:_send_referral_prompt_if_due",
                "message": "Referral prompt decision (pre-send gate)",
                "data": {"daysSince": days_since, "shouldSendNow": should_send_now},
                "timestamp": int(datetime.now().timestamp() * 1000),
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # endregion

    # Важно: не "догоняем" пропущенные этапы (7/14/30) пачкой.
    # Отправляем только самый актуальный этап для текущего days_since.
    if not should_send_now:
        return False

    # 30 дней и далее — не чаще, чем раз в 30 дней (по sent_at)
    if days_since >= 30:
        last_30d = (
            await NotificationMarks.filter(user_id=user.id, type="referral_prompt", key="30d")
            .order_by("-sent_at")
            .first()
        )
        # region agent log
        try:
            last_age_days = None
            if last_30d and last_30d.sent_at:
                last_age_days = (now_msk.date() - last_30d.sent_at.astimezone(MOSCOW).date()).days
            with open("/Users/urijgurov/Documents/resilcio/documents/PROJECTS/Разработки/Vectra/VectraConnectbot/.cursor/debug.log", "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "sessionId": "debug-session",
                    "runId": "pre-fix",
                    "hypothesisId": "B",
                    "location": "bloobcat/tasks/referral_prompts.py:_send_referral_prompt_if_due",
                    "message": "30d last mark age",
                    "data": {"lastAgeDays": last_age_days, "hasLast": bool(last_30d)},
                    "timestamp": int(datetime.now().timestamp() * 1000),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass
        # endregion

        if last_30d is None:
            await on_referral_prompt(user, 30)
            await NotificationMarks.create(user_id=user.id, type="referral_prompt", key="30d")
            logger.debug(f"Referral prompt 30d sent to user {user.id} (#1)")
            return True

        last_sent_date = last_30d.sent_at.astimezone(MOSCOW).date()
        if (now_msk.date() - last_sent_date).days >= 30:
            await on_referral_prompt(user, 30)
            await NotificationMarks.create(user_id=user.id, type="referral_prompt", key="30d")
            logger.debug(f"Referral prompt 30d sent to user {user.id} (>=30d since last)")
            return True

        return False

    # 14 дней — по одному разу (и только если ещё не отправляли)
    if days_since >= 14:
        exists_14 = await NotificationMarks.filter(user_id=user.id, type="referral_prompt", key="14d").exists()
        if not exists_14:
            await on_referral_prompt(user, 14)
            await NotificationMarks.create(user_id=user.id, type="referral_prompt", key="14d")
            logger.debug(f"Referral prompt 14d sent to user {user.id}")
            return True
        return False

    # 7 дней — по одному разу
    exists_7 = await NotificationMarks.filter(user_id=user.id, type="referral_prompt", key="7d").exists()
    if not exists_7:
        await on_referral_prompt(user, 7)
        await NotificationMarks.create(user_id=user.id, type="referral_prompt", key="7d")
        logger.debug(f"Referral prompt 7d sent to user {user.id}")
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


