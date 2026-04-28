from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import handle_telegram_forbidden_error, handle_telegram_bad_request, reset_user_failed_count

logger = get_logger("notifications.general.referral")

async def on_referral_payment(
    *,
    user: Users,
    referral: Users,
    amount: int,
    bonus_days: int = 0,
    friend_bonus_days: int = 0,
    months: int = 0,
    device_count: int = 1,
    applied_to_subscription: bool = False,
    cashback_rub: int = 0,
    cashback_percent: int = 0,
    level_name: str | None = None,
    level_up_name: str | None = None,
):
    """Notify the referrer about ordinary referral cashback.

    Ordinary users now earn internal service balance. Partner withdrawals and
    PartnerEarnings remain in the separate partner notification path.
    """
    lang = get_user_locale(user)
    if lang == 'ru':
        logger.info(
            f"Реферальный кэшбек: пользователь {user.id} получил +{cashback_rub} ₽ "
            f"за оплату реферала {referral.id} (percent={cashback_percent}, level={level_name}, amount={amount})"
        )
        level_line = (
            f"Текущий уровень: {level_name}, кэшбек {cashback_percent}%."
            if level_name and cashback_percent
            else "Текущий уровень обновится в приложении."
        )
        first_payment_line = (
            f"Друг получил +{friend_bonus_days} дней при первой оплате.\n"
            if int(friend_bonus_days or 0) > 0
            else ""
        )
        level_up_line = (
            f"\nНовый уровень: {level_up_name}. Открой сундук в приложении."
            if level_up_name
            else ""
        )
        text = (
            f"{user.full_name}, друг оплатил — тебе начислено +{int(cashback_rub or 0)} ₽ кэшбэка.\n"
            f"{level_line}\n"
            f"{first_payment_line}"
            f"Кэшбек зачислен на внутренний баланс Vectra."
            f"{level_up_line}"
        )
        button = await webapp_inline_button("Открыть рефералку")
    else:
        logger.info(
            f"Referral cashback: user {user.id} got +{cashback_rub} RUB "
            f"for referral {referral.id} (percent={cashback_percent}, level={level_name}, amount={amount})"
        )
        level_line = (
            f"Current level: {level_name}, cashback {cashback_percent}%."
            if level_name and cashback_percent
            else "Your referral level will update in the app."
        )
        first_payment_line = (
            f"Your friend got +{friend_bonus_days} days on the first payment.\n"
            if int(friend_bonus_days or 0) > 0
            else ""
        )
        level_up_line = (
            f"\nNew level: {level_up_name}. Open the chest in the app."
            if level_up_name
            else ""
        )
        text = (
            f"{user.full_name}, your friend paid — you got +{int(cashback_rub or 0)} RUB cashback.\n"
            f"{level_line}\n"
            f"{first_payment_line}"
            f"Cashback was added to your internal Vectra balance."
            f"{level_up_line}"
        )
        button = await webapp_inline_button("Open referrals")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о реферальном бонусе успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о реферальном бонусе пользователю {user.id}: {e}")

async def on_referral_friend_bonus(
    *,
    user: Users,
    referrer: Users,
    friend_bonus_days: int,
    months: int,
    device_count: int,
):
    """Notify the referred user about +days on their first payment via referral."""
    lang = get_user_locale(user)
    if lang == "ru":
        text = (
            f"{user.full_name}! Спасибо за оплату.\n"
            f"Вам начислено +{friend_bonus_days} бонусных дней за переход по реферальной ссылке.\n"
            f"Реферер: {referrer.name()}.\n"
            f"Покупка: {months} мес., устройств: {device_count}."
        )
        button = await webapp_inline_button("Личный кабинет")
    else:
        text = (
            f"Hi {user.full_name}! Thanks for your payment.\n"
            f"You got +{friend_bonus_days} bonus days for joining via a referral link.\n"
            f"Referrer: {referrer.name()}.\n"
            f"Purchase: {months} month(s), devices: {device_count}."
        )
        button = await webapp_inline_button("Dashboard")

    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info("Friend bonus notification sent to user=%s", user.id)
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error("Failed to send friend bonus notification to user=%s: %s", user.id, e)

async def on_referral_registration(user: Users, referral: Users):
    lang = get_user_locale(user)
    if lang == 'ru':
        logger.info(f"Реферальная регистрация: у пользователя {user.id} зарегистрировался реферал {referral.id}")
        text = (
            f"{user.full_name}, новый реферал зарегистрирован.\n\n"
            "Бонусы начисляются после первой оплаты друга."
        )
        button = await webapp_inline_button("Реферальная программа", "/ref")
    else:
        logger.info(f"Referral registration: user {user.id} got a new referral signup {referral.id}")
        text = (
            f"Hi {user.full_name}! Your referral {referral.name()} just signed up.\n"
            "Bonus days are credited after your friend's first payment. Thanks for spreading the word!"
        )
        button = await webapp_inline_button("Реферальная программа", "/ref")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о реферальной регистрации успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о реферальной регистрации пользователю {user.id}: {e}")

async def on_referral_prompt(user: Users, days: int):
    """Уведомление отключено: реферальные напоминания больше не используются."""
    logger.info(
        "Реферальные напоминания отключены. user=%s, days=%s",
        user.id,
        days,
    )
