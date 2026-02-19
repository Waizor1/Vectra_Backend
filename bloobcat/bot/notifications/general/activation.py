from asyncio import sleep
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
    # Выбор текстов в зависимости от языка
    if lang == 'ru':
        text1 = f"Привет, {user.full_name}! Ваш ключ VPN активирован!\n" \
                "Теперь вы можете включать и отключать VPN одним нажатием."
        text2 = (
            "Вернитесь в бот, чтобы управлять ключами и подписками.\n"
            "Мы всегда на связи, TVPN"
        )
        button = await webapp_inline_button()
    else:
        text1 = f"Hi {user.full_name}, your VPN key is now active!\n" \
                "You can turn your VPN on and off with ease."
        text2 = (
            "Return to the bot to manage your keys and subscriptions.\n"
            "We're here for you, TVPN"
        )
        button = await webapp_inline_button("Dashboard")
    # Отправка уведомлений
    logger.info(f"Отправка уведомления об активации ключа пользователю {user.id}")
    try:
        await bot.send_message(user.id, text1)
        logger.info(f"Первое уведомление об активации ключа успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
        return  # Если пользователь заблокирован, не отправляем второе сообщение
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
        return  # Если ошибка, не отправляем второе сообщение
    except Exception as e:
        logger.error(f"Ошибка при отправке первого уведомления об активации ключа пользователю {user.id}: {e}")
        return  # При любой ошибке не отправляем второе сообщение
    await sleep(5)
    logger.info(f"Отправка второго уведомления об активации ключа пользователю {user.id}")
    try:
        await bot.send_message(user.id, text2, reply_markup=button)
        logger.info(f"Второе уведомление об активации ключа успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке второго уведомления об активации ключа пользователю {user.id}: {e}") 