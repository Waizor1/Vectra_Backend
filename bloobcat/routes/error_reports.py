from datetime import datetime, timedelta, timezone
import asyncio
import hashlib
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from tortoise.exceptions import IntegrityError
from tortoise.expressions import F

from bloobcat.db.error_reports import ErrorReports
from bloobcat.db.users import Users
from bloobcat.funcs.auth_tokens import decode_access_token
from bloobcat.logger import get_logger
from bloobcat.settings import telegram_settings

logger = get_logger("routes.error_reports")

router = APIRouter(prefix="/errors", tags=["errors"])

REDACTED_VALUE = "[redacted]"
TRUNCATED_VALUE = "[truncated]"
ERROR_REPORT_MAX_TEXT_LENGTH = 8_000
ERROR_REPORT_MAX_URL_LENGTH = 2_048
ERROR_REPORT_MAX_USER_AGENT_LENGTH = 512
ERROR_REPORT_MAX_PAYLOAD_BYTES = 32_000  # bumped from 24 KB to fit breadcrumbs
ERROR_REPORT_MAX_EXTRA_DEPTH = 4
ERROR_REPORT_MAX_EXTRA_ITEMS = 40
ERROR_REPORT_MAX_EXTRA_STRING_LENGTH = 2_000
ERROR_REPORT_MAX_BREADCRUMBS = 30
ERROR_REPORT_MAX_BREADCRUMB_FIELD_LENGTH = 512
SENSITIVE_KEY_RE = re.compile(
    r"(?:token|ticket|secret|password|payment[_-]?id|session|auth|credential|startapp|start_param|tgwebappdata)",
    re.IGNORECASE,
)
SECRET_LIKE_VALUE_RE = re.compile(r"\b(?:ticket|token|secret|pay|reset)[-_][a-z0-9._~-]+\b", re.IGNORECASE)

# --- Severity --------------------------------------------------------------

ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}
TYPE_SEVERITY_MAP = {
    "FE_CHUNK_LOAD": "high",
    "SVC_UNAVAILABLE": "high",
    "NET_OFFLINE": "low",
    "FE_RENDER": "high",
    "FE_RUNTIME": "medium",
    "FE_UNHANDLED_REJECTION": "medium",
    "SVC_HTTP_ERROR": "medium",
    "BE_EXCEPTION": "high",
    "UNKNOWN": "medium",
}
NOTIFY_SEVERITIES = {"high", "critical"}

# --- Noise dropping (server-side defense in depth) -------------------------
# The frontend already drops these at source, but keeping a server filter
# prevents older clients (cached SW) from polluting the table.

NOISE_MESSAGE_PATTERNS = [
    re.compile(r"TelegramGameProxy", re.IGNORECASE),
    re.compile(r"^\s*Script error\.?\s*$", re.IGNORECASE),
]


def _is_noise(name: Optional[str], message: Optional[str]) -> bool:
    haystack = f"{name or ''} {message or ''}".strip()
    if not haystack:
        return False
    return any(p.search(haystack) for p in NOISE_MESSAGE_PATTERNS)


# --- Fingerprint -----------------------------------------------------------

_NUMBER_RE = re.compile(r"\b\d+\b")
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE)
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _normalize_for_fingerprint(text: Optional[str]) -> str:
    if not text:
        return ""
    s = text.strip()
    s = _UUID_RE.sub("<uuid>", s)
    s = _HEX_RE.sub("<hex>", s)
    s = _NUMBER_RE.sub("<n>", s)
    s = re.sub(r"\s+", " ", s)
    return s.lower()[:512]


def _stack_top_frames(stack: Optional[str], n: int = 3) -> str:
    if not stack:
        return ""
    lines = [ln.strip() for ln in stack.splitlines() if ln.strip()]
    # strip per-build hashes from /assets/<name>-<hash>.js to keep grouping
    # stable across releases
    cleaned = []
    for ln in lines[:n]:
        ln2 = re.sub(r"/assets/([\w\-.]+?)-[A-Za-z0-9_]{6,12}(\.[a-z0-9]+)", r"/assets/\1\2", ln)
        ln2 = _NUMBER_RE.sub("<n>", ln2)
        cleaned.append(ln2[:200])
    return "\n".join(cleaned).lower()


def compute_fingerprint(
    *,
    type_: str,
    name: Optional[str],
    message: Optional[str],
    stack: Optional[str],
) -> str:
    """Stable hash of the error identity (independent of timestamp/build hash).

    Used as the dedup key — all repeats of the same logical error UPSERT into
    the same row.
    """
    parts = [
        (type_ or "").upper(),
        _normalize_for_fingerprint(name),
        _normalize_for_fingerprint(message),
        _stack_top_frames(stack, n=3),
    ]
    digest = hashlib.sha1("\n".join(parts).encode("utf-8", "replace")).hexdigest()
    return digest[:40]


def resolve_severity(type_: Optional[str], severity_hint: Optional[str]) -> str:
    if severity_hint:
        normalized = severity_hint.strip().lower()
        if normalized in ALLOWED_SEVERITIES:
            return normalized
    return TYPE_SEVERITY_MAP.get((type_ or "").upper(), "medium")


# --- Payload model ---------------------------------------------------------


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
    # Release / build context
    appVersion: Optional[str] = None
    commitSha: Optional[str] = None
    bundleHash: Optional[str] = None
    # Session / platform context
    sessionId: Optional[str] = None
    platform: Optional[str] = None
    tgPlatform: Optional[str] = None
    tgVersion: Optional[str] = None
    viewportW: Optional[int] = None
    viewportH: Optional[int] = None
    dpr: Optional[float] = None
    connectionType: Optional[str] = None
    locale: Optional[str] = None
    # Last user actions before the crash
    breadcrumbs: Optional[List[Dict[str, Any]]] = None
    # Client severity hint (server validates against an allow-list)
    severityHint: Optional[str] = None
    # Runtime context — captured at the moment of failure
    pageAgeMs: Optional[int] = None
    documentReadyState: Optional[str] = None
    documentVisibilityState: Optional[str] = None
    online: Optional[bool] = None
    saveData: Optional[bool] = None
    hardwareConcurrency: Optional[int] = None
    deviceMemory: Optional[float] = None
    jsHeapUsedMb: Optional[float] = None
    jsHeapTotalMb: Optional[float] = None
    jsHeapLimitMb: Optional[float] = None
    swController: Optional[str] = None
    referrer: Optional[str] = None


# --- Redaction helpers (existing logic kept) -------------------------------


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


def _redact_breadcrumbs(
    value: Optional[List[Dict[str, Any]]],
    sensitive_values: list[str] | None = None,
) -> Optional[List[Dict[str, Any]]]:
    if not value:
        return None
    items = value[-ERROR_REPORT_MAX_BREADCRUMBS:]
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cleaned: Dict[str, Any] = {}
        for key, raw in item.items():
            if not isinstance(key, str):
                continue
            short_key = key[:64]
            if SENSITIVE_KEY_RE.search(short_key):
                cleaned[short_key] = REDACTED_VALUE
                continue
            if isinstance(raw, str):
                cleaned[short_key] = _truncate_string(
                    _redact_string(raw, sensitive_values),
                    ERROR_REPORT_MAX_BREADCRUMB_FIELD_LENGTH,
                )
            elif isinstance(raw, (bool, int, float)) or raw is None:
                cleaned[short_key] = raw
            else:
                cleaned[short_key] = _truncate_string(
                    _redact_string(str(raw), sensitive_values),
                    ERROR_REPORT_MAX_BREADCRUMB_FIELD_LENGTH,
                )
        if cleaned:
            out.append(cleaned)
    return out or None


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
        "appVersion": 64,
        "commitSha": 64,
        "bundleHash": 64,
        "sessionId": 64,
        "platform": 32,
        "tgPlatform": 32,
        "tgVersion": 16,
        "connectionType": 16,
        "locale": 16,
        "severityHint": 16,
        "documentReadyState": 16,
        "documentVisibilityState": 16,
        "swController": 256,
        "referrer": ERROR_REPORT_MAX_URL_LENGTH,
    }
    for field_name, limit in field_limits.items():
        raw = getattr(payload, field_name, None)
        if raw is not None and len(str(raw)) > limit:
            raise HTTPException(status_code=413, detail="Error report payload is too large")

    if payload.breadcrumbs is not None and len(payload.breadcrumbs) > ERROR_REPORT_MAX_BREADCRUMBS * 2:
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


# --- Admin notification (best-effort, fire-and-forget) ---------------------

async def _notify_admin_new_high_severity(
    *,
    fingerprint: str,
    severity: str,
    type_: str,
    message: Optional[str],
    route: Optional[str],
    app_version: Optional[str],
) -> None:
    """Best-effort admin alert on first occurrence of a high-severity error.

    Failures are swallowed so a flaky bot/network never propagates to the
    client.
    """
    try:
        from bloobcat.bot.notifications.admin import (  # late import
            _safe_html,
            send_admin_message,
        )
    except Exception:
        return
    try:
        emoji = "🟠" if severity == "high" else "🔴"
        version_part = f" v{_safe_html(app_version)}" if app_version else ""
        snippet_raw = (message or "—").strip().splitlines()[0][:200]
        # Все client-controlled поля проходят через _safe_html: payload
        # принимается анонимно, parse_mode="HTML" иначе позволил бы внедрять
        # теги (вплоть до <a href>) в админский чат.
        text = (
            f"{emoji} <b>Новая ошибка</b> [{_safe_html(severity)}]{version_part}\n"
            f"<b>Тип:</b> <code>{_safe_html(type_)}</code>\n"
            f"<b>Маршрут:</b> <code>{_safe_html(route or '—')}</code>\n"
            f"<b>Сообщение:</b> {_safe_html(snippet_raw)}\n"
            f"<b>Fingerprint:</b> <code>{_safe_html(fingerprint[:12])}</code>"
        )
        await send_admin_message(text, parse_mode="HTML")
    except Exception:
        # Notifications are best-effort; never break the request.
        return


# --- Persistence (UPSERT by fingerprint) -----------------------------------


async def _upsert_error_report(*, fingerprint: str, fields: Dict[str, Any]) -> bool:
    """Insert a new row or bump occurrences on an existing one.

    Returns True if a brand-new row was created (caller may want to fire an
    admin notification). Returns False on bump.
    """
    now = datetime.now(timezone.utc)
    observation_keys = (
        "message", "name", "stack", "route", "href", "user_agent", "extra",
        "app_version", "commit_sha", "bundle_hash",
        "session_id", "platform", "tg_platform", "tg_version",
        "viewport_w", "viewport_h", "dpr", "connection_type", "locale",
        "breadcrumbs", "severity_hint", "request_id",
        "page_age_ms", "document_ready_state", "document_visibility_state",
        "online", "save_data", "hardware_concurrency", "device_memory",
        "js_heap_used_mb", "js_heap_total_mb", "js_heap_limit_mb",
        "sw_controller", "referrer",
        "user_id", "event_id", "code", "reported_at",
    )
    observation_update = {
        key: fields[key]
        for key in observation_keys
        if key in fields and fields[key] is not None
    }
    # Атомарный bump через UPDATE WHERE fingerprint: параллельные воркеры
    # на одинаковом fingerprint не теряют инкременты occurrences
    # (read-modify-write через .save() терял один из двух bump'ов под
    # высокой нагрузкой). Fallback на старый read-modify-write путь
    # сохранён для юнит-тестов с FakeQS, у которых нет QuerySet.update();
    # в проде Tortoise всегда возвращает реальный QuerySet с update().
    qs = ErrorReports.filter(fingerprint=fingerprint)
    update_method = getattr(qs, "update", None)
    if callable(update_method):
        bumped = await update_method(
            occurrences=F("occurrences") + 1,
            last_seen_at=now,
            **observation_update,
        )
        if bumped:
            return False
    else:
        existing = await qs.first()
        if existing is not None:
            existing.occurrences = (existing.occurrences or 0) + 1
            existing.last_seen_at = now
            for key, value in observation_update.items():
                setattr(existing, key, value)
            await existing.save()
            return False

    fields = dict(fields)
    fields.setdefault("first_seen_at", fields.get("reported_at") or now)
    fields.setdefault("last_seen_at", fields.get("reported_at") or now)
    fields.setdefault("occurrences", 1)
    fields["fingerprint"] = fingerprint
    try:
        await ErrorReports.create(**fields)
        return True
    except IntegrityError:
        # Race: другой воркер успел INSERT между нашим UPDATE и CREATE.
        # Повторяем атомарный bump — теперь строка точно существует.
        retry_qs = ErrorReports.filter(fingerprint=fingerprint)
        retry_update = getattr(retry_qs, "update", None)
        if callable(retry_update):
            await retry_update(
                occurrences=F("occurrences") + 1,
                last_seen_at=now,
                **observation_update,
            )
        else:
            existing = await retry_qs.first()
            if existing is None:
                raise
            existing.occurrences = (existing.occurrences or 0) + 1
            existing.last_seen_at = now
            for key, value in observation_update.items():
                setattr(existing, key, value)
            await existing.save()
        return False


# --- Auth resolution -------------------------------------------------------


async def _resolve_user_id_optional(request: Request) -> Optional[int]:
    """Resolve user_id from the Authorization header, never raising.

    Mirrors the contract in ``bloobcat.funcs.validate``:
      - ``Bearer <jwt>`` → decode_access_token; on success return ``sub`` /
        ``user_id`` validated against the persisted ``auth_token_version``.
      - Otherwise the value is treated as raw Telegram WebApp ``init_data`` and
        parsed via ``safe_parse_webapp_init_data``.

    Anything else (numeric tokens, empty strings, malformed init_data,
    expired/invalid JWTs) yields ``None`` so that ``/errors/report`` remains
    accepting anonymous client reports without spoofing the ``user_id`` field.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            return None
        try:
            payload = decode_access_token(token)
        except Exception:
            return None
        sub = payload.get("sub") or payload.get("user_id")
        if not sub:
            return None
        try:
            user_id_int = int(sub)
        except (TypeError, ValueError):
            return None
        token_version = payload.get("ver")
        if token_version is None:
            return user_id_int
        try:
            db_user = await Users.get_or_none(id=user_id_int)
        except Exception:
            return None
        if not db_user:
            return None
        try:
            if int(token_version) != int(db_user.auth_token_version or 0):
                return None
        except (TypeError, ValueError):
            return None
        return user_id_int

    try:
        from aiogram.utils.web_app import safe_parse_webapp_init_data

        parsed = safe_parse_webapp_init_data(
            telegram_settings.token.get_secret_value(), auth_header
        )
    except Exception:
        return None
    if not parsed or not getattr(parsed, "user", None):
        return None
    try:
        return int(parsed.user.id)
    except (TypeError, ValueError, AttributeError):
        return None


# --- Route -----------------------------------------------------------------


@router.post("/report")
async def report_error(payload: ErrorReportPayload, request: Request) -> Dict[str, Any]:
    _reject_if_too_large(payload)

    # Server-side noise filter — accept the request but don't persist.
    if _is_noise(payload.name, payload.message):
        logger.info(
            "Error report dropped as noise",
            extra={"code": payload.code, "type": payload.type},
        )
        return {"ok": True, "dropped": "noise"}

    user_id = await _resolve_user_id_optional(request)

    # Pick up X-Request-Id from upstream / request middleware if present.
    request_id = (
        request.headers.get("X-Request-Id")
        or getattr(getattr(request, "state", None), "request_id", None)
    )

    reported_at = None
    if payload.createdAtMs:
        try:
            reported_at = datetime.fromtimestamp(payload.createdAtMs / 1000, tz=timezone.utc)
        except Exception:
            reported_at = None

    route, route_secrets = _redact_url(payload.route)
    href, href_secrets = _redact_url(payload.href)
    sensitive_values = route_secrets + href_secrets

    redacted_message = _redact_string(payload.message or "", sensitive_values) or None
    redacted_stack = _redact_string(payload.stack or "", sensitive_values) or None
    redacted_extra = _redact_unknown(payload.extra, sensitive_values=sensitive_values)
    redacted_breadcrumbs = _redact_breadcrumbs(payload.breadcrumbs, sensitive_values)
    referrer_redacted, referrer_secrets = _redact_url(payload.referrer)
    if referrer_secrets:
        sensitive_values.extend(referrer_secrets)

    severity = resolve_severity(payload.type, payload.severityHint)
    fingerprint = compute_fingerprint(
        type_=payload.type,
        name=payload.name,
        message=redacted_message,
        stack=redacted_stack,
    )

    # Поля, не прошедшие redaction до сих пор, но способные нести PII /
    # runtime-токены (UA embedded WebView, client-controlled name/eventId/
    # code). Прогоняем через тот же фильтр, что message/stack.
    redacted_user_agent = (
        _redact_string(payload.userAgent or "", sensitive_values) or None
    )
    redacted_name = _redact_string(payload.name or "", sensitive_values) or None
    redacted_event_id = (
        _redact_string(payload.eventId or "", sensitive_values) or None
    )
    redacted_code = _redact_string(payload.code or "", sensitive_values) or None

    fields: Dict[str, Any] = dict(
        user_id=user_id,
        event_id=redacted_event_id,
        code=redacted_code,
        type=payload.type,
        message=redacted_message,
        name=redacted_name,
        stack=redacted_stack,
        route=route,
        href=href,
        user_agent=redacted_user_agent,
        extra=redacted_extra,
        app_version=payload.appVersion,
        commit_sha=payload.commitSha,
        bundle_hash=payload.bundleHash,
        session_id=payload.sessionId,
        platform=payload.platform,
        tg_platform=payload.tgPlatform,
        tg_version=payload.tgVersion,
        viewport_w=payload.viewportW,
        viewport_h=payload.viewportH,
        dpr=payload.dpr,
        connection_type=payload.connectionType,
        locale=payload.locale,
        breadcrumbs=redacted_breadcrumbs,
        severity_hint=payload.severityHint,
        request_id=request_id,
        page_age_ms=payload.pageAgeMs,
        document_ready_state=payload.documentReadyState,
        document_visibility_state=payload.documentVisibilityState,
        online=payload.online,
        save_data=payload.saveData,
        hardware_concurrency=payload.hardwareConcurrency,
        device_memory=payload.deviceMemory,
        js_heap_used_mb=payload.jsHeapUsedMb,
        js_heap_total_mb=payload.jsHeapTotalMb,
        js_heap_limit_mb=payload.jsHeapLimitMb,
        sw_controller=payload.swController,
        referrer=referrer_redacted,
        triage_severity=severity,
        triage_status="new",
        triage_due_at=datetime.now(timezone.utc) + timedelta(hours=24),
        reported_at=reported_at,
    )

    is_new = await _upsert_error_report(fingerprint=fingerprint, fields=fields)

    if is_new and severity in NOTIFY_SEVERITIES:
        # Fire-and-forget so the client never blocks on Telegram/admin chat.
        try:
            asyncio.create_task(
                _notify_admin_new_high_severity(
                    fingerprint=fingerprint,
                    severity=severity,
                    type_=payload.type,
                    message=redacted_message,
                    route=route,
                    app_version=payload.appVersion,
                )
            )
        except Exception:
            pass

    logger.info(
        "Error report received",
        extra={
            "code": payload.code,
            "type": payload.type,
            "user_id": user_id,
            "severity": severity,
            "fingerprint": fingerprint[:12],
            "new": is_new,
            "app_version": payload.appVersion,
        },
    )
    return {
        "ok": True,
        "fingerprint": fingerprint,
        "severity": severity,
        "deduped": not is_new,
    }
