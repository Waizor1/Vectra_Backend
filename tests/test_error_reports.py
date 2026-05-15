from __future__ import annotations

import asyncio
import types

import pytest
from fastapi import HTTPException
from starlette.requests import Request


async def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/errors/report",
        "headers": headers or [],
    }
    return Request(scope, receive)


class _Existing:
    """Minimal stand-in for an ORM row used by the UPSERT path."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.saved = False

    async def save(self):
        self.saved = True


def _install_fake_repo(monkeypatch, *, existing=None, capture=None):
    captured = capture if capture is not None else {}
    last_query = {"fingerprint": None}
    saved_after = {"row": None}

    class FakeQS:
        def __init__(self, fingerprint):
            last_query["fingerprint"] = fingerprint
            self._fingerprint = fingerprint

        async def first(self):
            if existing is not None and existing.fingerprint == self._fingerprint:
                return existing
            return None

    class FakeErrorReports:
        @classmethod
        def filter(cls, **kwargs):
            return FakeQS(kwargs.get("fingerprint"))

        @classmethod
        async def create(cls, **kwargs):
            captured.update(kwargs)
            row = types.SimpleNamespace(**kwargs)
            saved_after["row"] = row
            return row

    from bloobcat.routes import error_reports

    monkeypatch.setattr(error_reports, "ErrorReports", FakeErrorReports)
    return captured, last_query, saved_after


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_compute_fingerprint_is_stable_across_build_hashes_and_timestamps():
    from bloobcat.routes.error_reports import compute_fingerprint

    fp_a = compute_fingerprint(
        type_="FE_RUNTIME",
        name="TypeError",
        message="Cannot read properties of undefined (reading 'foo')",
        stack=(
            "TypeError: ...\n"
            "  at i (https://app.vectra-pro.net/assets/externalLinks-Dtb5QFSG.js:1:3596)\n"
            "  at u (https://app.vectra-pro.net/assets/FullAppEntry-CsNQ7g2c.js:1:9001)"
        ),
    )
    fp_b = compute_fingerprint(
        type_="FE_RUNTIME",
        name="TypeError",
        message="Cannot read properties of undefined (reading 'foo')",
        # different bundle hashes + different line numbers — same identity
        stack=(
            "TypeError: ...\n"
            "  at i (https://app.vectra-pro.net/assets/externalLinks-AAAAAAAA.js:1:1234)\n"
            "  at u (https://app.vectra-pro.net/assets/FullAppEntry-BBBBBBBB.js:1:5678)"
        ),
    )
    assert fp_a == fp_b
    # but a different message must produce a different fingerprint
    fp_c = compute_fingerprint(
        type_="FE_RUNTIME",
        name="TypeError",
        message="Failed to fetch",
        stack=None,
    )
    assert fp_c != fp_a


def test_resolve_severity_validates_hint_and_falls_back_to_type_map():
    from bloobcat.routes.error_reports import resolve_severity

    assert resolve_severity("FE_CHUNK_LOAD", None) == "high"
    assert resolve_severity("NET_OFFLINE", None) == "low"
    assert resolve_severity("FE_RUNTIME", None) == "medium"
    assert resolve_severity("UNKNOWN_TYPE", None) == "medium"
    # explicit hint wins when allowed
    assert resolve_severity("FE_RUNTIME", "critical") == "critical"
    # invalid hint is ignored
    assert resolve_severity("FE_RUNTIME", "panic") == "medium"
    # hint normalization
    assert resolve_severity("FE_RUNTIME", "  HIGH  ") == "high"


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_error_redacts_sensitive_url_stack_and_extra(monkeypatch):
    from bloobcat.routes import error_reports

    captured, _, _ = _install_fake_repo(monkeypatch)

    payload = error_reports.ErrorReportPayload(
        eventId="event-1",
        code="HAPP-FE_RUNTIME-event-1",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        message="failed with reset-secret",
        stack="Error: ticket-secret",
        route="/auth/callback?ticket=ticket-secret&safe=ok#token=reset-secret",
        href="https://app.vectra-pro.net/auth/callback?ticket=ticket-secret#token=reset-secret",
        extra={"payment_id": "pay-secret", "safe": "ok"},
    )

    result = await error_reports.report_error(payload, await _request())
    assert result["ok"] is True
    serialized = repr(captured)
    assert "ticket-secret" not in serialized
    assert "reset-secret" not in serialized
    assert "pay-secret" not in serialized
    assert captured["route"] == "/auth/callback?ticket=[redacted]&safe=ok#token=[redacted]"
    assert captured["extra"] == {"payment_id": "[redacted]", "safe": "ok"}


@pytest.mark.asyncio
async def test_report_error_rejects_oversized_payload_before_db_write(monkeypatch):
    from bloobcat.routes import error_reports

    class FakeErrorReports:
        @classmethod
        def filter(cls, **_kwargs):
            class _Empty:
                async def first(self_inner):
                    return None
            return _Empty()

        @classmethod
        async def create(cls, **_kwargs):
            raise AssertionError("oversized reports must not be persisted")

    monkeypatch.setattr(error_reports, "ErrorReports", FakeErrorReports)

    payload = error_reports.ErrorReportPayload(
        eventId="event-oversized",
        code="HAPP-FE_RUNTIME-event-oversized",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        message="x" * (error_reports.ERROR_REPORT_MAX_TEXT_LENGTH + 1),
    )

    with pytest.raises(HTTPException) as exc:
        await error_reports.report_error(payload, await _request())

    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_report_error_bounds_nested_extra_before_persisting(monkeypatch):
    from bloobcat.routes import error_reports

    captured, _, _ = _install_fake_repo(monkeypatch)

    payload = error_reports.ErrorReportPayload(
        eventId="event-bounded-extra",
        code="HAPP-FE_RUNTIME-event-bounded-extra",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        extra={"level1": {"level2": {"level3": {"level4": {"level5": "secret-token"}}}}},
    )

    result = await error_reports.report_error(payload, await _request())
    assert result["ok"] is True

    assert captured["extra"] == {
        "level1": {"level2": {"level3": {"level4": "[truncated]"}}}
    }


@pytest.mark.asyncio
async def test_report_error_drops_telegram_game_proxy_noise(monkeypatch):
    from bloobcat.routes import error_reports

    class _Boom:
        @classmethod
        def filter(cls, **_kwargs):
            raise AssertionError("noise must not hit the DB")

        @classmethod
        async def create(cls, **_kwargs):
            raise AssertionError("noise must not hit the DB")

    monkeypatch.setattr(error_reports, "ErrorReports", _Boom)

    payload = error_reports.ErrorReportPayload(
        eventId="ev-noise-1",
        code="HAPP-FE_RUNTIME-ev-noise-1",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        name="TypeError",
        message=(
            "TypeError: undefined is not an object "
            "(evaluating 'window.TelegramGameProxy.receiveEvent')"
        ),
    )

    result = await error_reports.report_error(payload, await _request())
    assert result == {"ok": True, "dropped": "noise"}


@pytest.mark.asyncio
async def test_report_error_drops_bare_script_error_noise(monkeypatch):
    from bloobcat.routes import error_reports

    class _Boom:
        @classmethod
        def filter(cls, **_kwargs):
            raise AssertionError("noise must not hit the DB")

        @classmethod
        async def create(cls, **_kwargs):
            raise AssertionError("noise must not hit the DB")

    monkeypatch.setattr(error_reports, "ErrorReports", _Boom)

    payload = error_reports.ErrorReportPayload(
        eventId="ev-noise-2",
        code="HAPP-FE_RUNTIME-ev-noise-2",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        message="Script error.",
    )

    result = await error_reports.report_error(payload, await _request())
    assert result == {"ok": True, "dropped": "noise"}


@pytest.mark.asyncio
async def test_report_error_persists_new_observability_fields(monkeypatch):
    from bloobcat.routes import error_reports

    captured, _, _ = _install_fake_repo(monkeypatch)

    payload = error_reports.ErrorReportPayload(
        eventId="ev-rich",
        code="HAPP-FE_RENDER-ev-rich",
        type="FE_RENDER",
        createdAtMs=1_777_423_000_000,
        message="Render crashed",
        appVersion="0.234.0",
        commitSha="abc1234",
        bundleHash="CsNQ7g2c",
        sessionId="sess-abc",
        platform="telegram_miniapp",
        tgPlatform="ios",
        tgVersion="9.4",
        viewportW=390,
        viewportH=844,
        dpr=3.0,
        connectionType="4g",
        locale="ru-RU",
        breadcrumbs=[
            {"kind": "navigate", "to": "/subscription", "ts": 1_777_422_999_000},
            {"kind": "click", "selector": "button.buy", "ts": 1_777_422_999_500},
        ],
        severityHint="critical",
    )

    result = await error_reports.report_error(payload, await _request())
    assert result["ok"] is True
    assert result["severity"] == "critical"
    assert result["deduped"] is False
    assert captured["app_version"] == "0.234.0"
    assert captured["commit_sha"] == "abc1234"
    assert captured["session_id"] == "sess-abc"
    assert captured["platform"] == "telegram_miniapp"
    assert captured["tg_platform"] == "ios"
    assert captured["viewport_w"] == 390
    assert captured["dpr"] == 3.0
    assert captured["connection_type"] == "4g"
    assert captured["locale"] == "ru-RU"
    assert captured["triage_severity"] == "critical"
    assert captured["fingerprint"]
    assert isinstance(captured["breadcrumbs"], list) and len(captured["breadcrumbs"]) == 2


@pytest.mark.asyncio
async def test_report_error_picks_request_id_from_header(monkeypatch):
    from bloobcat.routes import error_reports

    captured, _, _ = _install_fake_repo(monkeypatch)

    payload = error_reports.ErrorReportPayload(
        eventId="ev-rid",
        code="c",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        message="boom",
    )

    headers = [(b"x-request-id", b"req-12345")]
    await error_reports.report_error(payload, await _request(headers))
    assert captured["request_id"] == "req-12345"


@pytest.mark.asyncio
async def test_report_error_upserts_existing_fingerprint_and_skips_notify(monkeypatch):
    from bloobcat.routes import error_reports

    fp = error_reports.compute_fingerprint(
        type_="FE_CHUNK_LOAD",
        name="ChunkLoadError",
        message="Loading chunk 42 failed.",
        stack=None,
    )
    existing = _Existing(
        fingerprint=fp,
        occurrences=4,
        last_seen_at=None,
    )
    captured = {}
    captured_existing = {"row": existing}

    class FakeQS:
        def __init__(self, fingerprint):
            self._fp = fingerprint

        async def first(self_inner):
            if self_inner._fp == fp:
                return existing
            return None

    class FakeErrorReports:
        @classmethod
        def filter(cls, **kwargs):
            return FakeQS(kwargs.get("fingerprint"))

        @classmethod
        async def create(cls, **kwargs):
            captured.update(kwargs)
            raise AssertionError("create() must not be called when fingerprint exists")

    monkeypatch.setattr(error_reports, "ErrorReports", FakeErrorReports)
    notified = {"called": False}

    async def fake_notify(**_):
        notified["called"] = True

    monkeypatch.setattr(error_reports, "_notify_admin_new_high_severity", fake_notify)

    payload = error_reports.ErrorReportPayload(
        eventId="ev-dup",
        code="c",
        type="FE_CHUNK_LOAD",
        createdAtMs=1_777_423_500_000,
        name="ChunkLoadError",
        message="Loading chunk 42 failed.",
        appVersion="0.234.0",
    )

    result = await error_reports.report_error(payload, await _request())
    assert result["deduped"] is True
    assert existing.occurrences == 5
    assert existing.saved is True
    # message refresh (in case it slightly differs across observations)
    assert existing.message == "Loading chunk 42 failed."
    assert existing.app_version == "0.234.0"
    # do NOT re-notify on duplicates
    assert notified["called"] is False
    # ensure captured (would-be create) stayed empty
    assert captured == {}
    # silence unused warning for captured_existing
    assert captured_existing["row"] is existing


@pytest.mark.asyncio
async def test_report_error_notifies_admin_on_first_high_severity(monkeypatch):
    from bloobcat.routes import error_reports

    _install_fake_repo(monkeypatch)

    # Capture the create_task target instead of actually scheduling it.
    awaited = {"called_with": None}

    async def fake_notify(**kwargs):
        awaited["called_with"] = kwargs

    monkeypatch.setattr(error_reports, "_notify_admin_new_high_severity", fake_notify)

    real_create_task = asyncio.create_task

    def sync_create_task(coro):
        # Run inline so the test can assert observable behaviour deterministically
        # without dealing with fire-and-forget scheduling.
        loop = asyncio.get_event_loop()
        return real_create_task(coro)

    monkeypatch.setattr(error_reports.asyncio, "create_task", sync_create_task)

    payload = error_reports.ErrorReportPayload(
        eventId="ev-high",
        code="c",
        type="FE_CHUNK_LOAD",
        createdAtMs=1_777_423_000_000,
        message="Loading chunk 7 failed.",
        appVersion="0.234.0",
    )

    result = await error_reports.report_error(payload, await _request())
    # let the scheduled task run
    await asyncio.sleep(0)
    assert result["severity"] == "high"
    assert awaited["called_with"] is not None
    assert awaited["called_with"]["severity"] == "high"
    assert awaited["called_with"]["app_version"] == "0.234.0"


@pytest.mark.asyncio
async def test_report_error_persists_runtime_context_fields(monkeypatch):
    from bloobcat.routes import error_reports

    captured, _, _ = _install_fake_repo(monkeypatch)

    payload = error_reports.ErrorReportPayload(
        eventId="ev-rt",
        code="c",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        message="boom",
        pageAgeMs=12345,
        documentReadyState="complete",
        documentVisibilityState="visible",
        online=True,
        saveData=False,
        hardwareConcurrency=8,
        deviceMemory=4.0,
        jsHeapUsedMb=120.5,
        jsHeapTotalMb=200.0,
        jsHeapLimitMb=2048.0,
        swController="https://app.vectra-pro.net/sw.js",
        referrer="https://t.me/vectra_pro_bot/app?startapp=secret-leak",
    )

    await error_reports.report_error(payload, await _request())
    assert captured["page_age_ms"] == 12345
    assert captured["document_ready_state"] == "complete"
    assert captured["document_visibility_state"] == "visible"
    assert captured["online"] is True
    assert captured["save_data"] is False
    assert captured["hardware_concurrency"] == 8
    assert captured["device_memory"] == 4.0
    assert captured["js_heap_used_mb"] == 120.5
    assert captured["sw_controller"] == "https://app.vectra-pro.net/sw.js"
    # referrer must have its sensitive `startapp` query param redacted
    assert "secret-leak" not in (captured["referrer"] or "")
    assert "[redacted]" in (captured["referrer"] or "")


@pytest.mark.asyncio
async def test_report_error_redacts_breadcrumb_sensitive_keys(monkeypatch):
    from bloobcat.routes import error_reports

    captured, _, _ = _install_fake_repo(monkeypatch)

    payload = error_reports.ErrorReportPayload(
        eventId="ev-bc",
        code="c",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        message="boom",
        breadcrumbs=[
            {"kind": "navigate", "to": "/oauth?token=secret-abc"},
            {"kind": "fetch", "url": "/api/foo", "auth": "Bearer abc"},
        ],
    )

    await error_reports.report_error(payload, await _request())
    bc = captured["breadcrumbs"]
    assert bc[1]["auth"] == "[redacted]"
    # url-shaped values are not auto-sanitized inside breadcrumbs (string field)
    # but secret-like patterns (`secret-...`, `token-...`) are redacted by regex.
    assert "secret-abc" not in repr(bc)


# ---------------------------------------------------------------------------
# Authorization → user_id resolution (B-1 strict auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_user_id_optional_returns_none_without_header():
    from bloobcat.routes import error_reports

    assert await error_reports._resolve_user_id_optional(await _request()) is None


@pytest.mark.asyncio
async def test_resolve_user_id_optional_rejects_numeric_authorization():
    """Regression: numeric Authorization was previously accepted as raw user_id."""
    from bloobcat.routes import error_reports

    req = await _request([(b"authorization", b"12345")])
    assert await error_reports._resolve_user_id_optional(req) is None


@pytest.mark.asyncio
async def test_resolve_user_id_optional_rejects_garbage_authorization():
    from bloobcat.routes import error_reports

    req = await _request([(b"authorization", b"definitely-not-init-data")])
    assert await error_reports._resolve_user_id_optional(req) is None


@pytest.mark.asyncio
async def test_resolve_user_id_optional_accepts_valid_bearer_without_ver(monkeypatch):
    from bloobcat.routes import error_reports

    monkeypatch.setattr(
        error_reports,
        "decode_access_token",
        lambda token: {"sub": "42"},
    )
    req = await _request([(b"authorization", b"Bearer valid-jwt")])
    assert await error_reports._resolve_user_id_optional(req) == 42


@pytest.mark.asyncio
async def test_resolve_user_id_optional_rejects_expired_or_invalid_bearer(monkeypatch):
    from bloobcat.routes import error_reports

    def _raise(_token):
        raise ValueError("invalid token")

    monkeypatch.setattr(error_reports, "decode_access_token", _raise)
    req = await _request([(b"authorization", b"Bearer broken-jwt")])
    assert await error_reports._resolve_user_id_optional(req) is None


@pytest.mark.asyncio
async def test_resolve_user_id_optional_rejects_empty_bearer():
    from bloobcat.routes import error_reports

    req = await _request([(b"authorization", b"Bearer ")])
    assert await error_reports._resolve_user_id_optional(req) is None


@pytest.mark.asyncio
async def test_resolve_user_id_optional_rejects_bearer_with_bad_subject(monkeypatch):
    from bloobcat.routes import error_reports

    monkeypatch.setattr(
        error_reports,
        "decode_access_token",
        lambda token: {"sub": "not-an-int"},
    )
    req = await _request([(b"authorization", b"Bearer x")])
    assert await error_reports._resolve_user_id_optional(req) is None


@pytest.mark.asyncio
async def test_resolve_user_id_optional_checks_token_version(monkeypatch):
    from bloobcat.routes import error_reports

    monkeypatch.setattr(
        error_reports,
        "decode_access_token",
        lambda token: {"sub": "7", "ver": 2},
    )

    class FakeUsers:
        @classmethod
        async def get_or_none(cls, id):
            return types.SimpleNamespace(id=id, auth_token_version=5)

    monkeypatch.setattr(error_reports, "Users", FakeUsers)
    req = await _request([(b"authorization", b"Bearer stale-jwt")])
    # ver mismatch → user_id is rejected
    assert await error_reports._resolve_user_id_optional(req) is None


@pytest.mark.asyncio
async def test_report_error_persists_user_id_none_for_numeric_auth(monkeypatch):
    """End-to-end: /errors/report no longer trusts numeric Authorization."""
    from bloobcat.routes import error_reports

    captured, _, _ = _install_fake_repo(monkeypatch)

    payload = error_reports.ErrorReportPayload(
        eventId="ev-numeric-auth",
        code="c",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        message="boom",
    )

    result = await error_reports.report_error(
        payload, await _request([(b"authorization", b"99999999")])
    )
    assert result["ok"] is True
    assert captured["user_id"] is None


# --- _truncate_field tests ---------------------------------------------------
# Bug observed in production 2026-05-15: FE sent /errors/report with
# tgWebApp `route` field of 736 chars, breaking the 512-cap CharField with
# tortoise ValidationError 500. Truncation in the route handler keeps the
# pipeline open instead of dropping reports on the floor.


def test_truncate_field_passes_through_short_values():
    from bloobcat.routes.error_reports import _truncate_field

    assert _truncate_field("short", "route") == "short"
    assert _truncate_field("/welcome", "route") == "/welcome"


def test_truncate_field_passes_through_none():
    from bloobcat.routes.error_reports import _truncate_field

    assert _truncate_field(None, "route") is None
    assert _truncate_field(None, "message") is None


def test_truncate_field_clips_route_at_512_with_marker():
    from bloobcat.routes.error_reports import _truncate_field

    # Reproduces the production crash: a 736-char tgWebApp URL.
    long_url = "/#tgWebAppData=" + "x" * 720
    assert len(long_url) > 512

    result = _truncate_field(long_url, "route")
    assert result is not None
    assert len(result) == 512
    assert result.endswith("…[truncated]")


def test_truncate_field_clips_href_at_1024():
    from bloobcat.routes.error_reports import _truncate_field

    big = "https://example.com/?" + "q=" * 800
    assert len(big) > 1024
    result = _truncate_field(big, "href")
    assert result is not None
    assert len(result) == 1024
    assert result.endswith("…[truncated]")


def test_truncate_field_unknown_field_passes_through():
    from bloobcat.routes.error_reports import _truncate_field

    # Defensive: unmapped field name = no clipping (pretend the schema is wider).
    long = "x" * 5000
    assert _truncate_field(long, "not_in_map") == long


def test_truncate_field_exact_max_length_unchanged():
    from bloobcat.routes.error_reports import _truncate_field

    exact = "x" * 512
    result = _truncate_field(exact, "route")
    assert result == exact
    assert len(result) == 512  # No suffix appended for exact-length values.


def test_truncate_field_marker_fits_within_max_length():
    """Suffix length is subtracted from the head so the result is always
    <= max_length. Off-by-one in suffix math would silently re-trigger the
    ValidationError this PR is fixing."""
    from bloobcat.routes.error_reports import _truncate_field, _TRUNCATION_SUFFIX

    long = "x" * 2000
    for field, expected_max in [("route", 512), ("href", 1024), ("user_agent", 512)]:
        result = _truncate_field(long, field)
        assert result is not None
        assert len(result) <= expected_max, f"{field} overflowed: {len(result)}"
        assert result.endswith(_TRUNCATION_SUFFIX)
