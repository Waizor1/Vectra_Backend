from typing import TYPE_CHECKING # Added to fix circular import
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.settings import app_settings # Import app_settings
from bloobcat.bot.error_handler import handle_telegram_forbidden_error, handle_telegram_bad_request, reset_user_failed_count
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

    trial_duration_days = app_settings.trial_days # Get trial duration

    if lang == 'ru':
        text = (
            f"🎉 Поздравляем, {user.full_name}! Вам предоставлен бесплатный {trial_duration_days}-дневный доступ к BlubCat VPN.\n\n"
            "Оцените все преимущества нашего сервиса прямо сейчас!"
        )
        button_text = "🚀 В кабинет"
    else:
        text = (
            f"🎉 Congratulations, {user.full_name}! You've been granted a free {trial_duration_days}-day access to BlubCat VPN.\n\n"
            "Explore all the benefits of our service right now!"
        )
        button_text = "🚀 To Dashboard"
    
    button = await webapp_inline_button(button_text) # Assuming default WebApp URL is fine

    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о предоставлении триала успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о предоставлении триала пользователю {user.id}: {e}") 