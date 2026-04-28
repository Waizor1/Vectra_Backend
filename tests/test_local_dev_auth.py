import types
from typing import Any, cast

import pytest
from fastapi import HTTPException

from bloobcat.funcs.local_dev_auth import (
    build_local_dev_init_data,
    resolve_local_dev_telegram_user,
)
from bloobcat.settings import LocalDevAuthSettings


def test_resolve_local_dev_telegram_user_rejects_when_disabled():
    request = cast(
        Any, types.SimpleNamespace(headers={"origin": "http://localhost:5173"})
    )

    assert (
        resolve_local_dev_telegram_user(
            build_local_dev_init_data(555),
            request,
            enabled=False,
            allowed_telegram_ids={555},
        )
        is None
    )


def test_resolve_local_dev_telegram_user_accepts_loopback_origin_and_allowed_id():
    request = cast(
        Any, types.SimpleNamespace(headers={"origin": "http://localhost:5173"})
    )

    resolved = resolve_local_dev_telegram_user(
        build_local_dev_init_data(555),
        request,
        enabled=True,
        allowed_telegram_ids={555},
    )

    assert resolved is not None
    assert resolved.user.id == 555


def test_resolve_local_dev_telegram_user_rejects_non_allowlisted_id():
    request = cast(
        Any, types.SimpleNamespace(headers={"origin": "http://localhost:5173"})
    )

    with pytest.raises(HTTPException) as exc_info:
        resolve_local_dev_telegram_user(
            build_local_dev_init_data(555),
            request,
            enabled=True,
            allowed_telegram_ids={777},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("allowed_telegram_ids", [None, set()])
def test_resolve_local_dev_telegram_user_rejects_missing_or_empty_allowlist(
    allowed_telegram_ids,
):
    request = cast(
        Any, types.SimpleNamespace(headers={"origin": "http://localhost:5173"})
    )

    with pytest.raises(HTTPException) as exc_info:
        resolve_local_dev_telegram_user(
            build_local_dev_init_data(555),
            request,
            enabled=True,
            allowed_telegram_ids=allowed_telegram_ids,
        )

    assert exc_info.value.status_code == 403


def test_resolve_local_dev_telegram_user_requires_loopback_origin():
    request = cast(
        Any, types.SimpleNamespace(headers={"origin": "https://app.example.test"})
    )

    with pytest.raises(HTTPException) as exc_info:
        resolve_local_dev_telegram_user(
            build_local_dev_init_data(555),
            request,
            enabled=True,
            allowed_telegram_ids={555},
        )

    assert exc_info.value.status_code == 403


def test_local_dev_auth_settings_accepts_single_numeric_allowlist_value():
    settings = LocalDevAuthSettings.model_validate({"allowed_telegram_ids": 999001})

    assert settings.allowed_telegram_ids == [999001]
