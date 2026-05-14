"""Web Push delivery service (VAPID-signed messages to PWA clients).

Used by broadcast and per-user notifications when a user has opted in via
`PushSubscription` rows. Falls back silently when web-push is not configured
(no VAPID keys) so the rest of the app keeps running.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from bloobcat.db.push_subscriptions import PushSubscription
from bloobcat.logger import get_logger
from bloobcat.settings import web_push_settings

logger = get_logger("web_push")

try:  # Optional dependency — keep service degradable.
    from pywebpush import WebPushException, webpush as _webpush_fn
    _PYWEBPUSH_AVAILABLE = True
except ImportError:  # pragma: no cover — install pywebpush to enable
    _webpush_fn = None  # type: ignore[assignment]
    WebPushException = Exception  # type: ignore[assignment,misc]
    _PYWEBPUSH_AVAILABLE = False
    logger.warning("pywebpush is not installed — Web Push delivery disabled")

# Endpoints that mean "subscription is gone forever": don't retry, mark inactive.
_GONE_STATUSES = {404, 410}


def is_configured() -> bool:
    return _PYWEBPUSH_AVAILABLE and web_push_settings.is_configured


def get_public_key() -> str | None:
    return web_push_settings.vapid_public_key


def _build_payload(
    *,
    title: str,
    body: str,
    url: str | None = None,
    icon: str | None = None,
    badge: str | None = None,
    tag: str | None = None,
    actions: list[dict[str, str]] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Serialize payload to JSON the SW handler expects."""
    payload: dict[str, Any] = {
        "title": title,
        "body": body,
    }
    target_url = (url or "").strip()
    if target_url:
        payload["url"] = target_url
    icon_url = (icon or web_push_settings.default_icon_url or "").strip()
    if icon_url:
        payload["icon"] = icon_url
    badge_url = (badge or web_push_settings.default_badge_url or "").strip()
    if badge_url:
        payload["badge"] = badge_url
    if tag:
        payload["tag"] = tag
    if actions:
        payload["actions"] = actions
    if extra:
        payload["data"] = extra
    return json.dumps(payload, ensure_ascii=False)


def _send_one_sync(
    subscription: PushSubscription,
    data: str,
    ttl: int,
) -> tuple[bool, int | None, str | None]:
    """Run blocking pywebpush call. Returns (ok, status_code, error_text)."""
    if not _PYWEBPUSH_AVAILABLE or _webpush_fn is None:
        return False, None, "pywebpush_not_installed"

    private_key_secret = web_push_settings.vapid_private_key
    if private_key_secret is None:
        return False, None, "vapid_not_configured"
    private_key = private_key_secret.get_secret_value()

    sub_info = {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": subscription.p256dh,
            "auth": subscription.auth,
        },
    }
    try:
        response = _webpush_fn(
            subscription_info=sub_info,
            data=data,
            vapid_private_key=private_key,
            vapid_claims={"sub": web_push_settings.vapid_subject},
            ttl=ttl,
            timeout=web_push_settings.request_timeout_seconds,
        )
        return True, getattr(response, "status_code", 201), None
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        err_text = str(exc)[:480]
        return False, status, err_text
    except Exception as exc:  # network/cryptography errors
        return False, None, f"{type(exc).__name__}: {exc!s}"[:480]


async def _persist_result(
    subscription: PushSubscription,
    *,
    ok: bool,
    status: int | None,
    error_text: str | None,
) -> None:
    now = datetime.now(timezone.utc)
    if ok:
        subscription.failure_count = 0
        subscription.last_success_at = now
        subscription.last_error = None
        await subscription.save(
            update_fields=["failure_count", "last_success_at", "last_error", "updated_at"]
        )
        return

    subscription.failure_count = (subscription.failure_count or 0) + 1
    subscription.last_failure_at = now
    subscription.last_error = error_text
    fields_to_update = ["failure_count", "last_failure_at", "last_error", "updated_at"]
    if status in _GONE_STATUSES or subscription.failure_count >= 8:
        subscription.is_active = False
        fields_to_update.append("is_active")
    await subscription.save(update_fields=fields_to_update)


async def send_push_to_subscription(
    subscription: PushSubscription,
    *,
    title: str,
    body: str,
    url: str | None = None,
    icon: str | None = None,
    badge: str | None = None,
    tag: str | None = None,
    actions: list[dict[str, str]] | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    if not is_configured():
        return False
    data = _build_payload(
        title=title, body=body, url=url, icon=icon, badge=badge,
        tag=tag, actions=actions, extra=extra,
    )
    ttl = max(60, int(web_push_settings.ttl_seconds))
    ok, status, err = await asyncio.to_thread(_send_one_sync, subscription, data, ttl)
    await _persist_result(subscription, ok=ok, status=status, error_text=err)
    if not ok:
        logger.debug(
            "web-push delivery failed user_id=%s sub_id=%s status=%s err=%s",
            subscription.user_id, subscription.id, status, err,
        )
    return ok


async def send_push_to_user(
    user_id: int,
    *,
    title: str,
    body: str,
    url: str | None = None,
    icon: str | None = None,
    badge: str | None = None,
    tag: str | None = None,
    actions: list[dict[str, str]] | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Send a push to every active subscription of one user.

    Returns (success_count, failure_count).
    """
    if not is_configured():
        return 0, 0
    subs = await PushSubscription.filter(user_id=user_id, is_active=True)
    if not subs:
        return 0, 0
    success = failure = 0
    for sub in subs:
        ok = await send_push_to_subscription(
            sub, title=title, body=body, url=url, icon=icon, badge=badge,
            tag=tag, actions=actions, extra=extra,
        )
        if ok:
            success += 1
        else:
            failure += 1
    return success, failure


async def send_push_to_users(
    user_ids: Iterable[int],
    *,
    title: str,
    body: str,
    url: str | None = None,
    icon: str | None = None,
    badge: str | None = None,
    tag: str | None = None,
    actions: list[dict[str, str]] | None = None,
    extra: dict[str, Any] | None = None,
    concurrency: int = 16,
    on_progress: Any = None,
) -> dict[str, int]:
    """Bulk send. Returns counters: users_total, users_with_subs, success_subs, failure_subs.

    `on_progress(processed:int, total:int, ok:int, fail:int)` may be an async callable
    invoked roughly every 10% of progress.
    """
    user_id_list = list(dict.fromkeys(int(uid) for uid in user_ids))
    total = len(user_id_list)
    if total == 0 or not is_configured():
        return {"users_total": total, "users_with_subs": 0, "success_subs": 0, "failure_subs": 0}

    users_with_subs = 0
    success_subs = 0
    failure_subs = 0
    semaphore = asyncio.Semaphore(max(1, concurrency))
    progress_step = max(1, total // 10)

    async def _process(uid: int) -> tuple[bool, int, int]:
        async with semaphore:
            subs = await PushSubscription.filter(user_id=uid, is_active=True)
            if not subs:
                return False, 0, 0
            ok_c = fail_c = 0
            for sub in subs:
                ok = await send_push_to_subscription(
                    sub, title=title, body=body, url=url, icon=icon, badge=badge,
                    tag=tag, actions=actions, extra=extra,
                )
                if ok:
                    ok_c += 1
                else:
                    fail_c += 1
            return True, ok_c, fail_c

    processed = 0
    tasks = [asyncio.create_task(_process(uid)) for uid in user_id_list]
    for task in asyncio.as_completed(tasks):
        had_sub, ok_c, fail_c = await task
        if had_sub:
            users_with_subs += 1
        success_subs += ok_c
        failure_subs += fail_c
        processed += 1
        if on_progress and (processed % progress_step == 0 or processed == total):
            try:
                await on_progress(processed, total, success_subs, failure_subs)
            except Exception:
                pass

    return {
        "users_total": total,
        "users_with_subs": users_with_subs,
        "success_subs": success_subs,
        "failure_subs": failure_subs,
    }
