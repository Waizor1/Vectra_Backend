"""In-app notifications API.

GET /active: returns list of active notifications for the user.
  - Empty list [] when none match (no error, backward compatible).
  - Stable response shape: [{ id, title, body, auto_hide_seconds }].

POST /{id}/view: records that the user viewed the notification.
  - 404 if notification not found; 400 on invalid session_id.
  - Duplicate (user_id, notification_id, session_id) returns success (idempotent).
  - Never 500 on normal user flows (IntegrityError handled).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from tortoise.exceptions import IntegrityError

from bloobcat.db.in_app_notifications import InAppNotification, NotificationView
from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate

router = APIRouter(prefix="/notifications", tags=["notifications"])

SESSION_ID_MAX_LEN = 128


class NotificationViewRequest(BaseModel):
    """Request body for POST /notifications/{id}/view."""

    session_id: str = Field(..., min_length=1, max_length=SESSION_ID_MAX_LEN)


class ActiveNotificationItem(BaseModel):
    """Single active notification for client. Stable contract for frontend."""

    id: int
    title: str
    body: str
    auto_hide_seconds: int | None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _user_view_count(user_id: int, notification_id: int) -> int:
    return await NotificationView.filter(
        user_id=user_id, notification_id=notification_id
    ).count()


async def _session_view_count(user_id: int, notification_id: int, session_id: str) -> int:
    return await NotificationView.filter(
        user_id=user_id, notification_id=notification_id, session_id=session_id
    ).count()


@router.get("/active", response_model=list[ActiveNotificationItem])
async def get_active_notifications(
    user: Users = Depends(validate),
    session_id: str | None = Query(None, max_length=SESSION_ID_MAX_LEN),
) -> list[ActiveNotificationItem]:
    """
    Returns active in-app notifications for the authenticated user.
    Filters by period (start_at/end_at), max_per_user, and optionally max_per_session.
    Returns [] when no notifications match (no error, backward compatible).
    """
    now = _now_utc()
    notifications = await InAppNotification.filter(
        is_active=True,
        start_at__lte=now,
        end_at__gte=now,
    ).order_by("id")

    result: list[ActiveNotificationItem] = []
    for n in notifications:
        user_count = await _user_view_count(user.id, n.id)
        if n.max_per_user is not None and user_count >= n.max_per_user:
            continue

        if session_id:
            session_count = await _session_view_count(user.id, n.id, session_id)
            if n.max_per_session is not None and session_count >= n.max_per_session:
                continue

        result.append(
            ActiveNotificationItem(
                id=n.id,
                title=n.title,
                body=n.body,
                auto_hide_seconds=n.auto_hide_seconds,
            )
        )

    return result


@router.post("/{notification_id:int}/view")
async def record_notification_view(
    notification_id: int,
    body: NotificationViewRequest,
    user: Users = Depends(validate),
) -> dict:
    """
    Records that the user viewed the notification in the given session.
    Validates notification exists; records view even if limits exceeded
    (client may have shown it before limits were hit).
    Returns 404 if notification not found; never 500 on normal user flows.
    """
    notification = await InAppNotification.get_or_none(id=notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    session_id = body.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required and cannot be empty")

    try:
        await NotificationView.create(
            user_id=user.id,
            notification_id=notification_id,
            session_id=session_id,
        )
    except IntegrityError as e:
        # Duplicate (unique violation) -> idempotent success.
        cause = getattr(e, "__cause__", e)
        sqlstate = getattr(cause, "sqlstate", None) if cause else None
        if sqlstate == "23505":
            return {"ok": True, "notification_id": notification_id}
        # FK violation or other: treat as not found.
        raise HTTPException(status_code=404, detail="Notification not found") from e

    return {"ok": True, "notification_id": notification_id}
