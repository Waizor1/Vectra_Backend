from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.payments import ProcessedPayments
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.settings import app_settings
from bloobcat.bot.error_handler import handle_telegram_forbidden_error, handle_telegram_bad_request, reset_user_failed_count

logger = get_logger("notifications.trial.no_trial")

async def notify_no_trial_taken(user, hours_passed: int):
    lang = get_user_locale(user)
    logger.info(f"Подготовка уведомления пользователю {user.id}, не взявшему пробную подписку (прошло {hours_passed} ч.)")
    has_payments = await ProcessedPayments.filter(
        user_id=user.id,
        status="succeeded"
    ).exists()
    if has_payments:
        logger.info(f"Пользователь {user.id} имеет платежи, уведомление не отправляется")
        return
    logger.info(f"Отправка уведомления пользователю {user.id}, не взявшему пробную подписку (прошло {hours_passed} ч.)")
    
    trial_duration_days = app_settings.trial_days

    if lang == 'ru':
        text = (
            f"👋 Привет, {user.full_name}! Еще не воспользовались бесплатным доступом к VPN? 🔓\n"
            f"Активируйте {trial_duration_days}-дневный пробный период прямо сейчас и оцените все преимущества TVPN.\n"
            "Если возникнут вопросы, обратитесь в поддержку TVPN."
        )
        button = await webapp_inline_button("Подключить VPN", "/second")
    else:
        text = (
            f"👋 Hi {user.full_name}! Haven't tried our free VPN access yet? 🔓\n"
            f"Activate a {trial_duration_days}-day trial now and experience all benefits of TVPN.\n"
            "Have questions? Contact TVPN support."
        )
        button = await webapp_inline_button("Connect VPN", "/second")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о невзятой пробной подписке успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о невзятой пробной подписке пользователю {user.id}: {e}") 