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
    bonus_days: int,
    friend_bonus_days: int,
    months: int,
    device_count: int,
    applied_to_subscription: bool,
):
    """Notify the referrer about the friend's first payment.

    IMPORTANT: In TVPN Mini App the referral program is days-based (not money-based).
    """
    lang = get_user_locale(user)
    if lang == 'ru':
        logger.info(
            f"Реферальные дни: пользователь {user.id} получил +{bonus_days} дней "
            f"за оплату реферала {referral.id} (months={months}, devices={device_count}, amount={amount})"
        )
        applied_line = (
            "Бонусные дни начислены в копилку (семейная подписка не продлевается бонусами)."
            if not applied_to_subscription
            else "Бонусные дни добавлены к вашей подписке."
        )
        text = (
            f"🎉 Привет, {user.full_name}! Ваш друг {referral.name()} оплатил подписку.\n"
            f"Вы получили +{bonus_days} дней (за покупку {months} мес.).\n"
            f"Друг получил +{friend_bonus_days} дней при первой оплате.\n"
            f"{applied_line}"
        )
        button = await webapp_inline_button("Личный кабинет")
    else:
        logger.info(
            f"Referral days: user {user.id} got +{bonus_days} days "
            f"for referral {referral.id} (months={months}, devices={device_count}, amount={amount})"
        )
        applied_line = (
            "Bonus days are stored (family subscription is not extended by bonuses)."
            if not applied_to_subscription
            else "Bonus days were added to your subscription."
        )
        text = (
            f"🎉 Hi {user.full_name}! Your friend {referral.name()} just paid for a subscription.\n"
            f"You got +{bonus_days} days (purchase: {months} month(s)).\n"
            f"Your friend got +{friend_bonus_days} days on the first payment.\n"
            f"{applied_line}"
        )
        button = await webapp_inline_button("Dashboard")
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

async def on_referral_registration(user: Users, referral: Users):
    lang = get_user_locale(user)
    if lang == 'ru':
        logger.info(f"Реферальная регистрация: у пользователя {user.id} зарегистрировался реферал {referral.id}")
        text = (
            f"🎉 Привет, {user.full_name}! Ваш реферал {referral.name()} только что зарегистрировался.\n"
            "Бонусные дни начисляются после первой оплаты друга. Спасибо, что рекомендуете нас! 🎊"
        )
        button = await webapp_inline_button("Реферальная программа", "/ref")
    else:
        logger.info(f"Referral registration: user {user.id} got a new referral signup {referral.id}")
        text = (
            f"🎉 Hi {user.full_name}! Your referral {referral.name()} just signed up.\n"
            "Bonus days are credited after your friend's first payment. Thanks for spreading the word! 🎊"
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
    """Уведомление для пользователей, чтобы пригласить друга и получить бонусы"""
    lang = get_user_locale(user)
    if lang == 'ru':
        text = (
            f"🎉 Привет, {user.full_name}! Уже {days} дней вместе.\n"
            "Пригласите друга — и получите бонусные дни к подписке.\n"
            "Друг тоже получит +7 дней при первой оплате."
        )
        button = await webapp_inline_button("Реферальная программа", "/ref")
    else:
        text = (
            f"🎉 Hi {user.full_name}! You've been with us for {days} days.\n"
            "Invite a friend and get bonus subscription days.\n"
            "Your friend also gets +7 days on the first payment."
        )
        button = await webapp_inline_button("Referral Program", "/ref")
    logger.info(f"Отправка реферального напоминания пользователю {user.id} ({days} дней)")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Реферальное напоминание успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке реферального напоминания пользователю {user.id}: {e}") 