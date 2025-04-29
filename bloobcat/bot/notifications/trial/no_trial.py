from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale

logger = get_logger("notifications.trial.no_trial")

async def notify_no_trial_taken(user, hours_passed: int):
    lang = get_user_locale(user)
    logger.info(f"Подготовка уведомления пользователю {user.id}, не взявшему пробную подписку (прошло {hours_passed} ч.)")
    has_payments = await ProcessedPayments.filter(
        user_id=user.id,
        status="succeeded"
    ).exists()
    if has_payments:
        logger.info(f"Пользователь {user.id} имеет платежи, уведомление не отправляется")
        return
    logger.info(f"Отправка уведомления пользователю {user.id}, не взявшему пробную подписку (прошло {hours_passed} ч.)")
    if lang == 'ru':
        text = (
            f"👋 Привет, {user.full_name}! Еще не воспользовались бесплатным доступом к VPN? 🔓\n"
            "Активируйте 3-дневный пробный период прямо сейчас и оцените все преимущества BlubCat.\n"
            "Если возникнут вопросы, пишите в поддержку @BlubCatVPN_support"
        )
        button = await webapp_inline_button("Подключить VPN", "second")
    else:
        text = (
            f"👋 Hi {user.full_name}! Haven't tried our free VPN access yet? 🔓\n"
            "Activate a 3-day trial now and experience all benefits of BlubCat.\n"
            "Have questions? Contact support @BlubCatVPN_support"
        )
        button = await webapp_inline_button("Connect VPN", "second")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о невзятой пробной подписке успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о невзятой пробной подписке пользователю {user.id}: {e}") 