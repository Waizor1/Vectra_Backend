from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale

logger = get_logger("notifications.subscription.key")

async def on_disabled(user: Users):
    """Уведомление об истечении подписки (ключа)"""
    lang = get_user_locale(user)
    # Выбор текста и кнопки в зависимости от языка
    if lang == 'ru':
        text = "😢Ваш ключ истек. Пожалуйста, продлите подписку в личном кабинете"
        button = await webapp_inline_button("Продлить подписку", "pay")
    else:
        text = "😢Your key has expired. Please renew your subscription in your dashboard"
        button = await webapp_inline_button("Renew subscription", "pay")
    logger.info(f"Отправка уведомления об истечении ключа пользователю {user.id}")
    await bot.send_message(user.id, text, reply_markup=button) 