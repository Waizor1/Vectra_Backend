from __future__ import annotations

import types

import pytest

from bloobcat.funcs import validate as validate_module


@pytest.mark.asyncio
async def test_validate_fast_path_keeps_existing_user_with_remnawave_uuid(monkeypatch):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=551, username="u", first_name="A", last_name=None)
    )
    existing_user = types.SimpleNamespace(id=551, remnawave_uuid="uuid-551")

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 551
        return existing_user

    async def _get_user(**kwargs):
        raise AssertionError("Users.get_user should not run when remnawave_uuid exists")

    monkeypatch.setattr(validate_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(validate_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(validate_module.Users, "get_user", _get_user)

    result = await validate_module.validate(init_data="ok")

    assert result is existing_user


@pytest.mark.asyncio
async def test_validate_heals_existing_user_without_remnawave_uuid(monkeypatch):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=552, username="u", first_name="B", last_name=None)
    )
    existing_user = types.SimpleNamespace(id=552, remnawave_uuid=None)
    healed_user = types.SimpleNamespace(id=552, remnawave_uuid="uuid-552")

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 552
        return existing_user

    async def _get_user(**kwargs):
        assert kwargs["telegram_user"].id == 552
        assert kwargs["referred_by"] == 0
        assert kwargs["utm"] is None
        return healed_user, False

    monkeypatch.setattr(validate_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(validate_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(validate_module.Users, "get_user", _get_user)

    result = await validate_module.validate(init_data="ok")

    assert result is healed_user
