import asyncio
import types

import pytest
from starlette.requests import Request

from bloobcat.db.users import _get_remnawave_user_lock
from bloobcat.middleware.rate_limit import RateLimiter, get_client_ip, get_user_id_from_request
from bloobcat.funcs import validate as validate_module


@pytest.mark.asyncio
async def test_user_lock_registry_is_atomic_under_concurrency():
    tasks = [_get_remnawave_user_lock(123456789) for _ in range(200)]
    locks = await asyncio.gather(*tasks)
    first = locks[0]
    assert all(lock is first for lock in locks)


@pytest.mark.asyncio
async def test_rate_limiter_enforces_limit():
    limiter = RateLimiter(requests_per_minute=2, window_seconds=60)
    allowed_1, wait_1 = await limiter.is_allowed("u1")
    allowed_2, wait_2 = await limiter.is_allowed("u1")
    allowed_3, wait_3 = await limiter.is_allowed("u1")

    assert allowed_1 is True and wait_1 is None
    assert allowed_2 is True and wait_2 is None
    assert allowed_3 is False
    assert isinstance(wait_3, int)


@pytest.mark.asyncio
async def test_rate_limiter_cleans_expired_entries(monkeypatch):
    limiter = RateLimiter(requests_per_minute=5, window_seconds=1)
    await limiter.is_allowed("u-clean")
    assert "u-clean" in limiter.requests

    original_time = __import__("time").time
    monkeypatch.setattr("time.time", lambda: original_time() + 10)
    await limiter.is_allowed("u-clean")

    # После очистки должен остаться только новый timestamp
    assert len(limiter.requests["u-clean"]) == 1


def test_get_client_ip_uses_xff_only_for_trusted_proxy(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_TRUSTED_PROXIES", "10.0.0.1")

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"198.51.100.55")],
        "client": ("10.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    req = Request(scope)
    assert get_client_ip(req) == "198.51.100.55"

    scope_untrusted = {
        **scope,
        "client": ("203.0.113.10", 12345),
    }
    req_untrusted = Request(scope_untrusted)
    assert get_client_ip(req_untrusted) == "203.0.113.10"


@pytest.mark.asyncio
async def test_get_user_id_from_request_supports_bearer(monkeypatch):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"authorization", b"Bearer token123")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    req = Request(scope)

    def _decode(_token):
        return {"sub": "777"}

    monkeypatch.setattr("bloobcat.funcs.auth_tokens.decode_access_token", _decode)
    user_id = await get_user_id_from_request(req)
    assert user_id == 777


@pytest.mark.asyncio
async def test_validate_fast_path_skips_get_user_only_without_start_param(monkeypatch):
    class _UserObj:
        def __init__(self, uid):
            self.id = uid
            self.remnawave_uuid = "uuid-ok"

    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=1001),
        start_param=None,
    )

    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 1001
        return _UserObj(1001)

    async def _should_not_call(*args, **kwargs):
        raise AssertionError("Users.get_user should not be called on fast-path")

    monkeypatch.setattr(validate_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(validate_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(validate_module.Users, "get_user", _should_not_call)

    result = await validate_module.validate("init-data")
    assert result.id == 1001


@pytest.mark.asyncio
async def test_validate_with_start_param_goes_through_get_user(monkeypatch):
    parsed = types.SimpleNamespace(
        user=types.SimpleNamespace(id=2002),
        start_param="campaign-abc",
    )
    existing = types.SimpleNamespace(id=2002, remnawave_uuid="uuid-ok")
    expected = types.SimpleNamespace(id=2002, remnawave_uuid="uuid-final")
    called = {"get_user": False}

    async def _get_or_none(**kwargs):
        return existing

    async def _get_user(**kwargs):
        called["get_user"] = True
        return expected

    monkeypatch.setattr(validate_module, "safe_parse_webapp_init_data", lambda *_: parsed)
    monkeypatch.setattr(validate_module.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(validate_module.Users, "get_user", _get_user)

    result = await validate_module.validate("init-data")
    assert called["get_user"] is True
    assert result is expected
