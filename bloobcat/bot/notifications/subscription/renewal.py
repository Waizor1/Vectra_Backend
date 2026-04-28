from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import (
    handle_telegram_forbidden_error,
    handle_telegram_bad_request,
    reset_user_failed_count,
)

logger = get_logger("notifications.subscription.renewal")


def _format_date_display(value) -> str:
    if not value:
        return "—"
    try:
        return value.strftime("%d.%m.%Y")
    except AttributeError:
        return str(value)


def _format_total_amount(
    amount_paid_via_yookassa: float,
    amount_from_balance: float,
    *,
    lang: str,
) -> str:
    total_amount = float(amount_from_balance or 0) + float(
        amount_paid_via_yookassa or 0
    )
    return f"{total_amount:.0f}₽" if lang == "ru" else f"{total_amount:.2f} RUB"


async def notify_auto_renewal_success_balance(user, days: int, amount: float):
    """
    Уведомляет пользователя об успешном автопродлении подписки с баланса.
    """
    logger.info(
        "Уведомление об успешном автопродлении с бонусного баланса отключено. user=%s days=%s amount=%s",
        user.id,
        days,
        amount,
    )


async def notify_frozen_base_auto_resumed_success(
    user,
    *,
    restored_days: int,
    restored_until,
):
    lang = get_user_locale(user)
    until_display = _format_date_display(restored_until)
    if lang == "ru":
        text = (
            f"🔄 👤 {user.full_name}, базовая подписка снова активна.\n\n"
            f"Восстановлено дней: {int(restored_days)}\n"
            f"Активна до: {until_display}"
        )
        button = await webapp_inline_button("Личный кабинет")
    else:
        text = (
            f"🔄 👤 {user.full_name}, your base subscription is active again.\n\n"
            f"Restored days: {int(restored_days)}\n"
            f"Active until: {until_display}"
        )
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
        logger.error(
            f"Ошибка отправки уведомления об автовосстановлении базовой подписки для {user.id}: {e}"
        )


async def notify_auto_renewal_failure(
    user, reason: str = "Неизвестная ошибка", will_retry: bool = True
):
    """
    Уведомляет пользователя о неудаче автоматического продления подписки.
    """
    lang = get_user_locale(user)
    logger.warning(
        f"Отправка уведомления о НЕУДАЧНОМ автопродлении пользователю {user.id}. Причина: {reason}"
    )
    retry_text = (
        "\n\nСледующая попытка будет выполнена завтра автоматически."
        if will_retry and lang == "ru"
        else "\n\nWe will try again automatically tomorrow."
        if will_retry and lang != "ru"
        else ""
    )
    if lang == "ru":
        text = (
            f"⚠️ 👤 {user.full_name}, автопродление не выполнено.\n\n"
            f"Причина: {reason}.{retry_text}"
        )
        button = await webapp_inline_button("Продлить вручную", "/pay")
    else:
        text = (
            f"⚠️ 👤 {user.full_name}, auto-renewal failed.\n\n"
            f"Reason: {reason}{retry_text}\n"
            "Please renew manually in your Vectra Connect dashboard or contact support."
        )
        button = await webapp_inline_button("Renew manually")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(
            f"Уведомление о неудачном автопродлении успешно отправлено пользователю {user.id}"
        )
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(
            f"Ошибка отправки уведомления о неудачном автопродлении для {user.id}: {e}"
        )


async def notify_renewal_success_yookassa(
    user,
    days: int,
    amount_paid_via_yookassa: float,
    amount_from_balance: float,
    migration_direction: str | None = None,
):
    """
    Уведомляет пользователя об успешном продлении подписки через Yookassa
    (включая возможное частичное списание с баланса).
    """
    lang = get_user_locale(user)
    logger.info(
        f"Отправка уведомления об успешном продлении (Yookassa) пользователю {user.id}"
    )
    amount_line = _format_total_amount(
        amount_paid_via_yookassa,
        amount_from_balance,
        lang=lang,
    )
    if lang == "ru":
        if migration_direction == "family_to_base":
            text = (
                f"✅ 👤 {user.full_name}, переход на базовый тариф оформлен.\n\n"
                "Текущая семейная подписка останется активной до конца срока.\n"
                "Базовый тариф активируется после её окончания.\n\n"
                f"Срок базового тарифа: {days} дней\n"
                f"Списано: {amount_line}"
            )
        else:
            text = (
                f"✅ 👤 {user.full_name}, подписка продлена.\n\n"
                f"Срок: {days} дней\n"
                f"Списано: {amount_line}"
            )
        button = await webapp_inline_button("Личный кабинет")
    else:
        if migration_direction == "family_to_base":
            text = (
                f"✅ 👤 {user.full_name}, your move to the base plan is confirmed.\n\n"
                "Your family subscription stays active until it ends.\n"
                "The base plan will activate right after that.\n\n"
                f"Base plan duration: {days} days\n"
                f"Charged: {amount_line}"
            )
        else:
            text = (
                f"✅ 👤 {user.full_name}, subscription renewed.\n\n"
                f"Duration: {days} days\n"
                f"Charged: {amount_line}"
            )
        button = await webapp_inline_button("Dashboard")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(
            f"Уведомление об успешном продлении (Yookassa) успешно отправлено пользователю {user.id}"
        )
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(
            f"Ошибка отправки уведомления об успешном продлении (Yookassa) для {user.id}: {e}"
        )


async def notify_family_purchase_success_yookassa(
    user,
    days: int,
    amount_paid_via_yookassa: float,
    amount_from_balance: float,
    device_count: int,
    migration_direction: str | None = None,
):
    """
    Уведомляет пользователя об успешной покупке семейной подписки через Yookassa/баланс.
    """
    lang = get_user_locale(user)
    logger.info(f"Отправка уведомления о семейной подписке пользователю {user.id}")
    amount_line = _format_total_amount(
        amount_paid_via_yookassa,
        amount_from_balance,
        lang=lang,
    )
    devices_limit = max(2, int(device_count or 2))
    limit_line = f"Лимит: до {devices_limit} устройств\n"
    if lang == "ru":
        if migration_direction == "base_to_family":
            text = (
                f"✅ 👤 {user.full_name}, переход на семейный тариф выполнен.\n\n"
                "Семейная подписка активирована сразу.\n"
                "Базовый тариф заморожен и восстановится после окончания семейного периода.\n\n"
                f"{limit_line}"
                f"Срок: {days} дней\n"
                f"Списано: {amount_line}"
            )
        else:
            text = (
                f"✅ 👤 {user.full_name}, семейная подписка активирована.\n\n"
                f"{limit_line}\n"
                f"Срок: {days} дней\n"
                f"Сумма: {amount_line}"
            )
        button = await webapp_inline_button(
            "Открыть раздел семьи", "/subscription/family"
        )
    else:
        if migration_direction == "base_to_family":
            text = (
                f"✅ 👤 {user.full_name}, your move to the family plan is complete.\n\n"
                "The family plan is active right away.\n"
                "Your base plan has been frozen and will return after the family period ends.\n\n"
                f"Duration: {days} days\n"
                f"Charged: {amount_line}"
            )
        else:
            text = (
                f"✅ 👤 {user.full_name}, your family subscription is now active for {days} days.\n\n"
                "You can add up to 10 people to your family plan.\n"
                f"Charged: {amount_line}"
            )
        button = await webapp_inline_button(
            "Open family section", "/subscription/family"
        )
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(
            f"Уведомление о семейной подписке успешно отправлено пользователю {user.id}"
        )
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(
            f"Ошибка отправки уведомления о семейной подписке для {user.id}: {e}"
        )


async def notify_payment_canceled_yookassa(user, reason: str | None = None):
    """
    Уведомляет пользователя об отмене/неуспешном завершении оплаты через YooKassa (ручной платеж).
    """
    lang = get_user_locale(user)
    fallback_reason = "не указана" if lang == "ru" else "not specified"
    why = (reason or "").strip() or fallback_reason
    reason_line = f"\n\nПричина: {why}" if lang == "ru" else f"\n\nReason: {why}"
    if lang == "ru":
        text = f"⚠️ 👤 {user.full_name}, платёж не завершён.{reason_line}"
        button = await webapp_inline_button("Повторить оплату", "/pay")
    else:
        text = (
            f"⚠️ 👤 {user.full_name}, the payment was not completed or was canceled."
            f"{reason_line}\n\n"
            "If this looks wrong, try again in your Vectra Connect dashboard or contact support."
        )
        button = await webapp_inline_button("Open dashboard")
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
        logger.error(
            f"Ошибка отправки уведомления об отмене оплаты (YooKassa) для {user.id}: {e}"
        )


async def notify_subscription_cancelled_after_failures(user) -> bool:
    """
    Уведомляет пользователя о том, что подписка отменена после нескольких неудачных попыток оплаты.
    """
    lang = get_user_locale(user)
    logger.warning(
        f"Отправка уведомления об отмене подписки из-за сбоев оплаты пользователю {user.id}"
    )
    if lang == "ru":
        text = (
            "⚠️ Автопродление отключено.\n\n"
            "Продление не выполнено после нескольких попыток.\n"
            "Вы можете продлить текущий тариф вручную в разделе «Подписка»."
        )
        button = await webapp_inline_button("Продлить вручную", "/subscription")
    else:
        text = (
            "⚠️ We couldn't renew your subscription.\n\n"
            "We tried to charge your payment method several times without success. Auto-renewal has been disabled.\n\n"
            "You can manually renew your current plan in the Subscription section."
        )
        button = await webapp_inline_button("Renew manually", "/subscription")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(
            f"Уведомление об отмене подписки успешно отправлено пользователю {user.id}"
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
            f"Ошибка отправки уведомления об отмене подписки для {user.id}: {e}"
        )
        return False


async def notify_frozen_base_activation_success(
    user,
    *,
    switched_until: str,
    frozen_current_days: int,
    activated_frozen_base_days: int,
):
    lang = get_user_locale(user)
    if lang == "ru":
        text = (
            f"✅ 👤 {user.full_name}, базовая подписка активирована.\n\n"
            f"Активный базовый период: {activated_frozen_base_days} дней\n"
            f"Заморожено из текущего семейного периода: {frozen_current_days} дней\n"
            f"Активна до: {switched_until}"
        )
        button = await webapp_inline_button("Открыть подписку", "/subscription")
    else:
        text = (
            f"✅ 👤 {user.full_name}, your frozen base subscription is now active.\n\n"
            f"Active base period: {activated_frozen_base_days} days\n"
            f"Frozen from current family period: {frozen_current_days} days\n"
            f"Active until: {switched_until}"
        )
        button = await webapp_inline_button("Open subscription", "/subscription")
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
        logger.error(
            f"Ошибка отправки уведомления об активации frozen base для {user.id}: {e}"
        )


async def notify_frozen_family_activation_success(
    user,
    *,
    switched_until: str,
    frozen_current_days: int,
    activated_frozen_family_days: int,
):
    lang = get_user_locale(user)
    if lang == "ru":
        text = (
            f"✅ 👤 {user.full_name}, семейная подписка активирована.\n\n"
            f"Активный семейный период: {activated_frozen_family_days} дней\n"
            f"Заморожено из текущего базового периода: {frozen_current_days} дней\n"
            f"Активна до: {switched_until}"
        )
        button = await webapp_inline_button("Открыть подписку", "/subscription")
    else:
        text = (
            f"✅ 👤 {user.full_name}, your frozen family subscription is now active.\n\n"
            f"Active family period: {activated_frozen_family_days} days\n"
            f"Frozen from current base period: {frozen_current_days} days\n"
            f"Active until: {switched_until}"
        )
        button = await webapp_inline_button("Open subscription", "/subscription")
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
        logger.error(
            f"Ошибка отправки уведомления об активации frozen family для {user.id}: {e}"
        )
