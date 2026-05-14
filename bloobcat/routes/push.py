"""Web Push subscription API.

Endpoints:
  GET  /push/config        — returns { enabled, public_key } so the client can subscribe.
  POST /push/subscribe     — upsert browser PushSubscription for the authenticated user.
  POST /push/unsubscribe   — mark a subscription inactive (by endpoint).
  GET  /push/status        — does the current user have any active subscriptions?
  POST /push/test          — send a smoke-test push to the calling user (rate-limited).

All write endpoints require authenticated user (`validate` dependency, the
same gate the rest of the app uses). Subscriptions are scoped to the user
id derived from auth — we never trust client-supplied user_id.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from tortoise.exceptions import IntegrityError

from bloobcat.bot.notifications.web_push import (
    get_public_key,
    is_configured,
    send_push_to_subscription,
)
from bloobcat.db.push_subscriptions import PushSubscription
from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.logger import get_logger

logger = get_logger("push_api")

router = APIRouter(prefix="/push", tags=["push"])


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=8, max_length=256)
    auth: str = Field(..., min_length=8, max_length=128)


class SubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=8, max_length=2048)
    keys: PushKeys
    user_agent: str | None = Field(None, max_length=512)
    locale: str | None = Field(None, max_length=16)

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_https(cls, value: str) -> str:
        v = value.strip()
        # Browsers only emit https push endpoints in prod. Reject anything
        # else — `http://localhost` was a debug convenience but enables a
        # backend-side SSRF primitive (pywebpush would POST to internal hosts).
        if not v.startswith("https://"):
            raise ValueError("endpoint must be an https URL")
        return v


class UnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., min_length=8, max_length=2048)


@router.get("/config")
async def push_config() -> dict:
    """Public — frontend reads this before subscribing.

    No auth needed: the VAPID public key is meant to be public by design.
    """
    return {
        "enabled": is_configured(),
        "public_key": get_public_key() if is_configured() else None,
    }


MAX_SUBSCRIPTIONS_PER_USER = 20


@router.post("/subscribe")
async def push_subscribe(
    body: SubscribeRequest,
    user: Users = Depends(validate),
) -> dict:
    if not is_configured():
        raise HTTPException(status_code=503, detail="web_push_not_configured")

    now = datetime.now(timezone.utc)
    locale = (body.locale or user.language_code or "ru")[:16]
    try:
        existing = await PushSubscription.get_or_none(endpoint=body.endpoint)
        if existing is None:
            # Cap per-user rows so a single account can't spam unique endpoints
            # to inflate the table. Tortoise count() is cheap with the
            # (user_id,is_active) index.
            owned = await PushSubscription.filter(user_id=user.id).count()
            if owned >= MAX_SUBSCRIPTIONS_PER_USER:
                raise HTTPException(status_code=429, detail="too_many_subscriptions")
        if existing is not None:
            existing.user_id = user.id
            existing.p256dh = body.keys.p256dh
            existing.auth = body.keys.auth
            existing.user_agent = (body.user_agent or "")[:512] or None
            existing.locale = locale
            existing.is_active = True
            existing.failure_count = 0
            existing.last_error = None
            existing.last_success_at = now
            await existing.save()
            return {"ok": True, "subscription_id": existing.id, "created": False}

        sub = await PushSubscription.create(
            user_id=user.id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            user_agent=(body.user_agent or "")[:512] or None,
            locale=locale,
            is_active=True,
        )
        return {"ok": True, "subscription_id": sub.id, "created": True}
    except IntegrityError:
        existing = await PushSubscription.get_or_none(endpoint=body.endpoint)
        if existing is not None:
            return {"ok": True, "subscription_id": existing.id, "created": False}
        raise HTTPException(status_code=400, detail="push_subscribe_conflict")


@router.post("/unsubscribe")
async def push_unsubscribe(
    body: UnsubscribeRequest,
    user: Users = Depends(validate),
) -> dict:
    sub = await PushSubscription.get_or_none(endpoint=body.endpoint, user_id=user.id)
    if sub is None:
        return {"ok": True, "found": False}
    sub.is_active = False
    await sub.save(update_fields=["is_active", "updated_at"])
    return {"ok": True, "found": True}


@router.get("/status")
async def push_status(user: Users = Depends(validate)) -> dict:
    if not is_configured():
        return {"enabled": False, "subscribed": False, "subscription_count": 0}
    count = await PushSubscription.filter(user_id=user.id, is_active=True).count()
    return {"enabled": True, "subscribed": count > 0, "subscription_count": count}


_TEST_PUSH_COOLDOWN_SECONDS = 30
_TEST_PUSH_PRUNE_AFTER = _TEST_PUSH_COOLDOWN_SECONDS * 4
_test_push_last_at: dict[int, float] = {}
_test_push_lock = asyncio.Lock()


def _prune_test_push_state(now_ts: float) -> None:
    """Drop entries older than the cooldown window so the dict stays bounded."""
    cutoff = now_ts - _TEST_PUSH_PRUNE_AFTER
    stale = [uid for uid, ts in _test_push_last_at.items() if ts < cutoff]
    for uid in stale:
        _test_push_last_at.pop(uid, None)


@router.post("/test")
async def push_test(user: Users = Depends(validate)) -> dict:
    """Send a tiny test notification to the calling user's subscriptions.

    Rate-limited per user (30s) to keep the user from accidentally hammering
    upstream push services from a dev tools loop.
    """
    if not is_configured():
        raise HTTPException(status_code=503, detail="web_push_not_configured")

    async with _test_push_lock:
        now = datetime.now(timezone.utc).timestamp()
        _prune_test_push_state(now)
        last = _test_push_last_at.get(user.id, 0.0)
        if now - last < _TEST_PUSH_COOLDOWN_SECONDS:
            wait = int(_TEST_PUSH_COOLDOWN_SECONDS - (now - last))
            raise HTTPException(status_code=429, detail={"retry_after": wait})
        _test_push_last_at[user.id] = now

    subs = await PushSubscription.filter(user_id=user.id, is_active=True)
    if not subs:
        raise HTTPException(status_code=404, detail="no_active_subscriptions")
    success = failure = 0
    for sub in subs:
        ok = await send_push_to_subscription(
            sub,
            title="Vectra Connect",
            body="Push-уведомления подключены 👍",
            url="/",
            tag="vectra-test",
        )
        if ok:
            success += 1
        else:
            failure += 1
    return {"ok": failure == 0, "success": success, "failure": failure}
