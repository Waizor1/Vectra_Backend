from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.notifications.rescue_link import append_rescue_link
from bloobcat.bot.error_handler import (
    handle_telegram_forbidden_error,
    handle_telegram_bad_request,
    reset_user_failed_count,
)

logger = get_logger("notifications.trial.expiring")


async def notify_expiring_trial(user) -> bool:
    """
    Уведомляет пользователя о скором истечении пробного периода
    """
    # Локализация
    lang = get_user_locale(user)
    logger.info(
        f"Подготовка уведомления о скором истечении триала для пользователя {user.id}"
    )

    # Вычисляем время до окончания триала
    moscow_tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(moscow_tz)
    logger.info(
        f"Отправка уведомления о скором истечении триала пользователю {user.id}, дата истечения: {user.expired_at}"
    )

    # Формируем текст уведомления
    if lang == "ru":
        text = (
            f"⚠️ 👤 {user.full_name}, пробный доступ закончится сегодня ночью.\n\n"
            "Чтобы сохранить подключение, оформите подписку."
        )
        button = await webapp_inline_button("Продлить доступ", "/pay")
    else:
        text = (
            f"⚠️ 👤 {user.full_name}, your Vectra Connect trial period will end tonight.\n"
            "Don't want to lose access to fast and secure VPN? Get a subscription now!\n"
            "Questions? Contact Vectra Connect support."
        )
        button = await webapp_inline_button("Get Subscription", "/pay")
    text = append_rescue_link(text, lang=lang)

    try:
        await bot.send_message(user.id, text, reply_markup=button)
        logger.info(
            f"Уведомление о скором истечении триала успешно отправлено пользователю {user.id}"
        )
        await reset_user_failed_count(user.id)
        return True
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
        return False
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
        return False
    except Exception as e:
        logger.error(
            f"Ошибка при отправке уведомления о скором истечении триала пользователю {user.id}: {e}"
        )
        return False
