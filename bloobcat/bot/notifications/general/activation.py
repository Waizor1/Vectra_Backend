from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import handle_telegram_forbidden_error, handle_telegram_bad_request, reset_user_failed_count

logger = get_logger("notifications.general.activation")

async def on_activated_key(user: Users):
    lang = get_user_locale(user)
    # Выбор текста в зависимости от языка
    if lang == 'ru':
        text = (
            f"{user.full_name}, ключ VPN активирован.\n\n"
            "Теперь управление устройствами доступно в приложении."
        )
        button = await webapp_inline_button("Список устройств", "/connect")
    else:
        text = (
            f"{user.full_name}, your VPN key is active.\n\n"
            "Device management is now available in the app."
        )
        button = await webapp_inline_button("Device list", "/connect")
    logger.info(f"Отправка уведомления об активации ключа пользователю {user.id}")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление об активации ключа успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об активации ключа пользователю {user.id}: {e}")
