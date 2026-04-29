from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import SecretStr


def test_captain_lookup_uses_constant_time_api_key_compare(monkeypatch):
    from bloobcat.routes import captain_user_lookup

    calls: list[tuple[str, str]] = []

    def _compare(provided: str, expected: str) -> bool:
        calls.append((provided, expected))
        return True

    monkeypatch.setattr(captain_user_lookup.hmac, "compare_digest", _compare)
    monkeypatch.setattr(captain_user_lookup.captain_lookup_settings, "api_key", SecretStr("expected-key"))

    assert captain_user_lookup._is_authorized("Bearer provided-key") is True
    assert calls == [("provided-key", "expected-key")]


@pytest.mark.asyncio
async def test_admin_integration_uses_constant_time_token_compare(monkeypatch):
    from bloobcat.routes import admin_integration

    calls: list[tuple[str, str]] = []

    def _compare(provided: str, expected: str) -> bool:
        calls.append((provided, expected))
        return True

    monkeypatch.setattr(admin_integration.hmac, "compare_digest", _compare)
    monkeypatch.setattr(admin_integration.admin_integration_settings, "token", SecretStr("expected-token"))

    await admin_integration.require_admin_integration_token("provided-token")
    assert calls == [("provided-token", "expected-token")]


@pytest.mark.asyncio
async def test_admin_integration_rejects_missing_token_without_compare(monkeypatch):
    from bloobcat.routes import admin_integration

    def _compare(_provided: str, _expected: str) -> bool:
        raise AssertionError("missing tokens should not be compared")

    monkeypatch.setattr(admin_integration.hmac, "compare_digest", _compare)
    monkeypatch.setattr(admin_integration.admin_integration_settings, "token", SecretStr("expected-token"))

    with pytest.raises(HTTPException) as exc:
        await admin_integration.require_admin_integration_token(None)

    assert exc.value.status_code == 401
