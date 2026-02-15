from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import handle_telegram_forbidden_error, handle_telegram_bad_request, reset_user_failed_count
from bloobcat.db.active_tariff import ActiveTariffs

logger = get_logger("notifications.subscription.renewal")

async def notify_auto_renewal_success_balance(user, days: int, amount: float):
    """
    Уведомляет пользователя об успешном автопродлении подписки с баланса.
    """
    # Локализация уведомления об успешном автопродлении с баланса
    lang = get_user_locale(user)
    logger.info(f"Отправка уведомления об успешном автопродлении с баланса пользователю {user.id}")
    lte_line = ""
    if user.active_tariff_id:
        active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if active_tariff and (active_tariff.lte_gb_total or 0) > 0:
            if lang == 'ru':
                lte_line = f"\n\nLTE-лимит обновлен: {int(active_tariff.lte_gb_total)} GB."
            else:
                lte_line = f"\n\nLTE limit refreshed: {int(active_tariff.lte_gb_total)} GB."
    if lang == 'ru':
        text = (
            f"🎉 Привет, {user.full_name}! Ваша подписка автоматически продлена на {days} дней! 💪\n"
            f"С вашего бонусного баланса списано {amount:.2f}₽.\n"
            f"Спасибо, что остаетесь с нами! 🌟{lte_line}"
        )
        button = await webapp_inline_button("Личный кабинет")
    else:
        text = (
            f"🎉 Hi {user.full_name}! Your subscription was auto-renewed for {days} days! 💪\n"
            f"{amount:.2f} RUB has been deducted from your bonus balance.\n"
            f"Thank you for staying with us! 🌟{lte_line}"
        )
        button = await webapp_inline_button("Dashboard")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление об успешном автопродлении с баланса успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об успешном автопродлении с баланса для {user.id}: {e}")


async def notify_auto_renewal_failure(user, reason: str = "Неизвестная ошибка", will_retry: bool = True):
    """
    Уведомляет пользователя о неудаче автоматического продления подписки.
    """
    lang = get_user_locale(user)
    logger.warning(f"Отправка уведомления о НЕУДАЧНОМ автопродлении пользователю {user.id}. Причина: {reason}")
    retry_text = "\n\nМы попробуем повторить попытку завтра автоматически." if will_retry and lang == 'ru' else "\n\nWe will try again automatically tomorrow." if will_retry and lang != 'ru' else ""
    if lang == 'ru':
        text = (
            f"⚠️ Привет, {user.full_name}! Не удалось автоматически продлить подписку.\n\n"
            f"Причина: {reason}{retry_text}\n"
            "Продлите вручную в личном кабинете TVPN или обратитесь в поддержку."
        )
        button = await webapp_inline_button("💳 Продлить вручную")
    else:
        text = (
            f"⚠️ Hi {user.full_name}! Auto-renewal failed.\n\n"
            f"Reason: {reason}{retry_text}\n"
            "Please renew manually in your TVPN dashboard or contact support."
        )
        button = await webapp_inline_button("💳 Renew manually")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о неудачном автопродлении успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о неудачном автопродлении для {user.id}: {e}")


async def notify_renewal_success_yookassa(user, days: int, amount_paid_via_yookassa: float, amount_from_balance: float):
    """
    Уведомляет пользователя об успешном продлении подписки через Yookassa
    (включая возможное частичное списание с баланса).
    """
    # Локализация уведомления об успешном продлении через Yookassa
    lang = get_user_locale(user)
    logger.info(f"Отправка уведомления об успешном продлении (Yookassa) пользователю {user.id}")
    lte_line = ""
    if user.active_tariff_id:
        active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if active_tariff and (active_tariff.lte_gb_total or 0) > 0:
            if lang == 'ru':
                lte_line = f"\n\nLTE-лимит обновлен: {int(active_tariff.lte_gb_total)} GB."
            else:
                lte_line = f"\n\nLTE limit refreshed: {int(active_tariff.lte_gb_total)} GB."
    if lang == 'ru':
        parts = [f"✅ Привет, {user.full_name}! Ваша подписка успешно продлена на {days} дней!"]
        if amount_from_balance > 0:
            parts.append(f"\nС вашего бонусного баланса списано {amount_from_balance:.2f}₽.")
        if amount_paid_via_yookassa > 0:
            parts.append(f"С вашего способа оплаты списано {amount_paid_via_yookassa:.2f}₽.")
        text = "\n".join(parts) + f"\nСпасибо, что остаетесь с нами! 🌟{lte_line}"
        button = await webapp_inline_button("Личный кабинет")
    else:
        parts = [f"✅ Hi {user.full_name}! Your subscription has been renewed for {days} days!"]
        if amount_from_balance > 0:
            parts.append(f"\n{amount_from_balance:.2f} RUB has been deducted from your bonus balance.")
        if amount_paid_via_yookassa > 0:
            parts.append(f"{amount_paid_via_yookassa:.2f} RUB has been charged to your payment method.")
        text = "\n".join(parts) + f"\nThank you for staying with us! 🌟{lte_line}"
        button = await webapp_inline_button("Dashboard")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление об успешном продлении (Yookassa) успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об успешном продлении (Yookassa) для {user.id}: {e}")


async def notify_family_purchase_success_yookassa(
    user,
    days: int,
    amount_paid_via_yookassa: float,
    amount_from_balance: float,
):
    """
    Уведомляет пользователя об успешной покупке семейной подписки через Yookassa/баланс.
    """
    lang = get_user_locale(user)
    logger.info(f"Отправка уведомления о семейной подписке пользователю {user.id}")
    if lang == 'ru':
        parts = [
            f"🎉 Привет, {user.full_name}! Теперь вам доступна семейная подписка на {days} дней.",
            "Можно добавить до 10 человек в семью.",
            "Скорее пригласите первых близких.",
        ]
        if amount_from_balance > 0:
            parts.append(f"\nС вашего бонусного баланса списано {amount_from_balance:.2f}₽.")
        if amount_paid_via_yookassa > 0:
            parts.append(f"С вашего способа оплаты списано {amount_paid_via_yookassa:.2f}₽.")
        text = "\n".join(parts)
        button = await webapp_inline_button("Открыть раздел семьи", "/subscription/family")
    else:
        parts = [
            f"🎉 Hi {user.full_name}! Your family subscription is now active for {days} days.",
            "You can add up to 10 people to your family plan.",
            "Invite your first close ones now.",
        ]
        if amount_from_balance > 0:
            parts.append(f"\n{amount_from_balance:.2f} RUB has been deducted from your bonus balance.")
        if amount_paid_via_yookassa > 0:
            parts.append(f"{amount_paid_via_yookassa:.2f} RUB has been charged to your payment method.")
        text = "\n".join(parts)
        button = await webapp_inline_button("Open family section", "/subscription/family")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о семейной подписке успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о семейной подписке для {user.id}: {e}")


async def notify_payment_canceled_yookassa(user, reason: str | None = None):
    """
    Уведомляет пользователя об отмене/неуспешном завершении оплаты через YooKassa (ручной платеж).
    """
    lang = get_user_locale(user)
    why = (reason or "").strip()
    reason_line = f"\n\nПричина: {why}" if (lang == "ru" and why) else (f"\n\nReason: {why}" if why else "")
    if lang == "ru":
        text = (
            f"⚠️ Привет, {user.full_name}! Оплата не была завершена или была отменена."
            f"{reason_line}\n\n"
            "Если это ошибка — попробуйте ещё раз в личном кабинете TVPN или обратитесь в поддержку."
        )
        button = await webapp_inline_button("💳 Перейти в личный кабинет")
    else:
        text = (
            f"⚠️ Hi {user.full_name}! The payment was not completed or was canceled."
            f"{reason_line}\n\n"
            "If this looks wrong, try again in your TVPN dashboard or contact support."
        )
        button = await webapp_inline_button("💳 Open dashboard")
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
        logger.error(f"Ошибка отправки уведомления об отмене оплаты (YooKassa) для {user.id}: {e}")


async def notify_subscription_cancelled_after_failures(user):
    """
    Уведомляет пользователя о том, что подписка отменена после нескольких неудачных попыток оплаты.
    """
    lang = get_user_locale(user)
    logger.warning(f"Отправка уведомления об отмене подписки из-за сбоев оплаты пользователю {user.id}")
    if lang == 'ru':
        text = (
            "❗️ Не удалось продлить вашу подписку.\n\n"
            "Мы несколько раз пытались списать средства, но не получилось. Автопродление отключено.\n\n"
            "Вы можете продлить текущий тариф вручную в разделе «Подписка»."
        )
        button = await webapp_inline_button("💳 Продлить вручную", "/subscription")
    else:
        text = (
            "❗️ We couldn't renew your subscription.\n\n"
            "We tried to charge your payment method several times without success. Auto-renewal has been disabled.\n\n"
            "You can manually renew your current plan in the Subscription section."
        )
        button = await webapp_inline_button("💳 Renew manually", "/subscription")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление об отмене подписки успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об отмене подписки для {user.id}: {e}") 
