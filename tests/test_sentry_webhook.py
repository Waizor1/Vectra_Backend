"""Tests for /admin/sentry-webhook endpoint and payload parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bloobcat.routes.sentry_webhook import (
    _format_telegram_message,
    _summarize_payload,
    router,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


# ── Sample Sentry payloads ──────────────────────────────────────────────────
# Trimmed real-world body shapes posted by Sentry's "Send a notification via
# webhook" action on an issue alert. Fields the renderer reads are
# represented; unrelated fields are omitted.

ISSUE_ALERT_PAYLOAD = {
    "id": "9999",
    "project": "vectra-backend",
    "project_name": "vectra-backend",
    "project_slug": "vectra-backend",
    "logger": "bloobcat.funcs.validate",
    "level": "error",
    "culprit": "validate:validate (bloobcat.funcs.validate)",
    "message": "Отсутствует заголовок Authorization",
    "url": "https://vectra-pro.sentry.io/issues/9999/",
    "triggering_rules": ["Send a notification for high priority issues"],
    "event_count": 42,
    "user_count": 7,
    "event": {
        "event_id": "abc123",
        "level": "error",
        "environment": "production",
        "release": "1.104.0",
        "title": "Отсутствует заголовок Authorization",
        "culprit": "validate:validate (bloobcat.funcs.validate)",
        "web_url": "https://vectra-pro.sentry.io/issues/9999/events/abc123/",
    },
}


WARNING_PAYLOAD = {
    "id": "1234",
    "project": "vectra-backend",
    "level": "warning",
    "message": "Slow query in subscription_resume",
    "url": "https://vectra-pro.sentry.io/issues/1234/",
    "triggering_rules": "fallback string rule",
    "event": {"environment": "production"},
}


MINIMAL_PAYLOAD: dict = {}


# ── _summarize_payload ──────────────────────────────────────────────────────


def test_summarize_full_payload():
    summary = _summarize_payload(ISSUE_ALERT_PAYLOAD)
    assert summary.short_id == "9999"
    assert summary.level == "error"
    assert summary.title == "Отсутствует заголовок Authorization"
    assert "validate" in summary.culprit
    assert summary.environment == "production"
    assert summary.release == "1.104.0"
    assert summary.project == "vectra-backend"
    assert summary.rule == "Send a notification for high priority issues"
    assert summary.event_count == 42
    assert summary.user_count == 7
    assert summary.permalink.startswith("https://")


def test_summarize_minimal_payload_does_not_crash():
    summary = _summarize_payload(MINIMAL_PAYLOAD)
    assert summary.short_id == "?"
    assert summary.level == "error"  # fallback default
    assert summary.title == "(no title)"
    assert summary.environment == "—"
    assert summary.release == "—"


def test_summarize_scalar_triggering_rule():
    summary = _summarize_payload(WARNING_PAYLOAD)
    assert summary.rule == "fallback string rule"
    assert summary.level == "warning"


# ── _format_telegram_message ────────────────────────────────────────────────


def test_format_includes_level_emoji():
    summary = _summarize_payload(ISSUE_ALERT_PAYLOAD)
    msg = _format_telegram_message(summary)
    assert "❌" in msg  # error level
    assert "ERROR" in msg
    assert "vectra-backend" in msg
    assert "production" in msg
    assert "1.104.0" in msg
    assert "#sentry" in msg


def test_format_escapes_html_in_title():
    payload = dict(ISSUE_ALERT_PAYLOAD)
    payload = {**payload, "message": "<script>alert(1)</script>", "event": {**payload["event"], "title": "<script>alert(1)</script>"}}
    summary = _summarize_payload(payload)
    msg = _format_telegram_message(summary)
    assert "<script>" not in msg
    assert "&lt;script&gt;" in msg


def test_format_warning_uses_warning_emoji():
    summary = _summarize_payload(WARNING_PAYLOAD)
    msg = _format_telegram_message(summary)
    assert "⚠️" in msg


def test_format_omits_empty_counts():
    summary = _summarize_payload(WARNING_PAYLOAD)  # no event_count / user_count
    msg = _format_telegram_message(summary)
    assert "events:" not in msg


# ── Endpoint: auth ──────────────────────────────────────────────────────────


def test_webhook_503_when_secret_not_configured():
    from bloobcat import settings as settings_module

    app = _make_app()
    with patch.object(
        settings_module.observability_settings, "sentry_webhook_secret", None
    ):
        client = TestClient(app)
        resp = client.post("/admin/sentry-webhook/whatever", json={})
    assert resp.status_code == 503


def test_webhook_401_with_wrong_secret():
    from bloobcat import settings as settings_module

    app = _make_app()
    with patch.object(
        settings_module.observability_settings,
        "sentry_webhook_secret",
        SecretStr("correct-secret-value"),
    ):
        client = TestClient(app)
        resp = client.post("/admin/sentry-webhook/wrong-secret", json={})
    assert resp.status_code == 401


def test_webhook_200_with_correct_secret_and_sends_telegram_message():
    from bloobcat import settings as settings_module

    app = _make_app()
    with patch.object(
        settings_module.observability_settings,
        "sentry_webhook_secret",
        SecretStr("the-right-secret"),
    ), patch.object(
        settings_module.observability_settings,
        "sentry_telegram_chat_id",
        None,
    ):
        mock_send = AsyncMock(return_value=True)
        with patch(
            "bloobcat.bot.notifications.admin.send_admin_message", mock_send
        ):
            client = TestClient(app)
            resp = client.post(
                "/admin/sentry-webhook/the-right-secret",
                json=ISSUE_ALERT_PAYLOAD,
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["short_id"] == "9999"
    assert mock_send.await_count == 1
    sent_text = mock_send.call_args.args[0]
    assert "vectra-backend" in sent_text
    assert "Отсутствует заголовок Authorization" in sent_text
    assert "1.104.0" in sent_text
    # Default behaviour: no override → send_admin_message receives chat_id=None
    # and falls back to the shared admin logs channel.
    assert mock_send.call_args.kwargs.get("chat_id") is None


def test_webhook_routes_to_dedicated_sentry_chat_when_configured():
    """When OBSERVABILITY_SENTRY_TELEGRAM_CHAT_ID is set, alert goes there
    instead of the shared admin logs channel."""
    from bloobcat import settings as settings_module

    app = _make_app()
    with patch.object(
        settings_module.observability_settings,
        "sentry_webhook_secret",
        SecretStr("s"),
    ), patch.object(
        settings_module.observability_settings,
        "sentry_telegram_chat_id",
        -1009999999999,  # representative supergroup id
    ):
        mock_send = AsyncMock(return_value=True)
        with patch(
            "bloobcat.bot.notifications.admin.send_admin_message", mock_send
        ):
            client = TestClient(app)
            resp = client.post(
                "/admin/sentry-webhook/s", json=ISSUE_ALERT_PAYLOAD
            )

    assert resp.status_code == 200
    assert mock_send.call_args.kwargs.get("chat_id") == -1009999999999


# ── Endpoint: tolerance to malformed input ──────────────────────────────────


def test_webhook_ignores_malformed_json_without_500():
    from bloobcat import settings as settings_module

    app = _make_app()
    with patch.object(
        settings_module.observability_settings,
        "sentry_webhook_secret",
        SecretStr("secret"),
    ):
        client = TestClient(app)
        resp = client.post(
            "/admin/sentry-webhook/secret",
            data="not-valid-json",
            headers={"Content-Type": "application/json"},
        )
    # Sentry must not see 5xx — endpoint absorbs the malformed body.
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_webhook_ignores_non_dict_payload():
    from bloobcat import settings as settings_module

    app = _make_app()
    with patch.object(
        settings_module.observability_settings,
        "sentry_webhook_secret",
        SecretStr("secret"),
    ):
        client = TestClient(app)
        resp = client.post(
            "/admin/sentry-webhook/secret",
            json=["this", "is", "a", "list"],
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_webhook_returns_ok_even_when_telegram_send_raises():
    from bloobcat import settings as settings_module

    app = _make_app()
    with patch.object(
        settings_module.observability_settings,
        "sentry_webhook_secret",
        SecretStr("secret"),
    ):
        with patch(
            "bloobcat.bot.notifications.admin.send_admin_message",
            AsyncMock(side_effect=RuntimeError("telegram is down")),
        ):
            client = TestClient(app)
            resp = client.post(
                "/admin/sentry-webhook/secret",
                json=ISSUE_ALERT_PAYLOAD,
            )

    # Sentry must never see a 500 — it would retry the webhook indefinitely
    # and create a notification cascade.
    assert resp.status_code == 200
    assert resp.json()["status"] == "delivery_error"


def test_webhook_returns_ok_when_telegram_send_returns_false():
    from bloobcat import settings as settings_module

    app = _make_app()
    with patch.object(
        settings_module.observability_settings,
        "sentry_webhook_secret",
        SecretStr("secret"),
    ):
        with patch(
            "bloobcat.bot.notifications.admin.send_admin_message",
            AsyncMock(return_value=False),
        ):
            client = TestClient(app)
            resp = client.post(
                "/admin/sentry-webhook/secret",
                json=ISSUE_ALERT_PAYLOAD,
            )

    assert resp.status_code == 200
    assert resp.json()["status"] == "delivery_unconfirmed"


# ── Endpoint mounted under main app: prefix sanity ──────────────────────────


def test_router_prefix_is_admin():
    routes = [r.path for r in router.routes]
    assert any("/admin/sentry-webhook" in p for p in routes)
