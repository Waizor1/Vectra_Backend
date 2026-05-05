from __future__ import annotations

import types

import pytest


def test_app_settings_default_trial_days_is_ten(monkeypatch):
    monkeypatch.delenv("TRIAL_DAYS", raising=False)

    from bloobcat.settings import AppSettings

    assert AppSettings().trial_days == 10


def test_app_settings_default_trial_lte_limit_is_one_gb(monkeypatch):
    monkeypatch.delenv("TRIAL_LTE_LIMIT_GB", raising=False)

    from bloobcat.settings import AppSettings

    assert AppSettings().trial_lte_limit_gb == 1.0


def test_app_settings_trial_lte_limit_is_env_configurable(monkeypatch):
    monkeypatch.setenv("TRIAL_LTE_LIMIT_GB", "2.5")

    from bloobcat.settings import AppSettings

    assert AppSettings().trial_lte_limit_gb == 2.5


@pytest.mark.asyncio
async def test_read_trial_lte_limit_prefers_directus_value(monkeypatch):
    from bloobcat.services import trial_lte

    class FakeConnection:
        async def execute_query_dict(self, _sql: str):
            return [{"trial_lte_limit_gb": 2.0}]

    monkeypatch.setattr(trial_lte.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(trial_lte.Tortoise, "get_connection", lambda _name: FakeConnection())
    trial_lte.clear_trial_lte_limit_cache()

    assert await trial_lte.read_trial_lte_limit_gb() == 2.0


@pytest.mark.asyncio
async def test_read_trial_lte_limit_falls_back_when_directus_is_missing(monkeypatch):
    from bloobcat.services import trial_lte

    monkeypatch.setattr(trial_lte.time, "monotonic", lambda: 1000.0)

    def raise_missing(_name: str):
        raise RuntimeError("not initialized")

    monkeypatch.setattr(trial_lte.Tortoise, "get_connection", raise_missing)
    monkeypatch.setattr(trial_lte.app_settings, "trial_lte_limit_gb", 1.5, raising=False)
    trial_lte.clear_trial_lte_limit_cache()

    assert await trial_lte.read_trial_lte_limit_gb() == 1.5


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
