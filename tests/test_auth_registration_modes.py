import types

import pytest

from bloobcat.routes import auth as auth_module


@pytest.mark.asyncio
async def test_auth_telegram_without_intent_returns_requires_registration(monkeypatch):
    parsed = types.SimpleNamespace(user=types.SimpleNamespace(id=111, username="u", first_name="A", last_name=None))

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 111
        return None

    async def _should_not_create(*args, **kwargs):
        raise AssertionError("Users.get_user should not be called without registration intent")

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(auth_module.Users, "get_user", _should_not_create)

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is True
    assert result.accessToken == ""
    assert result.expiresIn == 0


@pytest.mark.asyncio
async def test_auth_telegram_with_start_param_creates_user(monkeypatch):
    parsed = types.SimpleNamespace(user=types.SimpleNamespace(id=222, username="u", first_name="B", last_name=None))
    created_user = types.SimpleNamespace(id=222)

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 222
        return created_user, True

    async def _qr_get_or_none(**kwargs):
        return None

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_user", _get_user)
    monkeypatch.setattr(auth_module.PartnerQr, "get_or_none", _qr_get_or_none)
    monkeypatch.setattr(auth_module, "create_access_token", lambda user_id: (f"token-{user_id}", 3600))

    payload = auth_module.TelegramAuthRequest(initData="ok", startParam="qr_abc", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is False
    assert result.was_just_created is True
    assert result.accessToken == "token-222"
    assert result.expiresIn == 3600


@pytest.mark.asyncio
async def test_auth_telegram_with_unknown_start_param_still_requires_registration(monkeypatch):
    parsed = types.SimpleNamespace(user=types.SimpleNamespace(id=224, username="u", first_name="B", last_name=None))

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 224
        return None

    async def _should_not_create(*args, **kwargs):
        raise AssertionError("Users.get_user should not be called for non-whitelisted start_param")

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(auth_module.Users, "get_user", _should_not_create)

    payload = auth_module.TelegramAuthRequest(initData="ok", startParam="campaign-abc", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is True
    assert result.accessToken == ""
    assert result.expiresIn == 0


@pytest.mark.asyncio
async def test_auth_telegram_without_intent_uses_existing_user(monkeypatch):
    parsed = types.SimpleNamespace(user=types.SimpleNamespace(id=333, username="u", first_name="C", last_name=None))
    existing_user = types.SimpleNamespace(id=333)

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 333
        return existing_user

    async def _should_not_create(*args, **kwargs):
        raise AssertionError("Users.get_user should not be called when user already exists")

    monkeypatch.setattr(auth_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(auth_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(auth_module.Users, "get_user", _should_not_create)
    monkeypatch.setattr(auth_module, "create_access_token", lambda user_id: (f"token-{user_id}", 1800))

    payload = auth_module.TelegramAuthRequest(initData="ok", registerIntent=False)
    result = await auth_module.auth_telegram(payload)

    assert result.requires_registration is False
    assert result.was_just_created is False
    assert result.accessToken == "token-333"
    assert result.expiresIn == 1800
