from __future__ import annotations

from typing import TYPE_CHECKING
from datetime import datetime

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bloobcat.bot.bot import bot
from bloobcat.bot.error_handler import (
    handle_telegram_bad_request,
    handle_telegram_forbidden_error,
    reset_user_failed_count,
)
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.logger import get_logger

if TYPE_CHECKING:
    from bloobcat.db.users import Users


logger = get_logger("notifications.family.events")


async def _send_user_notification(user_id: int, text: str, button_text: str, button_url: str = "/subscription/family") -> None:
    try:
        button = await webapp_inline_button(button_text, button_url)
        await bot.send_message(user_id, text, reply_markup=button)
        await reset_user_failed_count(user_id)
    except TelegramForbiddenError as exc:
        logger.warning("User %s blocked the bot: %s", user_id, exc)
        await handle_telegram_forbidden_error(user_id, exc)
    except TelegramBadRequest as exc:
        logger.error("Bad request while sending family notification to %s: %s", user_id, exc)
        await handle_telegram_bad_request(user_id, exc)
    except Exception as exc:
        logger.error("Unexpected family notification error for %s: %s", user_id, exc)


async def notify_family_owner_member_joined(owner: "Users", member: "Users", allocated_devices: int, reactivated: bool = False) -> None:
    lang = get_user_locale(owner)
    member_name = member.name()
    if lang == "ru":
        verb = "вернулся в семью" if reactivated else "присоединился к семье"
        text = (
            "👥 Обновление семейной подписки TVPN\n\n"
            f"{member_name} {verb}.\n"
            f"Выделенный лимит: {allocated_devices} устройств."
        )
        button_text = "Открыть семью"
    else:
        verb = "rejoined your family" if reactivated else "joined your family"
        text = (
            "👥 TVPN family update\n\n"
            f"{member_name} has {verb}.\n"
            f"Allocated limit: {allocated_devices} devices."
        )
        button_text = "Open family"
    await _send_user_notification(int(owner.id), text, button_text)


async def notify_family_member_joined(member: "Users", owner: "Users", allocated_devices: int, reactivated: bool = False) -> None:
    lang = get_user_locale(member)
    owner_name = owner.name()
    if lang == "ru":
        lead = "Вы снова в семейной подписке TVPN." if reactivated else "Вы присоединились к семейной подписке TVPN."
        text = (
            f"✅ {lead}\n\n"
            f"Организатор: {owner_name}\n"
            f"Ваш лимит: {allocated_devices} устройств."
        )
        button_text = "Открыть семью"
    else:
        lead = "You are back in TVPN family subscription." if reactivated else "You joined TVPN family subscription."
        text = (
            f"✅ {lead}\n\n"
            f"Owner: {owner_name}\n"
            f"Your limit: {allocated_devices} devices."
        )
        button_text = "Open family"
    await _send_user_notification(int(member.id), text, button_text)


async def notify_family_member_limit_updated(member: "Users", owner: "Users", allocated_devices: int) -> None:
    lang = get_user_locale(member)
    owner_name = owner.name()
    if lang == "ru":
        text = (
            "📱 Лимит устройств обновлён\n\n"
            f"Организатор {owner_name} изменил ваш лимит.\n"
            f"Новый лимит: {allocated_devices} устройств."
        )
        button_text = "Проверить лимит"
    else:
        text = (
            "📱 Device limit updated\n\n"
            f"Owner {owner_name} updated your limit.\n"
            f"New limit: {allocated_devices} devices."
        )
        button_text = "Check limit"
    await _send_user_notification(int(member.id), text, button_text)


async def notify_family_member_removed(
    member: "Users",
    owner: "Users",
    *,
    removed_by_owner: bool,
    restored_limit: int,
) -> None:
    lang = get_user_locale(member)
    owner_name = owner.name()
    if lang == "ru":
        reason = f"Организатор {owner_name} удалил вас из семьи." if removed_by_owner else "Вы вышли из семьи."
        text = (
            "⚠️ Семейный доступ завершён\n\n"
            f"{reason}\n"
            f"Ваш персональный лимит восстановлен: {restored_limit} устройств."
        )
        button_text = "Открыть подписку"
    else:
        reason = f"Owner {owner_name} removed you from family." if removed_by_owner else "You left the family."
        text = (
            "⚠️ Family access ended\n\n"
            f"{reason}\n"
            f"Your personal limit is restored: {restored_limit} devices."
        )
        button_text = "Open subscription"
    await _send_user_notification(int(member.id), text, button_text, "/subscription")


async def notify_family_owner_invite_revoked(owner: "Users", allocated_devices: int) -> None:
    lang = get_user_locale(owner)
    if lang == "ru":
        text = (
            "🛑 Приглашение отозвано\n\n"
            f"Лимит из приглашения: {allocated_devices} устройств.\n"
            "При необходимости создайте новое приглашение."
        )
        button_text = "Открыть семью"
    else:
        text = (
            "🛑 Invite revoked\n\n"
            f"Invite allocation: {allocated_devices} devices.\n"
            "Create a new invite when needed."
        )
        button_text = "Open family"
    await _send_user_notification(int(owner.id), text, button_text)


async def notify_family_owner_invites_blocked(owner: "Users", blocked_until: datetime, reason: str | None = None) -> None:
    lang = get_user_locale(owner)
    blocked_until_text = blocked_until.strftime("%d.%m.%Y %H:%M UTC")
    reason_text = f"\nПричина: {reason}" if (lang == "ru" and reason) else (f"\nReason: {reason}" if reason else "")
    if lang == "ru":
        text = (
            "⚠️ Защита от аномалий активирована\n\n"
            "Создание приглашений временно ограничено для безопасности аккаунта."
            f"\nДо: {blocked_until_text}{reason_text}"
        )
        button_text = "Проверить семью"
    else:
        text = (
            "⚠️ Anomaly protection activated\n\n"
            "Invite creation is temporarily limited for account safety."
            f"\nUntil: {blocked_until_text}{reason_text}"
        )
        button_text = "Check family"
    await _send_user_notification(int(owner.id), text, button_text)


async def notify_family_owner_invites_unblocked(owner: "Users") -> None:
    lang = get_user_locale(owner)
    if lang == "ru":
        text = (
            "✅ Ограничение снято\n\n"
            "Создание приглашений снова доступно."
        )
        button_text = "Создать приглашение"
    else:
        text = (
            "✅ Restriction removed\n\n"
            "Invite creation is available again."
        )
        button_text = "Create invite"
    await _send_user_notification(int(owner.id), text, button_text)

