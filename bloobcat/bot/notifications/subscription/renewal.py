from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import handle_telegram_forbidden_error, handle_telegram_bad_request, reset_user_failed_count

logger = get_logger("notifications.subscription.renewal")

async def notify_auto_renewal_success_balance(user, days: int, amount: float):
    """
    Уведомляет пользователя об успешном автопродлении подписки с баланса.
    """
    # Локализация уведомления об успешном автопродлении с баланса
    lang = get_user_locale(user)
    logger.info(f"Отправка уведомления об успешном автопродлении с баланса пользователю {user.id}")
    if lang == 'ru':
        text = (
            f"🎉 Привет, {user.full_name}! Ваша подписка автоматически продлена на {days} дней! 💪\n"
            f"С вашего бонусного баланса списано {amount:.2f}₽.\n"
            "Спасибо, что остаетесь с нами! 🌟"
        )
        button = await webapp_inline_button("Личный кабинет")
    else:
        text = (
            f"🎉 Hi {user.full_name}! Your subscription was auto-renewed for {days} days! 💪\n"
            f"{amount:.2f} RUB has been deducted from your bonus balance.\n"
            "Thank you for staying with us! 🌟"
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
            "Продлите вручную в личном кабинете или обратитесь в поддержку."
        )
        button = await webapp_inline_button("💳 Продлить вручную")
    else:
        text = (
            f"⚠️ Hi {user.full_name}! Auto-renewal failed.\n\n"
            f"Reason: {reason}{retry_text}\n"
            "Please renew manually in your dashboard or contact support."
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
    if lang == 'ru':
        parts = [f"✅ Привет, {user.full_name}! Ваша подписка успешно продлена на {days} дней!"]
        if amount_from_balance > 0:
            parts.append(f"\nС вашего бонусного баланса списано {amount_from_balance:.2f}₽.")
        if amount_paid_via_yookassa > 0:
            parts.append(f"С привязанного способа оплаты списано {amount_paid_via_yookassa:.2f}₽.")
        text = "\n".join(parts) + "\nСпасибо, что остаетесь с нами! 🌟"
        button = await webapp_inline_button("Личный кабинет")
    else:
        parts = [f"✅ Hi {user.full_name}! Your subscription has been renewed for {days} days!"]
        if amount_from_balance > 0:
            parts.append(f"\n{amount_from_balance:.2f} RUB has been deducted from your bonus balance.")
        if amount_paid_via_yookassa > 0:
            parts.append(f"{amount_paid_via_yookassa:.2f} RUB has been charged to your saved payment method.")
        text = "\n".join(parts) + "\nThank you for staying with us! 🌟"
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


async def notify_subscription_cancelled_after_failures(user):
    """
    Уведомляет пользователя о том, что подписка отменена после нескольких неудачных попыток оплаты.
    """
    lang = get_user_locale(user)
    logger.warning(f"Отправка уведомления об отмене подписки из-за сбоев оплаты пользователю {user.id}")
    if lang == 'ru':
        text = (
            "❗️ Не удалось продлить вашу подписку.\n\n"
            "Мы несколько раз пытались списать средства, но не получилось. Ваша подписка отменена.\n\n"
            "Чтобы возобновить доступ, пожалуйста, выберите и оплатите тариф заново."
        )
        button = await webapp_inline_button("🛒 Выбрать тариф")
    else:
        text = (
            "❗️ We couldn't renew your subscription.\n\n"
            "We tried to charge your payment method several times without success. Your subscription has been cancelled.\n\n"
            "To regain access, please choose and pay for a new plan."
        )
        button = await webapp_inline_button("🛒 Choose a plan")
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