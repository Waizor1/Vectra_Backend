from typing import TYPE_CHECKING # Added to fix circular import

from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
# from bloobcat.db.users import Users # Moved under TYPE_CHECKING

if TYPE_CHECKING:
    from bloobcat.db.users import Users # For type hinting only

logger = get_logger("notifications.trial.granted")

async def notify_trial_granted(user: 'Users'): # Changed Users to 'Users'
    """
    Уведомляет пользователя о том, что ему был предоставлен триальный период.
    Отправляется сразу после назначения триала.
    """
    lang = get_user_locale(user)
    logger.info(f"Подготовка уведомления о предоставлении триала для пользователя {user.id}")

    if lang == 'ru':
        text = (
            f"🎉 Поздравляем, {user.full_name}! Вам предоставлен бесплатный 10-дневный доступ к BlubCat VPN.\n\n"
            "Оцените все преимущества нашего сервиса прямо сейчас!"
        )
        button_text = "🚀 В кабинет"
    else:
        text = (
            f"🎉 Congratulations, {user.full_name}! You've been granted a free 10-day access to BlubCat VPN.\n\n"
            "Explore all the benefits of our service right now!"
        )
        button_text = "🚀 To Dashboard"
    
    button = await webapp_inline_button(button_text) # Assuming default WebApp URL is fine

    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о предоставлении триала успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о предоставлении триала пользователю {user.id}: {e}") 