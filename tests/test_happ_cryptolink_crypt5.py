"""Тесты на crypt5-выдачу happ:// ссылок: cache + always-encrypt + fallback.

Покрытие:
- ``ToolsAPI.encrypt_happ_crypto_link`` всегда сначала пробует
  crypto.happ.su (crypt5), на ошибку падает на панельный crypt4 и
  нормализует ответ к crypt5://.
- ``UsersAPI.get_subscription_url`` всегда шифрует raw subscriptionUrl
  сам, игнорируя panel-side happ.cryptoLink даже когда тот присутствует.
- ``get_or_refresh_cryptolink`` уважает TTL, обновляет запись через
  save(update_fields=...), а ошибка save() не ломает выдачу ссылки.
- При истечении TTL ``encrypt_fn`` вызывается, и ``record.save()`` идёт
  ровно с двумя полями.
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta, timezone
from typing import cast
from unittest.mock import AsyncMock

import pytest

from bloobcat.routes.remnawave import client as remnawave_client
from bloobcat.services import happ_cryptolink_cache as cache_mod


@pytest.mark.asyncio
async def test_get_subscription_url_always_uses_raw_and_encrypts_crypt5(monkeypatch):
    """Панель отдаёт и cryptoLink (crypt4), и subscriptionUrl: наш код
    обязан всегда идти в encrypt_happ_crypto_link и игнорировать
    panel-side cryptoLink, как в референсном Blubcat."""
    client = remnawave_client.RemnaWaveClient("http://panel", "token")
    user = types.SimpleNamespace(
        id=7,
        remnawave_uuid="uuid-with-both",
        happ_cryptolink_v5=None,
        happ_cryptolink_v5_at=None,
    )
    user.save = AsyncMock()

    client.users.get_user_by_uuid = AsyncMock(
        return_value={
            "response": {
                "subscriptionUrl": "https://sub.example.com/api/sub/raw",
                "happ": {"cryptoLink": "happ://crypt4-from-panel"},
            }
        }
    )
    client.tools.encrypt_happ_crypto_link = AsyncMock(
        return_value="crypt5://encrypted-fresh"
    )

    result = await client.users.get_subscription_url(user)

    assert result == "crypt5://encrypted-fresh"
    client.tools.encrypt_happ_crypto_link.assert_awaited_once_with(
        "https://sub.example.com/api/sub/raw"
    )
    user.save.assert_awaited_once()
    assert user.happ_cryptolink_v5 == "crypt5://encrypted-fresh"
    assert user.happ_cryptolink_v5_at is not None


@pytest.mark.asyncio
async def test_get_subscription_url_falls_back_to_panel_crypto_link_only_when_raw_missing(
    monkeypatch,
):
    """Если raw subscriptionUrl отсутствует, отдаём панельный cryptoLink
    как последний шанс (нормализованный к crypt5://)."""
    client = remnawave_client.RemnaWaveClient("http://panel", "token")
    user = types.SimpleNamespace(
        id=8,
        remnawave_uuid="uuid-only-legacy",
        happ_cryptolink_v5=None,
        happ_cryptolink_v5_at=None,
    )
    user.save = AsyncMock()

    client.users.get_user_by_uuid = AsyncMock(
        return_value={
            "response": {
                "subscriptionUrl": "",
                "happ": {"cryptoLink": "crypt4://legacy-link"},
            }
        }
    )
    client.tools.encrypt_happ_crypto_link = AsyncMock(
        return_value="crypt5://should-not-be-called"
    )

    result = await client.users.get_subscription_url(user)

    assert result == "crypt5://legacy-link"
    client.tools.encrypt_happ_crypto_link.assert_not_awaited()
    user.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_hit_skips_encrypt_fn():
    """Свежий кэш (< TTL) → encrypt_fn вообще не вызывается."""
    record = types.SimpleNamespace(
        happ_cryptolink_v5="crypt5://cached",
        happ_cryptolink_v5_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    record.save = AsyncMock()

    encrypt_fn = AsyncMock(return_value="crypt5://should-not-be-called")
    result = await cache_mod.get_or_refresh_cryptolink(record, "https://raw", encrypt_fn)

    assert result == "crypt5://cached"
    encrypt_fn.assert_not_awaited()
    record.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_expired_refreshes_via_encrypt_fn():
    """Кэш старше TTL → encrypt_fn вызывается, save() идёт только с двумя полями."""
    record = types.SimpleNamespace(
        happ_cryptolink_v5="crypt5://stale",
        happ_cryptolink_v5_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )
    record.save = AsyncMock()

    encrypt_fn = AsyncMock(return_value="crypt5://fresh-from-api")
    result = await cache_mod.get_or_refresh_cryptolink(record, "https://raw", encrypt_fn)

    assert result == "crypt5://fresh-from-api"
    encrypt_fn.assert_awaited_once_with("https://raw")
    record.save.assert_awaited_once()
    save_kwargs = record.save.await_args.kwargs
    assert set(save_kwargs.get("update_fields") or []) == {
        "happ_cryptolink_v5",
        "happ_cryptolink_v5_at",
    }
    assert record.happ_cryptolink_v5 == "crypt5://fresh-from-api"


@pytest.mark.asyncio
async def test_cache_first_call_then_cached():
    """Первый промах кладёт в кэш, повторный вызов в пределах TTL — из кэша."""
    record = types.SimpleNamespace(
        happ_cryptolink_v5=None,
        happ_cryptolink_v5_at=None,
    )
    record.save = AsyncMock()

    encrypt_fn = AsyncMock(return_value="crypt5://from-api")

    first = await cache_mod.get_or_refresh_cryptolink(record, "https://raw", encrypt_fn)
    second = await cache_mod.get_or_refresh_cryptolink(record, "https://raw", encrypt_fn)

    assert first == "crypt5://from-api"
    assert second == "crypt5://from-api"
    assert encrypt_fn.await_count == 1
    record.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_treats_naive_timestamp_as_utc():
    """tz-naive cached_at трактуется как UTC (SQLite-тестовый сценарий)."""
    record = types.SimpleNamespace(
        happ_cryptolink_v5="crypt5://cached",
        happ_cryptolink_v5_at=(datetime.now(timezone.utc) - timedelta(hours=2)).replace(
            tzinfo=None
        ),
    )
    record.save = AsyncMock()
    encrypt_fn = AsyncMock(return_value="crypt5://should-not-be-called")

    result = await cache_mod.get_or_refresh_cryptolink(record, "https://raw", encrypt_fn)

    assert result == "crypt5://cached"
    encrypt_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_save_failure_does_not_block_response():
    """Если save() упал (serialization race), всё равно возвращаем ссылку."""
    record = types.SimpleNamespace(
        happ_cryptolink_v5=None,
        happ_cryptolink_v5_at=None,
    )
    record.save = AsyncMock(side_effect=RuntimeError("serialization failure"))

    encrypt_fn = AsyncMock(return_value="crypt5://fresh")
    result = await cache_mod.get_or_refresh_cryptolink(record, "https://raw", encrypt_fn)

    assert result == "crypt5://fresh"
    encrypt_fn.assert_awaited_once()
    record.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_link_uses_crypto_happ_su_when_ok(monkeypatch):
    """Главная ветка ToolsAPI.encrypt_happ_crypto_link — публичный API
    crypto.happ.su (crypt5)."""

    class _StubResp:
        status = 200

        async def text(self):
            return "ok"

        async def json(self):
            return {"encrypted_link": "crypt5://encrypted-from-public-api"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _StubSession:
        def __init__(self, *_args, **_kwargs):
            pass

        def post(self, url, json, headers):
            _StubSession.last_call = {"url": url, "json": json, "headers": headers}
            return _StubResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(remnawave_client.aiohttp, "ClientSession", _StubSession)

    rw_client = remnawave_client.RemnaWaveClient("http://panel", "token")
    tools = remnawave_client.ToolsAPI(client=rw_client)
    rw_client._request = AsyncMock(
        return_value={"response": {"encryptedLink": "crypt4://should-not-be-used"}}
    )

    result = await tools.encrypt_happ_crypto_link("https://sub.example.com/api/sub/raw")

    assert result == "crypt5://encrypted-from-public-api"
    rw_client._request.assert_not_awaited()
    assert _StubSession.last_call["json"] == {"url": "https://sub.example.com/api/sub/raw"}


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_link_falls_back_to_panel_on_crypto_happ_failure(
    monkeypatch,
):
    """Сбой crypto.happ.su → панель → нормализация crypt4://-ответа к crypt5://."""

    class _BoomSession:
        def __init__(self, *_args, **_kwargs):
            pass

        def post(self, *_args, **_kwargs):
            raise RuntimeError("network down")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(remnawave_client.aiohttp, "ClientSession", _BoomSession)

    rw_client = remnawave_client.RemnaWaveClient("http://panel", "token")
    tools = remnawave_client.ToolsAPI(client=rw_client)
    rw_client._request = AsyncMock(
        return_value={"response": {"encryptedLink": "crypt4://from-panel-fallback"}}
    )

    result = await tools.encrypt_happ_crypto_link("https://sub.example.com/api/sub/raw")

    # crypt4:// в ответе панели → нормализатор приводит к crypt5://.
    assert result == "crypt5://from-panel-fallback"
    rw_client._request.assert_awaited_once()
    request_call = rw_client._request.await_args
    assert request_call.args == ("POST", "/api/system/tools/happ/encrypt")
    assert request_call.kwargs == {"json": {"linkToEncrypt": "https://sub.example.com/api/sub/raw"}}


@pytest.mark.asyncio
async def test_encrypt_happ_crypto_link_raises_when_both_paths_fail(monkeypatch):
    """Если и crypto.happ.su и панель не дали encryptedLink — поднимаем ValueError."""

    class _BoomSession:
        def __init__(self, *_args, **_kwargs):
            pass

        def post(self, *_args, **_kwargs):
            raise RuntimeError("network down")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(remnawave_client.aiohttp, "ClientSession", _BoomSession)

    rw_client = remnawave_client.RemnaWaveClient("http://panel", "token")
    tools = remnawave_client.ToolsAPI(client=rw_client)
    rw_client._request = AsyncMock(return_value={"response": {"encryptedLink": ""}})

    with pytest.raises(ValueError, match="Encrypted link not found"):
        await tools.encrypt_happ_crypto_link("https://sub.example.com/api/sub/raw")
