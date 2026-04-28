from __future__ import annotations

import socket

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TEST

from bloobcat.bot.telegram_api import (
    TELEGRAM_API_HOST,
    TelegramFallbackAiohttpSession,
    TelegramFallbackResolver,
    build_fallback_resolve_results,
    create_bot_session,
    merge_resolve_results,
)
from bloobcat.settings import TelegramSettings


def test_telegram_settings_parse_api_fallback_ips():
    settings = TelegramSettings.model_validate(
        {
            "token": "123456:token",
            "webhook_secret": "secret",
            "webapp_url": "https://t.me/example_bot/app",
            "miniapp_url": "https://app.example.com/",
            "api_fallback_ips": "149.154.167.220, 2001:67c:4e8:f004::9",
        }
    )

    assert settings.api_fallback_ips == [
        "149.154.167.220",
        "2001:67c:4e8:f004::9",
    ]


def test_telegram_settings_parse_api_fallback_ips_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123456:token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("TELEGRAM_WEBAPP_URL", "https://t.me/example_bot/app")
    monkeypatch.setenv("TELEGRAM_MINIAPP_URL", "https://app.example.com/")
    monkeypatch.setenv("TELEGRAM_API_FALLBACK_IPS", "149.154.167.220")

    settings = TelegramSettings()

    assert settings.api_fallback_ips == ["149.154.167.220"]


def test_build_fallback_resolve_results_prefers_requested_family():
    results = build_fallback_resolve_results(
        TELEGRAM_API_HOST,
        443,
        ["149.154.167.220", "2001:67c:4e8:f004::9"],
        family=socket.AF_INET,
    )

    assert len(results) == 1
    assert results[0]["host"] == "149.154.167.220"
    assert results[0]["family"] == socket.AF_INET


def test_merge_resolve_results_deduplicates_entries():
    preferred = build_fallback_resolve_results(
        TELEGRAM_API_HOST,
        443,
        ["149.154.167.220"],
    )
    combined = merge_resolve_results(
        preferred,
        [
            {
                "hostname": TELEGRAM_API_HOST,
                "host": "149.154.167.220",
                "port": 443,
                "family": socket.AF_INET,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            },
            {
                "hostname": TELEGRAM_API_HOST,
                "host": "149.154.166.110",
                "port": 443,
                "family": socket.AF_INET,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            },
        ],
    )

    assert [record["host"] for record in combined] == [
        "149.154.167.220",
        "149.154.166.110",
    ]


@pytest.mark.asyncio
async def test_telegram_fallback_resolver_puts_fallback_ips_first(monkeypatch):
    resolver = TelegramFallbackResolver(["149.154.167.220"])

    class DummyResolver:
        async def resolve(self, host, port, family):
            return [
                {
                    "hostname": host,
                    "host": "149.154.166.110",
                    "port": port,
                    "family": socket.AF_INET,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                }
            ]

        async def close(self):
            return None

    monkeypatch.setattr(resolver, "_default_resolver", DummyResolver())

    results = await resolver.resolve(TELEGRAM_API_HOST, 443, socket.AF_INET)

    assert [record["host"] for record in results] == [
        "149.154.167.220",
        "149.154.166.110",
    ]


@pytest.mark.asyncio
async def test_telegram_fallback_resolver_delegates_for_other_hosts(monkeypatch):
    resolver = TelegramFallbackResolver(["149.154.167.220"])

    class DummyResolver:
        async def resolve(self, host, port, family):
            return [
                {
                    "hostname": host,
                    "host": "8.8.8.8",
                    "port": port,
                    "family": socket.AF_INET,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                }
            ]

        async def close(self):
            return None

    monkeypatch.setattr(resolver, "_default_resolver", DummyResolver())

    results = await resolver.resolve("www.google.com", 443, socket.AF_INET)

    assert [record["host"] for record in results] == ["8.8.8.8"]


def test_create_bot_session_returns_none_without_dev_or_fallbacks():
    assert create_bot_session(is_dev=False, fallback_ips=[]) is None


def test_create_bot_session_uses_fallback_session_when_ips_configured():
    session = create_bot_session(
        is_dev=False,
        fallback_ips=["149.154.167.220"],
    )

    assert isinstance(session, TelegramFallbackAiohttpSession)


def test_create_bot_session_keeps_test_api_in_dev_mode():
    session = create_bot_session(
        is_dev=True,
        fallback_ips=[],
    )

    assert isinstance(session, AiohttpSession)
    assert session is not None
    assert session.api is TEST
