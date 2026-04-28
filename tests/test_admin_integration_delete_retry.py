import importlib
import types
import asyncio
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import pytest
from tortoise.exceptions import IntegrityError

from bloobcat.db.users import Users
from bloobcat.services import admin_integration as ai_service


class _DummyClient:
    def __init__(self, users_api):
        self.users = users_api

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_enqueue_remnawave_delete_retry_dedupes_processing_job(monkeypatch):
    class _FakeJob:
        def __init__(self):
            self.id = 7001
            self.job_type = ai_service.DELETE_RETRY_JOB_TYPE
            self.user_id = 7001
            self.status = "processing"
            self.remnawave_uuid = "old-uuid"
            self.last_error = "old-error"
            self.saved_fields = []

        async def save(self, update_fields=None):
            self.saved_fields.append(tuple(update_fields or ()))

    class _FakeQuery:
        def __init__(self, job):
            self._job = job

        def order_by(self, *_fields):
            return self

        async def first(self):
            return self._job

    class _FakeRemnaWaveRetryJobs:
        job = _FakeJob()
        create_calls = 0

        @classmethod
        def filter(cls, **criteria):
            assert criteria["status__in"] == ["pending", "processing"]
            return _FakeQuery(cls.job)

        @classmethod
        async def create(cls, **_kwargs):
            cls.create_calls += 1
            return None

    monkeypatch.setattr(ai_service, "RemnaWaveRetryJobs", _FakeRemnaWaveRetryJobs)

    created = await ai_service.enqueue_remnawave_delete_retry(
        user_id=7001,
        remnawave_uuid="new-uuid",
        last_error="new-error",
    )

    assert created is False
    assert _FakeRemnaWaveRetryJobs.create_calls == 0
    assert _FakeRemnaWaveRetryJobs.job.remnawave_uuid == "new-uuid"
    assert _FakeRemnaWaveRetryJobs.job.last_error == "new-error"
    assert _FakeRemnaWaveRetryJobs.job.saved_fields == [("remnawave_uuid", "last_error")]


@pytest.mark.asyncio
async def test_enqueue_remnawave_delete_retry_race_reloads_active_job(monkeypatch):
    class _FakeJob:
        def __init__(self):
            self.id = 7002
            self.job_type = ai_service.DELETE_RETRY_JOB_TYPE
            self.user_id = 7002
            self.status = "pending"
            self.remnawave_uuid = "stale-uuid"
            self.last_error = None
            self.saved_fields = []

        async def save(self, update_fields=None):
            self.saved_fields.append(tuple(update_fields or ()))

    class _FakeQuery:
        def __init__(self, owner):
            self._owner = owner

        def order_by(self, *_fields):
            return self

        async def first(self):
            self._owner.first_calls += 1
            if self._owner.first_calls == 1:
                return None
            return self._owner.job

    class _FakeRemnaWaveRetryJobs:
        first_calls = 0
        create_calls = 0
        job = _FakeJob()

        @classmethod
        def filter(cls, **criteria):
            assert criteria["status__in"] == ["pending", "processing"]
            return _FakeQuery(cls)

        @classmethod
        async def create(cls, **_kwargs):
            cls.create_calls += 1
            raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(ai_service, "RemnaWaveRetryJobs", _FakeRemnaWaveRetryJobs)

    created = await ai_service.enqueue_remnawave_delete_retry(
        user_id=7002,
        remnawave_uuid="fresh-uuid",
        last_error="transient failure",
    )

    assert created is False
    assert _FakeRemnaWaveRetryJobs.create_calls == 1
    assert _FakeRemnaWaveRetryJobs.first_calls == 2
    assert _FakeRemnaWaveRetryJobs.job.remnawave_uuid == "fresh-uuid"
    assert _FakeRemnaWaveRetryJobs.job.last_error == "transient failure"
    assert _FakeRemnaWaveRetryJobs.job.saved_fields == [("remnawave_uuid", "last_error")]


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
async def test_prepare_user_delete_via_admin_a025_not_found_is_success(monkeypatch):
    user_obj = types.SimpleNamespace(id=10021, remnawave_uuid="uuid-10021")

    async def _get_or_none(**kwargs):
        return user_obj

    class _UsersApi:
        async def delete_user(self, _uuid):
            raise Exception("API error [A025]: User not found")

    monkeypatch.setattr(ai_service.Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(ai_service, "RemnaWaveClient", lambda *_args, **_kwargs: _DummyClient(_UsersApi()))

    enqueue_calls = {"count": 0}

    async def _enqueue(**_kwargs):
        enqueue_calls["count"] += 1
        return True

    monkeypatch.setattr(ai_service, "enqueue_remnawave_delete_retry", _enqueue)

    result = await ai_service.prepare_user_delete_via_admin(10021)
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
    monkeypatch.setattr("bloobcat.db.users.ensure_notification_marks_fk_cascade", _guard)
    monkeypatch.setattr("bloobcat.db.users.ensure_promo_usages_fk_cascade", _guard)
    monkeypatch.setattr("bloobcat.db.users.ensure_users_referred_by_fk_set_null", _guard)
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
    monkeypatch.setattr("bloobcat.db.users.ensure_notification_marks_fk_cascade", _guard)
    monkeypatch.setattr("bloobcat.db.users.ensure_promo_usages_fk_cascade", _guard)
    monkeypatch.setattr("bloobcat.db.users.ensure_users_referred_by_fk_set_null", _guard)
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
async def test_users_delete_a025_not_found_does_not_create_retry_job(monkeypatch):
    calls = {"enqueue": 0}

    async def _guard():
        return None

    def _cancel(_user_id):
        return None

    async def _super_delete(_self, *args, **kwargs):
        return 1

    class _UsersApi:
        async def delete_user(self, _uuid):
            raise Exception("API error [A025]: User not found")

    monkeypatch.setattr("bloobcat.db.users.ensure_active_tariffs_fk_cascade", _guard)
    monkeypatch.setattr("bloobcat.db.users.ensure_notification_marks_fk_cascade", _guard)
    monkeypatch.setattr("bloobcat.db.users.ensure_promo_usages_fk_cascade", _guard)
    monkeypatch.setattr("bloobcat.db.users.ensure_users_referred_by_fk_set_null", _guard)
    scheduler_module = importlib.import_module("bloobcat.scheduler")
    monkeypatch.setattr(scheduler_module, "cancel_user_tasks", _cancel, raising=False)
    monkeypatch.setattr("tortoise.models.Model.delete", _super_delete)
    monkeypatch.setattr("bloobcat.routes.remnawave.client.RemnaWaveClient", lambda *_args, **_kwargs: _DummyClient(_UsersApi()))

    async def _enqueue(**_kwargs):
        calls["enqueue"] += 1
        return True

    monkeypatch.setattr("bloobcat.services.admin_integration.enqueue_remnawave_delete_retry", _enqueue)

    user = Users()
    user.id = 30021
    user.remnawave_uuid = "uuid-30021"

    result = await user.delete()
    assert result == 1
    assert calls["enqueue"] == 0


def test_remnawave_not_found_helpers_do_not_match_generic_404_text():
    generic_404_error = "Gateway returned 404 from unrelated upstream"

    assert ai_service.is_remnawave_not_found_error(generic_404_error) is False
    assert Users._is_remnawave_not_found_error(generic_404_error) is False


@pytest.mark.parametrize(
    "error_text",
    [
        "API error [A-063]: User with specified params not found",
        "api ERROR [a_025]: user not found",
        "API ERROR [404] user-not-found",
    ],
)
def test_remnawave_not_found_helpers_handle_case_and_format_variants(error_text):
    assert ai_service.is_remnawave_not_found_error(error_text) is True
    assert Users._is_remnawave_not_found_error(error_text) is True


def test_remnawave_not_found_helpers_do_not_match_unrelated_404_not_found():
    unrelated_404 = "API error [404]: Tariff not found"

    assert ai_service.is_remnawave_not_found_error(unrelated_404) is False
    assert Users._is_remnawave_not_found_error(unrelated_404) is False


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
    user.key_activated = False

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

        async def get_user_hwid_devices(self, _uuid):
            return {"response": {"devices": []}}

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


@pytest.mark.asyncio
async def test_ensure_remnawave_user_rebind_imports_existing_connection_state(monkeypatch):
    user = Users()
    user.id = 4002
    user.full_name = "Historical User"
    user.email = None
    user.expired_at = date.today()
    user.used_trial = True
    user.is_trial = False
    user.hwid_limit = 1
    user.remnawave_uuid = None
    user.active_tariff_id = None
    user.lte_gb_total = None
    user.connected_at = None
    user.is_registered = False
    user.key_activated = False

    async def _get_or_none(**kwargs):
        return user

    async def _find_existing(_self, _client, base_username):
        assert base_username == str(user.id)
        return {
            "uuid": "remote-rebind-uuid",
            "userTraffic": {
                "firstConnectedAt": "2026-04-05T16:02:30.147Z",
                "onlineAt": "2026-04-28T01:08:30.118Z",
            },
        }

    saved_fields = []

    async def _save(_self, *args, **kwargs):
        saved_fields.append(tuple(kwargs.get("update_fields") or ()))
        return None

    class _UsersApi:
        async def create_user(self, **_kwargs):
            raise Exception("API error [A019]: User username already exists")

        async def update_user(self, _uuid, **_kwargs):
            return {}

        async def get_user_hwid_devices(self, _uuid):
            return {
                "response": {
                    "devices": [
                        {"hwid": "historical-hwid-1", "platform": "macOS"},
                    ],
                },
            }

    monkeypatch.setattr(Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(Users, "_find_existing_remnawave_user", _find_existing)
    monkeypatch.setattr(Users, "save", _save, raising=False)
    monkeypatch.setattr("bloobcat.routes.remnawave.client.RemnaWaveClient", lambda *_args, **_kwargs: _DummyClient(_UsersApi()))

    result = await user._ensure_remnawave_user()

    assert result is True
    assert user.remnawave_uuid == "remote-rebind-uuid"
    assert user.key_activated is True
    assert user.is_registered is True
    assert user.connected_at == datetime(
        2026, 4, 5, 16, 2, 30, 147000, tzinfo=timezone.utc
    )
    assert any("key_activated" in fields for fields in saved_fields)
    assert any("is_registered" in fields for fields in saved_fields)
    assert any("connected_at" in fields for fields in saved_fields)


@pytest.mark.asyncio
async def test_ensure_remnawave_user_create_preserves_future_expiry(monkeypatch):
    future_expiry = date.today() + timedelta(days=30)

    user = Users()
    user.id = 5001
    user.full_name = "Future Expiry User"
    user.email = None
    user.expired_at = future_expiry
    user.used_trial = True
    user.is_trial = False
    user.hwid_limit = 1
    user.remnawave_uuid = None
    user.active_tariff_id = None
    user.lte_gb_total = None

    async def _get_or_none(**_kwargs):
        return user

    captured = {}

    async def _save(_self, *args, **kwargs):
        captured["saved_update_fields"] = tuple(kwargs.get("update_fields") or ())
        return None

    class _UsersApi:
        async def create_user(self, **kwargs):
            captured["expire_at"] = kwargs.get("expire_at")
            return {"response": {"uuid": "new-uuid-5001"}}

    monkeypatch.setattr(Users, "get_or_none", _get_or_none)
    monkeypatch.setattr(Users, "save", _save, raising=False)
    monkeypatch.setattr("bloobcat.routes.remnawave.client.RemnaWaveClient", lambda *_args, **_kwargs: _DummyClient(_UsersApi()))

    result = await user._ensure_remnawave_user()
    assert result is True
    assert captured["expire_at"] == future_expiry
    assert user.remnawave_uuid == "new-uuid-5001"


@pytest.mark.asyncio
async def test_process_remnawave_delete_retry_jobs_claim_prevents_double_processing(monkeypatch):
    now = datetime.now(timezone.utc)

    class _FakeJob:
        def __init__(self):
            self.id = 1
            self.job_type = ai_service.DELETE_RETRY_JOB_TYPE
            self.user_id = 6001
            self.remnawave_uuid = "uuid-6001"
            self.attempts = 0
            self.status = "pending"
            self.next_retry_at = now - timedelta(seconds=1)
            self.last_error = None

    class _FakeQuery:
        def __init__(self, store, criteria):
            self._store = store
            self._criteria = criteria

        def order_by(self, *_fields):
            return self

        async def first(self):
            matches = [job for job in self._store.values() if _match(job, self._criteria)]
            if not matches:
                return None
            matches.sort(key=lambda job: (job.next_retry_at, job.id))
            return matches[0]

        async def update(self, **updates):
            async with _FakeRemnaWaveRetryJobs.lock:
                matches = [job for job in self._store.values() if _match(job, self._criteria)]
                for job in matches:
                    for key, value in updates.items():
                        setattr(job, key, value)
                return len(matches)

    def _match(job, criteria):
        for key, value in criteria.items():
            if key.endswith("__lte"):
                field = key[:-5]
                if getattr(job, field) > value:
                    return False
                continue
            if key.endswith("__in"):
                field = key[:-4]
                if getattr(job, field) not in value:
                    return False
                continue
            if getattr(job, key) != value:
                return False
        return True

    class _FakeRemnaWaveRetryJobs:
        store = {1: _FakeJob()}
        lock = asyncio.Lock()

        @classmethod
        def filter(cls, **criteria):
            return _FakeQuery(cls.store, criteria)

    delete_calls = {"count": 0}

    async def _delete_with_policy(**_kwargs):
        delete_calls["count"] += 1
        await asyncio.sleep(0.05)
        return {"ok": True, "deleted": True, "not_found": False, "queued_retry": False}

    monkeypatch.setattr(ai_service, "RemnaWaveRetryJobs", _FakeRemnaWaveRetryJobs)
    monkeypatch.setattr(ai_service, "_delete_remnawave_user_with_retry_policy", _delete_with_policy)

    first_stats, second_stats = await asyncio.gather(
        ai_service.process_remnawave_delete_retry_jobs(batch_limit=1),
        ai_service.process_remnawave_delete_retry_jobs(batch_limit=1),
    )

    assert delete_calls["count"] == 1
    assert first_stats["processed"] + second_stats["processed"] == 1
    final_job = _FakeRemnaWaveRetryJobs.store[1]
    assert final_job.status == "done"
    assert final_job.attempts == 1


def test_migration_91_dedup_prefers_freshest_updated_at_then_id():
    migration_path = Path(__file__).resolve().parents[1] / "migrations" / "models" / "91_20260301143000_remnawave_retry_jobs_active_unique.py"
    sql = migration_path.read_text(encoding="utf-8")

    assert "ORDER BY\n                        updated_at DESC,\n                        id DESC" in sql
    assert "CASE WHEN status = 'processing' THEN 0 ELSE 1 END" not in sql


@pytest.mark.asyncio
async def test_process_remnawave_delete_retry_jobs_skips_finalize_when_ownership_lost(monkeypatch):
    now = datetime.now(timezone.utc)

    class _FakeJob:
        def __init__(self):
            self.id = 2
            self.job_type = ai_service.DELETE_RETRY_JOB_TYPE
            self.user_id = 6002
            self.remnawave_uuid = "uuid-6002"
            self.attempts = 0
            self.status = "pending"
            self.next_retry_at = now - timedelta(seconds=1)
            self.last_error = None

    class _FakeQuery:
        def __init__(self, store, criteria):
            self._store = store
            self._criteria = criteria

        def order_by(self, *_fields):
            return self

        async def first(self):
            matches = [job for job in self._store.values() if _match(job, self._criteria)]
            if not matches:
                return None
            matches.sort(key=lambda job: (job.next_retry_at, job.id))
            return matches[0]

        async def update(self, **updates):
            matches = [job for job in self._store.values() if _match(job, self._criteria)]
            for job in matches:
                for key, value in updates.items():
                    setattr(job, key, value)
            return len(matches)

    def _match(job, criteria):
        for key, value in criteria.items():
            if key.endswith("__lte"):
                field = key[:-5]
                if getattr(job, field) > value:
                    return False
                continue
            if key.endswith("__in"):
                field = key[:-4]
                if getattr(job, field) not in value:
                    return False
                continue
            if getattr(job, key) != value:
                return False
        return True

    class _FakeRemnaWaveRetryJobs:
        store = {2: _FakeJob()}

        @classmethod
        def filter(cls, **criteria):
            return _FakeQuery(cls.store, criteria)

    async def _delete_with_policy(**_kwargs):
        # Simulate lease stolen by another worker before finalize.
        job = _FakeRemnaWaveRetryJobs.store[2]
        job.next_retry_at = now + timedelta(seconds=120)
        return {"ok": True, "deleted": True, "not_found": False, "queued_retry": False}

    monkeypatch.setattr(ai_service, "RemnaWaveRetryJobs", _FakeRemnaWaveRetryJobs)
    monkeypatch.setattr(ai_service, "_delete_remnawave_user_with_retry_policy", _delete_with_policy)

    stats = await ai_service.process_remnawave_delete_retry_jobs(batch_limit=1)

    assert stats["processed"] == 1
    assert stats["completed"] == 0
    assert stats["rescheduled"] == 0
    assert stats["dead_letter"] == 0
    final_job = _FakeRemnaWaveRetryJobs.store[2]
    assert final_job.status == "processing"
    assert final_job.attempts == 1


@pytest.mark.asyncio
async def test_process_remnawave_delete_retry_jobs_reschedule_uses_finalize_time_after_slow_attempt(monkeypatch):
    claim_time = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    finalize_time = claim_time + timedelta(seconds=5)

    class _FakeDatetime:
        calls = 0

        @classmethod
        def now(cls, _tz=None):
            cls.calls += 1
            if cls.calls == 1:
                return claim_time
            return finalize_time

    class _FakeJob:
        def __init__(self):
            self.id = 3
            self.job_type = ai_service.DELETE_RETRY_JOB_TYPE
            self.user_id = 6003
            self.remnawave_uuid = "uuid-6003"
            self.attempts = 0
            self.status = "pending"
            self.next_retry_at = claim_time - timedelta(seconds=1)
            self.last_error = None

    class _FakeQuery:
        def __init__(self, store, criteria):
            self._store = store
            self._criteria = criteria

        def order_by(self, *_fields):
            return self

        async def first(self):
            matches = [job for job in self._store.values() if _match(job, self._criteria)]
            if not matches:
                return None
            matches.sort(key=lambda job: (job.next_retry_at, job.id))
            return matches[0]

        async def update(self, **updates):
            matches = [job for job in self._store.values() if _match(job, self._criteria)]
            for job in matches:
                for key, value in updates.items():
                    setattr(job, key, value)
            return len(matches)

    def _match(job, criteria):
        for key, value in criteria.items():
            if key.endswith("__lte"):
                field = key[:-5]
                if getattr(job, field) > value:
                    return False
                continue
            if key.endswith("__in"):
                field = key[:-4]
                if getattr(job, field) not in value:
                    return False
                continue
            if getattr(job, key) != value:
                return False
        return True

    class _FakeRemnaWaveRetryJobs:
        store = {3: _FakeJob()}

        @classmethod
        def filter(cls, **criteria):
            return _FakeQuery(cls.store, criteria)

    async def _delete_with_policy(**_kwargs):
        await asyncio.sleep(0.05)
        return {
            "ok": False,
            "deleted": False,
            "not_found": False,
            "queued_retry": False,
            "error": "Network error: timeout",
        }

    monkeypatch.setattr(ai_service, "RemnaWaveRetryJobs", _FakeRemnaWaveRetryJobs)
    monkeypatch.setattr(ai_service, "_delete_remnawave_user_with_retry_policy", _delete_with_policy)
    monkeypatch.setattr(ai_service, "datetime", _FakeDatetime)

    stats = await ai_service.process_remnawave_delete_retry_jobs(batch_limit=1)

    assert stats["processed"] == 1
    assert stats["rescheduled"] == 1
    final_job = _FakeRemnaWaveRetryJobs.store[3]
    delay_seconds = ai_service._retry_backoff_seconds(1)
    assert final_job.status == "pending"
    assert final_job.attempts == 1
    assert final_job.next_retry_at == finalize_time + timedelta(seconds=delay_seconds)
    assert final_job.next_retry_at > finalize_time
