from __future__ import annotations

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

logger = get_logger("notifications.lte")


def _format_gb(value: float) -> str:
    number = float(value or 0)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}".rstrip("0").rstrip(".")


async def _send_lte_limit_notification(
    *,
    user,
    used_gb: float,
    total_gb: float,
    is_trial: bool,
    is_full: bool,
) -> bool:
    lang = get_user_locale(user)
    used_total = f"{_format_gb(used_gb)}/{_format_gb(total_gb)} ГБ"
    if lang == "ru":
        if is_full:
            text = (
                "LTE-трафик закончился.\n\n"
                f"Использовано: {used_total}.\n"
                "Пополните LTE баланс, чтобы снова включить LTE-серверы."
            )
        else:
            text = (
                "LTE-трафик скоро закончится.\n\n"
                f"Использовано: {used_total}.\n"
                "Пополните LTE баланс, чтобы слабая сеть продолжала работать без перебоев."
            )
        button_text = "Пополнить LTE"
    else:
        if is_full:
            text = (
                "LTE traffic is over.\n\n"
                f"Used: {used_total}.\n"
                "Top up your LTE balance to enable LTE servers again."
            )
        else:
            text = (
                "LTE traffic is almost over.\n\n"
                f"Used: {used_total}.\n"
                "Top up LTE so weak-network access keeps working."
            )
        button_text = "Top up LTE"

    try:
        button = await webapp_inline_button(button_text, "/subscription?lteTopup=1")
        await bot.send_message(user.id, text, reply_markup=button)
        await reset_user_failed_count(user.id)
        logger.info(
            "LTE threshold notification delivered user=%s used=%s total=%s trial=%s full=%s",
            user.id,
            used_gb,
            total_gb,
            is_trial,
            is_full,
        )
        return True
    except TelegramForbiddenError as exc:
        logger.warning("User %s blocked the bot: %s", user.id, exc)
        await handle_telegram_forbidden_error(user.id, exc)
    except TelegramBadRequest as exc:
        logger.error("Bad request while sending LTE notification to %s: %s", user.id, exc)
        await handle_telegram_bad_request(user.id, exc)
    except Exception as exc:
        logger.error("Failed to send LTE notification to %s: %s", user.id, exc)
    return False


async def notify_lte_half_limit(
    user, used_gb: float, total_gb: float, is_trial: bool = False
) -> bool:
    return await _send_lte_limit_notification(
        user=user,
        used_gb=used_gb,
        total_gb=total_gb,
        is_trial=is_trial,
        is_full=False,
    )


async def notify_lte_full_limit(
    user, used_gb: float, total_gb: float, is_trial: bool = False
) -> bool:
    return await _send_lte_limit_notification(
        user=user,
        used_gb=used_gb,
        total_gb=total_gb,
        is_trial=is_trial,
        is_full=True,
    )
