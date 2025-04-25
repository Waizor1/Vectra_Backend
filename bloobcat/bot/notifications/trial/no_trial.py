from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger

logger = get_logger("notifications.trial.no_trial")

async def notify_no_trial_taken(user, hours_passed: int):
    logger.info(f"Подготовка уведомления пользователю {user.id}, не взявшему пробную подписку (прошло {hours_passed} ч.)")
    if user.expired_at is not None:
        logger.info(f"Пользователь {user.id} имеет или имел подписку (expired_at={user.expired_at}), уведомление не отправляется")
        return
    has_payments = await ProcessedPayments.filter(
        user_id=user.id,
        status="succeeded"
    ).exists()
    if has_payments:
        logger.info(f"Пользователь {user.id} имеет платежи, уведомление не отправляется")
        return
    logger.info(f"Отправка уведомления пользователю {user.id}, не взявшему пробную подписку (прошло {hours_passed} ч.)")
    text = """❗Вы еще не подключили бесплатный доступ к VPN.
❗Поддержка поможет подключиться @BlubCatVPN_support
🔥Мы хотим подарить вам приятную скидку, напишите нам, пожалуйста"""
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Подключить VPN", "second")
        )
        logger.info(f"Уведомление о невзятой пробной подписке успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о невзятой пробной подписке пользователю {user.id}: {e}") 