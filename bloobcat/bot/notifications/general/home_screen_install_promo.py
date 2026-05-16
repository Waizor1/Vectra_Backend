"""Bot push that nudges users to add Vectra to their home screen.

Single inline button → `https://app.vectra-pro.net?startapp=hs_promo` (Mini App
deep link). The frontend on /account/security flashes the install card when it
sees `startapp=hs_promo` so the user lands directly on the right CTA.
"""

from __future__ import annotations

from urllib.parse import quote

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import (
    handle_telegram_forbidden_error,
    handle_telegram_bad_request,
    reset_user_failed_count,
)
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.settings import telegram_settings

logger = get_logger("notifications.general.home_screen_install_promo")


def _miniapp_deep_link(payload: str) -> str:
    base = (telegram_settings.miniapp_url or "").rstrip("/")
    if not base:
        return ""
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}startapp={quote(payload, safe='')}"


async def send_home_screen_install_promo(user: Users) -> bool:
    """Returns True when the bot message was accepted by Telegram."""
    lang = get_user_locale(user)
    if lang == "ru":
        text = (
            f"{user.full_name}, поставь Vectra на главный экран — открывайся в один тап. "
            "За установку начислим 50 ₽ на баланс или дадим 10% скидку (на выбор)."
        )
        button_text = "Открыть Vectra"
    else:
        text = (
            f"{user.full_name}, add Vectra to your home screen — open it in one tap. "
            "We'll credit ₽50 to your balance or a 10% discount (your pick)."
        )
        button_text = "Open Vectra"

    deep_link = _miniapp_deep_link("hs_promo")
    keyboard = await webapp_inline_button(text=button_text, url=deep_link)
    try:
        await bot.send_message(chat_id=user.id, text=text, reply_markup=keyboard)
    except TelegramForbiddenError as exc:
        await handle_telegram_forbidden_error(user.id, exc)
        return False
    except TelegramBadRequest as err:
        await handle_telegram_bad_request(user.id, err)
        return False
    except Exception as exc:
        logger.warning(
            "home_screen_install_promo send failed for user=%s: %s",
            user.id,
            exc,
        )
        return False

    await reset_user_failed_count(user.id)
    return True
