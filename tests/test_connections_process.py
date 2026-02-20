import pytest
from tortoise.exceptions import IntegrityError

from bloobcat.db.connections import Connections


@pytest.mark.asyncio
async def test_connections_process_handles_integrity_error(monkeypatch):
    calls = {"get_or_create": 0, "get": 0}

    async def _get_or_create(**kwargs):
        calls["get_or_create"] += 1
        raise IntegrityError("duplicate key value violates unique constraint")

    async def _get(**kwargs):
        calls["get"] += 1
        return object()

    monkeypatch.setattr(Connections, "get_or_create", _get_or_create)
    monkeypatch.setattr(Connections, "get", _get)

    await Connections.process(user_id=123, at="2026-02-20")

    assert calls["get_or_create"] == 1
    assert calls["get"] == 1
