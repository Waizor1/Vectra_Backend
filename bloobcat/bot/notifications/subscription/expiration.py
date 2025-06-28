from datetime import datetime, time
from zoneinfo import ZoneInfo
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale

logger = get_logger("notifications.subscription.expiration")

async def notify_auto_payment(user: Users):
    """
    Уведомляет пользователя о предстоящем автоматическом платеже
    """
    # Локализация уведомления об автоплатеже
    lang = get_user_locale(user)
    logger.info(f"Подготовка уведомления об автоплатеже для пользователя {user.id}")
    last_payment = await ProcessedPayments.filter(
        user_id=user.id,
        status="succeeded"
    ).order_by("-processed_at").first()
    if not last_payment:
        logger.warning(f"Не найден успешный платеж для пользователя {user.id}, уведомление об автоплатеже не отправлено")
        return
    days_remaining = (user.expired_at - datetime.now().date()).days
    logger.info(f"Отправка уведомления об автоплатеже пользователю {user.id}, дней до списания: {days_remaining}, сумма: {last_payment.amount}")
    if lang == 'ru':
        text = (
            f"🔄 Привет, {user.full_name}! Через {days_remaining} "
            f"{'день' if days_remaining == 1 else 'дня' if days_remaining < 5 else 'дней'} "
            f"ваша подписка автоматически продлится. С вас спишется {last_payment.amount}₽. "
            "Чтобы изменить автопродление, перейдите в личный кабинет."
        )
        button = await webapp_inline_button("Личный кабинет")
    else:
        text = (
            f"🔄 Hi {user.full_name}! In {days_remaining} "
            f"{'day' if days_remaining == 1 else 'days'} your subscription will auto-renew. "
            f"{last_payment.amount} RUB will be charged. "
            "To manage auto-renewal, go to Dashboard."
        )
        button = await webapp_inline_button("Dashboard")
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=button
        )
        logger.info(f"Уведомление об автоплатеже успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об автоплатеже пользователю {user.id}: {str(e)}")


async def notify_expiring_subscription(user: Users):
    """
    Уведомляет пользователя без автопродления о скором истечении подписки
    """
    # Локализация
    lang = get_user_locale(user)
    logger.info(f"Подготовка уведомления об истечении подписки для пользователя {user.id}")
    # Вычисляем точное время до окончания подписки с учетом московского часового пояса
    moscow_tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(moscow_tz)
    expire_dt = datetime.combine(user.expired_at, time(0, 0), tzinfo=moscow_tz)
    seconds_left = (expire_dt - now).total_seconds()
    # Определяем дни до окончания: 0 если меньше суток, иначе целые дни
    days = 0 if seconds_left < 24 * 3600 else int(seconds_left // (24 * 3600))
    logger.info(f"Отправка уведомления об истечении подписки пользователю {user.id}, дней до истечения: {days}")
    # Формируем строку даты окончания
    if days == 0:
        date_str_ru = "сегодня"
        date_str_en = "today"
    else:
        date_str_ru = f"через {days} {{'день' if days == 1 else 'дня' if days < 5 else 'дней'}}"
        date_str_en = f"in {days} {{'day' if days == 1 else 'days'}}"
    if lang == 'ru':
        if days == 7:
            text = (
                f"⏰ Привет, {user.full_name}! Ваша подписка истекает через 7 дней.\n"
                "Не забудьте продлить её, чтобы не прерывать доступ к VPN! 🔒"
            )
        elif days == 3:
            text = (
                f"🚨 Внимание, {user.full_name}! Ваша подписка истекает через 3 дня.\n"
                "Продлите её прямо сейчас, чтобы не потерять доступ к VPN! 🌐"
            )
        elif days == 1:
            text = (
                f"🔥 Срочно, {user.full_name}! Ваша подписка истекает завтра.\n"
                "Продлите её прямо сейчас, чтобы не прерывать доступ к VPN! ⚡"
            )
        else:
            text = (
                f"⏰ Привет, {user.full_name}! Ваша подписка истекает через {days} дней.\n"
                "Не забудьте продлить её, чтобы не прерывать доступ к VPN! 🔒"
            )
        button = await webapp_inline_button("Продлить сейчас", "/pay")
    else:
        if days == 7:
            text = (
                f"⏰ Hi {user.full_name}! Your subscription expires in 7 days.\n"
                "Don't forget to renew to keep your VPN access! 🔒"
            )
        elif days == 3:
            text = (
                f"🚨 Attention {user.full_name}! Your subscription expires in 3 days.\n"
                "Renew now to avoid losing VPN access! 🌐"
            )
        elif days == 1:
            text = (
                f"🔥 Urgent, {user.full_name}! Your subscription expires tomorrow.\n"
                "Renew now to keep your VPN active! ⚡"
            )
        else:
            text = (
                f"⏰ Hi {user.full_name}! Your subscription expires in {days} days.\n"
                "Don't forget to renew to keep your VPN access! 🔒"
            )
        button = await webapp_inline_button("Renew Now", "/pay")
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=button
        )
        logger.info(f"Уведомление об истечении подписки успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об истечении подписки пользователю {user.id}: {str(e)}") 