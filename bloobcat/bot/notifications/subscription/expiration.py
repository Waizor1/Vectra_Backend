from datetime import datetime
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
            f"🔄 Автоматическое продление подписки\n\n"
            f"Через {days_remaining} {'день' if days_remaining == 1 else 'дня'} закончится ваша текущая подписка.\n"
            f"В день окончания подписки будет произведено автоматическое списание в размере {last_payment.amount} руб. для её продления.\n\n"
            f"Если вы хотите отключить автопродление, нажмите кнопку ниже и перейдите в раздел настроек."
        )
        button = await webapp_inline_button("Личный кабинет")
    else:
        text = (
            f"🔄 Automatic subscription renewal\n\n"
            f"In {days_remaining} {'day' if days_remaining == 1 else 'days'} your current subscription will end.\n"
            f"On the day of expiration, an automatic charge of {last_payment.amount} RUB will be made to renew it.\n\n"
            f"If you wish to disable auto-renewal, press the button below and go to settings."
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
    days_remaining = (user.expired_at - datetime.now().date()).days
    logger.info(f"Отправка уведомления об истечении подписки пользователю {user.id}, дней до истечения: {days_remaining}")
    if lang == 'ru':
        if days_remaining == 1:
            text = "❗ Ваш ключ истекает через 1 день. Пожалуйста, продлите подписку в личном кабинете."
        elif days_remaining == 2:
            text = "❗ Ваш ключ истекает через 2 дня. Пожалуйста, продлите подписку в личном кабинете."
        elif days_remaining == 3:
            text = "❗ Ваш ключ истекает через 3 дня. Пожалуйста, продлите подписку в личном кабинете."
        else:
            text = (f"⚠️ Истечение подписки\n\nЧерез {days_remaining} "
                    f"{'день' if days_remaining == 1 else 'дня' if 1 < days_remaining < 5 else 'дней'} "
                    f"закончится ваша текущая подписка.\nАвтопродление не включено.\n\n" 
                    f"Чтобы VPN продолжил работать, пожалуйста, продлите подписку в личном кабинете.")
        button = await webapp_inline_button("Продлить подписку", "pay")
    else:
        if days_remaining == 1:
            text = "❗ Your key will expire in 1 day. Please renew your subscription in your dashboard."
        elif days_remaining == 2:
            text = "❗ Your key will expire in 2 days. Please renew your subscription in your dashboard."
        elif days_remaining == 3:
            text = "❗ Your key will expire in 3 days. Please renew your subscription in your dashboard."
        else:
            text = (f"⚠️ Subscription Expiry\n\nIn {days_remaining} "
                    f"{'day' if days_remaining == 1 else 'days'} your current subscription will expire.\n" 
                    f"Auto-renewal is not enabled.\n\n" 
                    f"Please renew your subscription in your dashboard to continue using VPN.")
        button = await webapp_inline_button("Renew subscription", "pay")
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=button
        )
        logger.info(f"Уведомление об истечении подписки успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об истечении подписки пользователю {user.id}: {str(e)}") 