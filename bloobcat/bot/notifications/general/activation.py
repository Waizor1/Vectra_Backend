from asyncio import sleep
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale

logger = get_logger("notifications.general.activation")

async def on_activated_key(user: Users):
    lang = get_user_locale(user)
    # Выбор текстов в зависимости от языка
    if lang == 'ru':
        text1 = f"🎉 Привет, {user.full_name}! Ваш ключ VPN активирован! 🔑\n" \
                "Теперь вы можете включать и отключать VPN одним нажатием."
        text2 = (
            "Вернитесь в бот, чтобы управлять ключами и подписками.\n"
            "Мы всегда на связи, BlubCat VPN 🤙"
        )
        button = await webapp_inline_button()
    else:
        text1 = f"🎉 Hi {user.full_name}, your VPN key is now active! 🔑\n" \
                "You can turn your VPN on and off with ease."
        text2 = (
            "Return to the bot to manage your keys and subscriptions.\n"
            "We're here for you, BlubCat VPN 🤙"
        )
        button = await webapp_inline_button("Dashboard")
    # Отправка уведомлений
    logger.info(f"Отправка уведомления об активации ключа пользователю {user.id}")
    try:
        await bot.send_message(user.id, text1)
        logger.info(f"Первое уведомление об активации ключа успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке первого уведомления об активации ключа пользователю {user.id}: {e}")
    await sleep(5)
    logger.info(f"Отправка второго уведомления об активации ключа пользователю {user.id}")
    try:
        await bot.send_message(user.id, text2, reply_markup=button)
        logger.info(f"Второе уведомление об активации ключа успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке второго уведомления об активации ключа пользователю {user.id}: {e}") 