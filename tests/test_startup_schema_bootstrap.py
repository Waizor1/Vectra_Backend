from __future__ import annotations

import pytest

from bloobcat import __main__ as app_main


@pytest.mark.asyncio
async def test_schema_bootstrap_adds_email_preference_column_when_schema_exists(monkeypatch):
    calls: list[tuple[str, object]] = []

    class _FakeConnection:
        async def execute_query_dict(self, _sql: str):
            calls.append(("query", "schema_initialized"))
            return [{"users_table": "users", "auth_table": "auth_identities"}]

        async def execute_script(self, sql: str):
            calls.append(("script", sql))

    class _FakeTortoise:
        @staticmethod
        async def init(config):
            calls.append(("init", config))

        @staticmethod
        def get_connection(name: str):
            calls.append(("get_connection", name))
            return _FakeConnection()

        @staticmethod
        async def generate_schemas(*, safe: bool):
            calls.append(("generate_schemas", safe))

        @staticmethod
        async def close_connections():
            calls.append(("close", None))

    monkeypatch.setattr(app_main, "Tortoise", _FakeTortoise)

    await app_main._initialize_schema_without_aerich()

    scripts = [value for kind, value in calls if kind == "script"]
    assert any("email_notifications_enabled" in str(sql) for sql in scripts)
    assert any("trial_started_at" in str(sql) for sql in scripts)
    assert any("CREATE TABLE IF NOT EXISTS \"user_devices\"" in str(sql) for sql in scripts)
    assert any("temp_setup_token" in str(sql) for sql in scripts)
    assert any("device_per_user_enabled" in str(sql) for sql in scripts)
    assert ("generate_schemas", True) not in calls
    assert ("close", None) in calls
