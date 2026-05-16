import asyncio
from datetime import UTC, datetime
from html import escape

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bloobcat.bot.bot import bot
from bloobcat.settings import admin_settings, telegram_settings
from bloobcat.logger import get_logger

logger = get_logger("admin_notifications")

ADMIN_MESSAGE_REQUEST_TIMEOUT_SECONDS = 12
ADMIN_MESSAGE_RETRY_DELAYS_SECONDS = (30, 120, 300)

_admin_msg_stats = {
    "sent": 0,
    "failed": 0,
    "last_error": None,
    "last_error_at": None,
    "retry_scheduled": 0,
    "retry_sent": 0,
}


def get_admin_msg_stats() -> dict:
    return _admin_msg_stats.copy()


def _safe_html(value) -> str:
    """Escape dynamic values before embedding into HTML parse_mode messages."""
    if value is None:
        return "—"
    return escape(str(value), quote=False)


def get_admin_log_chat_id() -> int:
    """Return the configured log channel, falling back to the personal admin chat."""
    return int(telegram_settings.logs_channel or admin_settings.telegram_id)


async def write_to(user_id: int, referrer_id: int = 0):
    kb = InlineKeyboardBuilder()
    kb.button(text="Написать", url=f"tg://user?id={user_id}")
    if referrer_id:
        kb.button(text="Реферер", url=f"tg://user?id={referrer_id}")
    kb.adjust(1)
    return kb.as_markup()


def _record_admin_msg_failure(error: Exception) -> None:
    _admin_msg_stats["failed"] += 1
    _admin_msg_stats["last_error"] = str(error)
    _admin_msg_stats["last_error_at"] = datetime.now(UTC).isoformat()


async def _send_admin_bot_message(chat_id: int, text: str, reply_markup=None) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        request_timeout=ADMIN_MESSAGE_REQUEST_TIMEOUT_SECONDS,
    )


def _schedule_admin_message_retry(text: str, reply_markup=None) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("Admin message retry not scheduled: no running event loop")
        return

    _admin_msg_stats["retry_scheduled"] += 1
    loop.create_task(_retry_admin_message(text, reply_markup=reply_markup))


async def _retry_admin_message(text: str, reply_markup=None) -> None:
    for attempt, delay_seconds in enumerate(
        ADMIN_MESSAGE_RETRY_DELAYS_SECONDS, start=1
    ):
        await asyncio.sleep(delay_seconds)
        delivered = await send_admin_message(
            text,
            reply_markup=reply_markup,
            _schedule_retry=False,
        )
        if delivered:
            _admin_msg_stats["retry_sent"] += 1
            logger.info("Admin message retry delivered on attempt %s", attempt)
            return
    logger.error(
        "Admin message retry exhausted after %s attempts",
        len(ADMIN_MESSAGE_RETRY_DELAYS_SECONDS),
    )


async def send_admin_message(
    text: str,
    reply_markup=None,
    *,
    chat_id: int | None = None,
    _schedule_retry: bool = True,
) -> bool:
    """Send a log message to the admin/log channel without blocking critical flows.

    `chat_id` overrides the default (TELEGRAM_LOGS_CHANNEL / ADMIN_TELEGRAM_ID)
    when a caller wants to route a notification to a dedicated channel — used
    e.g. by the Sentry webhook bridge so error alerts can land in a separate
    Telegram chat from regular admin notifications.
    """
    chat_id = chat_id if chat_id is not None else get_admin_log_chat_id()
    try:
        logger.info(f"Отправка сообщения в чат {chat_id}: {text[:100]}...")
        try:
            await _send_admin_bot_message(chat_id, text, reply_markup=reply_markup)
        except TelegramNetworkError as network_error:
            _record_admin_msg_failure(network_error)
            logger.error(
                f"Сетевая ошибка при отправке сообщения (chat_id={chat_id}): {network_error}"
            )
            if _schedule_retry:
                _schedule_admin_message_retry(text, reply_markup=reply_markup)
            return False
        except TelegramBadRequest as btn_error:
            if reply_markup is None:
                raise
            logger.warning(
                f"Не удалось отправить сообщение с кнопками: {str(btn_error)}. Отправляем без кнопок."
            )
            try:
                await _send_admin_bot_message(chat_id, text)
            except TelegramNetworkError as network_error:
                _record_admin_msg_failure(network_error)
                logger.error(
                    f"Сетевая ошибка при повторной отправке сообщения без кнопок "
                    f"(chat_id={chat_id}): {network_error}"
                )
                if _schedule_retry:
                    _schedule_admin_message_retry(text)
                return False
        _admin_msg_stats["sent"] += 1
        return True

    except TelegramBadRequest as e:
        _record_admin_msg_failure(e)
        if "chat not found" in str(e):
            logger.error(
                "Чат %s не найден. Убедитесь, что TELEGRAM_LOGS_CHANNEL/ADMIN_TELEGRAM_ID "
                "настроен корректно и бот добавлен в целевой чат." % chat_id
            )
        else:
            logger.error(f"Ошибка отправки в Telegram (chat_id={chat_id}): {str(e)}")
        return False
    except Exception as e:
        _record_admin_msg_failure(e)
        logger.error(
            f"Неожиданная ошибка при отправке сообщения (chat_id={chat_id}): {str(e)}"
        )
        return False


async def on_activated_bot(
    user_id: int,
    name: str,
    referrer_id: int | None,
    referrer_name: str | None,
    utm: str | None = None,
) -> bool:
    try:
        safe_name = _safe_html(name)
        safe_referrer_name = _safe_html(referrer_name) if referrer_name else None
        safe_utm = _safe_html(utm) if utm else None
        text = f"""🆕 Новая регистрация в боте!

👤 Пользователь: {safe_name}
🆔 ID пользователя: <code>{user_id}</code>"""

        if referrer_id:
            text += (
                f"\n👤 Реферер: {safe_referrer_name} (ID: <code>{referrer_id}</code>)"
            )
        else:
            text += "\n👤 Реферер: Отсутствует"

        if safe_utm:
            text += f"\nUTM: {safe_utm}"

        text += "\n\n#новый_пользователь"
        return await send_admin_message(
            text=text,
            reply_markup=await write_to(user_id, referrer_id if referrer_id else 0),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о активации бота: {str(e)}")
        return False


async def on_activated_key(
    user_id: int,
    name: str,
    referrer_id: int | None,
    referrer_name: str | None,
    utm: str | None = None,
) -> bool:
    try:
        safe_name = _safe_html(name)
        safe_referrer_name = _safe_html(referrer_name) if referrer_name else None
        safe_utm = _safe_html(utm) if utm else None
        text = f"""🔑 Активация ключа пользователем!

👤 Пользователь: {safe_name}
🆔 ID пользователя: <code>{user_id}</code>"""

        if referrer_name:
            text += (
                f"\n👤 Реферер: {safe_referrer_name} (ID: <code>{referrer_id}</code>)"
            )
        else:
            text += "\n👤 Реферер: Отсутствует"

        if safe_utm:
            text += f"\nUTM: {safe_utm}"

        text += "\n\n#активация #ключ"
        return await send_admin_message(
            text=text,
            reply_markup=await write_to(user_id, referrer_id if referrer_id else 0),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о активации ключа: {str(e)}")
        return False


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
    migration_direction: str | None = None,
):
    try:
        # Получаем информацию о пользователе
        from bloobcat.db.users import Users

        user = await Users.get_or_none(id=user_id)

        # Формируем имя пользователя
        user_name = (
            _safe_html(user.full_name) if user else f"ID: <code>{user_id}</code>"
        )
        username = (
            _safe_html(f"@{user.username}")
            if user and user.username
            else "Нет юзернейма"
        )

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
        new_expired_display = format_date(
            new_expired_at or (user.expired_at if user else None)
        )
        lte_display = "нет"
        if lte_gb_total is not None:
            try:
                lte_display = (
                    f"{int(lte_gb_total)} GB" if int(lte_gb_total) > 0 else "нет"
                )
            except (TypeError, ValueError):
                lte_display = "нет"

        migration_summary = ""
        if migration_direction == "base_to_family":
            migration_summary = (
                "\nМиграция: base → family"
                "\nРезультат: семейный тариф активирован сразу, базовый период заморожен."
            )
        elif migration_direction == "family_to_base":
            migration_summary = (
                "\nМиграция: family → base"
                "\nРезультат: базовый тариф добавлен в замороженный период и активируется после семейного."
            )

        text = f"""💳 Успешная оплата пользователя

👤 Пользователь: {user_name} ({username})
🆔 ID пользователя: <code>{user_id}</code>
Сумма платежа: {amount}₽
Период подписки: {months} месяц(ев)
Устройств: {device_count if device_count is not None else (user.hwid_limit if user and user.hwid_limit else 1)}
Дата окончания: {old_expired_display} → {new_expired_display}
LTE: {lte_display}
Автоматическое списание: {auto_payment_status}
Автопродление: {recurrent_status}
Метод оплаты: {_safe_html(method)}
ID платежа: <code>{payment_id}</code>{migration_summary}"""

        if referrer:
            text += f"\n👤 Реферер: {_safe_html(referrer)}"

        # Добавляем UTM при наличии
        user_utm = None
        if user is not None:
            user_utm = user.utm
        if utm or user_utm:
            text += f"\nUTM: {_safe_html(utm or user_utm)}"

        # Добавляем информацию о скидке, если была применена
        if discount_percent and int(discount_percent) > 0:
            text += f"\nСкидка применена: {int(discount_percent)}%"

        # Добавляем соответствующий хештег
        if is_auto_payment:
            text += "\n\n#оплата #подписка #автосписание"
        else:
            text += "\n\n#оплата #подписка"
        await send_admin_message(
            text=text,
            reply_markup=await write_to(user_id),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о платеже: {str(e)}")


async def cancel_subscription(user, reason="Пользователь отключил автопродление"):
    """
    Отправляет уведомление в лог-чат о том, что пользователь отключил автопродление подписки.

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
        expiration_info = (
            f"{user_expired_at.strftime('%d.%m.%Y')} (осталось {days_remaining} дн.)"
            if user_expired_at
            else "Нет активной подписки"
        )

        text = f"""⚠️ Отключение автопродления подписки

👤 Пользователь: {user_name} ({username})
🆔 ID пользователя: <code>{user.id}</code>
Дата окончания подписки: {expiration_info}
Причина: {reason}

После окончания текущего периода подписка не будет продлена автоматически.

#отмена #автопродление"""
        await send_admin_message(
            text=text,
            reply_markup=await write_to(user.id),
        )

        logger.info(
            f"Отправлено уведомление об отключении автопродления для пользователя {user.id}"
        )
    except Exception as e:
        logger.error(
            f"Ошибка отправки уведомления об отключении автопродления: {str(e)}"
        )


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
            next_charge = (
                f"{next_charge} (ручное продление)" if next_charge != "—" else "—"
            )

        change_note = ""
        if old_limit == new_limit and old_lte_gb != new_lte_gb:
            change_note = "\nℹ️ Изменён только LTE лимит."
        elif old_limit != new_limit and old_lte_gb == new_lte_gb:
            change_note = "\nℹ️ Изменён только лимит устройств."

        text = f"""🛠️ Обновление активного тарифа

👤 Пользователь: {user_name} ({username_display})
🆔 ID пользователя: <code>{getattr(user, "id", "—")}</code>

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
        logger.info(
            f"Отправлено уведомление об изменении тарифа для пользователя {getattr(user, 'id', '—')}"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об изменении тарифа: {str(e)}")


async def notify_manual_payment_canceled(
    user,
    *,
    payment_id: str,
    amount: int,
    method: str = "yookassa",
    reason: str | None = None,
):
    try:
        user_name = _safe_html(getattr(user, "full_name", "Неизвестно"))
        username = getattr(user, "username", None)
        username_display = f"@{_safe_html(username)}" if username else "Нет юзернейма"
        reason_display = _safe_html(reason) if reason else "—"
        text = f"""🧑‍💼 Ручной платёж отменён

👤 Пользователь: {user_name} ({username_display})
🆔 ID пользователя: <code>{getattr(user, "id", "—")}</code>
Сумма: {int(amount)}₽
Метод оплаты: {_safe_html(method)}
ID платежа: <code>{payment_id}</code>
Причина: {reason_display}

#оплата #отмена"""
        await send_admin_message(
            text=text,
            reply_markup=await write_to(getattr(user, "id", 0)),
        )
    except Exception as e:
        logger.error(f"Ошибка отправки admin-уведомления об отмене платежа: {str(e)}")


async def notify_frozen_base_activation(
    user,
    *,
    switched_until: str,
    frozen_current_days: int,
    activated_frozen_base_days: int,
):
    text = f"""🧊 Активирована замороженная базовая подписка

👤 Пользователь: {_safe_html(getattr(user, "full_name", "Неизвестно"))}
🆔 ID пользователя: <code>{getattr(user, "id", "—")}</code>
Активировано дней: {int(activated_frozen_base_days)}
Заморожено из текущего периода: {int(frozen_current_days)}
Активна до: {_safe_html(switched_until)}

#подписка #frozen_base"""
    try:
        await send_admin_message(
            text=text,
            reply_markup=await write_to(getattr(user, "id", 0)),
        )
    except Exception as e:
        logger.error(f"Ошибка admin-уведомления о frozen base activation: {str(e)}")


async def notify_frozen_family_activation(
    user,
    *,
    switched_until: str,
    frozen_current_days: int,
    activated_frozen_family_days: int,
):
    text = f"""🧊 Активирована замороженная семейная подписка

👤 Пользователь: {_safe_html(getattr(user, "full_name", "Неизвестно"))}
🆔 ID пользователя: <code>{getattr(user, "id", "—")}</code>
Активировано дней: {int(activated_frozen_family_days)}
Заморожено из текущего периода: {int(frozen_current_days)}
Активна до: {_safe_html(switched_until)}

#подписка #frozen_family"""
    try:
        await send_admin_message(
            text=text,
            reply_markup=await write_to(getattr(user, "id", 0)),
        )
    except Exception as e:
        logger.error(f"Ошибка admin-уведомления о frozen family activation: {str(e)}")


async def notify_frozen_base_auto_resumed_admin(
    user,
    *,
    freeze_id: int,
    restored_days: int,
    restored_until,
):
    text = f"""🔄 Автовосстановлена замороженная базовая подписка

👤 Пользователь: {_safe_html(getattr(user, "full_name", "Неизвестно"))}
🆔 ID пользователя: <code>{getattr(user, "id", "—")}</code>
🧊 Freeze ID: <code>{int(freeze_id)}</code>
📅 Восстановлено дней: {int(restored_days)}
✅ Активна до: {_safe_html(restored_until)}

#подписка #auto_resume #frozen_base"""
    try:
        await send_admin_message(
            text=text,
            reply_markup=await write_to(getattr(user, "id", 0)),
        )
    except Exception as e:
        logger.error(
            f"Ошибка admin-уведомления об автовосстановлении базовой подписки: {str(e)}"
        )


async def notify_family_membership_event(
    owner,
    member,
    *,
    event: str,
    allocated_devices: int | None = None,
    previous_allocated_devices: int | None = None,
    restored_limit: int | None = None,
):
    event_title = {
        "member_added": "✅ Участник добавлен в семью",
        "member_reactivated": "🔁 Участник возвращён в семью",
        "member_limit_updated": "🛠️ Обновлён лимит участника семьи",
        "member_deleted": "🗑️ Участник удалён из семьи",
        "member_left": "🚪 Участник вышел из семьи",
    }.get(event)
    if event_title is None:
        raise ValueError(f"Unsupported family membership admin event: {event}")

    details: list[str] = [
        event_title,
        "",
        f"👤 Участник: {_safe_html(getattr(member, 'full_name', 'Неизвестно'))}",
        f"🆔 ID участника: <code>{getattr(member, 'id', '—')}</code>",
        f"👤 Организатор: {_safe_html(getattr(owner, 'full_name', 'Неизвестно'))}",
        f"🆔 ID организатора: <code>{getattr(owner, 'id', '—')}</code>",
    ]
    if previous_allocated_devices is not None or allocated_devices is not None:
        before = (
            "—"
            if previous_allocated_devices is None
            else int(previous_allocated_devices)
        )
        after = "—" if allocated_devices is None else int(allocated_devices)
        details.append(f"📱 Лимит устройств: {before} → {after}")
    elif allocated_devices is not None:
        details.append(f"📱 Лимит устройств: {int(allocated_devices)}")
    if restored_limit is not None:
        details.append(f"♻️ Восстановлен личный лимит: {int(restored_limit)}")
    details.append("")
    details.append(f"#семья #{_safe_html(event)}")
    try:
        await send_admin_message(
            text="\n".join(details),
            reply_markup=await write_to(getattr(member, "id", 0)),
        )
    except Exception as e:
        logger.error(f"Ошибка admin-уведомления о family membership event: {str(e)}")


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
            expired_change = f"\nДата окончания: {format_date(old_expired_at)} → {format_date(new_expired_at)}"

        price_line = ""
        if price_per_gb is not None:
            try:
                price_line = f"\nЦена за 1 GB: {float(price_per_gb):.2f}₽"
            except Exception:
                price_line = ""

        text = f"""📶 Пополнение LTE-трафика

👤 Пользователь: {user_name} ({username})
🆔 ID пользователя: <code>{user_id}</code>

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
