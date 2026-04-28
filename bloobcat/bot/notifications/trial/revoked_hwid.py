from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import (
    handle_telegram_forbidden_error,
    handle_telegram_bad_request,
    reset_user_failed_count,
)
from bloobcat.logger import get_logger

logger = get_logger("notifications.trial.revoked_hwid")


async def notify_trial_revoked_hwid(user):
    """
    Уведомление пользователю: триал отозван из-за повторяющегося устройства (HWID).
    """
    lang = get_user_locale(user)
    if lang == "ru":
        text = (
            f"⚠️ 👤 {user.full_name}, пробный доступ недоступен.\n\n"
            "Это устройство уже использовалось ранее."
        )
        button_text = "Оформить подписку"
        button = await webapp_inline_button(button_text, "/pay")
    else:
        text = (
            f"⚠️ 👤 {user.full_name}, the free trial is unavailable because this device was used before.\n"
            "You can continue by purchasing a subscription."
        )
        button_text = "Buy Subscription"
        button = await webapp_inline_button(button_text, "/pay")

    try:
        await bot.send_message(user.id, text, reply_markup=button)
        await reset_user_failed_count(user.id)
        logger.info(
            "Отправлено уведомление об отзыве триала (HWID) пользователю %s", user.id
        )
    except TelegramForbiddenError as e:
        logger.warning("User %s blocked the bot: %s", user.id, e)
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error("Bad request for user %s: %s", user.id, e)
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:  # pragma: no cover
        logger.error(
            "Ошибка при отправке уведомления об отзыве триала пользователю %s: %s",
            user.id,
            e,
        )
