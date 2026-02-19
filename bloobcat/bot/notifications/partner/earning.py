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

logger = get_logger("notifications.partner.earning")


async def notify_partner_earning(
    *,
    partner: Users,
    referral: Users,
    amount_total_rub: int,
    reward_rub: int,
    percent: int,
    source: str,
    qr_title: str | None = None,
) -> None:
    """
    Best-effort Telegram notification to a partner about a cashback earning in RUB.

    Notes:
    - Partner program is money-based (RUB), separate from referral days-based program.
    - We keep this function side-effect only (no DB writes) and resilient to Telegram failures.
    """
    lang = get_user_locale(partner)
    src = (source or "").strip() or "unknown"
    qr_part = (qr_title or "").strip()

    if lang == "ru":
        qr_line = f"\nQR: {qr_part}" if qr_part else ""
        text = (
            "Начисление по партнерке\n\n"
            f"Кэшбек: +{int(reward_rub)} ₽ ({int(percent)}%)\n"
            f"Покупка: {int(amount_total_rub)} ₽\n"
            f"Пользователь: {referral.name()} (ID: {int(referral.id)})\n"
            f"Источник: {src}{qr_line}"
        )
        button = await webapp_inline_button("Кабинет партнера", "partner")
    else:
        qr_line = f"\nQR: {qr_part}" if qr_part else ""
        text = (
            "Partner earning\n\n"
            f"Cashback: +{int(reward_rub)} RUB ({int(percent)}%)\n"
            f"Purchase: {int(amount_total_rub)} RUB\n"
            f"User: {referral.name()} (ID: {int(referral.id)})\n"
            f"Source: {src}{qr_line}"
        )
        button = await webapp_inline_button("Partner dashboard", "partner")

    try:
        await bot.send_message(int(partner.id), text, reply_markup=button)
        await reset_user_failed_count(int(partner.id))
    except TelegramForbiddenError as e:
        logger.warning("Partner %s blocked the bot: %s", partner.id, e)
        await handle_telegram_forbidden_error(int(partner.id), e)
    except TelegramBadRequest as e:
        logger.error("Bad request for partner %s: %s", partner.id, e)
        await handle_telegram_bad_request(int(partner.id), e)
    except Exception as e:
        logger.error("Failed to send partner earning notification to %s: %s", partner.id, e)

