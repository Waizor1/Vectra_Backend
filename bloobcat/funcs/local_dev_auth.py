from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


LOCAL_DEV_INIT_DATA_PREFIX = "dev-local-tg:"


def build_local_dev_init_data(telegram_user_id: int | str) -> str:
    return f"{LOCAL_DEV_INIT_DATA_PREFIX}{telegram_user_id}"


def _parse_positive_int(raw_value: str | None) -> int | None:
    value = (raw_value or "").strip()
    if not value or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _is_loopback_host(host: str | None) -> bool:
    value = (host or "").strip().lower().strip("[]")
    return value in {"localhost", "127.0.0.1", "::1"}


def _extract_loopback_origin_allowed(request: Request | None) -> bool:
    if request is None:
        return False

    for header_name in ("origin", "referer"):
        raw_value = (request.headers.get(header_name) or "").strip()
        if not raw_value:
            continue
        try:
            parsed = urlsplit(raw_value)
        except ValueError:
            continue
        if parsed.scheme != "http":
            continue
        if _is_loopback_host(parsed.hostname):
            return True

    return False


def resolve_local_dev_telegram_user(
    init_data: str,
    request: Request | None,
    *,
    enabled: bool,
    allowed_telegram_ids: set[int] | None,
):
    raw_init_data = (init_data or "").strip()
    if not enabled or not raw_init_data.startswith(LOCAL_DEV_INIT_DATA_PREFIX):
        return None

    if not _extract_loopback_origin_allowed(request):
        raise HTTPException(
            status_code=403, detail="Local dev auth requires loopback browser origin"
        )

    telegram_user_id = _parse_positive_int(
        raw_init_data[len(LOCAL_DEV_INIT_DATA_PREFIX) :]
    )
    if telegram_user_id is None:
        raise HTTPException(status_code=403, detail="Invalid local dev auth payload")

    if not allowed_telegram_ids or telegram_user_id not in allowed_telegram_ids:
        raise HTTPException(
            status_code=403, detail="Local dev auth forbidden for this Telegram user"
        )

    return SimpleNamespace(
        user=SimpleNamespace(
            id=telegram_user_id,
            username=None,
            first_name="LocalDev",
            last_name=None,
        ),
        start_param=None,
    )
