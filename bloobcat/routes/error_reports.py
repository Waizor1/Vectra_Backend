from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from bloobcat.db.error_reports import ErrorReports
from bloobcat.logger import get_logger

logger = get_logger("routes.error_reports")

router = APIRouter(prefix="/errors", tags=["errors"])


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

    await ErrorReports.create(
        user_id=user_id,
        event_id=payload.eventId,
        code=payload.code,
        type=payload.type,
        message=payload.message,
        name=payload.name,
        stack=payload.stack,
        route=payload.route,
        href=payload.href,
        user_agent=payload.userAgent,
        extra=payload.extra,
        triage_severity="medium",
        triage_status="new",
        triage_due_at=datetime.now(timezone.utc) + timedelta(hours=24),
        reported_at=reported_at,
    )
    logger.info("Error report received", extra={"code": payload.code, "type": payload.type, "user_id": user_id})
    return {"ok": True}
