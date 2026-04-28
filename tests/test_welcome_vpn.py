from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTES_ROOT = PROJECT_ROOT / "bloobcat" / "routes"


class DummyLogger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class FakeUsersApi:
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {}

    async def get_user_by_username(self, username: str):
        _ = username
        return self.payload


class FakeToolsApi:
    def __init__(self, encrypted: str = "crypt4://welcome"):
        self.encrypted = encrypted
        self.calls: list[str] = []

    async def encrypt_happ_crypto_link(self, raw_link: str) -> str:
        self.calls.append(raw_link)
        return self.encrypted


class FakeRemnaWaveClient:
    def __init__(self, payload: dict | None = None, encrypted: str = "crypt4://welcome"):
        self.users = FakeUsersApi(payload)
        self.tools = FakeToolsApi(encrypted=encrypted)


def load_welcome_module(monkeypatch: pytest.MonkeyPatch):
    routes_pkg = types.ModuleType("bloobcat.routes")
    routes_pkg.__path__ = [str(ROUTES_ROOT)]
    monkeypatch.setitem(sys.modules, "bloobcat.routes", routes_pkg)

    fake_user_module = types.ModuleType("bloobcat.routes.user")
    fake_user_module.remnawave_client = FakeRemnaWaveClient()
    monkeypatch.setitem(sys.modules, "bloobcat.routes.user", fake_user_module)

    logger_module = types.ModuleType("bloobcat.logger")
    logger_module.get_logger = lambda name: DummyLogger()
    monkeypatch.setitem(sys.modules, "bloobcat.logger", logger_module)

    sys.modules.pop("bloobcat.routes.welcome_vpn", None)
    module = importlib.import_module("bloobcat.routes.welcome_vpn")
    monkeypatch.setitem(sys.modules, "bloobcat.routes.welcome_vpn", module)
    return module


@pytest.fixture()
def welcome_module(monkeypatch: pytest.MonkeyPatch):
    return load_welcome_module(monkeypatch)


def test_next_moscow_midnight_label_uses_next_day_in_moscow(welcome_module):
    now = datetime.fromisoformat("2026-04-08T07:59:00+03:00")
    assert welcome_module.next_moscow_midnight_label(now) == "09.04"


@pytest.mark.asyncio
async def test_build_welcome_vpn_response_prefers_ready_crypto_link(welcome_module):
    welcome_module.remnawave_client = FakeRemnaWaveClient(
        payload={
            "response": {
                "happ": {"cryptoLink": "crypt4://fresh-link"},
                "rotatedAt": "2026-04-08T00:00:00+03:00",
            }
        }
    )

    payload = await welcome_module.build_welcome_vpn_response(
        now=datetime.fromisoformat("2026-04-08T12:30:00+03:00")
    )

    assert payload.featureEnabled is True
    assert payload.subscriptionUrl == "crypt5://fresh-link"
    assert payload.rotatedAt == "2026-04-08T00:00:00+03:00"
    assert payload.activeUntilLabel == "09.04"
    assert payload.rotationMode == "recreate"
    assert "Временный доступ помогает продолжить настройку Vectra Connect." in payload.announce


@pytest.mark.asyncio
async def test_build_welcome_vpn_response_encrypts_raw_subscription_url(welcome_module):
    fake_client = FakeRemnaWaveClient(
        payload={"response": {"subscriptionUrl": "https://remnawave.example/subscription"}},
        encrypted="crypt4://encrypted-link",
    )
    welcome_module.remnawave_client = fake_client

    payload = await welcome_module.build_welcome_vpn_response(
        now=datetime.fromisoformat("2026-04-08T12:30:00+03:00")
    )

    assert payload.featureEnabled is True
    assert payload.subscriptionUrl == "crypt5://encrypted-link"
    assert fake_client.tools.calls == ["https://remnawave.example/subscription"]


@pytest.mark.asyncio
async def test_build_welcome_vpn_response_keeps_current_crypto_link(welcome_module):
    welcome_module.remnawave_client = FakeRemnaWaveClient(
        payload={"response": {"happ": {"cryptoLink": "crypt5://already-current"}}}
    )

    payload = await welcome_module.build_welcome_vpn_response(
        now=datetime.fromisoformat("2026-04-08T12:30:00+03:00")
    )

    assert payload.featureEnabled is True
    assert payload.subscriptionUrl == "crypt5://already-current"


@pytest.mark.asyncio
async def test_build_welcome_vpn_response_disables_feature_when_subscription_missing(welcome_module):
    welcome_module.remnawave_client = FakeRemnaWaveClient(payload={"response": {}})

    payload = await welcome_module.build_welcome_vpn_response(
        now=datetime.fromisoformat("2026-04-08T12:30:00+03:00")
    )

    assert payload.featureEnabled is False
    assert payload.subscriptionUrl is None
    assert payload.activeUntilLabel == "09.04"
