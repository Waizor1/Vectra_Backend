"""Sentry → Telegram admin-log webhook bridge.

Sentry's free plan does not include native Telegram integration, so we expose
a small webhook endpoint that the platform's built-in "Send a notification via
webhook" alert action can post to. The endpoint:

  1. Authenticates by comparing the URL path segment against a shared secret
     (`OBSERVABILITY_SENTRY_WEBHOOK_SECRET`). Constant-time comparison via
     `hmac.compare_digest` to avoid timing leaks. Secret lives only in the
     URL path because Sentry's UI does not let us add custom request headers
     to webhook actions.
  2. Parses the payload (Sentry's standard issue-alert format) into a small
     summary: short id, level, environment, release, title, culprit,
     triggered rule, permalink.
  3. Formats a compact HTML message and ships it to the existing admin/log
     Telegram channel via `send_admin_message()`.
  4. Always returns 200 so Sentry does not retry forever — Telegram delivery
     failures are logged but never surface as webhook errors.

The endpoint is mounted under the existing `/admin` prefix and is naturally
excluded from rate_limit_middleware (which uses positive path matching).

Configuration in Sentry UI:
  Settings → Alerts → existing rule → Add Action →
  "Send a notification via webhook" → URL:
    https://api-app.vectra-pro.net/admin/sentry-webhook/<SECRET>
"""

from __future__ import annotations

import hmac
import html
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from bloobcat.logger import get_logger
from bloobcat.settings import observability_settings

logger = get_logger("routes.sentry_webhook")

router = APIRouter(prefix="/admin", tags=["sentry-webhook"])


# Sentry payload structure documented at
# https://docs.sentry.io/product/integrations/integration-platform/webhooks/
# The legacy issue-alert webhook (which is what the `Send a notification via
# webhook` action posts) lives at the top level of the body. We model only
# the fields we surface and use `extra='ignore'` semantics implicitly by
# pulling fields with `.get()` against a raw dict instead of strict parsing —
# Sentry adds/removes fields between releases, and a strict parser would
# break delivery on every schema bump.
class SentryEventSummary(BaseModel):
    """Tiny normalized projection we render into the Telegram message."""

    short_id: str
    level: str
    title: str
    culprit: str
    environment: str
    release: str
    project: str
    rule: str
    permalink: str
    event_count: int
    user_count: int


_LEVEL_EMOJI = {
    "fatal": "🚨",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    "debug": "🐞",
}


def _summarize_payload(payload: dict[str, Any]) -> SentryEventSummary:
    """Pull the fields we need out of a raw Sentry webhook body.

    Tolerant by design: every field has a fallback because Sentry's free
    plan changes the webhook shape periodically and we'd rather render a
    half-empty alert than drop it entirely.
    """

    event = payload.get("event") or {}
    project_name = (
        payload.get("project_name")
        or payload.get("project")
        or event.get("project")
        or "unknown"
    )

    # `triggering_rules` may be a list (issue-alert) or scalar (metric-alert).
    rules = payload.get("triggering_rules") or []
    if isinstance(rules, str):
        rule = rules
    elif rules:
        rule = ", ".join(str(r) for r in rules)
    else:
        rule = "—"

    return SentryEventSummary(
        short_id=str(
            event.get("issue_id") or payload.get("id") or payload.get("issue_id") or "?"
        ),
        level=str(event.get("level") or payload.get("level") or "error").lower(),
        title=str(
            event.get("title")
            or payload.get("message")
            or payload.get("title")
            or "(no title)"
        ),
        culprit=str(event.get("culprit") or payload.get("culprit") or ""),
        environment=str(event.get("environment") or "—"),
        release=str(event.get("release") or "—"),
        project=str(project_name),
        rule=rule,
        permalink=str(
            payload.get("url")
            or payload.get("web_url")
            or event.get("web_url")
            or ""
        ),
        event_count=int(payload.get("event_count") or 0),
        user_count=int(payload.get("user_count") or 0),
    )


def _format_telegram_message(summary: SentryEventSummary) -> str:
    """Render a compact Telegram-friendly HTML alert."""

    emoji = _LEVEL_EMOJI.get(summary.level, "⚡")
    lines = [
        f"{emoji} <b>Sentry · {html.escape(summary.level.upper())}</b>",
        f"<code>{html.escape(summary.project)}</code> · "
        f"<code>{html.escape(summary.environment)}</code> · "
        f"<code>{html.escape(summary.release)}</code>",
        "",
        f"<b>{html.escape(summary.title)}</b>",
    ]
    if summary.culprit:
        lines.append(f"<i>{html.escape(summary.culprit)}</i>")
    if summary.event_count or summary.user_count:
        lines.append(
            f"events: {summary.event_count} · users: {summary.user_count}"
        )
    if summary.rule and summary.rule != "—":
        lines.append(f"rule: <i>{html.escape(summary.rule)}</i>")
    if summary.permalink:
        lines.append(f'<a href="{html.escape(summary.permalink)}">Open in Sentry →</a>')
    lines.append("#sentry")
    return "\n".join(lines)


@router.post("/sentry-webhook/{secret}", include_in_schema=False)
async def receive_sentry_webhook(secret: str, request: Request):
    """Receive Sentry alert webhook and forward to the admin Telegram channel.

    Always returns 200 OK on a valid secret — Telegram delivery is best-effort
    so Sentry does not back off and retry. Invalid secret returns 401;
    missing configuration returns 503.
    """

    configured = observability_settings.sentry_webhook_secret
    if configured is None:
        # Endpoint deliberately disabled — surface to the operator instead of
        # silently 200ing so a misconfigured Sentry alert is visible.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sentry webhook not configured",
        )

    expected = configured.get_secret_value()
    if not hmac.compare_digest(str(secret), str(expected)):
        # Don't reveal whether the secret length matched — a single 401 for
        # any mismatch is safer.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("Sentry webhook: malformed JSON body: {}", exc)
        # Still ack the webhook — Sentry's retry behaviour is not worth
        # the noise for a one-off malformed body.
        return {"status": "ignored", "reason": "malformed_json"}

    if not isinstance(payload, dict):
        logger.warning(
            "Sentry webhook: unexpected payload type {}", type(payload).__name__
        )
        return {"status": "ignored", "reason": "unexpected_type"}

    try:
        summary = _summarize_payload(payload)
        message = _format_telegram_message(summary)
    except Exception as exc:
        # Don't let a payload schema change crash the endpoint — log the body
        # snippet and ack.
        logger.warning(
            "Sentry webhook: payload parsing failed: {} (action={})",
            exc,
            payload.get("action"),
        )
        return {"status": "ignored", "reason": "parse_failed"}

    # Lazy import to avoid pulling the aiogram bot at module-load time
    # (keeps this router independent for unit-testing without a Telegram
    # token configured).
    #
    # If `OBSERVABILITY_SENTRY_TELEGRAM_CHAT_ID` is set, route the alert to a
    # dedicated Sentry chat/channel; otherwise fall back to the shared admin
    # logs channel.
    try:
        from bloobcat.bot.notifications.admin import send_admin_message  # noqa: WPS433

        delivered = await send_admin_message(
            message,
            chat_id=observability_settings.sentry_telegram_chat_id,
        )
    except Exception as exc:
        # Telegram delivery failures must never propagate — Sentry would
        # retry the webhook and create a cascade.
        logger.error(
            "Sentry webhook: Telegram delivery exception: {}", exc, exc_info=True
        )
        return {"status": "delivery_error", "summary": summary.model_dump()}

    if not delivered:
        logger.warning(
            "Sentry webhook: send_admin_message returned False for short_id={}",
            summary.short_id,
        )
        return {"status": "delivery_unconfirmed", "summary": summary.model_dump()}

    return {"status": "ok", "short_id": summary.short_id}
