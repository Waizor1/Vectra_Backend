import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from bloobcat.logger import get_logger
from bloobcat.db.users import Users

async def check_referral_notifications():
    logger = get_logger("schedules.referral")
    try:
        logger.info("Начало проверки реферальных уведомлений")
        moscow_tz = ZoneInfo("Europe/Moscow")
        today = datetime.now(moscow_tz).date()
        # Получаем активных пользователей с подпиской без рефералов и с ненаполненным лимитом уведомлений
        users = await Users.filter(
            is_registered=True,
            is_subscribed=True,
            referrals=0,
            referral_notification_sent_count__lt=3
        )
        logger.info(f"Найдено {len(users)} пользователей для реферальных уведомлений")
        for user in users:
            try:
                days = (today - user.registration_date.date()).days
                next_count = user.referral_notification_sent_count + 1
                if (next_count == 1 and days >= 7) or (next_count == 2 and days >= 14) or (next_count == 3 and days >= 30):
                    from bloobcat.bot.notifications.general.referral import on_referral_prompt
                    await on_referral_prompt(user, days)
                    user.referral_notification_sent_count = next_count
                    await user.save()
                    logger.info(f"Отправлено реферальное уведомление #{next_count} пользователю {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при обработке реферального уведомления для пользователя {user.id}: {e}")
        logger.info("Проверка реферальных уведомлений завершена")
    except Exception as e:
        logger.error(f"Ошибка при проверке реферальных уведомлений: {e}") 