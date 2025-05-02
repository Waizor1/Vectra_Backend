from datetime import datetime, time
from zoneinfo import ZoneInfo
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale

logger = get_logger("notifications.trial.expiring")

async def notify_expiring_trial(user):
    """
    Уведомляет пользователя о скором истечении пробного периода
    """
    # Локализация
    lang = get_user_locale(user)
    logger.info(f"Подготовка уведомления о скором истечении триала для пользователя {user.id}")
    
    # Вычисляем время до окончания триала
    moscow_tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(moscow_tz)
    expire_dt = datetime.combine(user.expired_at, time(0, 0), tzinfo=moscow_tz)
    logger.info(f"Отправка уведомления о скором истечении триала пользователю {user.id}, дата истечения: {user.expired_at}")
    
    # Формируем текст уведомления
    if lang == 'ru':
        text = (
            f"⚠️ Привет, {user.full_name}! Ваш пробный период BlubCat VPN истекает сегодня ночью (в 00:00). \n"
            "Не хотите терять доступ к быстрому и безопасному VPN? Оформите подписку прямо сейчас! 🔒\n"
            "Возникли вопросы? Обратитесь в поддержку @BlubCatVPN_support"
        )
        button = await webapp_inline_button("Оформить подписку", "pay")
    else:
        text = (
            f"⚠️ Hi {user.full_name}! Your BlubCat VPN trial period will expire tonight (at 00:00). \n"
            "Don't want to lose access to fast and secure VPN? Get a subscription now! 🔒\n"
            "Questions? Contact support @BlubCatVPN_support"
        )
        button = await webapp_inline_button("Get Subscription", "pay")
    
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о скором истечении триала успешно отправлено пользователю {user.id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о скором истечении триала пользователю {user.id}: {e}") 