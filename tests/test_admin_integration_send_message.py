"""Tests for POST /admin/integration/users/{user_id}/send-message endpoint.

Covers:
- 401 without token
- 404 for unknown user
- 200 + status:sent on success
- 200 + status:blocked when bot raises TelegramForbiddenError
- 400 when bot raises TelegramBadRequest
- Pydantic validation: empty / too-long text → 422
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tortoise import Tortoise

try:
    from tests._payment_test_stubs import install_stubs
except ModuleNotFoundError:
    from _payment_test_stubs import install_stubs

# Import real aiogram so bloobcat.db.users can import aiogram.types and aiogram.utils.
# We grab TelegramForbiddenError / TelegramBadRequest from the real module.
try:
    import aiogram  # noqa: F401 — just ensure it's importable
    from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
except ImportError:
    # Fallback stubs if aiogram is not installed (unlikely given this repo's deps)
    TelegramForbiddenError = type("TelegramForbiddenError", (Exception,), {})  # type: ignore[misc]
    TelegramBadRequest = type("TelegramBadRequest", (Exception,), {})  # type: ignore[misc]


# Module-scope sentinel: a key that was NOT present in sys.modules before our mutations.
_MODULE_MISSING = object()

# Keys this test mutates via install_stubs() and _build_app(). Snapshotted before
# any install and restored on teardown so subsequent test modules (family_*, discount_*)
# see the real bloobcat.* modules, not our test stubs.
_POLLUTED_SYS_MODULES_KEYS = (
    "bloobcat.services.admin_integration",
    "bloobcat.db.family_audit_logs",
    "bloobcat.settings",
    "bloobcat.routes.admin_integration",
)


@pytest.fixture(scope="module", autouse=True)
def _install_stubs_once():
    saved = {
        k: sys.modules.get(k, _MODULE_MISSING) for k in _POLLUTED_SYS_MODULES_KEYS
    }
    install_stubs()
    try:
        yield None
    finally:
        for k, original in saved.items():
            if original is _MODULE_MISSING:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = original


@pytest_asyncio.fixture(autouse=True)
async def db(_install_stubs_once):
    await Tortoise.init(
        config={
            "connections": {"default": "sqlite://:memory:"},
            "apps": {
                "models": {
                    "models": [
                        "bloobcat.db.users",
                        "bloobcat.db.tariff",
                        "bloobcat.db.active_tariff",
                        "bloobcat.db.family_members",
                        "bloobcat.db.payments",
                        "bloobcat.db.discounts",
                        "bloobcat.db.notifications",
                        "bloobcat.db.referral_rewards",
                        "bloobcat.db.subscription_freezes",
                        "bloobcat.db.segment_campaigns",
                    ],
                    "default_connection": "default",
                }
            },
        }
    )
    from bloobcat.db.users import Users

    Users._meta.fk_fields.discard("active_tariff")
    users_active_tariff_fk = Users._meta.fields_map.get("active_tariff")
    if users_active_tariff_fk is not None:
        users_active_tariff_fk.reference = False
        users_active_tariff_fk.db_constraint = False

    from tortoise.backends.sqlite.schema_generator import SqliteSchemaGenerator

    client = Tortoise.get_connection("default")
    generator = SqliteSchemaGenerator(client)
    models_to_create = []
    try:
        maybe = generator._get_models_to_create(models_to_create)
        if maybe is not None:
            models_to_create = maybe
    except TypeError:
        models_to_create = generator._get_models_to_create()
    tables = [generator._get_table_sql(m, safe=True) for m in models_to_create]
    sql = "\n".join(
        [t["table_creation_string"] for t in tables]
        + [m for t in tables for m in t["m2m_tables"]]
    )
    await generator.generate_from_string(sql)
    try:
        yield
    finally:
        await Tortoise.close_connections()


def _patch_admin_integration_settings():
    """Patch settings so require_admin_integration_token resolves 'test-admin-token'."""
    ai_route_mod = sys.modules.get("bloobcat.routes.admin_integration")
    if ai_route_mod is None:
        return

    class _FakeSecret:
        def get_secret_value(self):
            return "test-admin-token"

    class _FakeSettings:
        token = _FakeSecret()

    ai_route_mod.admin_integration_settings = _FakeSettings()


def _patch_family_audit_logs():
    """Stub FamilyAuditLogs to avoid needing the real db model in admin_integration."""
    ai_route_mod = sys.modules.get("bloobcat.routes.admin_integration")
    if ai_route_mod is None:
        return

    class _FakeAuditLogs:
        @classmethod
        async def create(cls, **kwargs):
            return None

    ai_route_mod.FamilyAuditLogs = _FakeAuditLogs


def _build_app():
    # Reset module cache so admin_integration re-imports with current stubs.
    sys.modules.pop("bloobcat.routes.admin_integration", None)

    # Stub heavy service deps
    ai_svc = types.ModuleType("bloobcat.services.admin_integration")

    async def _noop(*a, **kw):
        return {"ok": True}

    ai_svc.sync_user_lte = _noop
    ai_svc.sync_active_tariff_lte = _noop
    ai_svc.sync_user_remnawave_fields = _noop
    ai_svc.prepare_user_delete_via_admin = _noop
    ai_svc.delete_user_via_admin = _noop
    ai_svc.compute_tariff_effective_pricing = _noop
    ai_svc.preview_tariff_quote_rows = _noop
    ai_svc.preview_hwid_purge = _noop
    ai_svc.purge_hwid_everywhere = _noop

    class HwidPurgePreconditionError(Exception):
        pass

    ai_svc.HwidPurgePreconditionError = HwidPurgePreconditionError
    sys.modules["bloobcat.services.admin_integration"] = ai_svc

    # Stub family_audit_logs
    family_audit_mod = types.ModuleType("bloobcat.db.family_audit_logs")

    class _FakeAuditLogs:
        @classmethod
        async def create(cls, **kwargs):
            return None

    family_audit_mod.FamilyAuditLogs = _FakeAuditLogs
    sys.modules["bloobcat.db.family_audit_logs"] = family_audit_mod

    # Stub admin_integration_settings inside the settings module
    settings_mod = sys.modules.get("bloobcat.settings") or types.ModuleType("bloobcat.settings")

    class _FakeSecret:
        def get_secret_value(self):
            return "test-admin-token"

    class _FakeAdminIntegrationSettings:
        token = _FakeSecret()

    settings_mod.admin_integration_settings = _FakeAdminIntegrationSettings()
    sys.modules["bloobcat.settings"] = settings_mod

    from bloobcat.routes import admin_integration as ai_route

    app = FastAPI()
    app.include_router(ai_route.router)
    return app


TOKEN = "test-admin-token"
HEADERS_OK = {"X-Admin-Integration-Token": TOKEN}


@pytest.mark.asyncio
async def test_send_message_requires_token(_install_stubs_once):
    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/admin/integration/users/999/send-message",
            json={"text": "hello"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_send_message_404_unknown_user(_install_stubs_once):
    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/admin/integration/users/99999999/send-message",
            json={"text": "hello"},
            headers=HEADERS_OK,
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_send_message_success(_install_stubs_once):
    from bloobcat.db.users import Users

    user = await Users.create(
        id=7_777_001,
        username="msgtest",
        full_name="Msg Test",
        is_registered=True,
    )

    sent_msg = types.SimpleNamespace(message_id=42)
    bot_mod = sys.modules["bloobcat.bot.bot"]
    bot_mod.bot.send_message = AsyncMock(return_value=sent_msg)

    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            f"/admin/integration/users/{user.id}/send-message",
            json={"text": "Recovery notification", "parse_mode": "HTML"},
            headers=HEADERS_OK,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "sent"
    assert data["message_id"] == 42
    bot_mod.bot.send_message.assert_called_once_with(
        user.id, "Recovery notification", parse_mode="HTML"
    )


@pytest.mark.asyncio
async def test_send_message_blocked_returns_blocked_status(_install_stubs_once):
    from bloobcat.db.users import Users

    user = await Users.create(
        id=7_777_002,
        username="blocked_user",
        full_name="Blocked",
        is_registered=True,
    )

    bot_mod = sys.modules["bloobcat.bot.bot"]
    bot_mod.bot.send_message = AsyncMock(
        side_effect=TelegramForbiddenError(MagicMock(), "bot was blocked")
    )

    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            f"/admin/integration/users/{user.id}/send-message",
            json={"text": "hello"},
            headers=HEADERS_OK,
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "blocked"


@pytest.mark.asyncio
async def test_send_message_bad_request_returns_400(_install_stubs_once):
    from bloobcat.db.users import Users

    user = await Users.create(
        id=7_777_003,
        username="bad_req_user",
        full_name="Bad Req",
        is_registered=True,
    )

    bot_mod = sys.modules["bloobcat.bot.bot"]
    bot_mod.bot.send_message = AsyncMock(
        side_effect=TelegramBadRequest(MagicMock(), "can't parse entities")
    )

    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            f"/admin/integration/users/{user.id}/send-message",
            json={"text": "bad <b>html"},
            headers=HEADERS_OK,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_send_message_text_length_validation(_install_stubs_once):
    """Empty text or text > 4000 chars must be rejected by Pydantic (422)."""
    from bloobcat.db.users import Users

    user = await Users.create(
        id=7_777_004,
        username="len_test",
        full_name="Len Test",
        is_registered=True,
    )

    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp_empty = client.post(
            f"/admin/integration/users/{user.id}/send-message",
            json={"text": ""},
            headers=HEADERS_OK,
        )
        assert resp_empty.status_code == 422

        resp_long = client.post(
            f"/admin/integration/users/{user.id}/send-message",
            json={"text": "x" * 4001},
            headers=HEADERS_OK,
        )
        assert resp_long.status_code == 422
