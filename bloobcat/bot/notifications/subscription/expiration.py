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
    days_remaining = (user.expired_at - datetime.now().date()).days
    logger.info(f"Отправка уведомления об истечении подписки пользователю {user.id}, дней до истечения: {days_remaining}")
    if lang == 'ru':
        text = (
            f"⚠️ Привет, {user.full_name}! Ваша подписка истекает через {days_remaining} "
            f"{'день' if days_remaining == 1 else 'дня' if days_remaining < 5 else 'дней'}. "
            "Продлите сейчас, чтобы не прерывать доступ к VPN! 🔒"
        )
        button = await webapp_inline_button("Продлить сейчас", "pay")
    else:
        text = (
            f"⚠️ Hi {user.full_name}! Your subscription will expire in {days_remaining} "
            f"{'day' if days_remaining == 1 else 'days'}. "
            "Renew now to keep your VPN active! 🔒"
        )
        button = await webapp_inline_button("Renew Now", "pay")
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=button
        )
        logger.info(f"Уведомление об истечении подписки успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об истечении подписки пользователю {user.id}: {str(e)}") 