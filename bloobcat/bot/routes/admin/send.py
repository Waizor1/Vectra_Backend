"""Admin broadcast wizard.

`/send` launches a multi-step FSM:
    1. Pick channels        (Telegram / PWA Push / both)
    2. Pick segment         (14 predefined + free-form UTM)
    3. If push channel — ask for push title/body
       If telegram channel — ask for the source message to copy
    4. Optional inline buttons (URL only, parsed from text)
    5. Preview + confirm

Delivery is delegated to `bloobcat.bot.notifications.broadcast.run_broadcast`,
which handles channel fan-out, progress callbacks, and per-user error
isolation.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from html import escape as html_escape

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bloobcat.bot.notifications.broadcast import (
    BroadcastChannels,
    SEGMENT_BY_KEY,
    SEGMENTS,
    buttons_to_push_actions,
    first_button_url,
    parse_buttons_spec,
    resolve_segment,
    run_broadcast,
)
from bloobcat.bot.notifications.web_push import is_configured as web_push_is_configured
from bloobcat.bot.routes.admin.functions import IsAdmin
from bloobcat.logger import get_logger

from .keyboards import get_back_to_main_menu, get_broadcast_channel_keyboard
from .states import SendFSM

logger = get_logger("bot_admin_send")

router = Router()

# Per-admin in-flight broadcasts. Prevents the same admin from double-tapping
# "Подтвердить" (callback race) or starting a second `/send` while the first
# is still delivering. Keyed by Telegram user id.
_in_flight_broadcasts: set[int] = set()
_in_flight_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Step 0: /send → channel picker
# ---------------------------------------------------------------------------

def _segment_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    # 2 columns where short labels allow it; we keep one button per row for safety —
    # labels are long enough that two-per-row clips on phones.
    for seg in SEGMENTS:
        rows.append([InlineKeyboardButton(text=seg.label, callback_data=f"send_seg:{seg.key}")])
    rows.append([InlineKeyboardButton(text="❌ Отменить", callback_data="send_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _channel_label(key: str) -> str:
    return {
        "tg": "Только Telegram",
        "push": "Только PWA Push",
        "both": "Telegram + PWA Push",
    }.get(key, "Только Telegram")


@router.message(Command("send"), IsAdmin())
async def send(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "📣 <b>Новая рассылка</b>\n\nВыберите каналы доставки:",
        reply_markup=get_broadcast_channel_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(SendFSM.waiting_for_channel)


@router.callback_query(
    SendFSM.waiting_for_channel,
    lambda c: c.data and (c.data.startswith("send_channel:") or c.data == "send_cancel"),
    IsAdmin(),
)
async def process_channel_callback(callback_query: CallbackQuery, state: FSMContext) -> None:
    if callback_query.data == "send_cancel":
        await _cancel_and_close(callback_query, state)
        return

    key = callback_query.data.split(":", 1)[1]
    if key == "disabled":
        await callback_query.answer("PWA Push ещё не настроен (нет VAPID-ключей)", show_alert=True)
        return

    if key not in {"tg", "push", "both"}:
        await callback_query.answer("Неизвестный канал", show_alert=True)
        return

    await state.update_data(channel_key=key, channel_label=_channel_label(key))
    await callback_query.answer()
    await callback_query.message.edit_reply_markup()
    await callback_query.message.answer(
        f"Канал: <b>{_channel_label(key)}</b>\n\nВыберите аудиторию:",
        reply_markup=_segment_keyboard(),
        parse_mode="HTML",
    )
    await state.set_state(SendFSM.waiting_for_segment)


# ---------------------------------------------------------------------------
# Step 1: segment picker
# ---------------------------------------------------------------------------

@router.callback_query(
    SendFSM.waiting_for_segment,
    lambda c: c.data and (c.data.startswith("send_seg:") or c.data == "send_cancel"),
    IsAdmin(),
)
async def process_segment_callback(callback_query: CallbackQuery, state: FSMContext) -> None:
    if callback_query.data == "send_cancel":
        await _cancel_and_close(callback_query, state)
        return

    seg_key = callback_query.data.split(":", 1)[1]
    seg = SEGMENT_BY_KEY.get(seg_key)
    if seg is None:
        await callback_query.answer("Неизвестный сегмент", show_alert=True)
        return

    await state.update_data(segment_key=seg.key, segment_label=seg.label)
    await callback_query.answer()
    await callback_query.message.edit_reply_markup()

    if seg.needs_value:
        await callback_query.message.answer(seg.value_prompt or "Введите значение:")
        await state.set_state(SendFSM.waiting_for_segment_value)
        return

    await _advance_after_segment(callback_query.message, state)


@router.message(SendFSM.waiting_for_segment_value, IsAdmin())
async def receive_segment_value(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value or len(value) > 200:
        await message.answer("Введите непустое значение длиной до 200 символов.")
        return
    await state.update_data(segment_value=value)
    await _advance_after_segment(message, state)


async def _advance_after_segment(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    channel_key = data.get("channel_key", "tg")

    if channel_key == "push":
        await message.answer(
            "📱 <b>PWA Push</b>\n\nВведите <b>заголовок</b> уведомления (до 60 символов):",
            parse_mode="HTML",
        )
        await state.set_state(SendFSM.waiting_for_push_title)
        return

    # tg or both → need source TG message first
    await message.answer(
        "Отправьте сообщение для рассылки (текст и/или одно вложение: фото, видео, документ)."
    )
    await state.set_state(SendFSM.waiting_for_message)


# ---------------------------------------------------------------------------
# Step 2a: push title/body (push-only path)
# ---------------------------------------------------------------------------

@router.message(SendFSM.waiting_for_push_title, IsAdmin())
async def receive_push_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title or len(title) > 60:
        await message.answer("Заголовок должен быть длиной 1–60 символов.")
        return
    await state.update_data(push_title=title)
    await message.answer(
        "Введите <b>текст</b> уведомления (до 240 символов):",
        parse_mode="HTML",
    )
    await state.set_state(SendFSM.waiting_for_push_body)


@router.message(SendFSM.waiting_for_push_body, IsAdmin())
async def receive_push_body(message: Message, state: FSMContext) -> None:
    body = (message.text or "").strip()
    if not body or len(body) > 240:
        await message.answer("Текст должен быть длиной 1–240 символов.")
        return
    await state.update_data(push_body=body)
    await _ask_for_buttons(message, state)


# ---------------------------------------------------------------------------
# Step 2b: Telegram source message (tg / both path)
# ---------------------------------------------------------------------------

@router.message(SendFSM.waiting_for_message, IsAdmin())
async def receive_tg_message(message: Message, state: FSMContext) -> None:
    if not (message.text or message.photo or message.video or message.document):
        await message.answer("Сообщение должно содержать текст или вложение. Попробуйте ещё раз.")
        return
    await state.update_data(
        orig_chat_id=message.chat.id,
        orig_message_id=message.message_id,
    )
    # If this is a "both" run, also pre-fill push title/body from the text to save a step
    data = await state.get_data()
    channel_key = data.get("channel_key", "tg")
    if channel_key == "both":
        text = (message.text or message.caption or "").strip()
        if text:
            push_title = text.split("\n", 1)[0][:60] or "Vectra Connect"
            push_body = text[:240]
            await state.update_data(push_title=push_title, push_body=push_body)
        else:
            await state.update_data(push_title="Vectra Connect", push_body="Новое сообщение от Vectra Connect")
    await _ask_for_buttons(message, state)


# ---------------------------------------------------------------------------
# Step 3: optional inline buttons
# ---------------------------------------------------------------------------

_BUTTONS_HELP = (
    "🔘 <b>Кнопки</b> (необязательно)\n\n"
    "Отправьте список кнопок — по одной строке на ряд, формат:\n"
    "<code>Текст кнопки | https://url</code>\n\n"
    "Несколько кнопок в одном ряду — разделяйте <code>||</code>:\n"
    "<code>Открыть | https://app.vectra-pro.net || Помощь | https://t.me/support</code>\n\n"
    "Чтобы пропустить — отправьте <code>-</code>"
)


async def _ask_for_buttons(message: Message, state: FSMContext) -> None:
    await message.answer(_BUTTONS_HELP, parse_mode="HTML")
    await state.set_state(SendFSM.waiting_for_buttons)


@router.message(SendFSM.waiting_for_buttons, IsAdmin())
async def receive_buttons(message: Message, state: FSMContext) -> None:
    spec = (message.text or "").strip()
    rows = parse_buttons_spec(spec)
    if spec and spec.lower() not in {"-", "нет", "no", "none", "skip"} and not rows:
        await message.answer(
            "Не удалось распознать ни одной валидной кнопки.\n"
            "Проверьте формат и попробуйте снова, или отправьте <code>-</code> чтобы пропустить.",
            parse_mode="HTML",
        )
        return

    serialized: list[list[dict[str, str]]] | None = (
        [
            [{"text": btn.text, "url": btn.url or ""} for btn in row]
            for row in rows
        ]
        if rows else None
    )
    await state.update_data(buttons=serialized)
    await _show_preview(message, state)


# ---------------------------------------------------------------------------
# Step 4: preview + confirmation
# ---------------------------------------------------------------------------

def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить и запустить", callback_data="send_confirm")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="send_cancel")],
    ])


def _rows_from_serialized(serialized: list[list[dict[str, str]]] | None) -> list[list[InlineKeyboardButton]] | None:
    if not serialized:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for row in serialized:
        built: list[InlineKeyboardButton] = []
        for item in row:
            text = (item.get("text") or "").strip()
            url = (item.get("url") or "").strip()
            if text and url:
                built.append(InlineKeyboardButton(text=text, url=url))
        if built:
            rows.append(built)
    return rows or None


async def _show_preview(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    channel_key = data.get("channel_key", "tg")
    channel_label = data.get("channel_label", "Telegram")
    segment_label = data.get("segment_label", "?")
    segment_key = data.get("segment_key", "all")
    segment_value = data.get("segment_value")

    users = await resolve_segment(segment_key, segment_value)
    audience_count = len(users)
    await state.update_data(audience_count=audience_count)

    summary_lines = [
        "🧾 <b>Превью рассылки</b>",
        f"• Канал: <b>{html_escape(channel_label)}</b>",
        f"• Аудитория: <b>{html_escape(segment_label)}</b>",
    ]
    if segment_value:
        summary_lines.append(f"• Параметр сегмента: <code>{html_escape(segment_value)}</code>")
    summary_lines.append(f"• Получателей: <b>{audience_count}</b>")

    rows = _rows_from_serialized(data.get("buttons"))
    if rows:
        summary_lines.append(f"• Кнопок: <b>{sum(len(r) for r in rows)}</b> в {len(rows)} ряд(а)")

    if channel_key in {"push", "both"}:
        push_title = data.get("push_title", "")
        push_body = data.get("push_body", "")
        summary_lines.append(
            f"\n📱 <b>Push:</b>\n<b>{html_escape(push_title)}</b>\n{html_escape(push_body)}"
        )

    # First — copy the source TG message so the admin can verify rendering with the actual buttons
    if channel_key in {"tg", "both"}:
        orig_chat_id = data.get("orig_chat_id")
        orig_message_id = data.get("orig_message_id")
        if orig_chat_id and orig_message_id:
            try:
                await message.bot.copy_message(
                    chat_id=message.chat.id,
                    from_chat_id=orig_chat_id,
                    message_id=orig_message_id,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
                )
            except Exception as exc:
                logger.error(f"preview copy failed: {exc}")

    if audience_count == 0:
        await message.answer(
            "\n".join(summary_lines)
            + "\n\n⚠️ Сегмент пустой — рассылка не имеет получателей.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить", callback_data="send_cancel")],
            ]),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "\n".join(summary_lines),
            reply_markup=_confirm_keyboard(),
            parse_mode="HTML",
        )
    await state.set_state(SendFSM.waiting_for_confirmation)


# ---------------------------------------------------------------------------
# Step 5: execution
# ---------------------------------------------------------------------------

@router.callback_query(
    SendFSM.waiting_for_confirmation,
    lambda c: c.data == "send_confirm",
    IsAdmin(),
)
async def confirm_broadcast(callback_query: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    admin_id = callback_query.from_user.id if callback_query.from_user else 0

    # Reject double-tap / second `/send` while a previous run is still
    # in-flight. Without this, two callbacks racing through here would each
    # call `run_broadcast` and the audience would receive a duplicate.
    async with _in_flight_lock:
        if admin_id in _in_flight_broadcasts:
            await callback_query.answer("Рассылка уже выполняется — дождитесь её завершения.", show_alert=True)
            return
        _in_flight_broadcasts.add(admin_id)

    await callback_query.answer()
    data = await state.get_data()
    try:
        await callback_query.message.edit_reply_markup()
    except Exception:
        pass

    channel_key = data.get("channel_key", "tg")
    channel_label = data.get("channel_label", "Telegram")
    segment_label = data.get("segment_label", "?")
    segment_key = data.get("segment_key", "all")
    segment_value = data.get("segment_value")

    progress_message = await callback_query.message.answer(
        f"🚀 Запускаю рассылку…\nКанал: <b>{html_escape(channel_label)}</b>\nАудитория: <b>{html_escape(segment_label)}</b>",
        parse_mode="HTML",
    )
    await state.clear()

    try:
        users = await resolve_segment(segment_key, segment_value)
        total = len(users)
        if total == 0:
            await progress_message.edit_text(
                "⚠️ Аудитория пустая — рассылка не выполнена.",
                reply_markup=get_back_to_main_menu(),
            )
            return

        channels = BroadcastChannels(
            telegram=channel_key in {"tg", "both"},
            web_push=channel_key in {"push", "both"} and web_push_is_configured(),
        )

        rows = _rows_from_serialized(data.get("buttons"))
        push_actions = buttons_to_push_actions(rows)
        primary_url = first_button_url(rows)

        telegram_message = None
        if channels.telegram:
            telegram_message = {
                "orig_chat_id": data.get("orig_chat_id"),
                "orig_message_id": data.get("orig_message_id"),
            }

        push_message = None
        if channels.web_push:
            push_message = {
                "title": data.get("push_title") or "Vectra Connect",
                "body": data.get("push_body") or "",
                "url": primary_url or "/",
                "actions": push_actions,
                "tag": "vectra-broadcast",
            }

        last_update_at = 0.0
        progress_lock = asyncio.Lock()
        progress_state = {"tg": (0, 0, 0, 0), "push": (0, 0, 0, 0)}

        async def _on_progress(channel: str, processed: int, ch_total: int, ok: int, fail: int) -> None:
            nonlocal last_update_at
            now = time.monotonic()
            async with progress_lock:
                progress_state[channel if channel == "push" else "tg"] = (processed, ch_total, ok, fail)
                if now - last_update_at < 1.2 and processed != ch_total:
                    return
                last_update_at = now
                tg_p, tg_t, tg_ok, tg_fail = progress_state["tg"]
                push_p, push_t, push_ok, push_fail = progress_state["push"]
                lines = ["📡 Рассылка в процессе…"]
                if channels.telegram and tg_t:
                    pct = round(tg_p / tg_t * 100) if tg_t else 0
                    lines.append(f"• Telegram: {tg_p}/{tg_t} ({pct}%) ✅ {tg_ok} ❌ {tg_fail}")
                if channels.web_push and push_t:
                    pct = round(push_p / push_t * 100) if push_t else 0
                    lines.append(f"• PWA Push: {push_p}/{push_t} ({pct}%) ✅ {push_ok} ❌ {push_fail}")
                try:
                    await progress_message.edit_text("\n".join(lines))
                except Exception:
                    pass

        result = await run_broadcast(
            bot,
            users,
            channels=channels,
            telegram_message=telegram_message,
            push_message=push_message,
            reply_markup_rows=rows,
            on_progress=_on_progress,
        )

        # Final summary
        summary_lines = [
            "✅ <b>Рассылка завершена</b>",
            f"• Канал: <b>{html_escape(channel_label)}</b>",
            f"• Аудитория: <b>{html_escape(segment_label)}</b> ({total} польз.)",
        ]
        tg_stats = result.get("telegram")
        if tg_stats:
            summary_lines.append(
                f"\n📨 Telegram: ✅ <b>{tg_stats['success']}</b>   ❌ <b>{tg_stats['failure']}</b>"
            )
        push_stats = result.get("push")
        if push_stats:
            summary_lines.append(
                "\n📱 PWA Push:"
                f"\n  • Подписки доставлены: ✅ <b>{push_stats['success_subs']}</b>"
                f"   ❌ <b>{push_stats['failure_subs']}</b>"
                f"\n  • Юзеров с подпиской: <b>{push_stats['users_with_subs']}</b>/{push_stats['users_total']}"
            )
        await progress_message.edit_text(
            "\n".join(summary_lines),
            reply_markup=get_back_to_main_menu(),
            parse_mode="HTML",
        )
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error(f"Broadcast error: {exc}\n{tb}")
        try:
            await progress_message.edit_text(
                f"❌ <b>Ошибка рассылки</b>\n\n<code>{html_escape(str(exc))[:500]}</code>",
                reply_markup=get_back_to_main_menu(),
                parse_mode="HTML",
            )
        except Exception:
            pass
    finally:
        async with _in_flight_lock:
            _in_flight_broadcasts.discard(admin_id)


@router.callback_query(
    SendFSM.waiting_for_confirmation,
    lambda c: c.data == "send_cancel",
    IsAdmin(),
)
async def cancel_broadcast(callback_query: CallbackQuery, state: FSMContext) -> None:
    await _cancel_and_close(callback_query, state)


async def _cancel_and_close(callback_query: CallbackQuery, state: FSMContext) -> None:
    await callback_query.answer()
    try:
        await callback_query.message.edit_reply_markup()
    except Exception:
        pass
    await callback_query.message.answer(
        "❌ <b>Рассылка отменена</b>",
        reply_markup=get_back_to_main_menu(),
        parse_mode="HTML",
    )
    await state.clear()
