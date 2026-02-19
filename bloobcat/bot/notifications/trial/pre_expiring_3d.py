from __future__ import annotations

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
from bloobcat.db.tariff import Tariffs
from bloobcat.services.discounts import get_best_discount

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
        return f"{years} {_plural_ru(years, ('год', 'года', 'лет'))}"
    if total_days >= 30:
        months = total_days // 30
        return f"{months} {_plural_ru(months, ('месяц', 'месяца', 'месяцев'))}"
    if total_days >= 7:
        weeks = total_days // 7
        return f"{weeks} {_plural_ru(weeks, ('неделя', 'недели', 'недель'))}"
    days = max(total_days, 0)
    return f"{days} {_plural_ru(days, ('день', 'дня', 'дней'))}"


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


def _apply_percent(price: int, percent: int) -> int:
    if percent <= 0:
        return max(0, int(price))
    discount_value = int(round(price * (percent / 100.0)))
    return max(0, int(price) - discount_value)


async def _get_pricing_suggestions(user):
    """
    Возвращает словарь с ценами за устройство в месяц для 1/2/10 устройств
    с учётом персональной скидки пользователя.
    """
    annual_tariff = (
        await Tariffs.filter(months__gte=12).order_by("-months").first()
        or await Tariffs.order_by("-months").first()
    )
    if not annual_tariff:
        return None

    discount_percent = 0
    best_discount = await get_best_discount(user.id)
    if best_discount:
        _, discount_percent = best_discount
        discount_percent = int(discount_percent or 0)

    months = max(1, int(annual_tariff.months or 1))
    prices = {}
    for device_count in (1, 2, 10):
        total_price = annual_tariff.calculate_price(device_count)
        if discount_percent > 0:
            total_price = _apply_percent(total_price, discount_percent)
        per_device_month = total_price / months / max(1, device_count)
        prices[device_count] = max(1, int(round(per_device_month)))

    return {
        "prices": prices,
        "months": months,
        "discount_percent": discount_percent,
        "tariff_name": annual_tariff.name,
    }


def _billing_note_ru(months: int | None, discount_percent: int) -> str:
    if months == 12:
        base = "при оплате за год"
    elif months == 1:
        base = "при помесячной оплате"
    elif months:
        base = f"при оплате за {months} мес"
    else:
        base = "при выбранном тарифе"
    if discount_percent > 0:
        return f"{base}, с учётом вашей скидки {discount_percent}%"
    return base


def _billing_note_en(months: int | None, discount_percent: int) -> str:
    if months == 12:
        base = "for annual billing"
    elif months == 1:
        base = "for monthly billing"
    elif months:
        base = f"for the {months}-month plan"
    else:
        base = "for the current plan"
    if discount_percent > 0:
        return f"{base}, incl. your {discount_percent}% discount"
    return base


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
    pricing = await _get_pricing_suggestions(user)
    default_prices = {1: 75, 2: 71, 10: 49}
    prices = pricing["prices"] if pricing else {}
    price_one = prices.get(1, default_prices[1])
    price_two = prices.get(2, default_prices[2])
    price_family = prices.get(10, default_prices[10])
    discount_percent = pricing["discount_percent"] if pricing else 0
    months = pricing["months"] if pricing else 12

    if locale == "ru":
        tenure_text = _format_tenure_ru(tenure_days)
        text = (
            f"<b>Спасибо, что вы с нами уже {tenure_text}!</b>\n"
            "Рады, что TVPN был вам полезен. Хотим, чтобы так оставалось и дальше — ещё и выгоднее для вас.\n\n"
            "До окончания триала: <b>3 дня</b>\n"
            f"Вы использовали: <b>{devices_count}</b> устройство(а)\n\n"
            "<b>Рассказываем как продлить и платить меньше</b>:\n"
            f"• <b>Зайдите в \"Подписку\" и выберите 2 устройства — выйдет всего {price_two} ₽/мес</b>\n"
            f"• <b>Если нужно только 1 устройство — {price_one} ₽/мес</b>\n"
            f"• <b>Хотите выгоднее? Соберите семью до 10 устройств — всего {price_family} ₽/мес за устройство</b>\n\n"
            f"<i>(цены указаны {_billing_note_ru(months, discount_percent)} — так намного выгоднее)</i>\n\n"
            "Если не уверены, что выбрать, или нужны спецусловия — обратитесь в поддержку TVPN. Мы рядом, подскажем и даже поможем с настройкой."
        )
        button_text = "Открыть приложение"
        button_url = "/pay"
    else:
        tenure_text = _format_tenure_en(tenure_days)
        text = (
            f"<b>Thanks for being with us for {tenure_text}!</b>\n"
            "We hope TVPN has been useful. Keep it going — even more affordably for you.\n\n"
            "Trial ends in: <b>3 days</b>\n"
            f"Devices used: <b>{devices_count}</b>\n\n"
            "<b>How to renew and pay less</b>:\n"
            f"• <b>Select 2 devices in \"Subscription\" — just {price_two} RUB/mo</b>\n"
            f"• <b>Only 1 device needed — {price_one} RUB/mo</b>\n"
            f"• <b>Want the best deal? Family up to 10 devices — {price_family} RUB/mo per device</b>\n\n"
            f"<i>(prices shown {_billing_note_en(months, discount_percent)})</i>\n\n"
            "If you’re not sure what to choose or need special terms — contact TVPN support. We’ll help and even assist with setup."
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
