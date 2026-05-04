from __future__ import annotations

import types

import pytest


def test_app_settings_default_trial_days_is_ten(monkeypatch):
    monkeypatch.delenv("TRIAL_DAYS", raising=False)

    from bloobcat.settings import AppSettings

    assert AppSettings().trial_days == 10


@pytest.mark.asyncio
async def test_read_maintenance_settings_caches_short_lived_db_result(monkeypatch):
    from bloobcat.routes import app_info

    calls = 0

    class FakeConnection:
        async def execute_query_dict(self, _sql: str):
            nonlocal calls
            calls += 1
            return [{"maintenance_mode": True, "maintenance_message": " кратко "}]

    monkeypatch.setattr(app_info.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(app_info.Tortoise, "get_connection", lambda _name: FakeConnection())
    app_info.clear_maintenance_settings_cache()

    assert await app_info.read_maintenance_settings() == (True, "кратко")
    assert await app_info.read_maintenance_settings() == (True, "кратко")
    assert calls == 1


@pytest.mark.asyncio
async def test_read_maintenance_settings_cache_expires(monkeypatch):
    from bloobcat.routes import app_info

    now = 1000.0
    calls = 0

    class FakeConnection:
        async def execute_query_dict(self, _sql: str):
            nonlocal calls
            calls += 1
            return [{"maintenance_mode": calls == 1, "maintenance_message": f"m{calls}"}]

    monkeypatch.setattr(app_info.time, "monotonic", lambda: now)
    monkeypatch.setattr(app_info.Tortoise, "get_connection", lambda _name: FakeConnection())
    app_info.clear_maintenance_settings_cache()

    assert await app_info.read_maintenance_settings() == (True, "m1")
    now += app_info.MAINTENANCE_SETTINGS_CACHE_TTL_SECONDS + 1
    assert await app_info.read_maintenance_settings() == (False, "m2")
    assert calls == 2
