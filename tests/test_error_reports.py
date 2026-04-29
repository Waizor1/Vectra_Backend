from __future__ import annotations

import types

import pytest
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
