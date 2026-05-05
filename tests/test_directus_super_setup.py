from __future__ import annotations

from typing import Any

from scripts.directus_super_setup import ensure_admin_settings


class _Response:
    def __init__(self, status_code: int = 200, data: Any | None = None) -> None:
        self.status_code = status_code
        self._data = data

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def json(self) -> dict[str, Any]:
        return {"data": self._data}

    def raise_for_status(self) -> None:
        if not self.ok:
            raise AssertionError(f"unexpected status: {self.status_code}")


class _AdminSettingsClient:
    def __init__(self, item_payload: Any) -> None:
        self.item_payload = item_payload
        self.patches: list[tuple[str, dict[str, Any] | None]] = []
        self.posts: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> _Response:
        if path == "/collections/tvpn_admin_settings":
            return _Response(200, {"collection": "tvpn_admin_settings"})
        if path.startswith("/fields/tvpn_admin_settings/"):
            return _Response(200, {"field": path.rsplit("/", 1)[-1]})
        if path == "/items/tvpn_admin_settings":
            return _Response(200, self.item_payload)
        if path == "/policies":
            return _Response(200, [])
        raise AssertionError(f"unexpected GET {path} params={params!r}")

    def post(self, path: str, *, json: dict[str, Any] | None = None) -> _Response:
        self.posts.append((path, json))
        return _Response(200, {"id": 777})

    def patch(self, path: str, *, json: dict[str, Any] | None = None) -> _Response:
        self.patches.append((path, json))
        return _Response(200, {"id": path.rsplit("/", 1)[-1], **(json or {})})


def test_ensure_admin_settings_handles_directus_singleton_object_payload() -> None:
    client = _AdminSettingsClient({"id": 42, "trial_lte_limit_gb": None})

    ensure_admin_settings(client)  # type: ignore[arg-type]

    assert (
        "/items/tvpn_admin_settings/42",
        {"trial_lte_limit_gb": 1.0},
    ) in client.patches


def test_ensure_admin_settings_preserves_existing_trial_lte_limit() -> None:
    client = _AdminSettingsClient({"id": 42, "trial_lte_limit_gb": 0.0})

    ensure_admin_settings(client)  # type: ignore[arg-type]

    assert not [
        payload
        for path, payload in client.patches
        if path == "/items/tvpn_admin_settings/42"
    ]
