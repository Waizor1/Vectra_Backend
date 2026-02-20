from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from html import escape

from bloobcat.bot.bot import bot
from bloobcat.settings import admin_settings
from bloobcat.logger import get_logger

logger = get_logger("admin_notifications")

_admin_msg_stats = {"sent": 0, "failed": 0, "last_error": None, "last_error_at": None}


def get_admin_msg_stats() -> dict:
    return _admin_msg_stats.copy()


def _safe_html(value) -> str:
    """Escape dynamic values before embedding into HTML parse_mode messages."""
    if value is None:
        return "—"
    return escape(str(value), quote=False)

async def write_to(user_id: int, referrer_id: int = 0):
    kb = InlineKeyboardBuilder()
    kb.button(text="Написать", url=f"tg://user?id={user_id}")
    if referrer_id:
        kb.button(text="Реферер", url=f"tg://user?id={referrer_id}")
    kb.adjust(1)
    return kb.as_markup()

async def send_admin_message(text: str, reply_markup=None):
    """Общая функция для отправки сообщений админу/в канал"""
    from datetime import datetime
    chat_id = admin_settings.telegram_id
    try:
        logger.info(f"Отправка сообщения в чат {chat_id}: {text[:100]}...")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception as btn_error:
            logger.warning(f"Не удалось отправить сообщение с кнопками: {str(btn_error)}. Отправляем без кнопок.")
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML"
            )
        _admin_msg_stats["sent"] += 1

    except TelegramBadRequest as e:
        _admin_msg_stats["failed"] += 1
        _admin_msg_stats["last_error"] = str(e)
        _admin_msg_stats["last_error_at"] = datetime.utcnow().isoformat()
        if "chat not found" in str(e):
            logger.error(f"Чат {chat_id} не найден. Убедитесь, что бот добавлен в канал или начат диалог с админом (ADMIN_TELEGRAM_ID={chat_id})")
        else:
            logger.error(f"Ошибка отправки в Telegram (chat_id={chat_id}): {str(e)}")
    except Exception as e:
        _admin_msg_stats["failed"] += 1
        _admin_msg_stats["last_error"] = str(e)
        _admin_msg_stats["last_error_at"] = datetime.utcnow().isoformat()
        logger.error(f"Неожиданная ошибка при отправке сообщения (chat_id={chat_id}): {str(e)}")

async def on_activated_bot(
    user_id: int, name: str, referrer_id: int | None, referrer_name: str | None, utm: str | None = None
):
    try:
        text = f"""Новая регистрация в боте!

Пользователь: {name}
ID пользователя: <code>{user_id}</code>"""

        if referrer_id:
            text += f"\nРеферер: {referrer_name} (ID: <code>{referrer_id}</code>)"
        else:
            text += "\nРеферер: Отсутствует"
            
        if utm:
            text += f"\nUTM: {utm}"
            
        text += "\n\n#новый_пользователь"
        
        try:
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                reply_markup=await write_to(
                    user_id, referrer_id if referrer_id else 0
                ),
                parse_mode="HTML",
            )
        except Exception as btn_error:
            logger.warning(f"Не удалось отправить сообщение с кнопками: {str(btn_error)}. Отправляем без кнопок.")
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о активации бота: {str(e)}")

async def on_activated_key(
    user_id: int, name: str, referrer_id: int | None, referrer_name: str | None, utm: str | None = None
):
    try:
        text = f"""Активация ключа пользователем!

Пользователь: {name}
ID пользователя: <code>{user_id}</code>"""

        if referrer_name:
            text += f"\nРеферер: {referrer_name} (ID: <code>{referrer_id}</code>)"
        else:
            text += "\nРеферер: Отсутствует"
            
        if utm:
            text += f"\nUTM: {utm}"
            
        text += "\n\n#активация #ключ"
            
        try:
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                reply_markup=await write_to(
                    user_id, referrer_id if referrer_id else 0
                ),
                parse_mode="HTML",
            )
        except Exception as btn_error:
            logger.warning(f"Не удалось отправить сообщение с кнопками: {str(btn_error)}. Отправляем без кнопок.")
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о активации ключа: {str(e)}")

async def on_payment(
    user_id: int,
    is_sub: bool,
    referrer: str | None,
    amount: int,
    months: int,
    method: str,
    payment_id: str,
    is_auto: bool = False,
    utm: str | None = None,
    discount_percent: int | None = None,
    device_count: int | None = None,
    old_expired_at=None,
    new_expired_at=None,
    lte_gb_total: int | None = None,
):
    try:
        # Получаем информацию о пользователе
        from bloobcat.db.users import Users
        user = await Users.get_or_none(id=user_id)
        
        # Формируем имя пользователя
        user_name = _safe_html(user.full_name) if user else f"ID: <code>{user_id}</code>"
        username = _safe_html(f"@{user.username}") if user and user.username else "Нет юзернейма"
        
        # Проверяем статус автопродления
        # is_sub передается из payment.py и содержит значение user.is_subscribed
        # Дополнительно проверяем наличие renew_id у пользователя (для подстраховки)
        recurrent_status = "Да" if is_sub and (user and user.renew_id) else "Нет"
        
        # Определяем, является ли текущий платеж автосписанием
        is_auto_payment = is_auto or ("auto" in method.lower())
        auto_payment_status = "Да" if is_auto_payment else "Нет"
        
        def format_date(value) -> str:
            if not value:
                return "—"
            try:
                return value.strftime("%d.%m.%Y")
            except AttributeError:
                return str(value)

        old_expired_display = format_date(old_expired_at)
        new_expired_display = format_date(new_expired_at or (user.expired_at if user else None))
        lte_display = "нет"
        if lte_gb_total is not None:
            try:
                lte_display = f"{int(lte_gb_total)} GB" if int(lte_gb_total) > 0 else "нет"
            except (TypeError, ValueError):
                lte_display = "нет"

        text = f"""Успешная оплата пользователя!

Пользователь: {user_name} ({username})
ID пользователя: <code>{user_id}</code>
Сумма платежа: {amount}₽
Период подписки: {months} месяц(ев)
Устройств: {device_count if device_count is not None else (user.hwid_limit if user and user.hwid_limit else 1)}
Дата окончания: {old_expired_display} → {new_expired_display}
LTE: {lte_display}
Автоматическое списание: {auto_payment_status}
Автопродление: {recurrent_status}
Метод оплаты: {_safe_html(method)}
ID платежа: <code>{payment_id}</code>"""

        if referrer:
            text += f"\nРеферер: {_safe_html(referrer)}"

        # Добавляем UTM при наличии
        if utm or (user and user.utm):
            text += f"\nUTM: {_safe_html(utm or user.utm)}"

        # Добавляем информацию о скидке, если была применена
        if discount_percent and int(discount_percent) > 0:
            text += f"\nСкидка применена: {int(discount_percent)}%"
            
        # Добавляем соответствующий хештег
        if is_auto_payment:
            text += "\n\n#оплата #подписка #автосписание"
        else:
            text += "\n\n#оплата #подписка"
            
        try:
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                reply_markup=await write_to(user_id),
                parse_mode="HTML",
            )
        except Exception as btn_error:
            logger.warning(f"Не удалось отправить сообщение с кнопками: {str(btn_error)}. Отправляем без кнопок.")
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о платеже: {str(e)}")

async def cancel_subscription(user, reason="Пользователь отключил автопродление"):
    """
    Отправляет уведомление в админ чат о том, что пользователь отключил автопродление подписки.
    
    Args:
        user: Пользователь, отключивший автопродление
        reason: Причина отключения автопродления (по умолчанию - пользователь сам отключил)
    """
    try:
        # Формируем информацию о пользователе
        user_name = user.full_name
        username = f"@{user.username}" if user.username else "Нет юзернейма"
        
        # Определяем дату окончания подписки
        from datetime import date
        from bloobcat.db.users import normalize_date
        user_expired_at = normalize_date(user.expired_at)
        days_remaining = (user_expired_at - date.today()).days if user_expired_at else 0
        expiration_info = f"{user_expired_at.strftime('%d.%m.%Y')} (осталось {days_remaining} дн.)" if user_expired_at else "Нет активной подписки"
        
        text = f"""Отключение автопродления подписки!

Пользователь: {user_name} ({username})
ID пользователя: <code>{user.id}</code>
Дата окончания подписки: {expiration_info}
Причина: {reason}

После окончания текущего периода подписка не будет продлена автоматически.

#отмена #автопродление"""
        
        try:
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                reply_markup=await write_to(user.id),
                parse_mode="HTML",
            )
        except Exception as btn_error:
            logger.warning(f"Не удалось отправить уведомление об отключении автопродления с кнопками: {str(btn_error)}. Отправляем без кнопок.")
            await bot.send_message(
                chat_id=admin_settings.telegram_id,
                text=text,
                parse_mode="HTML",
            )
            
        logger.info(f"Отправлено уведомление об отключении автопродления для пользователя {user.id}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об отключении автопродления: {str(e)}")


async def notify_active_tariff_change(
    user,
    *,
    tariff_name: str,
    months: int,
    old_limit: int,
    new_limit: int,
    old_price: int | None,
    new_price: int | None,
    old_expired_at,
    new_expired_at,
    old_lte_gb: int | None = None,
    new_lte_gb: int | None = None,
    auto_renew_enabled: bool,
):
    """
    Отправляет уведомление в лог-чат о том, что пользователь изменил параметры активного тарифа.
    """
    try:
        def format_price(value: int | None) -> str:
            return f"{int(value)}₽" if value is not None else "—"

        def format_date(value) -> str:
            if not value:
                return "—"
            try:
                return value.strftime("%d.%m.%Y")
            except AttributeError:
                return str(value)

        def format_lte(value: int | None) -> str:
            if value is None:
                return "LTE нет"
            try:
                return f"{int(value)} GB" if int(value) > 0 else "LTE нет"
            except (TypeError, ValueError):
                return "LTE нет"

        user_name = getattr(user, "full_name", "Неизвестно")
        username = getattr(user, "username", None)
        username_display = f"@{username}" if username else "Нет юзернейма"

        auto_status = "Активно" if auto_renew_enabled else "Выключено"
        renew_id = getattr(user, "renew_id", None)
        if auto_renew_enabled and renew_id:
            auto_status += f" (ID: <code>{renew_id}</code>)"

        next_charge = format_date(new_expired_at)
        if not auto_renew_enabled:
            next_charge = f"{next_charge} (ручное продление)" if next_charge != "—" else "—"

        change_note = ""
        if old_limit == new_limit and old_lte_gb != new_lte_gb:
            change_note = "\n[INFO] Изменён только LTE лимит."
        elif old_limit != new_limit and old_lte_gb == new_lte_gb:
            change_note = "\n[INFO] Изменён только лимит устройств."

        text = f"""Обновление активного тарифа

Пользователь: {user_name} ({username_display})
ID пользователя: <code>{getattr(user, 'id', '—')}</code>

Тариф: {tariff_name} · {months} мес.
Лимит устройств: {old_limit} → {new_limit}
LTE: {format_lte(old_lte_gb)} → {format_lte(new_lte_gb)}
Стоимость тарифа: {format_price(old_price)} → {format_price(new_price)}
Дата окончания: {format_date(old_expired_at)} → {format_date(new_expired_at)}

Автосписание: {auto_status}
Следующее списание: {next_charge}
{change_note}

#тариф #изменение"""

        await send_admin_message(
            text=text,
            reply_markup=await write_to(getattr(user, "id", 0)),
        )
        logger.info(f"Отправлено уведомление об изменении тарифа для пользователя {getattr(user, 'id', '—')}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об изменении тарифа: {str(e)}")


async def notify_lte_topup(
    *,
    user_id: int,
    payment_id: str,
    method: str,
    lte_gb_delta: int,
    lte_gb_before: int | None = None,
    lte_gb_after: int | None = None,
    price_per_gb: float | None = None,
    amount_total: int,
    amount_external: int = 0,
    amount_from_balance: int = 0,
    old_hwid_limit: int | None = None,
    new_hwid_limit: int | None = None,
    old_expired_at=None,
    new_expired_at=None,
):
    """
    Лог в админ-канал о доплате за LTE-трафик.
    Отдельно от on_payment(), т.к. LTE-topup не является покупкой тарифа.
    """
    try:
        from bloobcat.db.users import Users

        user = await Users.get_or_none(id=user_id)
        user_name = user.full_name if user else f"ID: <code>{user_id}</code>"
        username = f"@{user.username}" if user and user.username else "Нет юзернейма"

        def format_date(value) -> str:
            if not value:
                return "—"
            try:
                return value.strftime("%d.%m.%Y")
            except AttributeError:
                return str(value)

        before_display = "—"
        after_display = "—"
        try:
            if lte_gb_before is not None:
                before_display = f"{int(lte_gb_before)} GB"
        except Exception:
            before_display = "—"
        try:
            if lte_gb_after is not None:
                after_display = f"{int(lte_gb_after)} GB"
        except Exception:
            after_display = "—"

        hwid_change = ""
        if old_hwid_limit is not None or new_hwid_limit is not None:
            old_hwid = old_hwid_limit if old_hwid_limit is not None else "—"
            new_hwid = new_hwid_limit if new_hwid_limit is not None else "—"
            hwid_change = f"\nУстройств: {old_hwid} → {new_hwid}"

        expired_change = ""
        if old_expired_at or new_expired_at:
            expired_change = (
                f"\nДата окончания: {format_date(old_expired_at)} → {format_date(new_expired_at)}"
            )

        price_line = ""
        if price_per_gb is not None:
            try:
                price_line = f"\nЦена за 1 GB: {float(price_per_gb):.2f}₽"
            except Exception:
                price_line = ""

        text = f"""Пополнение LTE-трафика

Пользователь: {user_name} ({username})
ID пользователя: <code>{user_id}</code>

Добавлено: {int(lte_gb_delta)} GB
LTE: {before_display} → {after_display}{price_line}{hwid_change}{expired_change}

Сумма: {int(amount_total)}₽
С внешней оплаты: {int(amount_external)}₽
С баланса: {int(amount_from_balance)}₽
Метод: {method}
ID платежа: <code>{payment_id}</code>

#lte #пополнение #оплата"""

        await send_admin_message(
            text=text,
            reply_markup=await write_to(user_id),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о LTE пополнении: {str(e)}")
