from asyncio import sleep
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("notifications.general.activation")

async def on_activated_key(user: Users):
    logger.info(f"Отправка уведомления об активации ключа пользователю {user.id}")
    await bot.send_message(
        user.id,
        "Включайте, отключайте, меняйте страну VPN в приложении которое Вы установили",
    )
    await sleep(5)
    logger.info(f"Отправка второго уведомления об активации ключа пользователю {user.id}")
    await bot.send_message(
        user.id,
        "Не забудьте вернуться в бот чтобы проверить свой личный кабинет 🎁\nУправление ключами VPN происходит в боте. Рекомендуем закрепить бот, чтобы не потерять.\nМы на связи, BlubCat VPN 🤙",
        reply_markup=await webapp_inline_button(),
    ) 