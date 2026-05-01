from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest
from fastapi import Response


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTES_ROOT = PROJECT_ROOT / "bloobcat" / "routes"


def load_welcome_module(monkeypatch: pytest.MonkeyPatch):
    # Avoid importing bloobcat.routes.__init__: the disabled route must be standalone
    # and must not import/call the old RemnaWave user route dependency.
    routes_pkg = types.ModuleType("bloobcat.routes")
    routes_pkg.__path__ = [str(ROUTES_ROOT)]
    monkeypatch.setitem(sys.modules, "bloobcat.routes", routes_pkg)
    sys.modules.pop("bloobcat.routes.welcome_vpn", None)
    sys.modules.pop("bloobcat.routes.user", None)

    module = importlib.import_module("bloobcat.routes.welcome_vpn")
    monkeypatch.setitem(sys.modules, "bloobcat.routes.welcome_vpn", module)
    return module


@pytest.fixture()
def welcome_module(monkeypatch: pytest.MonkeyPatch):
    return load_welcome_module(monkeypatch)


def test_welcome_vpn_module_no_longer_depends_on_remnawave(welcome_module):
    assert not hasattr(welcome_module, "remnawave_client")
    assert welcome_module.WELCOME_ALIAS == "browser-entry"
    assert welcome_module.WELCOME_ROTATION_MODE == "disabled"


@pytest.mark.asyncio
async def test_build_welcome_vpn_response_is_disabled_for_browser_entry(welcome_module):
    payload = await welcome_module.build_welcome_vpn_response()

    assert payload.featureEnabled is False
    assert payload.subscriptionUrl is None
    assert payload.activeUntilLabel == ""
    assert payload.alias == "browser-entry"
    assert payload.rotationMode == "disabled"
    assert payload.unavailableReason == "browser_entry_available"
    assert "вход через браузер" in payload.announce.lower()
    assert "welcome-agent" not in payload.announce


def test_unavailable_reason_stays_internal(welcome_module):
    payload = welcome_module.WelcomeVpnResponse(
        featureEnabled=False,
        subscriptionUrl=None,
        announce=welcome_module.build_welcome_announce(),
        activeUntilLabel="",
        unavailableReason="browser_entry_available",
    )

    assert "unavailableReason" not in payload.model_dump()


@pytest.mark.asyncio
async def test_get_welcome_vpn_keeps_no_store_headers(welcome_module):
    response = Response()

    payload = await welcome_module.get_welcome_vpn(response)

    assert payload.featureEnabled is False
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
