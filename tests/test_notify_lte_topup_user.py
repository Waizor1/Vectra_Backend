"""Tests for notify_lte_topup_user in bloobcat/bot/notifications/lte.py.

Covers:
- RU locale copy snapshot
- EN locale copy snapshot
- TelegramForbiddenError swallowed (returns False)
- Invalid/negative user_id skip
"""

from __future__ import annotations

import types
import sys
from unittest.mock import AsyncMock, patch

import pytest


def _make_user(user_id: int, lang: str = "ru"):
    return types.SimpleNamespace(id=user_id, language_code=lang)


def _install_lte_module_stubs():
    """Minimal stubs for lte.py dependencies."""
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # aiogram exceptions stub
    aiogram_exc = types.ModuleType("aiogram.exceptions")
    aiogram_exc.TelegramForbiddenError = type("TelegramForbiddenError", (Exception,), {})
    aiogram_exc.TelegramBadRequest = type("TelegramBadRequest", (Exception,), {})
    aiogram_mod = types.ModuleType("aiogram")
    aiogram_mod.exceptions = aiogram_exc
    sys.modules["aiogram"] = aiogram_mod
    sys.modules["aiogram.exceptions"] = aiogram_exc

    # bot stub
    bot_mod = types.ModuleType("bloobcat.bot.bot")
    bot_mod.bot = AsyncMock()
    sys.modules["bloobcat.bot.bot"] = bot_mod

    # error_handler stub
    eh_mod = types.ModuleType("bloobcat.bot.error_handler")
    eh_mod.handle_telegram_forbidden_error = AsyncMock(return_value=True)
    eh_mod.handle_telegram_bad_request = AsyncMock(return_value=True)
    eh_mod.reset_user_failed_count = AsyncMock(return_value=True)
    sys.modules["bloobcat.bot.error_handler"] = eh_mod

    # keyboard stub (not used by notify_lte_topup_user but imported at top of lte.py)
    kb_mod = types.ModuleType("bloobcat.bot.keyboard")
    kb_mod.webapp_inline_button = AsyncMock(return_value=None)
    sys.modules["bloobcat.bot.keyboard"] = kb_mod

    # localization stub
    notifications_path = str(project_root / "bloobcat" / "bot" / "notifications")
    bot_pkg = types.ModuleType("bloobcat.bot")
    bot_pkg.__path__ = [str(project_root / "bloobcat" / "bot")]
    sys.modules.setdefault("bloobcat.bot", bot_pkg)

    loc_pkg = types.ModuleType("bloobcat.bot.notifications")
    loc_pkg.__path__ = [notifications_path]
    sys.modules["bloobcat.bot.notifications"] = loc_pkg

    loc_mod = types.ModuleType("bloobcat.bot.notifications.localization")

    def get_user_locale(user):
        return getattr(user, "language_code", "ru") or "ru"

    loc_mod.get_user_locale = get_user_locale
    sys.modules["bloobcat.bot.notifications.localization"] = loc_mod

    # logger stub
    logger_mod = types.ModuleType("bloobcat.logger")

    class _Logger:
        def info(self, *a, **kw): ...
        def warning(self, *a, **kw): ...
        def error(self, *a, **kw): ...
        def debug(self, *a, **kw): ...

    logger_mod.get_logger = lambda *a, **kw: _Logger()
    sys.modules["bloobcat.logger"] = logger_mod

    # Reset cached lte module to pick up fresh stubs
    sys.modules.pop("bloobcat.bot.notifications.lte", None)


_install_lte_module_stubs()


def _get_lte_module():
    import importlib
    return importlib.import_module("bloobcat.bot.notifications.lte")


@pytest.mark.asyncio
async def test_notify_lte_topup_user_ru_copy():
    lte = _get_lte_module()
    bot_stub = sys.modules["bloobcat.bot.bot"].bot
    bot_stub.send_message = AsyncMock(return_value=types.SimpleNamespace(message_id=1))

    user = _make_user(999, lang="ru")
    result = await lte.notify_lte_topup_user(
        user=user, lte_gb_delta=5, lte_gb_after=15, method="platega_lte_topup"
    )

    assert result is True
    bot_stub.send_message.assert_called_once()
    text = bot_stub.send_message.call_args[0][1]
    assert "5 ГБ" in text
    assert "15 ГБ" in text
    assert "Спасибо" in text


@pytest.mark.asyncio
async def test_notify_lte_topup_user_en_copy():
    lte = _get_lte_module()
    bot_stub = sys.modules["bloobcat.bot.bot"].bot
    bot_stub.send_message = AsyncMock(return_value=types.SimpleNamespace(message_id=2))

    user = _make_user(888, lang="en")
    result = await lte.notify_lte_topup_user(
        user=user, lte_gb_delta=10, lte_gb_after=20, method="yookassa_lte_topup"
    )

    assert result is True
    bot_stub.send_message.assert_called_once()
    text = bot_stub.send_message.call_args[0][1]
    assert "10 GB" in text
    assert "20 GB" in text
    assert "enjoy" in text


@pytest.mark.asyncio
async def test_notify_lte_topup_user_forbidden_returns_false():
    lte = _get_lte_module()
    TelegramForbiddenError = sys.modules["aiogram.exceptions"].TelegramForbiddenError
    bot_stub = sys.modules["bloobcat.bot.bot"].bot
    bot_stub.send_message = AsyncMock(side_effect=TelegramForbiddenError("blocked"))

    user = _make_user(777, lang="ru")
    result = await lte.notify_lte_topup_user(
        user=user, lte_gb_delta=3, lte_gb_after=8, method="balance_lte_topup"
    )

    assert result is False


@pytest.mark.asyncio
async def test_notify_lte_topup_user_skips_invalid_user_id():
    lte = _get_lte_module()
    bot_stub = sys.modules["bloobcat.bot.bot"].bot
    bot_stub.send_message = AsyncMock()

    user_neg = types.SimpleNamespace(id=-1, language_code="ru")
    result = await lte.notify_lte_topup_user(
        user=user_neg, lte_gb_delta=5, lte_gb_after=10, method="test"
    )
    assert result is False
    bot_stub.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_notify_lte_topup_user_formats_gb_integer():
    lte = _get_lte_module()
    bot_stub = sys.modules["bloobcat.bot.bot"].bot
    bot_stub.send_message = AsyncMock(return_value=types.SimpleNamespace(message_id=3))

    user = _make_user(555, lang="en")
    await lte.notify_lte_topup_user(
        user=user, lte_gb_delta=1, lte_gb_after=6, method="test"
    )
    text = bot_stub.send_message.call_args[0][1]
    # _format_gb should produce "1" not "1.0"
    assert "1.0 GB" not in text
    assert "1 GB" in text
