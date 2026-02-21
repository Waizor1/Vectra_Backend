import importlib
import types
from datetime import date

import pytest

from bloobcat.db.users import Users
from bloobcat.services import admin_integration as ai_service


class _DummyClient:
    def __init__(self, users_api):
        self.users = users_api

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_prepare_user_delete_via_admin_missing_user_noop(monkeypatch):
    async def _get_or_none(**kwargs):
        assert kwargs["id"] == 1001
        return None

    monkeypatch.setattr(ai_service.Users, "get_or_none", _get_or_none)

    result = await ai_service.prepare_user_delete_via_admin(1001)
    assert result["ok"] is True
    assert result["noop"] is True
    assert result["queued_retry"] is False


@pytest.mark.asyncio
async def test_prepare_user_delete_via_admin_not_found_is_success(monkeypatch):
    user_obj = types.SimpleNamespace(id=1002, remnawave_uuid="uuid-1002")

    async def _get_or_none(**kwargs):
        return user_obj

    class _UsersApi:
        async def delete_user(self, _uuid):
            raise Exception("API error [A063]: User with specified params not found")

    monkeypatch.setattr(ai_service.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(ai_service, "RemnaWaveClient", lambda *_args, **_kwargs: _DummyClient(_UsersApi()))

    enqueue_calls = {"count": 0}

    async def _enqueue(**_kwargs):
        enqueue_calls["count"] += 1
        return True

    monkeypatch.setattr(ai_service, "enqueue_remnawave_delete_retry", _enqueue)

    result = await ai_service.prepare_user_delete_via_admin(1002)
    assert result["ok"] is True
    assert result["not_found"] is True
    assert enqueue_calls["count"] == 0


@pytest.mark.asyncio
async def test_prepare_user_delete_via_admin_transient_error_queues_retry(monkeypatch):
    user_obj = types.SimpleNamespace(id=1003, remnawave_uuid="uuid-1003")

    async def _get_or_none(**kwargs):
        return user_obj

    class _UsersApi:
        async def delete_user(self, _uuid):
            raise Exception("Network error: timeout")

    monkeypatch.setattr(ai_service.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(ai_service, "RemnaWaveClient", lambda *_args, **_kwargs: _DummyClient(_UsersApi()))

    enqueue_calls = {"count": 0}

    async def _enqueue(**_kwargs):
        enqueue_calls["count"] += 1
        return True

    monkeypatch.setattr(ai_service, "enqueue_remnawave_delete_retry", _enqueue)

    result = await ai_service.prepare_user_delete_via_admin(1003)
    assert result["ok"] is False
    assert result["queued_retry"] is True
    assert enqueue_calls["count"] == 1


@pytest.mark.asyncio
async def test_delete_user_via_admin_missing_user_is_idempotent(monkeypatch):
    async def _get_or_none(**kwargs):
        return None

    monkeypatch.setattr(ai_service.Users, "get_or_none", _get_or_none)

    assert await ai_service.delete_user_via_admin(2001) is True


@pytest.mark.asyncio
async def test_users_delete_transient_error_creates_retry_job(monkeypatch):
    calls = {"enqueue": 0}

    async def _guard():
        return None

    def _cancel(_user_id):
        return None

    async def _super_delete(_self, *args, **kwargs):
        return 1

    class _UsersApi:
        async def delete_user(self, _uuid):
            raise Exception("Network error: timeout")

    monkeypatch.setattr("bloobcat.db.users.ensure_active_tariffs_fk_cascade", _guard)
    scheduler_module = importlib.import_module("bloobcat.scheduler")
    monkeypatch.setattr(scheduler_module, "cancel_user_tasks", _cancel, raising=False)
    monkeypatch.setattr("tortoise.models.Model.delete", _super_delete)
    monkeypatch.setattr("bloobcat.routes.remnawave.client.RemnaWaveClient", lambda *_args, **_kwargs: _DummyClient(_UsersApi()))

    async def _enqueue(**_kwargs):
        calls["enqueue"] += 1
        return True

    monkeypatch.setattr("bloobcat.services.admin_integration.enqueue_remnawave_delete_retry", _enqueue)

    user = Users()
    user.id = 3001
    user.remnawave_uuid = "uuid-3001"

    result = await user.delete()
    assert result == 1
    assert calls["enqueue"] == 1


@pytest.mark.asyncio
async def test_users_delete_not_found_does_not_create_retry_job(monkeypatch):
    calls = {"enqueue": 0}

    async def _guard():
        return None

    def _cancel(_user_id):
        return None

    async def _super_delete(_self, *args, **kwargs):
        return 1

    class _UsersApi:
        async def delete_user(self, _uuid):
            raise Exception("API error [A063]: User with specified params not found")

    monkeypatch.setattr("bloobcat.db.users.ensure_active_tariffs_fk_cascade", _guard)
    scheduler_module = importlib.import_module("bloobcat.scheduler")
    monkeypatch.setattr(scheduler_module, "cancel_user_tasks", _cancel, raising=False)
    monkeypatch.setattr("tortoise.models.Model.delete", _super_delete)
    monkeypatch.setattr("bloobcat.routes.remnawave.client.RemnaWaveClient", lambda *_args, **_kwargs: _DummyClient(_UsersApi()))

    async def _enqueue(**_kwargs):
        calls["enqueue"] += 1
        return True

    monkeypatch.setattr("bloobcat.services.admin_integration.enqueue_remnawave_delete_retry", _enqueue)

    user = Users()
    user.id = 3002
    user.remnawave_uuid = "uuid-3002"

    result = await user.delete()
    assert result == 1
    assert calls["enqueue"] == 0


@pytest.mark.asyncio
async def test_ensure_remnawave_user_rebind_normalizes_hwid_limit_and_syncs(monkeypatch):
    user = Users()
    user.id = 4001
    user.full_name = "Test User"
    user.email = None
    user.expired_at = date.today()
    user.used_trial = True
    user.is_trial = False
    user.hwid_limit = 0
    user.remnawave_uuid = None
    user.active_tariff_id = None
    user.lte_gb_total = None

    async def _get_or_none(**kwargs):
        return user

    async def _find_existing(_self, _client, base_username):
        assert base_username == str(user.id)
        return {"uuid": "remote-rebind-uuid"}

    saved_fields = []

    async def _save(_self, *args, **kwargs):
        saved_fields.append(tuple(kwargs.get("update_fields") or ()))
        return None

    class _UsersApi:
        async def create_user(self, **_kwargs):
            raise Exception("API error [A019]: User username already exists")

        async def update_user(self, _uuid, **kwargs):
            _ = kwargs.get("hwidDeviceLimit")
            updates.append(kwargs)
            return {}

    updates: list[dict] = []
    monkeypatch.setattr(Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(Users, "_find_existing_remnawave_user", _find_existing)
    monkeypatch.setattr(Users, "save", _save, raising=False)
    monkeypatch.setattr("bloobcat.routes.remnawave.client.RemnaWaveClient", lambda *_args, **_kwargs: _DummyClient(_UsersApi()))

    result = await user._ensure_remnawave_user()
    assert result is True
    assert user.remnawave_uuid == "remote-rebind-uuid"
    assert user.hwid_limit == 1
    assert any("hwid_limit" in fields for fields in saved_fields)
    assert updates
    assert updates[0]["hwidDeviceLimit"] == 1
