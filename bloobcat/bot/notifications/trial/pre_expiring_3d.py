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
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings

logger = get_logger("notifications.trial.pre_expiring_3d")


async def _get_devices_count(user) -> int:
    """Fetch user's HWID devices count from RemnaWave safely."""
    if not getattr(user, "remnawave_uuid", None):
        return 0
    client = None
    try:
        client = RemnaWaveClient(
            remnawave_settings.url,
            remnawave_settings.token.get_secret_value(),
        )
        raw_resp = await client.users.get_user_hwid_devices(str(user.remnawave_uuid))
        devices_list = []
        if isinstance(raw_resp, list):
            devices_list = raw_resp
        elif isinstance(raw_resp, dict):
            resp = raw_resp.get("response")
            if isinstance(resp, list):
                devices_list = resp
            elif isinstance(resp, dict) and isinstance(resp.get("devices"), list):
                devices_list = resp.get("devices")
        return len(devices_list)
    except Exception as e:
        logger.error(f"Ошибка получения списка HWID устройств для пользователя {user.id}: {e}")
        return 0
    finally:
        if client:
            try:
                await client.close()
            except Exception:
                pass


async def notify_trial_three_days_left(user) -> bool:
    """
    Legacy function name preserved for compatibility.
    Sends the updated marketing reminder template.
    """
    locale = get_user_locale(user)
    devices_count = await _get_devices_count(user)

    if locale == "ru":
        text = (
            f"{user.full_name}, до окончания пробного периода: <b>1 день</b>\n\n"
            f"Активные устройства: <b>{devices_count}</b>\n\n"
            "Доступные варианты:\n\n"
            "• 1 месяц — 199 ₽\n"
            "• 3 месяца — 150 ₽ / мес\n"
            "• 6 месяцев — 125 ₽ / мес\n"
            "• 12 месяцев — 108 ₽ / мес\n\n"
            "<i>Стоимость указана за 1 устройство. Дополнительные устройства считаются в приложении со скидкой.</i>"
        )
        button_text = "Выбрать тариф"
        button_url = "/pay"
    else:
        text = (
            f"{user.full_name}, trial ends in <b>1 day</b>\n\n"
            f"Active devices: <b>{devices_count}</b>\n\n"
            "Available plans:\n\n"
            "• 1 month — 199 RUB\n"
            "• 3 months — 150 RUB / month\n"
            "• 6 months — 125 RUB / month\n"
            "• 12 months — 108 RUB / month\n\n"
            "<i>Price is for 1 device. Extra devices are calculated in the app with a discount.</i>"
        )
        button_text = "Choose plan"
        button_url = "/pay"
    text = append_rescue_link(text, lang=locale)

    try:
        keyboard = await webapp_inline_button(button_text, button_url)
    except Exception:
        keyboard = None

    try:
        await bot.send_message(user.id, text, reply_markup=keyboard, parse_mode="HTML")
        logger.info(f"Marketing trial reminder sent to user {user.id}")
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
        logger.error(f"Ошибка при отправке маркетингового напоминания о триале пользователю {user.id}: {e}")
        return False
