from __future__ import annotations

import importlib

import pytest


admin_notifications = importlib.import_module("bloobcat.bot.notifications.admin")


@pytest.fixture(autouse=True)
def _reset_admin_notification_state(monkeypatch):
    admin_notifications._admin_msg_stats.update(
        sent=0,
        failed=0,
        last_error=None,
        last_error_at=None,
        retry_scheduled=0,
        retry_sent=0,
    )
    monkeypatch.setattr(
        admin_notifications.admin_settings,
        "telegram_id",
        290606713,
        raising=False,
    )
    monkeypatch.setattr(
        admin_notifications.telegram_settings,
        "logs_channel",
        None,
        raising=False,
    )


@pytest.mark.asyncio
async def test_send_admin_message_prefers_logs_channel(monkeypatch):
    calls: list[dict] = []

    async def fake_send_message(
        *,
        chat_id,
        text,
        reply_markup=None,
        parse_mode=None,
        request_timeout=None,
    ):
        calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode,
                "request_timeout": request_timeout,
            }
        )

    monkeypatch.setattr(admin_notifications.bot, "send_message", fake_send_message)
    monkeypatch.setattr(
        admin_notifications.telegram_settings,
        "logs_channel",
        3686125598,
        raising=False,
    )

    delivered = await admin_notifications.send_admin_message("test notification")

    assert delivered is True
    assert len(calls) == 1
    assert calls[0]["chat_id"] == 3686125598
    assert calls[0]["parse_mode"] == "HTML"
    assert (
        calls[0]["request_timeout"]
        == admin_notifications.ADMIN_MESSAGE_REQUEST_TIMEOUT_SECONDS
    )
    assert admin_notifications.get_admin_msg_stats()["sent"] == 1
    assert admin_notifications.get_admin_msg_stats()["failed"] == 0


@pytest.mark.asyncio
async def test_send_admin_message_falls_back_to_personal_admin(monkeypatch):
    calls: list[dict] = []

    async def fake_send_message(
        *,
        chat_id,
        text,
        reply_markup=None,
        parse_mode=None,
        request_timeout=None,
    ):
        calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode,
                "request_timeout": request_timeout,
            }
        )

    monkeypatch.setattr(admin_notifications.bot, "send_message", fake_send_message)

    delivered = await admin_notifications.send_admin_message("test notification")

    assert delivered is True
    assert len(calls) == 1
    assert calls[0]["chat_id"] == 290606713
    assert (
        calls[0]["request_timeout"]
        == admin_notifications.ADMIN_MESSAGE_REQUEST_TIMEOUT_SECONDS
    )
    assert admin_notifications.get_admin_msg_stats()["sent"] == 1
    assert admin_notifications.get_admin_msg_stats()["failed"] == 0


@pytest.mark.asyncio
async def test_on_activated_bot_routes_registration_log_to_logs_channel(monkeypatch):
    calls: list[dict] = []

    async def fake_send_message(
        *,
        chat_id,
        text,
        reply_markup=None,
        parse_mode=None,
        request_timeout=None,
    ):
        calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode,
                "request_timeout": request_timeout,
            }
        )

    monkeypatch.setattr(admin_notifications.bot, "send_message", fake_send_message)
    monkeypatch.setattr(
        admin_notifications.telegram_settings,
        "logs_channel",
        3686125598,
        raising=False,
    )

    delivered = await admin_notifications.on_activated_bot(
        user_id=42,
        name="Alice <Admin>",
        referrer_id=11,
        referrer_name="Bob",
        utm="launch",
    )

    assert delivered is True
    assert len(calls) == 1
    assert calls[0]["chat_id"] == 3686125598
    assert calls[0]["parse_mode"] == "HTML"
    assert "#новый_пользователь" in calls[0]["text"]
    assert calls[0]["reply_markup"] is not None
    assert admin_notifications.get_admin_msg_stats()["sent"] == 1


@pytest.mark.asyncio
async def test_send_admin_message_returns_false_and_tracks_failure(monkeypatch):
    class DummyBadRequest(Exception):
        pass

    async def fake_send_message(
        *,
        chat_id,
        text,
        reply_markup=None,
        parse_mode=None,
        request_timeout=None,
    ):
        _ = chat_id, text, reply_markup, parse_mode, request_timeout
        raise DummyBadRequest("chat not found")

    monkeypatch.setattr(admin_notifications, "TelegramBadRequest", DummyBadRequest)
    monkeypatch.setattr(admin_notifications.bot, "send_message", fake_send_message)

    delivered = await admin_notifications.send_admin_message("test notification")

    stats = admin_notifications.get_admin_msg_stats()
    assert delivered is False
    assert stats["sent"] == 0
    assert stats["failed"] == 1
    assert stats["last_error"] == "chat not found"
    assert stats["last_error_at"] is not None


@pytest.mark.asyncio
async def test_send_admin_message_does_not_retry_plain_send_on_network_error(
    monkeypatch,
):
    class DummyNetworkError(Exception):
        pass

    calls: list[dict] = []
    scheduled: list[dict] = []

    async def fake_send_message(
        *,
        chat_id,
        text,
        reply_markup=None,
        parse_mode=None,
        request_timeout=None,
    ):
        calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode,
                "request_timeout": request_timeout,
            }
        )
        raise DummyNetworkError("Request timeout error")

    monkeypatch.setattr(
        admin_notifications, "TelegramNetworkError", DummyNetworkError
    )
    monkeypatch.setattr(
        admin_notifications,
        "_schedule_admin_message_retry",
        lambda text, reply_markup=None: scheduled.append(
            {"text": text, "reply_markup": reply_markup}
        ),
    )
    monkeypatch.setattr(admin_notifications.bot, "send_message", fake_send_message)

    delivered = await admin_notifications.send_admin_message(
        "test notification",
        reply_markup={"inline_keyboard": []},
    )

    stats = admin_notifications.get_admin_msg_stats()
    assert delivered is False
    assert len(calls) == 1
    assert calls[0]["reply_markup"] == {"inline_keyboard": []}
    assert (
        calls[0]["request_timeout"]
        == admin_notifications.ADMIN_MESSAGE_REQUEST_TIMEOUT_SECONDS
    )
    assert scheduled == [
        {"text": "test notification", "reply_markup": {"inline_keyboard": []}}
    ]
    assert stats["sent"] == 0
    assert stats["failed"] == 1
    assert stats["last_error"] == "Request timeout error"


@pytest.mark.asyncio
async def test_send_admin_message_retries_without_buttons_on_bad_request(monkeypatch):
    class DummyBadRequest(Exception):
        pass

    calls: list[dict] = []
    scheduled: list[dict] = []

    async def fake_send_message(
        *,
        chat_id,
        text,
        reply_markup=None,
        parse_mode=None,
        request_timeout=None,
    ):
        calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode,
                "request_timeout": request_timeout,
            }
        )
        if reply_markup is not None:
            raise DummyBadRequest("button invalid")

    monkeypatch.setattr(admin_notifications, "TelegramBadRequest", DummyBadRequest)
    monkeypatch.setattr(
        admin_notifications,
        "_schedule_admin_message_retry",
        lambda text, reply_markup=None: scheduled.append(
            {"text": text, "reply_markup": reply_markup}
        ),
    )
    monkeypatch.setattr(admin_notifications.bot, "send_message", fake_send_message)

    delivered = await admin_notifications.send_admin_message(
        "test notification",
        reply_markup={"inline_keyboard": []},
    )

    stats = admin_notifications.get_admin_msg_stats()
    assert delivered is True
    assert len(calls) == 2
    assert calls[0]["reply_markup"] == {"inline_keyboard": []}
    assert calls[1]["reply_markup"] is None
    assert scheduled == []
    assert stats["sent"] == 1
    assert stats["failed"] == 0
