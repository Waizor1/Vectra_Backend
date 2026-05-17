"""Regression tests for the /send broadcast FSM caption-length guard.

Background: VECTRA-BACKEND-E (release 1.112.0) — Telegram returned
`Bad Request: message caption is too long` when the broadcast FSM tried to
copy_message a source media post whose caption exceeded the Telegram-side
1024-char limit. The fix early-rejects the source message in
`receive_tg_message` so the admin gets an actionable hint instead of a silent
log + broken preview + ultimately a doomed run_broadcast.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bloobcat.bot.routes.admin.send import (
    TELEGRAM_CAPTION_MAX_LENGTH,
    TELEGRAM_TEXT_MAX_LENGTH,
    receive_tg_message,
)


def _make_message(*, text=None, caption=None, has_photo=False) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = caption
    msg.photo = [MagicMock()] if has_photo else None
    msg.video = None
    msg.document = None
    msg.animation = None
    msg.audio = None
    msg.voice = None
    msg.chat.id = 100
    msg.message_id = 200
    msg.answer = AsyncMock()
    return msg


def _make_state(initial: dict | None = None) -> MagicMock:
    state = MagicMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value=initial or {})
    state.set_state = AsyncMock()
    return state


@pytest.mark.asyncio
async def test_receive_tg_message_rejects_overlong_media_caption():
    msg = _make_message(caption="x" * (TELEGRAM_CAPTION_MAX_LENGTH + 1), has_photo=True)
    state = _make_state()

    await receive_tg_message(msg, state)

    msg.answer.assert_awaited_once()
    answer_text = msg.answer.await_args.args[0]
    assert "1024" in answer_text or "слишком длинная" in answer_text
    state.update_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_receive_tg_message_accepts_media_caption_at_limit():
    msg = _make_message(caption="x" * TELEGRAM_CAPTION_MAX_LENGTH, has_photo=True)
    state = _make_state({"channel_key": "tg"})

    await receive_tg_message(msg, state)

    state.update_data.assert_any_await(orig_chat_id=100, orig_message_id=200)


@pytest.mark.asyncio
async def test_receive_tg_message_rejects_overlong_plain_text():
    msg = _make_message(text="y" * (TELEGRAM_TEXT_MAX_LENGTH + 1))
    state = _make_state()

    await receive_tg_message(msg, state)

    msg.answer.assert_awaited_once()
    answer_text = msg.answer.await_args.args[0]
    assert "4096" in answer_text or "слишком длинн" in answer_text
    state.update_data.assert_not_awaited()
