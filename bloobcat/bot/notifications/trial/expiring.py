from datetime import datetime, time
from zoneinfo import ZoneInfo
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import handle_telegram_forbidden_error, handle_telegram_bad_request, reset_user_failed_count

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
            f"[!] Привет, {user.full_name}! Ваш пробный период TVPN истекает сегодня ночью (в 00:00). \n"
            "Не хотите терять доступ к быстрому и безопасному VPN? Оформите подписку прямо сейчас!\n"
            "Возникли вопросы? Обратитесь в поддержку TVPN."
        )
        button = await webapp_inline_button("Оформить подписку", "/pay")
    else:
        text = (
            f"[!] Hi {user.full_name}! Your TVPN trial period will expire tonight (at 00:00). \n"
            "Don't want to lose access to fast and secure VPN? Get a subscription now!\n"
            "Questions? Contact TVPN support."
        )
        button = await webapp_inline_button("Get Subscription", "/pay")
    
    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(f"Уведомление о скором истечении триала успешно отправлено пользователю {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о скором истечении триала пользователю {user.id}: {e}") 