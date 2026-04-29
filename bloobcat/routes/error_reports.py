from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from bloobcat.db.error_reports import ErrorReports
from bloobcat.logger import get_logger

logger = get_logger("routes.error_reports")

router = APIRouter(prefix="/errors", tags=["errors"])

REDACTED_VALUE = "[redacted]"
TRUNCATED_VALUE = "[truncated]"
ERROR_REPORT_MAX_TEXT_LENGTH = 8_000
ERROR_REPORT_MAX_URL_LENGTH = 2_048
ERROR_REPORT_MAX_USER_AGENT_LENGTH = 512
ERROR_REPORT_MAX_PAYLOAD_BYTES = 24_000
ERROR_REPORT_MAX_EXTRA_DEPTH = 4
ERROR_REPORT_MAX_EXTRA_ITEMS = 40
ERROR_REPORT_MAX_EXTRA_STRING_LENGTH = 2_000
SENSITIVE_KEY_RE = re.compile(
    r"(?:token|ticket|secret|password|payment[_-]?id|session|auth|credential|startapp|start_param|tgwebappdata)",
    re.IGNORECASE,
)
SECRET_LIKE_VALUE_RE = re.compile(r"\b(?:ticket|token|secret|pay|reset)[-_][a-z0-9._~-]+\b", re.IGNORECASE)


class ErrorReportPayload(BaseModel):
    eventId: str
    code: str
    type: str
    createdAtMs: int
    message: Optional[str] = None
    name: Optional[str] = None
    stack: Optional[str] = None
    route: Optional[str] = None
    href: Optional[str] = None
    userAgent: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


def _redact_string(value: str, sensitive_values: list[str] | None = None) -> str:
    next_value = value
    for raw in sensitive_values or []:
        if raw:
            next_value = next_value.replace(raw, REDACTED_VALUE)
    return SECRET_LIKE_VALUE_RE.sub(REDACTED_VALUE, next_value)


def _redact_url(value: str | None) -> tuple[str | None, list[str]]:
    if not value:
        return value, []
    sensitive_values: list[str] = []
    try:
        split = urlsplit(value)
        query_pairs = []
        for key, item in parse_qsl(split.query, keep_blank_values=True):
            if SENSITIVE_KEY_RE.search(key):
                if item:
                    sensitive_values.append(item)
                query_pairs.append((key, REDACTED_VALUE))
            else:
                query_pairs.append((key, item))

        fragment_pairs = parse_qsl(split.fragment, keep_blank_values=True)
        if fragment_pairs:
            next_fragment_pairs = []
            for key, item in fragment_pairs:
                if SENSITIVE_KEY_RE.search(key):
                    if item:
                        sensitive_values.append(item)
                    next_fragment_pairs.append((key, REDACTED_VALUE))
                else:
                    next_fragment_pairs.append((key, item))
            fragment = urlencode(next_fragment_pairs, doseq=True)
        else:
            fragment = _redact_string(split.fragment, sensitive_values)

        redacted = urlunsplit(
            (
                split.scheme,
                split.netloc,
                split.path,
                urlencode(query_pairs, doseq=True),
                fragment,
            )
        ).replace("%5Bredacted%5D", REDACTED_VALUE)
        return redacted, sensitive_values
    except Exception:
        return _redact_string(value), sensitive_values


def _truncate_string(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[:max_length] + TRUNCATED_VALUE


def _redact_unknown(
    value: Any,
    key_hint: str = "",
    sensitive_values: list[str] | None = None,
    *,
    depth: int = 0,
) -> Any:
    if depth >= ERROR_REPORT_MAX_EXTRA_DEPTH:
        return TRUNCATED_VALUE
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if SENSITIVE_KEY_RE.search(key_hint):
            return REDACTED_VALUE
        return _truncate_string(
            _redact_string(value, sensitive_values),
            ERROR_REPORT_MAX_EXTRA_STRING_LENGTH,
        )
    if isinstance(value, list):
        bounded = value[:ERROR_REPORT_MAX_EXTRA_ITEMS]
        result = [
            _redact_unknown(item, key_hint, sensitive_values, depth=depth + 1)
            for item in bounded
        ]
        if len(value) > ERROR_REPORT_MAX_EXTRA_ITEMS:
            result.append(TRUNCATED_VALUE)
        return result
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= ERROR_REPORT_MAX_EXTRA_ITEMS:
                result[TRUNCATED_VALUE] = TRUNCATED_VALUE
                break
            result[str(key)[:128]] = _redact_unknown(
                item,
                str(key),
                sensitive_values,
                depth=depth + 1,
            )
        return result
    return _truncate_string(
        _redact_string(str(value), sensitive_values),
        ERROR_REPORT_MAX_EXTRA_STRING_LENGTH,
    )


def _reject_if_too_large(payload: ErrorReportPayload) -> None:
    field_limits = {
        "eventId": 128,
        "code": 128,
        "type": 64,
        "message": ERROR_REPORT_MAX_TEXT_LENGTH,
        "name": 256,
        "stack": ERROR_REPORT_MAX_TEXT_LENGTH,
        "route": ERROR_REPORT_MAX_URL_LENGTH,
        "href": ERROR_REPORT_MAX_URL_LENGTH,
        "userAgent": ERROR_REPORT_MAX_USER_AGENT_LENGTH,
    }
    for field_name, limit in field_limits.items():
        raw = getattr(payload, field_name, None)
        if raw is not None and len(str(raw)) > limit:
            raise HTTPException(status_code=413, detail="Error report payload is too large")

    try:
        serialized = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception:
        return
    if len(serialized.encode("utf-8")) > ERROR_REPORT_MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Error report payload is too large")


@router.post("/report")
async def report_error(payload: ErrorReportPayload, request: Request) -> Dict[str, Any]:
    _reject_if_too_large(payload)

    user_id = None
    auth_header = request.headers.get("Authorization")
    if auth_header:
        # best-effort: extract user id from Authorization if it is a numeric id
        # (avoid validate() here to keep the endpoint resilient when auth is broken)
        try:
            if auth_header.isdigit():
                user_id = int(auth_header)
        except Exception:
            user_id = None

    reported_at = None
    if payload.createdAtMs:
        try:
            reported_at = datetime.fromtimestamp(payload.createdAtMs / 1000, tz=timezone.utc)
        except Exception:
            reported_at = None

    route, route_secrets = _redact_url(payload.route)
    href, href_secrets = _redact_url(payload.href)
    sensitive_values = route_secrets + href_secrets

    await ErrorReports.create(
        user_id=user_id,
        event_id=payload.eventId,
        code=payload.code,
        type=payload.type,
        message=_redact_string(payload.message or "", sensitive_values) or None,
        name=payload.name,
        stack=_redact_string(payload.stack or "", sensitive_values) or None,
        route=route,
        href=href,
        user_agent=payload.userAgent,
        extra=_redact_unknown(payload.extra, sensitive_values=sensitive_values),
        triage_severity="medium",
        triage_status="new",
        triage_due_at=datetime.now(timezone.utc) + timedelta(hours=24),
        reported_at=reported_at,
    )
    logger.info("Error report received", extra={"code": payload.code, "type": payload.type, "user_id": user_id})
    return {"ok": True}
