from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger

logger = get_logger("notifications.trial.extend")

async def notify_trial_extended(user, days: int):
    """
    Уведомляет пользователя о продлении пробного периода
    """
    logger.info(f"Отправка уведомления о продлении пробного периода пользователю {user.id}")
    text = (
        f"🎉 Привет, {user.full_name}! Ваш пробный период продлен на {days} дней! 🔄\n"
        "Используйте это время, чтобы полностью оценить скорость и безопасность BlubCat VPN.\n"
        "Вопросы? Пишите в поддержку @BlubCatVPN_support"
    )
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Подключить VPN", "second")
        )
        logger.info(f"Уведомление о продлении пробного периода успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о продлении пробного периода пользователю {user.id}: {e}") 