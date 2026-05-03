from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_user_unsubscribe_disables_email_notifications_without_canceling_vpn(
    monkeypatch,
):
    from bloobcat.routes import user as user_route

    cancel_subscription = AsyncMock()
    monkeypatch.setattr(
        user_route,
        "cancel_subscription",
        cancel_subscription,
        raising=False,
    )

    saved_fields: list[list[str] | None] = []

    class _User(types.SimpleNamespace):
        async def save(self, update_fields=None):
            saved_fields.append(update_fields)

    user = _User(
        id=1001,
        is_subscribed=True,
        email_notifications_enabled=True,
    )

    result = await user_route.unsubscribe(user)

    assert result == {"status": "ok"}
    assert user.is_subscribed is True
    assert user.email_notifications_enabled is False
    assert saved_fields == [["email_notifications_enabled"]]
    cancel_subscription.assert_not_awaited()


def test_unsubscribe_token_round_trips_and_rejects_tampering(monkeypatch):
    from bloobcat.services import email_preferences

    monkeypatch.setattr(
        email_preferences,
        "_signing_secret",
        lambda: b"test-email-unsubscribe-secret-0001",
    )

    token = email_preferences.generate_unsubscribe_token(42)

    assert email_preferences.verify_unsubscribe_token(token) == 42
    assert email_preferences.verify_unsubscribe_token(token + "x") is None


@pytest.mark.asyncio
async def test_public_unsubscribe_endpoint_updates_preference(monkeypatch):
    from bloobcat.routes import email as email_route

    monkeypatch.setattr(email_route, "verify_unsubscribe_token", lambda token: 77)

    saved_fields: list[list[str] | None] = []

    class _User(types.SimpleNamespace):
        async def save(self, update_fields=None):
            saved_fields.append(update_fields)

    user = _User(
        id=77,
        email="person@example.com",
        email_notifications_enabled=True,
    )

    async def get_or_none(**kwargs):
        assert kwargs == {"id": 77}
        return user

    monkeypatch.setattr(email_route.Users, "get_or_none", get_or_none)

    result = await email_route.unsubscribe(token="valid-token")

    assert result == {"status": "unsubscribed"}
    assert user.email_notifications_enabled is False
    assert saved_fields == [["email_notifications_enabled"]]


@pytest.mark.asyncio
async def test_public_unsubscribe_one_click_updates_preference(monkeypatch):
    from bloobcat.routes import email as email_route

    monkeypatch.setattr(email_route, "verify_unsubscribe_token", lambda token: 78)

    saved_fields: list[list[str] | None] = []

    class _User(types.SimpleNamespace):
        async def save(self, update_fields=None):
            saved_fields.append(update_fields)

    user = _User(
        id=78,
        email="person@example.com",
        email_notifications_enabled=True,
    )

    async def get_or_none(**kwargs):
        assert kwargs == {"id": 78}
        return user

    monkeypatch.setattr(email_route.Users, "get_or_none", get_or_none)

    result = await email_route.unsubscribe_one_click(token="valid-token")

    assert result == {"status": "unsubscribed"}
    assert user.email_notifications_enabled is False
    assert saved_fields == [["email_notifications_enabled"]]


@pytest.mark.asyncio
async def test_public_unsubscribe_status_does_not_expose_email(monkeypatch):
    from bloobcat.routes import email as email_route

    monkeypatch.setattr(email_route, "verify_unsubscribe_token", lambda token: 79)

    user = types.SimpleNamespace(
        id=79,
        email="person@example.com",
        email_notifications_enabled=False,
    )

    async def get_or_none(**kwargs):
        assert kwargs == {"id": 79}
        return user

    monkeypatch.setattr(email_route.Users, "get_or_none", get_or_none)

    result = await email_route.unsubscribe_status(token="valid-token")

    assert result == {"email_notifications_enabled": False}


@pytest.mark.asyncio
async def test_public_unsubscribe_rejects_invalid_token(monkeypatch):
    from fastapi import HTTPException

    from bloobcat.routes import email as email_route

    monkeypatch.setattr(email_route, "verify_unsubscribe_token", lambda token: None)

    with pytest.raises(HTTPException) as exc_info:
        await email_route.unsubscribe(token="bad-token")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid_unsubscribe_token"
