from __future__ import annotations

import types

import pytest
from fastapi import HTTPException
from starlette.requests import Request


async def _request() -> Request:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/errors/report", "headers": []}, receive)


@pytest.mark.asyncio
async def test_report_error_redacts_sensitive_url_stack_and_extra(monkeypatch):
    from bloobcat.routes import error_reports

    captured: dict[str, object] = {}

    class FakeErrorReports:
        @classmethod
        async def create(cls, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(**kwargs)

    monkeypatch.setattr(error_reports, "ErrorReports", FakeErrorReports)

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

    assert await error_reports.report_error(payload, await _request()) == {"ok": True}
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

    captured: dict[str, object] = {}

    class FakeErrorReports:
        @classmethod
        async def create(cls, **kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(**kwargs)

    monkeypatch.setattr(error_reports, "ErrorReports", FakeErrorReports)

    payload = error_reports.ErrorReportPayload(
        eventId="event-bounded-extra",
        code="HAPP-FE_RUNTIME-event-bounded-extra",
        type="FE_RUNTIME",
        createdAtMs=1_777_423_000_000,
        extra={"level1": {"level2": {"level3": {"level4": {"level5": "secret-token"}}}}},
    )

    assert await error_reports.report_error(payload, await _request()) == {"ok": True}

    assert captured["extra"] == {
        "level1": {"level2": {"level3": {"level4": "[truncated]"}}}
    }
