"""Shared helpers for the Golden Period notification family.

The five public modules (`activated`, `payout`, `cap_reached`, `expired`,
`clawback`) share three concerns:

    1. Locale-aware text resolution that prefers per-tenant overrides from
       `GoldenPeriodConfig.message_templates` and falls back to ru/en defaults
       hard-coded here.
    2. Parallel delivery to Telegram, Web Push, and InAppNotification with
       per-channel error isolation — a Telegram block must not stop the push.
    3. Quiet-hours deferral for non-critical events so we do not wake users
       between midnight and 08:00 MSK.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bloobcat.bot.bot import bot
from bloobcat.bot.error_handler import (
    handle_telegram_bad_request,
    handle_telegram_forbidden_error,
    reset_user_failed_count,
)
from bloobcat.bot.keyboard import webapp_inline_button
from bloobcat.bot.notifications.localization import get_user_locale
from bloobcat.bot.notifications.web_push import send_push_to_user
from bloobcat.tasks.quiet_hours import is_quiet_hours

if TYPE_CHECKING:
    from bloobcat.db.golden_period import GoldenPeriod
    from bloobcat.db.users import Users

logger = logging.getLogger(__name__)
WEB_USER_ID_FLOOR = 8_000_000_000_000_000

# Defaults are intentionally short and emoji-free. The Directus admin extension
# can override anything via GoldenPeriodConfig.message_templates without a
# code deploy.
_DEFAULT_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "ru": {
        "activated": {
            "telegram": (
                "Привет, {name}! У тебя 24 часа Золотого периода Vectra.\n\n"
                "За каждого друга, который активирует ключ за это время, +{amount}₽ "
                "сразу на баланс. Лимит {cap} начислений."
            ),
            "push_title": "Золотой период открыт",
            "push_body": "У тебя 24 часа на приглашение друзей. +{amount}₽ за каждого.",
            "in_app_title": "Золотой период открыт",
            "in_app_body": (
                "За 24 часа можно получить до {cap}× по {amount}₽ за активацию ключа другом."
            ),
        },
        "payout": {
            "telegram": (
                "{name}, +{amount}₽ за активацию друга в Золотом периоде.\n\n"
                "Уже {paid}/{cap} начислений. Осталось {remaining}."
            ),
            "push_title": "+{amount}₽ от друга",
            "push_body": "Друг активировал Vectra. {paid}/{cap} начислений.",
            "in_app_title": "Начислено +{amount}₽",
            "in_app_body": "Друг активировал ключ Vectra.",
        },
        "cap_reached": {
            "telegram": (
                "{name}, ты полностью использовал Золотой период: {cap}/{cap} начислений, "
                "итого +{total}₽. Спасибо!"
            ),
            "push_title": "Лимит Золотого периода исчерпан",
            "push_body": "{cap}/{cap}, всего +{total}₽. Спасибо!",
            "in_app_title": "Лимит Золотого периода исчерпан",
            "in_app_body": "Получено {cap} из {cap}. Итого +{total}₽.",
        },
        "expired": {
            "telegram": (
                "{name}, окно Золотого периода закрыто. Заработано: {total}₽ "
                "за {paid} активаций друзей."
            ),
            "push_title": "Золотой период завершён",
            "push_body": "Итого: {total}₽ за {paid} начислений.",
            "in_app_title": "Золотой период завершён",
            "in_app_body": "Итого: {total}₽ за {paid} начислений.",
        },
        "clawback": {
            "telegram": (
                "{name}, начисление в Золотом периоде ({amount}₽) отменено: "
                "обнаружено нарушение правил сервиса.\n\n{breakdown}\n\n"
                "Просим не нарушать правила сервиса."
            ),
            "push_title": "Начисление отменено",
            "push_body": "Сработала проверка Золотого периода. Подробности в приложении.",
            "in_app_title": "Начисление отменено",
            "in_app_body": (
                "Бонус {amount}₽ отозван по проверке Золотого периода. {breakdown}"
            ),
        },
    },
    "en": {
        "activated": {
            "telegram": (
                "Hi {name}! Your 24h Golden Period is open.\n\n"
                "For every friend who activates the VPN within 24h you get "
                "+{amount}₽ to your balance. Capped at {cap} payouts."
            ),
            "push_title": "Golden Period unlocked",
            "push_body": "24h to invite friends. +{amount}₽ each.",
            "in_app_title": "Golden Period unlocked",
            "in_app_body": (
                "Up to {cap}× {amount}₽ for friends activating Vectra in 24h."
            ),
        },
        "payout": {
            "telegram": (
                "{name}, +{amount}₽ from a friend activating Vectra.\n\n"
                "{paid}/{cap} done, {remaining} to go."
            ),
            "push_title": "+{amount}₽ from a friend",
            "push_body": "Friend activated Vectra. {paid}/{cap} payouts.",
            "in_app_title": "+{amount}₽ credited",
            "in_app_body": "A friend activated their Vectra VPN key.",
        },
        "cap_reached": {
            "telegram": (
                "{name}, you've maxed out the Golden Period: {cap}/{cap} payouts, "
                "+{total}₽ total. Thank you!"
            ),
            "push_title": "Golden Period cap reached",
            "push_body": "{cap}/{cap}, +{total}₽ total. Thanks!",
            "in_app_title": "Golden Period cap reached",
            "in_app_body": "{cap}/{cap} payouts, +{total}₽ total.",
        },
        "expired": {
            "telegram": (
                "{name}, the Golden Period window closed. You earned {total}₽ "
                "across {paid} friend activations."
            ),
            "push_title": "Golden Period ended",
            "push_body": "Total: {total}₽ across {paid} payouts.",
            "in_app_title": "Golden Period ended",
            "in_app_body": "Total: {total}₽ across {paid} payouts.",
        },
        "clawback": {
            "telegram": (
                "{name}, your Golden Period bonus of {amount}₽ has been reversed: "
                "service rules violation detected.\n\n{breakdown}\n\n"
                "Please follow the service rules."
            ),
            "push_title": "Bonus reversed",
            "push_body": "Golden Period anti-fraud check triggered. See app for details.",
            "in_app_title": "Bonus reversed",
            "in_app_body": (
                "{amount}₽ bonus reversed by Golden Period check. {breakdown}"
            ),
        },
    },
}


async def _resolve_template(
    *,
    event: str,
    channel: str,
    locale: str,
) -> str:
    """Look up the user-facing string in the order: config[locale][event][channel],
    config['ru'][event][channel], DEFAULT[locale][event][channel],
    DEFAULT['ru'][event][channel].
    """
    try:
        from bloobcat.services.golden_period import (
            get_active_golden_period_config,
        )

        config = await get_active_golden_period_config()
        templates: Mapping[str, Any] = config.message_templates or {}
    except Exception:  # noqa: BLE001 - fall back to hard defaults
        templates = {}

    for loc in (locale, "ru"):
        loc_block = templates.get(loc) if isinstance(templates, Mapping) else None
        if isinstance(loc_block, Mapping):
            event_block = loc_block.get(event)
            if isinstance(event_block, Mapping):
                value = event_block.get(channel)
                if isinstance(value, str) and value.strip():
                    return value
        defaults_loc = _DEFAULT_TEMPLATES.get(loc)
        if defaults_loc:
            defaults_event = defaults_loc.get(event)
            if defaults_event:
                value = defaults_event.get(channel)
                if isinstance(value, str) and value.strip():
                    return value
    return ""


async def _send_telegram(
    user: "Users",
    *,
    text: str,
    button_text: str,
    button_path: str,
) -> None:
    if int(user.id) >= WEB_USER_ID_FLOOR:
        return
    try:
        button = await webapp_inline_button(button_text, button_path)
        await bot.send_message(int(user.id), text, reply_markup=button)
        await reset_user_failed_count(int(user.id))
    except TelegramForbiddenError as exc:
        await handle_telegram_forbidden_error(int(user.id), exc)
    except TelegramBadRequest as exc:
        await handle_telegram_bad_request(int(user.id), exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "golden_period_telegram_failed user=%s err=%s", user.id, exc
        )


async def _send_web_push(
    user_id: int,
    *,
    title: str,
    body: str,
    tag: str,
    deeplink: str = "/referrals",
) -> None:
    try:
        await send_push_to_user(
            int(user_id),
            title=title,
            body=body,
            url=deeplink,
            tag=tag,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "golden_period_push_failed user=%s err=%s", user_id, exc
        )


async def _create_in_app_notification(
    user_id: int,
    *,
    event_id: str,
    title: str,
    body: str,
) -> None:
    """Persist an InAppNotification + deduped NotificationView for the user.

    Implementation note: we model "user-targeted Golden Period notification"
    by creating a fresh InAppNotification row with a deterministic title prefix
    and recording a NotificationView pegged to `event_id` (used as session_id).
    The composite UNIQUE (user_id, notification_id, session_id) on
    NotificationView gives us idempotency without schema changes.
    """
    try:
        from bloobcat.db.in_app_notifications import (
            InAppNotification,
            NotificationView,
        )

        now_utc = datetime.now(timezone.utc)
        # Reuse a single per-event-id banner row when one exists, otherwise
        # create. The (user_id, notification_id, session_id) UNIQUE makes the
        # NotificationView insert the actual idempotency point.
        existing = await InAppNotification.filter(
            title=title, body=body, is_active=True
        ).first()
        if existing is None:
            existing = await InAppNotification.create(
                title=title,
                body=body,
                start_at=now_utc,
                end_at=now_utc.replace(year=now_utc.year + 1),
                max_per_user=1,
                max_per_session=1,
                is_active=True,
            )
        try:
            await NotificationView.create(
                user_id=int(user_id),
                notification_id=int(existing.id),
                session_id=str(event_id),
            )
        except Exception:  # noqa: BLE001 - duplicate event_id == dedupe
            return
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "golden_period_in_app_failed user=%s err=%s", user_id, exc
        )


def _format_template(template: str, **kwargs: Any) -> str:
    if not template:
        return ""
    try:
        return template.format(**kwargs)
    except Exception:  # noqa: BLE001 - fall back to raw template
        return template


async def dispatch_event(
    *,
    user: "Users",
    period: "GoldenPeriod",
    event: str,
    event_id: str,
    deeplink: str,
    template_kwargs: dict[str, Any],
    critical: bool = False,
) -> None:
    """Fan out one Golden Period event to Telegram / Push / InAppNotification.

    `critical` events ignore quiet hours; non-critical events skip the push
    channel during quiet hours so the user is not woken up at 04:00 MSK.
    """
    locale = get_user_locale(user)
    quiet = is_quiet_hours()

    tg_text = _format_template(
        await _resolve_template(event=event, channel="telegram", locale=locale),
        **template_kwargs,
    )
    push_title = _format_template(
        await _resolve_template(event=event, channel="push_title", locale=locale),
        **template_kwargs,
    )
    push_body = _format_template(
        await _resolve_template(event=event, channel="push_body", locale=locale),
        **template_kwargs,
    )
    in_app_title = _format_template(
        await _resolve_template(event=event, channel="in_app_title", locale=locale),
        **template_kwargs,
    )
    in_app_body = _format_template(
        await _resolve_template(event=event, channel="in_app_body", locale=locale),
        **template_kwargs,
    )

    button_text = "Открыть" if locale == "ru" else "Open"

    coros: list[Any] = []
    if tg_text:
        coros.append(
            _send_telegram(
                user,
                text=tg_text,
                button_text=button_text,
                button_path=deeplink,
            )
        )
    if push_title and push_body and (critical or not quiet):
        coros.append(
            _send_web_push(
                int(user.id),
                title=push_title,
                body=push_body,
                tag=f"vectra-golden-{event}-{period.id}",
                deeplink=deeplink,
            )
        )
    if in_app_title and in_app_body:
        coros.append(
            _create_in_app_notification(
                int(user.id),
                event_id=event_id,
                title=in_app_title,
                body=in_app_body,
            )
        )

    if not coros:
        return
    # Errors inside each coroutine are already swallowed; gather without
    # raising so a single channel failure cannot kill the others.
    await asyncio.gather(*coros, return_exceptions=True)
