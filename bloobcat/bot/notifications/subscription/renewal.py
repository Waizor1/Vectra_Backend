from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale

logger = get_logger("notifications.subscription.renewal")

async def notify_auto_renewal_success_balance(user, days: int, amount: float):
    """
    Уведомляет пользователя об успешном автопродлении подписки с баланса.
    """
    # Локализация уведомления об успешном автопродлении с баланса
    lang = get_user_locale(user)
    logger.info(f"Отправка уведомления об успешном автопродлении с баланса пользователю {user.id}")
    if lang == 'ru':
        text = f"✅ Ваша подписка успешно продлена на {days} дней!\n\nС вашего реферального баланса было списано {amount:.2f} руб."
        button = await webapp_inline_button("Личный кабинет")
    else:
        text = f"✅ Your subscription has been renewed for {days} days!\n\n{amount:.2f} RUB has been deducted from your referral balance."
        button = await webapp_inline_button("Dashboard")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об успешном автопродлении с баланса для {user.id}: {str(e)}")


async def notify_auto_renewal_failure(user, reason: str = "Неизвестная ошибка", will_retry: bool = True):
    """
    Уведомляет пользователя о неудаче автоматического продления подписки.
    """
    lang = get_user_locale(user)
    logger.warning(f"Отправка уведомления о НЕУДАЧНОМ автопродлении пользователю {user.id}. Причина: {reason}")
    retry_text = "\n\nМы попробуем повторить попытку завтра автоматически." if will_retry and lang == 'ru' else "\n\nWe will try again automatically tomorrow." if will_retry and lang != 'ru' else ""
    if lang == 'ru':
        text = (
            f"⚠️ Не удалось автоматически продлить вашу подписку.\n\n"
            f"Причина: {reason}{retry_text}\n\n"
            f"Пожалуйста, продлите подписку вручную в личном кабинете или обратитесь в поддержку."
        )
        button = await webapp_inline_button("💳 Продлить вручную")
    else:
        text = (
            f"⚠️ Auto-renewal failed.\n\n"
            f"Reason: {reason}{retry_text}\n\n"
            f"Please renew your subscription manually in your dashboard or contact support."
        )
        button = await webapp_inline_button("💳 Renew manually")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о неудачном автопродлении для {user.id}: {str(e)}")


async def notify_renewal_success_yookassa(user, days: int, amount_paid_via_yookassa: float, amount_from_balance: float):
    """
    Уведомляет пользователя об успешном продлении подписки через Yookassa
    (включая возможное частичное списание с баланса).
    """
    # Локализация уведомления об успешном продлении через Yookassa
    lang = get_user_locale(user)
    logger.info(f"Отправка уведомления об успешном продлении (Yookassa) пользователю {user.id}")
    if lang == 'ru':
        parts = [f"✅ Ваша подписка успешно продлена на {days} дней!"]
        if amount_from_balance > 0:
            parts.append(f"\nС вашего реферального баланса было списано {amount_from_balance:.2f} руб.")
        if amount_paid_via_yookassa > 0:
            parts.append(f"С привязанного способа оплаты списано {amount_paid_via_yookassa:.2f} руб.")
        text = "\n".join(parts)
        button = await webapp_inline_button("Личный кабинет")
    else:
        parts = [f"✅ Your subscription has been renewed for {days} days!"]
        if amount_from_balance > 0:
            parts.append(f"\n{amount_from_balance:.2f} RUB has been deducted from your referral balance.")
        if amount_paid_via_yookassa > 0:
            parts.append(f"{amount_paid_via_yookassa:.2f} RUB has been charged to your saved payment method.")
        text = "\n".join(parts)
        button = await webapp_inline_button("Dashboard")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об успешном продлении (Yookassa) для {user.id}: {str(e)}") 