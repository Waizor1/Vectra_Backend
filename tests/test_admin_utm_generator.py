from __future__ import annotations

from types import SimpleNamespace

import pytest

from bloobcat.bot.routes.admin import utm_generator as utm_module


class CaptureMessage:
    def __init__(self, *, text: str = "", user_id: int = 4242):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id, language_code="ru")
        self.bot = SimpleNamespace()
        self.answers: list[dict[str, object]] = []

    async def answer(self, text: str, parse_mode: str | None = None, **_: object):
        self.answers.append({"text": text, "parse_mode": parse_mode})


class FakeState:
    def __init__(self) -> None:
        self.cleared = False
        self.state: object = None

    async def set_state(self, state) -> None:
        self.state = state

    async def clear(self) -> None:
        self.cleared = True


@pytest.mark.asyncio
async def test_cmd_utm_prompts_and_sets_state():
    message = CaptureMessage()
    state = FakeState()

    await utm_module.cmd_utm(message, state)

    assert state.state == utm_module.UtmForm.waiting_for_source
    assert state.cleared is False
    assert len(message.answers) == 1
    text = str(message.answers[0]["text"])
    assert "UTM" in text
    assert "START" in text


@pytest.mark.asyncio
async def test_process_utm_source_builds_chat_start_link(monkeypatch):
    async def fake_username() -> str:
        return "VectraConnect_bot"

    monkeypatch.setattr(utm_module, "get_bot_username", fake_username)

    message = CaptureMessage(text="vk_ads", user_id=12345)
    state = FakeState()
    fake_bot = SimpleNamespace()

    await utm_module.process_utm_source(message, state, fake_bot)

    assert state.cleared is True
    assert len(message.answers) == 1
    body = str(message.answers[0]["text"])
    assert "https://t.me/VectraConnect_bot?start=vk_ads-12345" in body
    assert "startapp" not in body
    assert message.answers[0]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_process_utm_source_rejects_invalid_chars(monkeypatch):
    async def fake_username() -> str:
        return "VectraConnect_bot"

    monkeypatch.setattr(utm_module, "get_bot_username", fake_username)

    message = CaptureMessage(text="vk ads!")
    state = FakeState()
    fake_bot = SimpleNamespace()

    await utm_module.process_utm_source(message, state, fake_bot)

    assert state.cleared is False
    assert len(message.answers) == 1
    assert "Некорректное" in str(message.answers[0]["text"])


@pytest.mark.asyncio
async def test_process_utm_source_rejects_overlong_input(monkeypatch):
    async def fake_username() -> str:
        return "VectraConnect_bot"

    monkeypatch.setattr(utm_module, "get_bot_username", fake_username)

    message = CaptureMessage(text="a" * (utm_module.UTM_SOURCE_MAX_LEN + 1))
    state = FakeState()
    fake_bot = SimpleNamespace()

    await utm_module.process_utm_source(message, state, fake_bot)

    assert state.cleared is False
    assert len(message.answers) == 1
    assert "Некорректное" in str(message.answers[0]["text"])


@pytest.mark.asyncio
async def test_process_utm_source_handles_missing_bot_username(monkeypatch):
    async def fake_username() -> str:
        return ""

    monkeypatch.setattr(utm_module, "get_bot_username", fake_username)

    message = CaptureMessage(text="vk_ads")
    state = FakeState()
    fake_bot = SimpleNamespace()

    await utm_module.process_utm_source(message, state, fake_bot)

    assert state.cleared is True
    assert len(message.answers) == 1
    assert "ошибка" in str(message.answers[0]["text"]).lower()
