"""Telegram notification fired when a reverse trial is granted to a new user.

Mirrors the existing trial-granted template style: single send, locale-aware
text, graceful handling of bot-blocked users via the shared error handler.
Web-only users (id >= WEB_USER_ID_FLOOR) skip the Telegram path because
there is no chat to send to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bloobcat.bot.bot import bot
from bloobcat.bot.error_handler import (
    handle_telegram_bad_request,
    handle_telegram_forbidden_error,
    reset_user_failed_count,
)
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

if TYPE_CHECKING:
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.db.users import Users

logger = get_logger("notifications.reverse_trial.granted")
WEB_USER_ID_FLOOR = 8_000_000_000_000_000


async def notify_reverse_trial_granted(
    user: "Users", state: "ReverseTrialState"
) -> None:
    if int(user.id) >= WEB_USER_ID_FLOOR:
        logger.info(
            "Skipping Telegram reverse-trial-granted notification for web-only user %s",
            user.id,
        )
        return

    days = int(app_settings.reverse_trial_days or 7)
    lang = get_user_locale(user)

    if lang == "ru":
        text = (
            f"{user.full_name}, у тебя {days} дней полного доступа Vectra.\n\n"
            "Все функции открыты: больше устройств, выше скорость, без ограничений LTE.\n"
            "По окончании пробного доступа мы напомним и предложим продлить со скидкой."
        )
        button_text = "Открыть подписку"
    else:
        text = (
            f"Hi {user.full_name}, you have {days} days of full Vectra access.\n\n"
            "All features unlocked: more devices, higher speeds, no LTE caps.\n"
            "When the trial ends we'll send a reminder with a discount to continue."
        )
        button_text = "Open subscription"

    button = await webapp_inline_button(button_text, "/subscription")

    try:
        await bot.send_message(user.id, text, reply_markup=button)
        await reset_user_failed_count(user.id)
        logger.info("Reverse-trial granted notification sent to user=%s", user.id)
    except TelegramForbiddenError as exc:
        logger.warning("User %s blocked the bot: %s", user.id, exc)
        await handle_telegram_forbidden_error(user.id, exc)
    except TelegramBadRequest as exc:
        logger.error("Bad request error for user %s: %s", user.id, exc)
        await handle_telegram_bad_request(user.id, exc)
    except Exception as exc:  # pragma: no cover - operational tail
        logger.error(
            "Failed to send reverse-trial granted notification to user=%s: %s",
            user.id,
            exc,
        )
