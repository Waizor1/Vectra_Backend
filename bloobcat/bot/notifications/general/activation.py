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
        text1 = "Включайте, отключайте, меняйте страну VPN в приложении которое Вы установили"
        text2 = (
            "Не забудьте вернуться в бот чтобы проверить свой личный кабинет 🎁\n"
            "Управление ключами VPN происходит в боте. Рекомендуем закрепить бот, чтобы не потерять.\n"
            "Мы на связи, BlubCat VPN 🤙"
        )
        button = await webapp_inline_button()
    else:
        text1 = "Turn the VPN on and off and change the server location in the app you installed"
        text2 = (
            "Don't forget to go back to the bot to check your dashboard 🎁\n"
            "Manage your VPN keys in the bot. We recommend pinning it for easy access.\n"
            "We're here for you, BlubCat VPN 🤙"
        )
        button = await webapp_inline_button("Dashboard")
    # Отправка уведомлений
    logger.info(f"Отправка уведомления об активации ключа пользователю {user.id}")
    await bot.send_message(user.id, text1)
    await sleep(5)
    logger.info(f"Отправка второго уведомления об активации ключа пользователю {user.id}")
    await bot.send_message(user.id, text2, reply_markup=button) 