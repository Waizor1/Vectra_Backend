import pytest
from fastapi import HTTPException

from bloobcat.routes import admin_integration as ai_route
from bloobcat.services import admin_integration as ai_service


class _DummyClient:
    def __init__(self, users_api):
        self.users = users_api

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_preview_hwid_purge_merges_local_history_live_matches_and_users(monkeypatch):
    local_history = [
        {
            "id": 1,
            "hwid": "hwid-1",
            "user_uuid": "uuid-local",
            "telegram_user_id": 49991603,
            "first_seen_at": "2026-04-20T10:00:00+00:00",
            "last_seen_at": "2026-04-22T10:00:00+00:00",
        },
        {
            "id": 2,
            "hwid": "hwid-1",
            "user_uuid": "uuid-local",
            "telegram_user_id": 49991603,
            "first_seen_at": "2026-04-21T10:00:00+00:00",
            "last_seen_at": "2026-04-22T11:00:00+00:00",
        },
    ]
    live_scan = {
        "ok": True,
        "matches": [
            {
                "hwid": "hwid-1",
                "user_uuid": "uuid-live",
                "platform": "iOS",
                "device_model": "iPhone 15",
                "updated_at": "2026-04-22T12:00:00+00:00",
            }
        ],
        "pages_fetched": 2,
        "total_devices": 501,
        "error": None,
    }
    users = [
        {
            "id": 49991603,
            "username": "juliashv",
            "full_name": "Julia Sh",
            "remnawave_uuid": "uuid-local",
            "is_trial": False,
            "used_trial": True,
            "expired_at": "2026-04-22",
            "active_tariff_id": None,
            "is_registered": True,
        },
        {
            "id": 77,
            "username": "other",
            "full_name": "Other User",
            "remnawave_uuid": "uuid-live",
            "is_trial": False,
            "used_trial": False,
            "expired_at": None,
            "active_tariff_id": 15,
            "is_registered": True,
        },
    ]

    async def _load_local(hwid):
        assert hwid == "hwid-1"
        return local_history

    async def _scan_live(hwid):
        assert hwid == "hwid-1"
        return live_scan

    async def _load_users(*, telegram_user_ids, owner_uuids):
        assert telegram_user_ids == [49991603]
        assert owner_uuids == ["uuid-local", "uuid-live"]
        return users

    monkeypatch.setattr(ai_service, "_load_local_hwid_history", _load_local)
    monkeypatch.setattr(ai_service, "_scan_live_hwid_matches", _scan_live)
    monkeypatch.setattr(ai_service, "_load_related_hwid_users", _load_users)

    preview = await ai_service.preview_hwid_purge("  hwid-1  ")

    assert preview["hwid"] == "hwid-1"
    assert preview["summary"] == {
        "local_history_rows": 2,
        "remnawave_live_matches": 1,
        "owners": 2,
        "local_users": 2,
        "has_matches": True,
    }
    assert preview["owner_uuids"] == ["uuid-local", "uuid-live"]
    assert preview["remnawave_scan"] == {
        "ok": True,
        "pages_fetched": 2,
        "total_devices": 501,
        "error": None,
    }

    first_owner, second_owner = preview["owners"]
    assert first_owner["user_uuid"] == "uuid-local"
    assert first_owner["telegram_user_id"] == 49991603
    assert first_owner["local_history_rows"] == 2
    assert first_owner["live_matches"] == 0
    assert first_owner["source_local_history"] is True
    assert first_owner["source_remnawave_live"] is False
    assert first_owner["used_trial"] is True
    assert first_owner["local_last_seen_at"] == "2026-04-22T11:00:00+00:00"

    assert second_owner["user_uuid"] == "uuid-live"
    assert second_owner["telegram_user_id"] == 77
    assert second_owner["source_local_history"] is False
    assert second_owner["source_remnawave_live"] is True
    assert second_owner["live_matches"] == 1
    assert second_owner["live_platforms"] == ["iOS"]
    assert second_owner["live_device_models"] == ["iPhone 15"]
    assert second_owner["active_tariff_id"] == 15


@pytest.mark.asyncio
async def test_delete_hwid_from_remnawave_owners_classifies_statuses(monkeypatch):
    class _UsersApi:
        async def delete_user_hwid_device(self, user_uuid, hwid):
            assert hwid == "hwid-2"
            if user_uuid == "uuid-deleted":
                return {"ok": True}
            if user_uuid == "uuid-absent":
                raise Exception("API error [A101]: Delete hwid user device error")
            if user_uuid == "uuid-missing":
                raise Exception("API error [A063]: User with specified params not found")
            raise Exception("upstream exploded")

    monkeypatch.setattr(
        ai_service,
        "RemnaWaveClient",
        lambda *_args, **_kwargs: _DummyClient(_UsersApi()),
    )

    results = await ai_service._delete_hwid_from_remnawave_owners(
        "hwid-2",
        ["uuid-deleted", "uuid-absent", "uuid-missing", "uuid-error"],
    )

    assert results == [
        {"user_uuid": "uuid-deleted", "status": "deleted", "error": None},
        {"user_uuid": "uuid-absent", "status": "already_absent", "error": None},
        {"user_uuid": "uuid-missing", "status": "user_missing", "error": None},
        {"user_uuid": "uuid-error", "status": "error", "error": "upstream exploded"},
    ]


@pytest.mark.asyncio
async def test_purge_hwid_everywhere_returns_partial_summary_and_normalized_actor(monkeypatch):
    collected_context = {
        "hwid": "hwid-3",
        "summary": {
            "local_history_rows": 3,
            "remnawave_live_matches": 2,
            "owners": 3,
            "local_users": 2,
            "has_matches": True,
        },
        "owner_uuids": ["uuid-a", "uuid-b", "uuid-c"],
        "local_history": [],
        "live_matches": [],
        "owners": [],
        "users": [],
        "remnawave_scan": {"ok": True, "pages_fetched": 1, "total_devices": 100, "error": None},
    }

    async def _collect(hwid):
        assert hwid == "hwid-3"
        return collected_context

    async def _delete_remote(hwid, owner_uuids):
        assert hwid == "hwid-3"
        assert owner_uuids == ["uuid-a", "uuid-b", "uuid-c"]
        return [
            {"user_uuid": "uuid-a", "status": "deleted", "error": None},
            {"user_uuid": "uuid-b", "status": "already_absent", "error": None},
            {"user_uuid": "uuid-c", "status": "error", "error": "timeout"},
        ]

    async def _delete_local(hwid):
        assert hwid == "hwid-3"
        return 3

    monkeypatch.setattr(ai_service, "_collect_hwid_context", _collect)
    monkeypatch.setattr(ai_service, "_delete_hwid_from_remnawave_owners", _delete_remote)
    monkeypatch.setattr(ai_service, "_delete_local_hwid_history", _delete_local)

    result = await ai_service.purge_hwid_everywhere(
        " hwid-3 ",
        reason="  stale anti-twink trace  ",
        actor={
            "user_id": " 11 ",
            "role_id": " support ",
            "name": "Operator",
            "admin": True,
        },
    )

    assert result["hwid"] == "hwid-3"
    assert result["ok"] is False
    assert result["partial"] is True
    assert result["reason"] == "stale anti-twink trace"
    assert result["actor"] == {
        "directus_user_id": "11",
        "directus_role_id": "support",
        "name": "Operator",
        "is_admin": True,
    }
    assert result["context"] is collected_context
    assert result["summary"] == {
        "local_history_deleted": 3,
        "remnawave_attempts": 3,
        "remnawave_deleted": 1,
        "remnawave_already_absent": 1,
        "remnawave_user_missing": 0,
        "remnawave_errors": 1,
    }


@pytest.mark.asyncio
async def test_purge_hwid_everywhere_aborts_when_live_scan_fails(monkeypatch):
    collected_context = {
        "hwid": "hwid-scan-fail",
        "summary": {
            "local_history_rows": 1,
            "remnawave_live_matches": 0,
            "owners": 0,
            "local_users": 0,
            "has_matches": True,
        },
        "owner_uuids": [],
        "local_history": [
            {
                "id": 1,
                "hwid": "hwid-scan-fail",
                "user_uuid": None,
                "telegram_user_id": 49991603,
                "first_seen_at": None,
                "last_seen_at": None,
            }
        ],
        "live_matches": [],
        "owners": [],
        "users": [],
        "remnawave_scan": {
            "ok": False,
            "pages_fetched": 1,
            "total_devices": None,
            "error": "timeout",
        },
    }

    async def _collect(hwid):
        assert hwid == "hwid-scan-fail"
        return collected_context

    async def _delete_remote(*_args, **_kwargs):
        raise AssertionError("remote deletion must not run after failed live scan")

    async def _delete_local(*_args, **_kwargs):
        raise AssertionError("local history must not be deleted after failed live scan")

    monkeypatch.setattr(ai_service, "_collect_hwid_context", _collect)
    monkeypatch.setattr(ai_service, "_delete_hwid_from_remnawave_owners", _delete_remote)
    monkeypatch.setattr(ai_service, "_delete_local_hwid_history", _delete_local)

    with pytest.raises(ai_service.HwidPurgePreconditionError) as exc_info:
        await ai_service.purge_hwid_everywhere(" hwid-scan-fail ")

    assert "Live RemnaWave HWID scan failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_purge_hwid_route_maps_failed_live_scan_to_conflict(monkeypatch):
    async def _purge(*_args, **_kwargs):
        raise ai_service.HwidPurgePreconditionError("scan failed")

    monkeypatch.setattr(ai_route, "purge_hwid_everywhere", _purge)

    with pytest.raises(HTTPException) as exc_info:
        await ai_route.purge_hwid(ai_route.HwidPurgePayload(hwid="hwid-scan-fail"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "scan failed"
