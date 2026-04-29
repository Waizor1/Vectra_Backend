from datetime import datetime, timedelta, timezone
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Request
from pydantic import BaseModel

from bloobcat.db.error_reports import ErrorReports
from bloobcat.logger import get_logger

logger = get_logger("routes.error_reports")

router = APIRouter(prefix="/errors", tags=["errors"])

REDACTED_VALUE = "[redacted]"
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


def _redact_unknown(value: Any, key_hint: str = "", sensitive_values: list[str] | None = None) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return REDACTED_VALUE if SENSITIVE_KEY_RE.search(key_hint) else _redact_string(value, sensitive_values)
    if isinstance(value, list):
        return [_redact_unknown(item, key_hint, sensitive_values) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_unknown(item, str(key), sensitive_values) for key, item in value.items()}
    return _redact_string(str(value), sensitive_values)


@router.post("/report")
async def report_error(payload: ErrorReportPayload, request: Request) -> Dict[str, Any]:
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
