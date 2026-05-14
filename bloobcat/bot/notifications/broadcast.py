"""Broadcast engine: segmentation + multi-channel delivery.

Exposed primitives:
  - `SEGMENTS`             — segment definitions (label + filter builder).
  - `resolve_segment(...)` — returns a list of users matching a segment key/value.
  - `BroadcastChannels`    — which channels to use (Telegram / Web Push / both).
  - `run_broadcast(...)`   — orchestrates send across channels with progress callbacks.

Segmentation is intentionally a pure-Python layer on top of the existing Users
ORM so it's easy to test and reason about. Heavy aggregate joins (e.g.
"has-paid") are folded into a single targeted query when possible.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from tortoise.expressions import Q

from bloobcat.bot.notifications.web_push import (
    is_configured as web_push_is_configured,
    send_push_to_users,
)
from bloobcat.db.payments import Payments
from bloobcat.db.push_subscriptions import PushSubscription
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger: logging.Logger = get_logger("broadcast")


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SegmentDef:
    key: str
    label: str
    needs_value: bool = False
    value_prompt: str | None = None


SEGMENTS: list[SegmentDef] = [
    SegmentDef("all", "👥 Все пользователи"),
    SegmentDef("active", "✅ Активные (есть подписка + было подключение)"),
    SegmentDef("inactive", "💤 Неактивные (никогда / истёк срок)"),
    SegmentDef("trial", "🎁 Сейчас на триале"),
    SegmentDef("paid_ever", "💰 Платили хотя бы раз"),
    SegmentDef("never_paid", "🆓 Никогда не платили"),
    SegmentDef("with_referrals", "👫 Привели хотя бы одного друга"),
    SegmentDef("with_email", "✉️ Указали email"),
    SegmentDef("lang_ru", "🇷🇺 Язык: русский"),
    SegmentDef("lang_en", "🇬🇧 Язык: английский"),
    SegmentDef("expiring_3d", "⏳ Подписка истекает в ближайшие 3 дня"),
    SegmentDef("expired_recently", "🔚 Подписка истекла за последние 7 дней"),
    SegmentDef("registered_recent", "🆕 Зарегистрированы за последние 7 дней"),
    SegmentDef("pwa_installed", "📱 Установили PWA (есть push-подписка)"),
    SegmentDef(
        "utm",
        "🏷️ По UTM-метке (введите код)",
        needs_value=True,
        value_prompt="Введите значение UTM (точное совпадение):",
    ),
]

SEGMENT_BY_KEY: dict[str, SegmentDef] = {s.key: s for s in SEGMENTS}


async def resolve_segment(segment_key: str, value: str | None = None) -> list[Users]:
    # Anchor all relative date math to UTC so the cutoff doesn't shift with
    # container TZ. `expired_at` is a DateField — comparing it to a UTC date
    # is consistent across all segments.
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()
    in_3_days = today + timedelta(days=3)
    seven_days_ago_dt = now_utc - timedelta(days=7)
    seven_days_ago_date = today - timedelta(days=7)

    if segment_key == "all":
        return await Users.all()

    if segment_key == "active":
        return await Users.filter(
            is_registered=True,
            expired_at__not_isnull=True,
            expired_at__gt=today,
            connected_at__not_isnull=True,
        )

    if segment_key == "inactive":
        # Single query: either never connected, or registered with expired sub.
        return await Users.filter(
            Q(is_registered=True, connected_at__isnull=True)
            | Q(is_registered=True, expired_at__not_isnull=True, expired_at__lte=today)
        )

    if segment_key == "trial":
        return await Users.filter(
            is_trial=True,
            expired_at__not_isnull=True,
            expired_at__gt=today,
        )

    if segment_key == "paid_ever":
        # Distinct user_ids from payments table.
        payments_qs = await Payments.all().values_list("user_id", flat=True)
        user_ids = list({int(uid) for uid in payments_qs if uid is not None})
        if not user_ids:
            return []
        return await Users.filter(id__in=user_ids)

    if segment_key == "never_paid":
        payments_qs = await Payments.all().values_list("user_id", flat=True)
        paid_ids = {int(uid) for uid in payments_qs if uid is not None}
        all_users = await Users.all()
        return [u for u in all_users if u.id not in paid_ids]

    if segment_key == "with_referrals":
        return await Users.filter(referrals__gt=0)

    if segment_key == "with_email":
        return await Users.filter(email__not_isnull=True, email_notifications_enabled=True)

    if segment_key == "lang_ru":
        return await Users.filter(language_code__icontains="ru")

    if segment_key == "lang_en":
        return await Users.filter(language_code__icontains="en")

    if segment_key == "expiring_3d":
        return await Users.filter(
            is_registered=True,
            expired_at__gte=today,
            expired_at__lte=in_3_days,
        )

    if segment_key == "expired_recently":
        return await Users.filter(
            is_registered=True,
            expired_at__gte=seven_days_ago_date,
            expired_at__lt=today,
        )

    if segment_key == "registered_recent":
        return await Users.filter(registration_date__gte=seven_days_ago_dt)

    if segment_key == "pwa_installed":
        sub_user_ids = await PushSubscription.filter(is_active=True).values_list("user_id", flat=True)
        unique_ids = list({int(uid) for uid in sub_user_ids if uid is not None})
        if not unique_ids:
            return []
        return await Users.filter(id__in=unique_ids)

    if segment_key == "utm":
        target = (value or "").strip()
        if not target:
            return []
        return await Users.filter(utm=target)

    # Unknown key — safest is empty list (admin should pick from menu).
    logger.warning("unknown segment key: %s", segment_key)
    return []


# ---------------------------------------------------------------------------
# Inline buttons parsing
# ---------------------------------------------------------------------------

# Hosts allowed as targets for *Web Push* click navigation. Telegram inline
# buttons themselves accept a broader set (https + tg://) — the allowlist
# only constrains what we forward into push payloads, so a typo or compromise
# can't turn a notification into an open-redirect phishing surface.
_PUSH_ALLOWED_HOSTS: frozenset[str] = frozenset({
    "app.vectra-pro.net",
    "vectra-pro.net",
    "www.vectra-pro.net",
    "t.me",
    "telegram.me",
})


def _safe_push_url(url: str | None) -> str | None:
    """Return the URL if safe to embed in a Web Push payload, else None."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname.lower() in _PUSH_ALLOWED_HOSTS:
        return url
    return None


def parse_buttons_spec(spec: str | None) -> list[list[InlineKeyboardButton]] | None:
    """Parse admin-provided buttons spec.

    Format (one row per line, columns separated by ' | '):
        Открыть приложение | https://app.vectra-pro.net
        Подробнее | https://example.com  ||  Связаться | https://t.me/support

    Use '||' (double pipe) to put multiple buttons on the same row.
    Returns None when spec is empty / unusable.
    """
    if not spec:
        return None
    spec = spec.strip()
    if not spec or spec.lower() in {"-", "нет", "no", "none", "skip"}:
        return None

    rows: list[list[InlineKeyboardButton]] = []
    for raw_line in spec.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        row: list[InlineKeyboardButton] = []
        for chunk in line.split("||"):
            piece = chunk.strip()
            if not piece:
                continue
            if "|" not in piece:
                continue
            text, url = piece.split("|", 1)
            text = text.strip()[:64]
            url = url.strip()
            if not text or not url:
                continue
            if not (url.startswith("https://") or url.startswith("http://") or url.startswith("tg://")):
                continue
            row.append(InlineKeyboardButton(text=text, url=url))
        if row:
            rows.append(row)
    return rows or None


def buttons_to_push_actions(rows: list[list[InlineKeyboardButton]] | None) -> list[dict[str, str]] | None:
    """Convert Telegram button rows to Web Push notification actions.

    Browsers cap visible actions at ~2 — we keep the first two safe URL buttons.
    URLs outside the allowlist are silently dropped from the push payload
    (the same buttons still render in Telegram).
    """
    if not rows:
        return None
    actions: list[dict[str, str]] = []
    for row in rows:
        for btn in row:
            safe_url = _safe_push_url(btn.url)
            if not safe_url:
                continue
            actions.append({
                "action": f"open_{len(actions)}",
                "title": btn.text[:32],
                "url": safe_url,
            })
            if len(actions) >= 2:
                return actions
    return actions or None


def first_button_url(rows: list[list[InlineKeyboardButton]] | None) -> str | None:
    """Pick a URL to use as primary click target for a push notification.

    Returns None if no button has an allowed-host URL — the SW then defaults to '/'.
    """
    if not rows:
        return None
    for row in rows:
        for btn in row:
            safe_url = _safe_push_url(btn.url)
            if safe_url:
                return safe_url
    return None


# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------

async def send_telegram_to_users(
    bot: Bot,
    users: Iterable[Users],
    *,
    orig_chat_id: int,
    orig_message_id: int,
    reply_markup_rows: list[list[InlineKeyboardButton]] | None = None,
    on_progress: Callable[[int, int, int, int], Awaitable[None]] | None = None,
) -> tuple[int, int]:
    """Copy a prepared message to each user, optionally attaching buttons.

    Returns (success, failure).
    """
    user_list = list(users)
    total = len(user_list)
    if total == 0:
        return 0, 0

    reply_markup = (
        InlineKeyboardMarkup(inline_keyboard=reply_markup_rows)
        if reply_markup_rows
        else None
    )

    success = failure = 0
    progress_step = max(1, total // 10)
    for index, user in enumerate(user_list, start=1):
        try:
            await bot.copy_message(
                chat_id=user.id,
                from_chat_id=orig_chat_id,
                message_id=orig_message_id,
                reply_markup=reply_markup,
            )
            success += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(getattr(exc, "retry_after", 2) + 0.5)
            try:
                await bot.copy_message(
                    chat_id=user.id,
                    from_chat_id=orig_chat_id,
                    message_id=orig_message_id,
                    reply_markup=reply_markup,
                )
                success += 1
            except Exception:
                failure += 1
        except TelegramForbiddenError:
            failure += 1
        except Exception as exc:
            failure += 1
            logger.debug("telegram broadcast send failed user=%s err=%s", user.id, exc)

        if on_progress and (index % progress_step == 0 or index == total):
            try:
                await on_progress(index, total, success, failure)
            except Exception:
                pass
        # ~20 msg/sec — under Telegram global broadcast limits.
        await asyncio.sleep(0.05)

    return success, failure


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BroadcastChannels:
    telegram: bool = True
    web_push: bool = False

    @property
    def label(self) -> str:
        if self.telegram and self.web_push:
            return "Telegram + PWA Push"
        if self.web_push:
            return "Только PWA Push"
        return "Только Telegram"


async def run_broadcast(
    bot: Bot,
    users: list[Users],
    *,
    channels: BroadcastChannels,
    telegram_message: dict[str, Any] | None = None,
    push_message: dict[str, Any] | None = None,
    reply_markup_rows: list[list[InlineKeyboardButton]] | None = None,
    on_progress: Callable[[str, int, int, int, int], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Run a broadcast across the chosen channels.

    `telegram_message` keys:   orig_chat_id, orig_message_id
    `push_message`  keys:      title, body, url (optional), icon (optional)
    """
    result: dict[str, Any] = {
        "users_total": len(users),
        "telegram": None,
        "push": None,
    }

    async def _tg_progress(processed: int, total: int, ok: int, fail: int) -> None:
        if on_progress:
            await on_progress("telegram", processed, total, ok, fail)

    async def _push_progress(processed: int, total: int, ok: int, fail: int) -> None:
        if on_progress:
            await on_progress("push", processed, total, ok, fail)

    if channels.telegram and telegram_message is not None:
        ok, fail = await send_telegram_to_users(
            bot,
            users,
            orig_chat_id=int(telegram_message["orig_chat_id"]),
            orig_message_id=int(telegram_message["orig_message_id"]),
            reply_markup_rows=reply_markup_rows,
            on_progress=_tg_progress,
        )
        result["telegram"] = {"success": ok, "failure": fail}

    if channels.web_push and push_message is not None and web_push_is_configured():
        stats = await send_push_to_users(
            (u.id for u in users),
            title=str(push_message.get("title", "")),
            body=str(push_message.get("body", "")),
            url=push_message.get("url"),
            icon=push_message.get("icon"),
            actions=push_message.get("actions"),
            tag=push_message.get("tag") or "vectra-broadcast",
            on_progress=_push_progress,
        )
        result["push"] = stats

    return result
