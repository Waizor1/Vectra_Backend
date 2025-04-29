from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale

logger = get_logger("notifications.trial.extend")

async def notify_trial_extended(user, days: int):
    lang = get_user_locale(user)
    # Choose message and button based on user language
    if lang == 'ru':
        text = (
            f"🎉 Привет, {user.full_name}! Ваш пробный период продлен на {days} дней! 🔄\n"
            "Используйте это время, чтобы полностью оценить скорость и безопасность BlubCat VPN.\n"
            "Вопросы? Пишите в поддержку @BlubCatVPN_support"
        )
        button = await webapp_inline_button("Подключить VPN", "second")
    else:
        text = (
            f"🎉 Hi {user.full_name}! Your trial period has been extended by {days} days! 🔄\n"
            "Use this time to fully experience the speed and security of BlubCat VPN.\n"
            "Questions? Contact support @BlubCatVPN_support"
        )
        button = await webapp_inline_button("Connect VPN", "second")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о продлении пробного периода успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о продлении пробного периода пользователю {user.id}: {e}") 