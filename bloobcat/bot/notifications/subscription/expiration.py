from datetime import datetime
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger

logger = get_logger("notifications.subscription.expiration")

async def notify_auto_payment(user: Users):
    """
    Уведомляет пользователя о предстоящем автоматическом платеже
    """
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
    text = f"""🔄 Автоматическое продление подписки

Через {days_remaining} {'день' if days_remaining == 1 else 'дня'} закончится ваша текущая подписка.
В день окончания подписки будет произведено автоматическое списание в размере {last_payment.amount} руб.
для её продления.

Если вы хотите отключить автопродление, нажмите кнопку ниже и перейдите в раздел настроек."""
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Личный кабинет")
        )
        logger.info(f"Уведомление об автоплатеже успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об автоплатеже пользователю {user.id}: {e}")


async def notify_expiring_subscription(user: Users):
    """
    Уведомляет пользователя без автопродления о скором истечении подписки
    """
    logger.info(f"Подготовка уведомления об истечении подписки для пользователя {user.id}")
    days_remaining = (user.expired_at - datetime.now().date()).days
    logger.info(f"Отправка уведомления об истечении подписки пользователю {user.id}, дней до истечения: {days_remaining}")
    if days_remaining == 1:
        text = "❗ Ваш ключ истекает через 1 день. Пожалуйста, продлите подписку в личном кабинете."
    elif days_remaining == 2:
        text = "❗ Ваш ключ истекает через 2 дня. Пожалуйста, продлите подписку в личном кабинете."
    elif days_remaining == 3:
        text = "❗ Ваш ключ истекает через 3 дня. Пожалуйста, продлите подписку в личном кабинете."
    else:
        text = f"⚠️ Истечение подписки\n\nЧерез {days_remaining} {'день' if days_remaining == 1 else 'дня' if 1 < days_remaining < 5 else 'дней'} закончится ваша текущая подписка.\nАвтоматическое продление не включено.\n\nЧтобы VPN продолжил работать, пожалуйста, продлите подписку в личном кабинете."
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Продлить подписку", "pay")
        )
        logger.info(f"Уведомление об истечении подписки успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об истечении подписки пользователю {user.id}: {e}") 