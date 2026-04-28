from datetime import date, datetime
from zoneinfo import ZoneInfo
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users, normalize_date
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.notifications.rescue_link import append_rescue_link
from bloobcat.bot.error_handler import (
    handle_telegram_forbidden_error,
    handle_telegram_bad_request,
    reset_user_failed_count,
)

logger = get_logger("notifications.subscription.expiration")


def _format_rub_amount(amount: float | int, *, lang: str) -> str:
    value = float(amount or 0)
    return f"{value:.0f}₽" if lang == "ru" else f"{value:.2f} RUB"


def _resolve_auto_payment_quote(
    *,
    total_amount: float | int | None,
    amount_external: float | int | None,
    amount_from_balance: float | int | None,
) -> tuple[float, float, float] | None:
    if (
        total_amount is None
        and amount_external is None
        and amount_from_balance is None
    ):
        return None

    resolved_external = float(amount_external or 0)
    resolved_balance = float(amount_from_balance or 0)
    resolved_total = (
        float(total_amount)
        if total_amount is not None
        else resolved_external + resolved_balance
    )
    return resolved_total, resolved_external, resolved_balance


def _resolve_charge_date(value: date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    return value


async def notify_auto_payment(
    user: Users,
    *,
    total_amount: float | int | None = None,
    amount_external: float | int | None = None,
    amount_from_balance: float | int | None = None,
    charge_date: date | datetime | None = None,
) -> bool:
    """
    Уведомляет пользователя о предстоящем автоматическом платеже
    """
    lang = get_user_locale(user)
    logger.info(f"Подготовка уведомления об автоплатеже для пользователя {user.id}")
    quote = _resolve_auto_payment_quote(
        total_amount=total_amount,
        amount_external=amount_external,
        amount_from_balance=amount_from_balance,
    )
    resolved_charge_date = _resolve_charge_date(charge_date)
    button_label = "Управление подпиской" if lang == "ru" else "Manage subscription"
    button = await webapp_inline_button(button_label, "/subscription")

    if quote is None:
        last_payment = (
            await ProcessedPayments.filter(user_id=user.id, status="succeeded")
            .order_by("-processed_at")
            .first()
        )
        if not last_payment:
            logger.warning(
                f"Не найден успешный платеж для пользователя {user.id}, уведомление об автоплатеже не отправлено"
            )
            return False
        user_expired_at = normalize_date(user.expired_at)
        effective_charge_date = resolved_charge_date or user_expired_at
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        days_remaining = (
            (effective_charge_date - today).days if effective_charge_date else 0
        )
        logger.info(
            "Отправка fallback-уведомления об автоплатеже пользователю %s, дней до списания: %s, сумма: %s",
            user.id,
            days_remaining,
            last_payment.amount,
        )
        if lang == "ru":
            text = (
                f"👤 {user.full_name}, через {days_remaining} "
                f"{'день' if days_remaining == 1 else 'дня' if days_remaining < 5 else 'дней'} "
                "произойдёт автопродление.\n\n"
                f"Сумма списания: {_format_rub_amount(last_payment.amount, lang=lang)}"
            )
        else:
            text = (
                f"👤 {user.full_name}, in {days_remaining} "
                f"{'day' if days_remaining == 1 else 'days'} your subscription will auto-renew.\n\n"
                f"Charge amount: {_format_rub_amount(last_payment.amount, lang=lang)}"
            )
    else:
        resolved_total, resolved_external, resolved_balance = quote
        logger.info(
            "Отправка reminder-а об автоплатеже пользователю %s, charge_date=%s, total=%s, external=%s, balance=%s",
            user.id,
            resolved_charge_date,
            resolved_total,
            resolved_external,
            resolved_balance,
        )
        if lang == "ru":
            if resolved_external > 0 and resolved_balance > 0:
                text = (
                    f"⚠️ 👤 {user.full_name}, сегодня ночью будет выполнена попытка автопродления подписки.\n\n"
                    f"Всего: {_format_rub_amount(resolved_total, lang=lang)}\n"
                    f"С баланса: {_format_rub_amount(resolved_balance, lang=lang)}\n"
                    f"Через YooKassa: {_format_rub_amount(resolved_external, lang=lang)}"
                )
            elif resolved_external > 0:
                text = (
                    f"⚠️ 👤 {user.full_name}, сегодня ночью будет выполнена попытка автопродления подписки.\n\n"
                    f"К списанию: {_format_rub_amount(resolved_external, lang=lang)}"
                )
            else:
                text = (
                    f"⚠️ 👤 {user.full_name}, сегодня ночью будет выполнено автопродление подписки.\n\n"
                    f"С баланса будет списано: {_format_rub_amount(resolved_total, lang=lang)}"
                )
        else:
            if resolved_external > 0 and resolved_balance > 0:
                text = (
                    f"⚠️ 👤 {user.full_name}, your subscription will be auto-renewed tonight.\n\n"
                    f"Total: {_format_rub_amount(resolved_total, lang=lang)}\n"
                    f"From balance: {_format_rub_amount(resolved_balance, lang=lang)}\n"
                    f"Via YooKassa: {_format_rub_amount(resolved_external, lang=lang)}"
                )
            elif resolved_external > 0:
                text = (
                    f"⚠️ 👤 {user.full_name}, your subscription will be auto-renewed tonight.\n\n"
                    f"Charge amount: {_format_rub_amount(resolved_external, lang=lang)}"
                )
            else:
                text = (
                    f"⚠️ 👤 {user.full_name}, your subscription will be auto-renewed tonight.\n\n"
                    f"Amount from balance: {_format_rub_amount(resolved_total, lang=lang)}"
                )
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(
            f"Уведомление об автоплатеже успешно отправлено пользователю {user.id}"
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
        logger.error(
            f"Ошибка при отправке уведомления об автоплатеже пользователю {user.id}: {str(e)}"
        )
        return False


async def notify_expiring_subscription(user: Users) -> bool:
    """
    Уведомляет пользователя без автопродления о скором истечении подписки
    """
    # Локализация
    lang = get_user_locale(user)
    logger.info(
        f"Подготовка уведомления об истечении подписки для пользователя {user.id}"
    )
    # Используем календарную разницу по Москве, чтобы текст оставался корректным
    # после переноса части уведомлений с полуночи на утро.
    moscow_tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(moscow_tz)
    user_expired_at = normalize_date(user.expired_at)
    days = (user_expired_at - now.date()).days if user_expired_at else 0
    if days < 0:
        days = 0
    logger.info(
        f"Отправка уведомления об истечении подписки пользователю {user.id}, дней до истечения: {days}"
    )
    if lang == "ru":
        if days == 7:
            text = f"👤 {user.full_name}, подписка истекает через 7 дней."
        elif days == 3:
            text = f"⚠️ 👤 {user.full_name}, подписка истекает через 3 дня."
        elif days == 1:
            text = f"⚠️ 👤 {user.full_name}, подписка истекает завтра."
        else:
            text = f"👤 {user.full_name}, подписка истекает через {days} дней."
        button = await webapp_inline_button("Продлить", "/pay")
    else:
        if days == 7:
            text = (
                f"👤 {user.full_name}, your subscription expires in 7 days.\n"
                "Don't forget to renew to keep your VPN access!"
            )
        elif days == 3:
            text = (
                f"⚠️ 👤 {user.full_name}, your subscription expires in 3 days.\n"
                "Renew now to avoid losing VPN access!"
            )
        elif days == 1:
            text = (
                f"⚠️ 👤 {user.full_name}, your subscription expires tomorrow.\n"
                "Renew now to keep your VPN active!"
            )
        else:
            text = (
                f"👤 {user.full_name}, your subscription expires in {days} days.\n"
                "Don't forget to renew to keep your VPN access!"
            )
        button = await webapp_inline_button("Renew Now", "/pay")
    text = append_rescue_link(text, lang=lang)
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(
            f"Уведомление об истечении подписки успешно отправлено пользователю {user.id}"
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
        logger.error(
            f"Ошибка при отправке уведомления об истечении подписки пользователю {user.id}: {str(e)}"
        )
        return False
