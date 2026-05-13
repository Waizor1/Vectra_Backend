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


async def notify_lte_topup_user(
    *,
    user,
    lte_gb_delta: int,
    lte_gb_after: int | None,
) -> bool:
    """Send a user-facing payment confirmation after a successful LTE top-up.

    Covers both Platega and YooKassa webhook paths via the shared
    _apply_lte_topup_effect helper, plus the balance-only path in user.py.
    Returns True on success, False on delivery failure.
    """
    user_id = getattr(user, "id", None)
    if not isinstance(user_id, int) or user_id is None or user_id <= 0:
        logger.warning(
            "notify_lte_topup_user: skipping invalid user_id=%s", user_id
        )
        return False

    lang = get_user_locale(user)
    delta_str = _format_gb(lte_gb_delta)
    after_str = _format_gb(lte_gb_after) if lte_gb_after is not None else "?"

    if lang == "ru":
        text = (
            f"✅ LTE пополнен на {delta_str} ГБ. "
            f"Доступно: {after_str} ГБ.\n\n"
            "Спасибо за оплату — "
            "приятного использования!"
        )
    else:
        text = (
            f"✅ LTE topped up by {delta_str} GB. "
            f"Available: {after_str} GB.\n\n"
            "Thanks for the payment — enjoy!"
        )

    try:
        await bot.send_message(user_id, text)
        await reset_user_failed_count(user_id)
        logger.info(
            "notify_lte_topup_user: delivered user=%s delta=%s after=%s",
            user_id,
            lte_gb_delta,
            lte_gb_after,
        )
        return True
    except TelegramForbiddenError as exc:
        logger.warning("notify_lte_topup_user: user %s blocked the bot: %s", user_id, exc)
        await handle_telegram_forbidden_error(user_id, exc)
    except TelegramBadRequest as exc:
        logger.error(
            "notify_lte_topup_user: bad request for user %s: %s", user_id, exc
        )
        await handle_telegram_bad_request(user_id, exc)
    except Exception as exc:
        logger.error(
            "notify_lte_topup_user: unexpected error for user %s: %s", user_id, exc
        )
    return False
