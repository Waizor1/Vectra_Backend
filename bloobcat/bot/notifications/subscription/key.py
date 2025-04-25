from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("notifications.subscription.key")

async def on_disabled(user: Users):
    """Уведомление об истечении ключа"""
    logger.info(f"Отправка уведомления об истечении ключа пользователю {user.id}")
    await bot.send_message(
        user.id,
        "😢Ваш ключ истек. Пожалуйста, продлите подписку в личном кабинете",
        reply_markup=await webapp_inline_button("Продлить подписку", "pay"),
    ) 