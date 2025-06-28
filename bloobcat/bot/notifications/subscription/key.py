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
        text = f"😢 Привет, {user.full_name}! Ваша подписка истекла. Продлите её прямо сейчас, чтобы не прерывать доступ к VPN 🌐🔒"
        button = await webapp_inline_button("Продлить сейчас", "/pay")
    else:
        text = f"😢 Hi {user.full_name}, your subscription has expired. Renew now to keep your VPN active 🌐🔒"
        button = await webapp_inline_button("Renew Now", "/pay")
    logger.info(f"Отправка уведомления об истечении ключа пользователю {user.id}")
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление об истечении подписки успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления об истечении подписки пользователю {user.id}: {e}") 