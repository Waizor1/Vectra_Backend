from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import handle_telegram_forbidden_error, handle_telegram_bad_request, reset_user_failed_count

logger = get_logger("notifications.trial.end")

async def notify_trial_ended(user):
    """
    Уведомляет пользователя о завершении пробного периода
    """
    lang = get_user_locale(user)
    if lang == 'ru':
        text = (
            f"👋 Привет, {user.full_name}! Ваш пробный период завершен. 🎉\n"
            "Не упустите возможность продления и получите эксклюзивные условия! 🔥\n"
            "Напишите в поддержку @BloopCat_supbot или продлите прямо сейчас."
        )
        button = await webapp_inline_button("Продлить сейчас", "/pay")
    else:
        text = (
            f"👋 Hi {user.full_name}! Your trial period has ended. 🎉\n"
            "Don't miss out on exclusive offers and renew now! 🔥\n"
            "Contact support @BloopCat_supbot or renew now."
        )
        button = await webapp_inline_button("Renew Now", "/pay")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о завершении пробного периода успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о завершении пробного периода пользователю {user.id}: {e}")