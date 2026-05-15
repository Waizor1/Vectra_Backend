"""Notification fired right after the daily downgrade scheduler closes a
reverse trial — surfaces the freshly issued -50 % personal discount.
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
    from bloobcat.db.discounts import PersonalDiscount
    from bloobcat.db.reverse_trial import ReverseTrialState
    from bloobcat.db.users import Users

logger = get_logger("notifications.reverse_trial.downgraded")
WEB_USER_ID_FLOOR = 8_000_000_000_000_000


async def notify_reverse_trial_downgraded(
    user: "Users",
    state: "ReverseTrialState",
    discount: "PersonalDiscount",
) -> None:
    percent = int(getattr(discount, "percent", 0) or app_settings.reverse_trial_discount_percent)
    ttl_days = int(app_settings.reverse_trial_discount_ttl_days or 14)
    lang = get_user_locale(user)

    if lang == "ru":
        title = "Пробный доступ завершён"
        body = (
            f"Держи персональную скидку -{percent}% на 1-й месяц. "
            f"Действует {ttl_days} дней — успей продлить."
        )
        button_text = "Применить скидку"
    else:
        title = "Trial ended"
        body = (
            f"Here's your -{percent}% discount for the first month. "
            f"Valid for {ttl_days} days — renew while it's hot."
        )
        button_text = "Apply discount"

    deep_link = "/subscription?reverse_trial=1"

    if int(user.id) < WEB_USER_ID_FLOOR:
        try:
            button = await webapp_inline_button(button_text, deep_link)
            await bot.send_message(user.id, f"<b>{title}</b>\n\n{body}", reply_markup=button)
            await reset_user_failed_count(user.id)
            logger.info("Reverse-trial downgraded telegram sent to user=%s", user.id)
        except TelegramForbiddenError as exc:
            logger.warning("User %s blocked the bot: %s", user.id, exc)
            await handle_telegram_forbidden_error(user.id, exc)
        except TelegramBadRequest as exc:
            logger.error("Bad request error for user %s: %s", user.id, exc)
            await handle_telegram_bad_request(user.id, exc)
        except Exception as exc:  # pragma: no cover
            logger.error(
                "Failed to send reverse-trial downgraded telegram user=%s: %s",
                user.id,
                exc,
            )

    try:
        await send_push_to_user(
            int(user.id),
            title=title,
            body=body,
            url=deep_link,
            tag=f"reverse-trial-downgraded-{int(state.id)}",
        )
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "Failed to send reverse-trial downgraded web-push user=%s: %s",
            user.id,
            exc,
        )
