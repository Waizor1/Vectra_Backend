"""Day-before reminder for an active reverse trial.

Fires from the daily 12:00 MSK pre-warning scheduler when ``expires_at`` is
within the 23-25h window. Sends both a Telegram message and a Web Push so
users on the PWA still get notified even if they have not opened the bot
recently.
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
from bloobcat.bot.notifications.web_push import send_push_to_user
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

if TYPE_CHECKING:
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.db.users import Users

logger = get_logger("notifications.reverse_trial.pre_expiry")
WEB_USER_ID_FLOOR = 8_000_000_000_000_000


async def notify_reverse_trial_pre_expiry(
    user: "Users", state: "ReverseTrialState"
) -> None:
    discount_percent = int(app_settings.reverse_trial_discount_percent or 50)
    lang = get_user_locale(user)

    if lang == "ru":
        title = "Завтра тариф закончится"
        body = (
            "Успей оплатить со скидкой "
            f"-{discount_percent}% после окончания пробного доступа."
        )
        button_text = "Продлить подписку"
    else:
        title = "Trial ends tomorrow"
        body = (
            "Renew now and keep all features. "
            f"You'll get -{discount_percent}% after the trial ends."
        )
        button_text = "Renew subscription"

    if int(user.id) < WEB_USER_ID_FLOOR:
        try:
            button = await webapp_inline_button(button_text, "/subscription")
            await bot.send_message(user.id, f"<b>{title}</b>\n\n{body}", reply_markup=button)
            await reset_user_failed_count(user.id)
            logger.info("Reverse-trial pre-expiry telegram sent to user=%s", user.id)
        except TelegramForbiddenError as exc:
            logger.warning("User %s blocked the bot: %s", user.id, exc)
            await handle_telegram_forbidden_error(user.id, exc)
        except TelegramBadRequest as exc:
            logger.error("Bad request error for user %s: %s", user.id, exc)
            await handle_telegram_bad_request(user.id, exc)
        except Exception as exc:  # pragma: no cover
            logger.error(
                "Failed to send reverse-trial pre-expiry to user=%s: %s",
                user.id,
                exc,
            )

    try:
        await send_push_to_user(
            int(user.id),
            title=title,
            body=body,
            url="/subscription",
            tag=f"reverse-trial-pre-expiry-{int(state.id)}",
        )
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "Failed to send reverse-trial pre-expiry web-push to user=%s: %s",
            user.id,
            exc,
        )
