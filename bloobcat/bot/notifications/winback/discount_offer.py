from datetime import date

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bloobcat.bot.bot import bot
from bloobcat.bot.error_handler import (
    handle_telegram_bad_request,
    handle_telegram_forbidden_error,
    reset_user_failed_count,
)
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("notifications.winback.discount_offer")


async def notify_winback_discount_offer(
    user: Users, percent: int, expires_at: date
) -> bool:
    """
    Уведомляет пользователя о персональной скидке для возвращения.
    """
    lang = get_user_locale(user)
    logger.info(f"Подготовка уведомления о скидке для возвращения для пользователя {user.id}")

    expires_formatted = expires_at.strftime("%d.%m.%Y")

    if lang == 'ru':
        text = (
            f"{user.full_name}, доступно персональное предложение.\n\n"
            f"Скидка: {percent}%\n\n"
            "Срок действия: 4 часа"
        )
        button_text = "Получить скидку"
    else:
        text = (
            f"Hi {user.full_name}! We haven't seen you in a while.\n\n"
            "We miss you and want to invite you back! "
            f"That's why we're giving you a personal discount of {percent}% on your next purchase of a subscription.\n\n"
            f"The discount is valid until {expires_formatted}.\n\n"
            "Don't miss the chance to enjoy a fast and secure VPN again!"
        )
        button_text = "Get Discount"

    button = await webapp_inline_button(button_text, "/pay")

    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(
            f"Уведомление о скидке для возвращения успешно отправлено пользователю {user.id}"
        )
        await reset_user_failed_count(user.id)
        return True
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
        return False
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
        return False
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о скидке для возвращения пользователю {user.id}: {e}")
        return False
