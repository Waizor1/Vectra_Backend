from __future__ import annotations

from types import SimpleNamespace

import pytest

from bloobcat.bot import keyboard as keyboard_module
from bloobcat.bot.routes import start as start_module


class CaptureMessage:
    def __init__(self, *, language_code: str | None = "ru", user_id: int = 1001):
        self.from_user = SimpleNamespace(id=user_id, language_code=language_code)
        self.bot = SimpleNamespace()
        self.answers: list[dict[str, object]] = []

    async def answer(self, text: str, reply_markup=None):
        self.answers.append({"text": text, "reply_markup": reply_markup})


def _button_rows(reply_markup):
    return reply_markup.inline_keyboard


@pytest.mark.asyncio
async def test_start_keyboard_keeps_launch_and_adds_documents(monkeypatch):
    monkeypatch.setattr(keyboard_module.telegram_settings, "miniapp_url", "https://app.vectra-pro.net")
    monkeypatch.setattr(start_module.telegram_settings, "miniapp_url", "https://app.vectra-pro.net")

    async def fake_get_or_none(**_kwargs):
        return None

    monkeypatch.setattr(start_module.Users, "get_or_none", fake_get_or_none)

    message = CaptureMessage(language_code="ru")
    await start_module.command_start_handler(message, SimpleNamespace(args="ref_123"))

    assert len(message.answers) == 1
    assert "Это Vectra Connect" in str(message.answers[0]["text"])

    rows = _button_rows(message.answers[0]["reply_markup"])
    assert rows[0][0].text == "Запустить"
    assert rows[0][0].web_app.url == "https://app.vectra-pro.net?start=ref_123"
    assert rows[1][0].text == "Документы"
    assert rows[1][0].url == "https://app.vectra-pro.net/legal/"


@pytest.mark.asyncio
async def test_documents_command_returns_permanent_legal_links(monkeypatch):
    monkeypatch.setattr(keyboard_module.telegram_settings, "miniapp_url", "https://app.vectra-pro.net/")

    message = CaptureMessage(language_code="ru")
    await start_module.documents_handler(message)

    assert len(message.answers) == 1
    assert "Документы Vectra Connect" in str(message.answers[0]["text"])
    assert "всегда доступны" in str(message.answers[0]["text"])

    rows = _button_rows(message.answers[0]["reply_markup"])
    assert rows[0][0].text == "Политика конфиденциальности"
    assert rows[0][0].url == "https://app.vectra-pro.net/legal/privacy/"
    assert rows[1][0].text == "Пользовательское соглашение"
    assert rows[1][0].url == "https://app.vectra-pro.net/legal/terms/"
    assert rows[2][0].text == "Поддержка"
    assert rows[2][0].url == "https://t.me/VectraConnect_support_bot"


@pytest.mark.asyncio
async def test_documents_command_localizes_english(monkeypatch):
    monkeypatch.setattr(keyboard_module.telegram_settings, "miniapp_url", "https://app.vectra-pro.net")

    message = CaptureMessage(language_code="en")
    await start_module.documents_handler(message)

    assert "Vectra Connect documents" in str(message.answers[0]["text"])
    rows = _button_rows(message.answers[0]["reply_markup"])
    assert rows[0][0].text == "Privacy Policy"
    assert rows[1][0].text == "Terms of Use"
    assert rows[2][0].text == "Support"
