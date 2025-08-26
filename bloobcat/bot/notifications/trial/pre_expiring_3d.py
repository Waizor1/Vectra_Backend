import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bloobcat.bot.bot import bot
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.error_handler import handle_telegram_forbidden_error, handle_telegram_bad_request, reset_user_failed_count
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings

logger = get_logger("notifications.trial.pre_expiring_3d")


async def _get_devices_count(user) -> int:
    """Fetch user's HWID devices count from RemnaWave safely."""
    if not getattr(user, "remnawave_uuid", None):
        return 0
    client = None
    try:
        client = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
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


def _plural_ru(n: int, forms: tuple[str, str, str]) -> str:
    n_abs = abs(n)
    rem10 = n_abs % 10
    rem100 = n_abs % 100
    if rem10 == 1 and rem100 != 11:
        return forms[0]
    if 2 <= rem10 <= 4 and not (12 <= rem100 <= 14):
        return forms[1]
    return forms[2]


def _format_tenure_ru(total_days: int) -> str:
    if total_days >= 365:
        years = total_days // 365
        return f"{years} {_plural_ru(years, ("год", "года", "лет"))}"
    if total_days >= 30:
        months = total_days // 30
        return f"{months} {_plural_ru(months, ("месяц", "месяца", "месяцев"))}"
    if total_days >= 7:
        weeks = total_days // 7
        return f"{weeks} {_plural_ru(weeks, ("неделя", "недели", "недель"))}"
    days = max(total_days, 0)
    return f"{days} {_plural_ru(days, ("день", "дня", "дней"))}"


def _format_tenure_en(total_days: int) -> str:
    if total_days >= 365:
        years = total_days // 365
        return f"{years} year{'s' if years != 1 else ''}"
    if total_days >= 30:
        months = total_days // 30
        return f"{months} month{'s' if months != 1 else ''}"
    if total_days >= 7:
        weeks = total_days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''}"
    days = max(total_days, 0)
    return f"{days} day{'s' if days != 1 else ''}"


async def notify_trial_three_days_left(user):
    """
    Sends a marketing reminder 3 days before trial ends with device count included.
    """
    locale = get_user_locale(user)
    moscow_tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(moscow_tz)
    # Вычисляем срок с нами по registration_date/created_at
    reg_dt = getattr(user, "registration_date", None) or getattr(user, "created_at", None)
    try:
        tenure_days = (now.date() - reg_dt.date()).days if reg_dt else 0
    except Exception:
        tenure_days = 0
    devices_count = await _get_devices_count(user)

    if locale == "ru":
        tenure_text = _format_tenure_ru(tenure_days)
        text = (
            f"<b>Спасибо, что вы с нами уже {tenure_text}!</b><br>"
            "Рады, что BloopCat был вам полезен. Хотим, чтобы так оставалось и дальше — ещё и выгоднее для вас.<br><br>"
            "\n⏳ До окончания триала: <b>3 дня</b><br>"
            f"📱 Вы использовали: <b>{devices_count}</b> устройство(а)<br><br>"
            "\n<b>Рассказываем как продлить и платить меньше</b>:<br>"
            "• <b>Зайдите в \"Подписку\" и выберите 2 устройства — выйдет всего 71 ₽/мес</b><br>"
            "• <b>Если нужно только 1 устройство — 75 ₽/мес</b><br>"
            "• <b>Хотите выгоднее? Соберите семью до 10 устройств — всего 49 ₽/мес за устройство</b><br><br>"
            "<i>(цены указаны при оплате за год — так намного выгоднее)</i><br><br>"
            "Если не уверены, что выбрать, или нужны спецусловия — напишите нам в "
            "<a href=\"https://t.me/BloopCat\">@BloopCat</a>. Мы рядом, подскажем и даже поможем с настройкой."
        )
        button_text = "Открыть приложение"
        button_url = "/pay"
    else:
        tenure_text = _format_tenure_en(tenure_days)
        text = (
            f"<b>Thanks for being with us for {tenure_text}!</b><br>"
            "We hope BloopCat has been useful. Keep it going — even more affordably for you.<br><br>"
            "\n⏳ Trial ends in: <b>3 days</b><br>"
            f"📱 Devices used: <b>{devices_count}</b><br><br>"
            "\n<b>How to renew and pay less</b>:<br>"
            "• <b>Select 2 devices in \"Subscription\" — just 71 RUB/mo</b><br>"
            "• <b>Only 1 device needed — 75 RUB/mo</b><br>"
            "• <b>Want the best deal? Family up to 10 devices — 49 RUB/mo per device</b><br><br>"
            "<i>(prices shown for annual billing)</i><br><br>"
            "If you’re not sure what to choose or need special terms — text "
            "<a href=\"https://t.me/BloopCat\">@BloopCat</a>. We’ll help and even assist with setup."
        )
        button_text = "Open App"
        button_url = "/pay"

    try:
        keyboard = await webapp_inline_button(button_text, button_url)
    except Exception:
        keyboard = None

    try:
        await bot.send_message(user.id, text, reply_markup=keyboard, parse_mode="HTML")
        logger.info(f"3-day trial reminder sent to user {user.id}")
        await reset_user_failed_count(user.id)
    except TelegramForbiddenError as e:
        logger.warning(f"User {user.id} blocked the bot: {e}")
        await handle_telegram_forbidden_error(user.id, e)
    except TelegramBadRequest as e:
        logger.error(f"Bad request error for user {user.id}: {e}")
        await handle_telegram_bad_request(user.id, e)
    except Exception as e:
        logger.error(f"Ошибка при отправке 3-дневного напоминания о триале пользователю {user.id}: {e}")


