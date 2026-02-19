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

logger = get_logger("notifications.lte")


def _format_gb(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


async def notify_lte_half_limit(user, used_gb: float, total_gb: float, is_trial: bool = False):
    lang = get_user_locale(user)
    used = _format_gb(used_gb)
    total = _format_gb(total_gb)

    if lang == "ru":
        text = (
            f"[!] Вы использовали 50% LTE-лимита: {used} из {total} GB.\n"
            "Доступ к LTE-нодам будет отключен при исчерпании лимита."
        )
        if is_trial:
            text += "\n\nЭто ограничение пробного периода."
        button = await webapp_inline_button("Личный кабинет")
    else:
        text = (
            f"[!] You have used 50% of your LTE limit: {used} out of {total} GB.\n"
            "LTE access will be disabled once the limit is reached."
        )
        if is_trial:
            text += "\n\nThis is the trial limit."
        button = await webapp_inline_button("Dashboard")

    try:
        await bot.send_message(user.id, text, reply_markup=button)
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка отправки LTE уведомления (50%) для {user.id}: {e}")


async def notify_lte_full_limit(user, used_gb: float, total_gb: float, is_trial: bool = False):
    lang = get_user_locale(user)
    used = _format_gb(used_gb)
    total = _format_gb(total_gb)

    if lang == "ru":
        text = (
            f"[!] LTE-лимит исчерпан: {used} из {total} GB.\n"
            "Мы отключили доступ к LTE-нодам до обновления лимита.\n"
            "Можно увеличить LTE в разделе «Подписка → Изменить тариф»."
        )
        if is_trial:
            text += "\n\nЭто ограничение пробного периода."
        button = await webapp_inline_button("Докупить LTE", "subscription/change")
    else:
        text = (
            f"[!] LTE limit reached: {used} out of {total} GB.\n"
            "We disabled LTE access until the limit is refreshed.\n"
            "You can increase LTE in Subscription → Change plan."
        )
        if is_trial:
            text += "\n\nThis is the trial limit."
        button = await webapp_inline_button("Add LTE", "subscription/change")

    try:
        await bot.send_message(user.id, text, reply_markup=button)
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка отправки LTE уведомления (100%) для {user.id}: {e}")
