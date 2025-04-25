from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger

logger = get_logger("notifications.subscription.renewal")

async def notify_auto_renewal_success_balance(user, days: int, amount: float):
    """
    Уведомляет пользователя об успешном автопродлении подписки с баланса.
    """
    logger.info(f"Отправка уведомления об успешном автопродлении с баланса пользователю {user.id}")
    text = f"""✅ Ваша подписка успешно продлена на {days} дней!

С вашего реферального баланса было списано {amount:.2f} руб."""
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Личный кабинет"),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об успешном автопродлении с баланса для {user.id}: {e}")


async def notify_auto_renewal_failure(user, reason: str = "Неизвестная ошибка"):
    """
    Уведомляет пользователя о неудаче автоматического продления подписки.
    """
    logger.warning(f"Отправка уведомления о НЕУДАЧНОМ автопродлении пользователю {user.id}. Причина: {reason}")
    text = f"""⚠️ Не удалось автоматически продлить вашу подписку.

Причина: {reason}

Пожалуйста, продлите подписку вручную в личном кабинете или обратитесь в поддержку.

Ваш текущий статус автопродления был отключен."""
    kb = await webapp_inline_button("💳 Продлить вручную")
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=kb,
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о неудачном автопродлении для {user.id}: {e}")


async def notify_renewal_success_yookassa(user, days: int, amount_paid_via_yookassa: float, amount_from_balance: float):
    """
    Уведомляет пользователя об успешном продлении подписки через Yookassa
    (включая возможное частичное списание с баланса).
    """
    logger.info(f"Отправка уведомления об успешном продлении (Yookassa) пользователю {user.id}")
    message_parts = [f"✅ Ваша подписка успешно продлена на {days} дней!"]
    if amount_from_balance > 0:
        message_parts.append(f"\nС вашего реферального баланса было списано {amount_from_balance:.2f} руб.")
    if amount_paid_via_yookassa > 0:
        message_parts.append(f"С привязанного способа оплаты списано {amount_paid_via_yookassa:.2f} руб.")
    text = "\n".join(message_parts)
    try:
        await bot.send_message(
            user.id,
            text,
            reply_markup=await webapp_inline_button("Личный кабинет"),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об успешном продлении (Yookassa) для {user.id}: {e}") 