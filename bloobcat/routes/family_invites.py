from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException
from httpx import AsyncClient
from pydantic import BaseModel, Field

from bloobcat.bot.notifications.admin import (
    notify_family_membership_event,
    send_admin_message,
)
from bloobcat.bot.notifications.family.events import (
    notify_family_member_joined,
    notify_family_member_limit_updated,
    notify_family_member_removed,
    notify_family_owner_invites_blocked,
    notify_family_owner_invites_unblocked,
    notify_family_owner_invite_revoked,
    notify_family_owner_member_joined,
)
from bloobcat.funcs.validate import validate
from bloobcat.db.users import Users
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.family_invites import FamilyInvites
from bloobcat.db.family_members import FamilyMembers
from bloobcat.db.family_audit_logs import FamilyAuditLogs
from bloobcat.routes.family_quota import (
    build_family_quota_snapshot,
    compute_owner_quota_limit,
    family_devices_limit,
    get_family_allocation_summary,
    is_subscription_active,
    resolve_owner_base_devices_limit,
    utc_now,
)
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.routes.remnawave.hwid_utils import count_active_devices
from bloobcat.settings import remnawave_settings, app_settings, telegram_settings
from bloobcat.logger import get_logger
from tortoise.expressions import F, Q

logger = get_logger("routes.family_invites")

router = APIRouter(prefix="/family", tags=["family"])


def _token_secret() -> str:
    secret = getattr(app_settings, "family_invite_secret", None)
    if secret:
        return str(secret)
    return "family_invite_secret_fallback"


def _hash_token(token: str) -> str:
    return hmac.new(
        _token_secret().encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _now() -> datetime:
    return utc_now()


async def _resolve_personal_device_limit(user: Users) -> int:
    # If member has own active tariff, restore this tariff limit.
    # Otherwise use safe baseline = 1 device.
    if user.active_tariff_id and user.expired_at and user.expired_at >= _now().date():
        tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if tariff and int(getattr(tariff, "hwid_limit", 0) or 0) > 0:
            return int(tariff.hwid_limit)
    return 1


async def _sync_user_hwid_limit(user: Users, limit: int) -> None:
    normalized_limit = max(0, int(limit or 0))
    changed = user.hwid_limit != normalized_limit
    if not changed:
        return
    user.hwid_limit = normalized_limit
    await user.save(update_fields=["hwid_limit"])
    if user.remnawave_uuid:
        client = RemnaWaveClient(
            remnawave_settings.url,
            remnawave_settings.token.get_secret_value(),
        )
        try:
            await client.users.update_user(
                user.remnawave_uuid, hwidDeviceLimit=normalized_limit
            )
        finally:
            await client.close()


async def _sum_active_family_allocations(owner_id: int) -> int:
    summary = await get_family_allocation_summary(owner_id)
    return summary.member_allocated_devices


async def _owner_effective_devices_limit(owner: Users) -> int:
    summary = await get_family_allocation_summary(owner.id)
    return compute_owner_quota_limit(
        family_limit=await _owner_family_capacity(owner),
        member_allocated_devices=summary.member_allocated_devices,
        invite_reserved_devices=summary.invite_reserved_devices,
    )


async def _sync_owner_effective_remnawave_limit(owner: Users) -> None:
    # Keep owner entitlement source in DB intact, but enforce effective remaining
    # device quota in RemnaWave based on active member allocations and invite
    # reservations.
    if not owner.remnawave_uuid:
        return
    effective_limit = await _owner_effective_devices_limit(owner)
    client = RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )
    try:
        await client.users.update_user(
            owner.remnawave_uuid, hwidDeviceLimit=int(effective_limit)
        )
    finally:
        await client.close()


@dataclass(slots=True)
class _InviteJoinContext:
    join_mode: str
    existing_membership: FamilyMembers | None = None
    current_member_family: FamilyMembers | None = None
    current_family_owner: Users | None = None
    current_family_allocated_devices: int | None = None
    current_connected_devices: int = 0
    devices_to_remove: int = 0


async def _notify_family_membership_admin_log(
    *,
    owner: Users,
    member: Users,
    event: str,
    allocated_devices: int | None = None,
    previous_allocated_devices: int | None = None,
    restored_limit: int | None = None,
) -> None:
    await notify_family_membership_event(
        owner=owner,
        member=member,
        event=event,
        allocated_devices=allocated_devices,
        previous_allocated_devices=previous_allocated_devices,
        restored_limit=restored_limit,
    )


def _append_startapp_payload(base_url: str, payload: str) -> str:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("Invalid base URL")
    query = parsed.query
    next_query = (
        f"{query}&startapp={quote(payload, safe='')}"
        if query
        else f"startapp={quote(payload, safe='')}"
    )
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            next_query,
            parsed.fragment,
        )
    )


async def _build_family_invite_link(token: str) -> str:
    payload = f"family_{token}"
    webapp_url = (getattr(telegram_settings, "webapp_url", None) or "").strip()
    if webapp_url:
        try:
            return _append_startapp_payload(webapp_url, payload)
        except Exception:
            logger.warning(
                "Invalid TELEGRAM_WEBAPP_URL for family invite link: {}", webapp_url
            )
    # Fallback for misconfigured environments.
    from bloobcat.bot.bot import get_bot_username

    try:
        bot_name = await get_bot_username()
    except Exception:
        bot_name = "VectraConnect_bot"
    return f"https://t.me/{bot_name}?start={payload}"


def _family_limit() -> int:
    # Compatibility helper: family threshold, not capacity.
    return family_devices_limit()


async def _owner_family_capacity(owner: Users) -> int:
    return max(_family_limit(), await resolve_owner_base_devices_limit(owner))


async def _has_family_owner_access_async(user: Users) -> bool:
    return _is_subscription_active(user) and await resolve_owner_base_devices_limit(user) >= _family_limit()


def _serialize_invite_user(user: Users | None) -> InvitePreviewUser | None:
    if user is None:
        return None
    return InvitePreviewUser(
        id=int(user.id),
        username=getattr(user, "username", None),
        full_name=getattr(user, "full_name", None),
    )


def _has_family_owner_access(user: Users) -> bool:
    return _is_subscription_active(user) and int(getattr(user, "hwid_limit", 0) or 0) >= _family_limit()


async def _get_user_connected_devices_count(user: Users) -> int:
    if not user.remnawave_uuid:
        return 0
    client = RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )
    try:
        raw_resp = await client.users.get_user_hwid_devices(str(user.remnawave_uuid))
        return count_active_devices(raw_resp)
    except Exception as exc:
        logger.warning(
            "family_member_connected_devices_fetch_failed user=%s err=%s",
            user.id,
            exc,
        )
        return 0
    finally:
        await client.close()


async def _get_invite_or_raise(token: str) -> FamilyInvites:
    token_hash = _hash_token(token)
    invite = await FamilyInvites.get_or_none(token_hash=token_hash)
    if not invite or invite.revoked_at:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.expires_at and invite.expires_at < _now():
        raise HTTPException(status_code=410, detail="Invite expired")
    if invite.used_count >= invite.max_uses:
        raise HTTPException(status_code=409, detail="Invite already used")
    return invite


async def _resolve_invite_join_context(
    *,
    owner: Users,
    invite: FamilyInvites,
    user: Users,
) -> _InviteJoinContext:
    existing_membership = await FamilyMembers.get_or_none(
        owner_id=owner.id, member_id=user.id
    )
    current_member_family = await FamilyMembers.get_or_none(
        member_id=user.id, status="active", allocated_devices__gt=0
    ).prefetch_related("owner")
    connected_devices = await _get_user_connected_devices_count(user)

    if owner.id == user.id:
        return _InviteJoinContext(
            join_mode="self_invite",
            existing_membership=existing_membership,
            current_family_owner=user,
            current_family_allocated_devices=await _owner_family_capacity(user),
            current_connected_devices=connected_devices,
        )

    if current_member_family and current_member_family.owner_id == owner.id:
        return _InviteJoinContext(
            join_mode="already_in_same_family",
            existing_membership=existing_membership or current_member_family,
            current_member_family=current_member_family,
            current_family_owner=owner,
            current_family_allocated_devices=int(
                current_member_family.allocated_devices or 0
            ),
            current_connected_devices=connected_devices,
        )

    if current_member_family is None and await _has_family_owner_access_async(user):
        return _InviteJoinContext(
            join_mode="owner_blocked",
            existing_membership=existing_membership,
            current_family_owner=user,
            current_family_allocated_devices=await _owner_family_capacity(user),
            current_connected_devices=connected_devices,
        )

    if current_member_family and current_member_family.owner_id != owner.id:
        devices_to_remove = max(
            0, connected_devices - int(invite.allocated_devices or 0)
        )
        return _InviteJoinContext(
            join_mode=(
                "switch_family_cleanup_required"
                if devices_to_remove > 0
                else "switch_family_ready"
            ),
            existing_membership=existing_membership,
            current_member_family=current_member_family,
            current_family_owner=current_member_family.owner,
            current_family_allocated_devices=int(
                current_member_family.allocated_devices or 0
            ),
            current_connected_devices=connected_devices,
            devices_to_remove=devices_to_remove,
        )

    return _InviteJoinContext(
        join_mode="join_ready",
        existing_membership=existing_membership,
        current_connected_devices=connected_devices,
    )


def _default_invite_ttl_hours() -> int:
    return int(getattr(app_settings, "family_invite_ttl_hours", 48) or 48)


def _invite_rate_limit_per_hour() -> int:
    return int(getattr(app_settings, "family_invite_rate_limit_per_hour", 20) or 20)


def _max_active_invites() -> int:
    return int(getattr(app_settings, "family_max_active_invites", 5) or 5)


def _invite_cooldown_seconds() -> int:
    return int(getattr(app_settings, "family_invite_cooldown_seconds", 30) or 30)


def _invite_hard_block_multiplier() -> int:
    return int(getattr(app_settings, "family_invite_hard_block_multiplier", 2) or 2)


def _anomaly_invite_threshold() -> int:
    return int(getattr(app_settings, "family_anomaly_invite_threshold", 25) or 25)


def _anomaly_accept_threshold() -> int:
    return int(getattr(app_settings, "family_anomaly_accept_threshold", 20) or 20)


def _anomaly_revoke_threshold() -> int:
    return int(getattr(app_settings, "family_anomaly_revoke_threshold", 10) or 10)


def _alerts_enabled() -> bool:
    return bool(getattr(app_settings, "family_alerts_enabled", True))


def _alerts_webhook_url() -> str | None:
    return getattr(app_settings, "family_alerts_webhook_url", None)


def _alerts_webhook_timeout_seconds() -> float:
    return float(getattr(app_settings, "family_alerts_webhook_timeout_seconds", 5) or 5)


def _anomaly_block_threshold() -> int:
    return int(getattr(app_settings, "family_anomaly_block_threshold", 3) or 3)


def _anomaly_block_window_hours() -> int:
    return int(getattr(app_settings, "family_anomaly_block_window_hours", 6) or 6)


def _anomaly_block_duration_minutes() -> int:
    return int(getattr(app_settings, "family_anomaly_block_duration_minutes", 60) or 60)


def _is_subscription_active(user: Users) -> bool:
    return is_subscription_active(user)


async def _audit(
    owner: Users,
    actor: Users,
    action: str,
    target_id: str | None = None,
    details: Dict[str, Any] | None = None,
) -> None:
    try:
        await FamilyAuditLogs.create(
            owner=owner,
            actor=actor,
            action=action,
            target_id=target_id,
            details=details or {},
        )
    except Exception as exc:
        logger.warning("family_audit_failed action=%s err=%s", action, exc)


async def _audit_anomaly(
    owner: Users, actor: Users, kind: str, details: Dict[str, Any]
) -> None:
    await _audit(
        owner=owner,
        actor=actor,
        action="anomaly",
        target_id=None,
        details={"kind": kind, **details},
    )
    logger.warning(
        "family_anomaly_detected owner=%s kind=%s details=%s", owner.id, kind, details
    )


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


async def _get_active_invite_block(owner: Users) -> datetime | None:
    last_block = (
        await FamilyAuditLogs.filter(owner_id=owner.id, action="invite_blocked")
        .order_by("-created_at")
        .first()
    )
    if not last_block or not last_block.details:
        return None
    last_unblock = (
        await FamilyAuditLogs.filter(owner_id=owner.id, action="invite_unblocked")
        .order_by("-created_at")
        .first()
    )
    if (
        last_unblock
        and last_unblock.created_at
        and last_block.created_at
        and last_unblock.created_at >= last_block.created_at
    ):
        return None
    blocked_until = _parse_dt(last_block.details.get("blocked_until"))
    if not blocked_until:
        return None
    return blocked_until if blocked_until > _now() else None


async def _send_family_alert(owner: Users, kind: str, details: Dict[str, Any]) -> None:
    if not _alerts_enabled():
        return
    try:
        username = f"@{owner.username}" if owner.username else "no_username"
        text = (
            "⚠️ Family anomaly\n"
            f"Owner: {owner.full_name or 'unknown'} ({username})\n"
            f"Owner ID: <code>{owner.id}</code>\n"
            f"Kind: {kind}\n"
            f"Details: {details}\n"
            "#family_anomaly"
        )
        await send_admin_message(text=text)
    except Exception as exc:
        logger.warning(
            "family_alert_telegram_failed owner=%s kind=%s err=%s", owner.id, kind, exc
        )

    webhook_url = _alerts_webhook_url()
    if not webhook_url:
        return
    payload = {
        "event": "family_anomaly",
        "kind": kind,
        "owner_id": owner.id,
        "owner_username": owner.username,
        "details": details,
        "created_at": _now().isoformat(),
    }
    try:
        async with AsyncClient(timeout=_alerts_webhook_timeout_seconds()) as client:
            await client.post(webhook_url, json=payload)
    except Exception as exc:
        logger.warning(
            "family_alert_webhook_failed owner=%s kind=%s err=%s", owner.id, kind, exc
        )


async def _maybe_block_invites(owner: Users, actor: Users, kind: str) -> None:
    if await _get_active_invite_block(owner):
        return
    window_start = _now() - timedelta(hours=_anomaly_block_window_hours())
    anomaly_count = await FamilyAuditLogs.filter(
        owner_id=owner.id, action="anomaly", created_at__gte=window_start
    ).count()
    if anomaly_count < _anomaly_block_threshold():
        return
    blocked_until = _now() + timedelta(minutes=_anomaly_block_duration_minutes())
    await _audit(
        owner=owner,
        actor=actor,
        action="invite_blocked",
        target_id=None,
        details={
            "blocked_until": blocked_until.isoformat(),
            "reason": "repeated_anomalies",
            "kind": kind,
            "anomaly_count": anomaly_count,
            "window_hours": _anomaly_block_window_hours(),
        },
    )
    logger.warning("family_invite_blocked owner=%s until=%s", owner.id, blocked_until)
    await _send_family_alert(
        owner,
        "invite_blocked",
        {
            "blocked_until": blocked_until.isoformat(),
            "anomaly_count": anomaly_count,
            "window_hours": _anomaly_block_window_hours(),
        },
    )
    try:
        await notify_family_owner_invites_blocked(
            owner=owner,
            blocked_until=blocked_until,
            reason=f"repeated_anomalies:{kind}",
        )
    except Exception as exc:
        logger.warning(
            "family_owner_block_notification_failed owner=%s err=%s", owner.id, exc
        )


async def _maybe_notify_owner_invites_unblocked(owner: Users, actor: Users) -> None:
    """
    Sends a one-time "unblocked" message when a previous temporary block has expired.
    """
    last_block = (
        await FamilyAuditLogs.filter(owner_id=owner.id, action="invite_blocked")
        .order_by("-created_at")
        .first()
    )
    if not last_block or not last_block.details:
        return
    last_unblock = (
        await FamilyAuditLogs.filter(owner_id=owner.id, action="invite_unblocked")
        .order_by("-created_at")
        .first()
    )
    if (
        last_unblock
        and last_block.created_at
        and last_unblock.created_at
        and last_unblock.created_at >= last_block.created_at
    ):
        return
    blocked_until = _parse_dt(last_block.details.get("blocked_until"))
    if not blocked_until or blocked_until > _now():
        return
    await _audit(
        owner=owner,
        actor=actor,
        action="invite_unblocked",
        target_id=None,
        details={"blocked_until": blocked_until.isoformat(), "reason": "expired"},
    )
    try:
        await notify_family_owner_invites_unblocked(owner)
    except Exception as exc:
        logger.warning(
            "family_owner_unblock_notification_failed owner=%s err=%s", owner.id, exc
        )


async def _check_anomaly(owner: Users, actor: Users, action: str) -> None:
    window_start = _now() - timedelta(hours=1)
    count = await FamilyAuditLogs.filter(
        owner_id=owner.id, action=action, created_at__gte=window_start
    ).count()
    if action == "invite_created" and count >= _anomaly_invite_threshold():
        details = {"count": count, "window_hours": 1}
        await _audit_anomaly(owner, actor, "invite_rate", details)
        await _send_family_alert(owner, "invite_rate", details)
        await _maybe_block_invites(owner, actor, "invite_rate")
    if action == "member_added" and count >= _anomaly_accept_threshold():
        details = {"count": count, "window_hours": 1}
        await _audit_anomaly(owner, actor, "accept_rate", details)
        await _send_family_alert(owner, "accept_rate", details)
        await _maybe_block_invites(owner, actor, "accept_rate")
    if action == "invite_revoked" and count >= _anomaly_revoke_threshold():
        details = {"count": count, "window_hours": 1}
        await _audit_anomaly(owner, actor, "revoke_rate", details)
        await _send_family_alert(owner, "revoke_rate", details)
        await _maybe_block_invites(owner, actor, "revoke_rate")


class InviteCreateRequest(BaseModel):
    allocated_devices: int = Field(ge=1)
    ttl_hours: int | None = Field(default=None, ge=1, le=168)


class InviteValidateResponse(BaseModel):
    ok: bool
    expires_at: datetime | None = None


class InvitePreviewUser(BaseModel):
    id: int
    username: str | None = None
    full_name: str | None = None


class InvitePreviewResponse(BaseModel):
    ok: bool = True
    expires_at: datetime | None = None
    allocated_devices: int
    join_mode: Literal[
        "self_invite",
        "owner_blocked",
        "already_in_same_family",
        "join_ready",
        "switch_family_ready",
        "switch_family_cleanup_required",
    ]
    owner: InvitePreviewUser
    current_family_owner: InvitePreviewUser | None = None
    current_family_allocated_devices: int | None = None
    current_connected_devices: int = 0
    devices_to_remove: int = 0


@router.post("/invites")
async def create_invite(
    payload: InviteCreateRequest, user: Users = Depends(validate)
) -> Dict[str, Any]:
    if not _is_subscription_active(user):
        raise HTTPException(status_code=403, detail="Subscription expired")
    family_capacity = await _owner_family_capacity(user)
    if family_capacity < _family_limit():
        raise HTTPException(status_code=403, detail="Family subscription required")
    await _maybe_notify_owner_invites_unblocked(owner=user, actor=user)
    blocked_until = await _get_active_invite_block(user)
    if blocked_until:
        retry_after = max(1, int((blocked_until - _now()).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail="Invite creation temporarily blocked",
            headers={"Retry-After": str(retry_after)},
        )
    if payload.allocated_devices > family_capacity:
        raise HTTPException(
            status_code=400, detail="Allocated devices exceed family limit"
        )
    owner_quota = await build_family_quota_snapshot(user)
    if owner_quota.reserved_devices + int(payload.allocated_devices or 0) > owner_quota.family_limit:
        raise HTTPException(status_code=409, detail="Family allocation limit exceeded")
    # cooldown: минимальный интервал между созданием инвайтов
    last_invite = (
        await FamilyInvites.filter(owner_id=user.id).order_by("-created_at").first()
    )
    if last_invite and last_invite.created_at:
        seconds_since_last = max(
            0, int((_now() - last_invite.created_at).total_seconds())
        )
        cooldown = _invite_cooldown_seconds()
        if seconds_since_last < cooldown:
            retry_after = cooldown - seconds_since_last
            raise HTTPException(
                status_code=429,
                detail="Invite cooldown in effect",
                headers={"Retry-After": str(retry_after)},
            )

    # rate limit: invites per hour
    window_start = _now() - timedelta(hours=1)
    created_recent = await FamilyInvites.filter(
        owner_id=user.id, created_at__gte=window_start
    ).count()
    if created_recent >= _invite_rate_limit_per_hour():
        retry_after = int((_now() - window_start).total_seconds())
        retry_after = max(1, 3600 - retry_after)
        raise HTTPException(
            status_code=429,
            detail="Too many invites, try later",
            headers={"Retry-After": str(retry_after)},
        )
    # hard block window (anti‑abuse)
    hard_multiplier = _invite_hard_block_multiplier()
    if hard_multiplier > 1:
        hard_window_start = _now() - timedelta(hours=hard_multiplier)
        created_hard = await FamilyInvites.filter(
            owner_id=user.id, created_at__gte=hard_window_start
        ).count()
        if created_hard >= _invite_rate_limit_per_hour() * hard_multiplier:
            retry_after = int((_now() - hard_window_start).total_seconds())
            retry_after = max(1, 3600 * hard_multiplier - retry_after)
            raise HTTPException(
                status_code=429,
                detail="Invite creation temporarily blocked",
                headers={"Retry-After": str(retry_after)},
            )
    # cap active invites to avoid abuse
    active_count = (
        await FamilyInvites.filter(
            owner_id=user.id,
            revoked_at=None,
            used_count__lt=F("max_uses"),
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=_now()))
        .count()
    )
    if active_count >= _max_active_invites():
        raise HTTPException(status_code=409, detail="Too many active invites")
    ttl_hours = payload.ttl_hours or _default_invite_ttl_hours()
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    expires_at = _now() + timedelta(hours=ttl_hours)
    invite = await FamilyInvites.create(
        owner=user,
        allocated_devices=payload.allocated_devices,
        token_hash=token_hash,
        expires_at=expires_at,
        max_uses=1,
    )
    await _audit(
        owner=user,
        actor=user,
        action="invite_created",
        target_id=str(invite.id),
        details={
            "allocated_devices": payload.allocated_devices,
            "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
        },
    )
    await _check_anomaly(user, user, "invite_created")
    logger.info(
        "family_invite_created owner=%s invite=%s devices=%s expires_at=%s",
        user.id,
        invite.id,
        payload.allocated_devices,
        invite.expires_at,
    )
    try:
        await _sync_owner_effective_remnawave_limit(user)
    except Exception as exc:
        logger.warning(
            "family_owner_effective_limit_sync_failed owner=%s err=%s",
            user.id,
            exc,
        )
    return {
        "id": str(invite.id),
        "token": token,
        "invite_url": await _build_family_invite_link(token),
        "expires_at": invite.expires_at,
    }


@router.get("/invites/{token}")
async def validate_invite(token: str) -> InviteValidateResponse:
    invite = await _get_invite_or_raise(token)
    return InviteValidateResponse(ok=True, expires_at=invite.expires_at)


@router.get("/invites/{token}/preview")
async def preview_invite(
    token: str, user: Users = Depends(validate)
) -> InvitePreviewResponse:
    invite = await _get_invite_or_raise(token)
    owner = await Users.get(id=invite.owner_id)
    context = await _resolve_invite_join_context(owner=owner, invite=invite, user=user)
    return InvitePreviewResponse(
        ok=True,
        expires_at=invite.expires_at,
        allocated_devices=int(invite.allocated_devices or 0),
        join_mode=context.join_mode,  # type: ignore[arg-type]
        owner=_serialize_invite_user(owner),
        current_family_owner=_serialize_invite_user(context.current_family_owner),
        current_family_allocated_devices=context.current_family_allocated_devices,
        current_connected_devices=context.current_connected_devices,
        devices_to_remove=context.devices_to_remove,
    )


@router.post("/invites/{token}/accept")
async def accept_invite(token: str, user: Users = Depends(validate)) -> Dict[str, Any]:
    invite = await _get_invite_or_raise(token)
    owner = await Users.get(id=invite.owner_id)
    if not _is_subscription_active(owner):
        raise HTTPException(status_code=403, detail="Owner subscription expired")
    join_context = await _resolve_invite_join_context(owner=owner, invite=invite, user=user)
    if join_context.join_mode == "self_invite":
        raise HTTPException(status_code=400, detail="Owner cannot accept own invite")
    if join_context.join_mode == "owner_blocked":
        raise HTTPException(
            status_code=409,
            detail="Family owner cannot join another family",
        )
    if join_context.join_mode == "switch_family_cleanup_required":
        raise HTTPException(status_code=409, detail="Device cleanup required")
    if join_context.join_mode == "already_in_same_family":
        existing_membership = join_context.existing_membership
        if existing_membership is None:
            existing_membership = await FamilyMembers.get_or_none(
                owner_id=owner.id, member_id=user.id
            )
        if existing_membership is None:
            raise HTTPException(status_code=409, detail="Member already in this family")
        await _audit(
            owner=owner,
            actor=user,
            action="invite_accept_idempotent",
            target_id=str(existing_membership.id),
            details={"allocated_devices": existing_membership.allocated_devices},
        )
        logger.info(
            "family_invite_accept_idempotent owner=%s member=%s member_record=%s",
            owner.id,
            user.id,
            existing_membership.id,
        )
        return {
            "ok": True,
            "member_id": str(existing_membership.id),
            "allocated_devices": existing_membership.allocated_devices,
        }

    # Referral attribution: family invite owner becomes referrer if not already set.
    _user_referred_by = int(getattr(user, "referred_by", 0) or 0)
    if _user_referred_by == 0 and int(owner.id) != int(user.id):
        user.referred_by = int(owner.id)
        await user.save(update_fields=["referred_by"])
        logger.info(
            "family_invite_referral_attribution member=%s referred_by=%s",
            user.id,
            owner.id,
        )

    existing = join_context.existing_membership
    transition_membership = (
        join_context.current_member_family
        if join_context.join_mode == "switch_family_ready"
        else None
    )
    transition_previous_allocated_devices = int(
        transition_membership.allocated_devices or 0
    ) if transition_membership else None
    transition_old_owner = transition_membership.owner if transition_membership else None
    if existing:
        existing_allocated = int(existing.allocated_devices or 0)
        existing_status = str(existing.status or "disabled")
        should_reactivate = existing.status == "disabled" or existing_allocated <= 0
        if should_reactivate:
            total_alloc = await _sum_active_family_allocations(owner.id)
            total_alloc = (
                total_alloc - existing_allocated + int(invite.allocated_devices or 0)
            )
            if total_alloc > await _owner_family_capacity(owner):
                raise HTTPException(
                    status_code=409, detail="Family allocation limit exceeded"
                )
        else:
            await _audit(
                owner=owner,
                actor=user,
                action="invite_accept_idempotent",
                target_id=str(existing.id),
                details={"allocated_devices": existing.allocated_devices},
            )
            logger.info(
                "family_invite_accept_idempotent owner=%s member=%s member_record=%s",
                owner.id,
                user.id,
                existing.id,
            )
            return {
                "ok": True,
                "member_id": str(existing.id),
                "allocated_devices": existing.allocated_devices,
            }

    # Ensure owner has family plan
    owner_capacity = await _owner_family_capacity(owner)
    if owner_capacity < _family_limit():
        raise HTTPException(status_code=403, detail="Owner has no family subscription")
    # Ensure owner has remaining allocation at accept time
    total_alloc = await _sum_active_family_allocations(owner.id)
    if total_alloc + invite.allocated_devices > owner_capacity:
        raise HTTPException(status_code=409, detail="Family allocation limit exceeded")

    # Reserve invite usage (anti‑race). Roll back if provisioning fails.
    reserved = await FamilyInvites.filter(
        id=invite.id,
        revoked_at=None,
        used_count__lt=invite.max_uses,
    ).update(used_count=F("used_count") + 1, used_at=_now())
    if reserved == 0:
        raise HTTPException(status_code=409, detail="Invite already used")

    transition_deleted = False
    member: FamilyMembers | None = None
    reactivated = False
    try:
        # Create RemnaWave user if missing
        if not user.remnawave_uuid:
            await user._ensure_remnawave_user()
            user = await Users.get(id=user.id)

        if transition_membership and transition_old_owner:
            await transition_membership.delete()
            transition_deleted = True
            try:
                await _sync_owner_effective_remnawave_limit(transition_old_owner)
            except Exception as exc:
                logger.warning(
                    "family_owner_effective_limit_sync_failed owner=%s err=%s",
                    transition_old_owner.id,
                    exc,
                )
            await _audit(
                owner=transition_old_owner,
                actor=user,
                action="member_switched_out",
                target_id=str(transition_membership.id),
                details={
                    "new_owner_id": int(owner.id),
                    "new_owner_username": owner.username,
                    "previous_allocated_devices": transition_previous_allocated_devices,
                },
            )

        # Update member limit both in local DB and RemnaWave.
        await _sync_user_hwid_limit(user, int(invite.allocated_devices or 0))

        if existing and (existing.status == "disabled" or int(existing.allocated_devices or 0) <= 0):
            existing.allocated_devices = int(invite.allocated_devices or 0)
            existing.status = "active"
            await existing.save(update_fields=["allocated_devices", "status"])
            member = existing
            reactivated = True
        else:
            member = await FamilyMembers.create(
                owner=owner,
                member=user,
                allocated_devices=invite.allocated_devices,
                status="active",
            )
            reactivated = False
        try:
            await _sync_owner_effective_remnawave_limit(owner)
        except Exception as exc:
            logger.warning(
                "family_owner_effective_limit_sync_failed owner=%s err=%s",
                owner.id,
                exc,
            )
        await _audit(
            owner=owner,
            actor=user,
            action="member_reactivated" if reactivated else "member_added",
            target_id=str(member.id),
            details={
                "allocated_devices": int(invite.allocated_devices or 0),
                "previous_allocated_devices": existing_allocated if existing else None,
                "previous_owner_id": int(transition_old_owner.id)
                if transition_old_owner
                else None,
            },
        )
        await _check_anomaly(owner, user, "member_added")
        logger.info(
            "family_invite_accept_success owner=%s member=%s member_record=%s devices=%s reactivated=%s switched=%s",
            owner.id,
            user.id,
            member.id,
            invite.allocated_devices,
            reactivated,
            transition_deleted,
        )
        try:
            await _notify_family_membership_admin_log(
                owner=owner,
                member=user,
                event="member_reactivated" if reactivated else "member_added",
                allocated_devices=int(invite.allocated_devices or 0),
                previous_allocated_devices=existing_allocated if existing else None,
            )
        except Exception as exc:
            logger.warning(
                "family_membership_admin_log_failed owner=%s member=%s event=%s err=%s",
                owner.id,
                user.id,
                "member_reactivated" if reactivated else "member_added",
                exc,
            )
        try:
            await notify_family_owner_member_joined(
                owner=owner,
                member=user,
                allocated_devices=int(invite.allocated_devices or 0),
                reactivated=reactivated,
            )
            await notify_family_member_joined(
                member=user,
                owner=owner,
                allocated_devices=int(invite.allocated_devices or 0),
                reactivated=reactivated,
            )
        except Exception as exc:
            logger.warning(
                "family_join_notifications_failed owner=%s member=%s err=%s",
                owner.id,
                user.id,
                exc,
            )
        return {
            "ok": True,
            "member_id": str(member.id),
            "allocated_devices": member.allocated_devices,
        }
    except Exception as exc:
        logger.warning("family invite accept failed, reverting reservation: %s", exc)
        await FamilyInvites.filter(id=invite.id).update(
            used_count=F("used_count") - 1, used_at=None
        )
        if member is not None:
            try:
                if reactivated and existing is not None:
                    existing.allocated_devices = int(existing_allocated or 0)
                    existing.status = existing_status
                    await existing.save(update_fields=["allocated_devices", "status"])
                else:
                    await FamilyMembers.filter(id=member.id).delete()
            except Exception as cleanup_exc:
                logger.error(
                    "family_transition_cleanup_failed owner=%s member=%s err=%s",
                    owner.id,
                    user.id,
                    cleanup_exc,
                )
        if transition_deleted and transition_membership and transition_old_owner:
            try:
                await FamilyMembers.create(
                    id=transition_membership.id,
                    owner=transition_old_owner,
                    member=user,
                    allocated_devices=int(
                        transition_previous_allocated_devices or 0
                    ),
                    status="active",
                )
                await _sync_user_hwid_limit(
                    user, int(transition_previous_allocated_devices or 0)
                )
                await _sync_owner_effective_remnawave_limit(transition_old_owner)
            except Exception as restore_exc:
                logger.error(
                    "family_transition_restore_failed old_owner=%s member=%s err=%s",
                    transition_old_owner.id,
                    user.id,
                    restore_exc,
                )
        else:
            try:
                restored_personal_limit = await _resolve_personal_device_limit(user)
                await _sync_user_hwid_limit(user, int(restored_personal_limit or 0))
            except Exception as restore_limit_exc:
                logger.error(
                    "family_accept_restore_personal_limit_failed member=%s err=%s",
                    user.id,
                    restore_limit_exc,
                )
        raise HTTPException(
            status_code=502, detail="Failed to provision family member"
        ) from exc


@router.get("/members")
async def list_members(user: Users = Depends(validate)) -> List[Dict[str, Any]]:
    if not await _has_family_owner_access_async(user):
        raise HTTPException(status_code=403, detail="Family subscription required")
    members = await FamilyMembers.filter(
        owner_id=user.id, status="active", allocated_devices__gt=0
    ).prefetch_related("member")
    result = []
    for item in members:
        member_user = item.member
        result.append(
            {
                "id": str(item.id),
                "member_id": member_user.id,
                "username": member_user.username,
                "full_name": member_user.full_name,
                "allocated_devices": item.allocated_devices,
                "status": item.status,
            }
        )
    return result


class MemberLimitPatch(BaseModel):
    allocated_devices: int = Field(ge=0)


@router.patch("/members/{member_id}")
async def update_member(
    member_id: str, payload: MemberLimitPatch, user: Users = Depends(validate)
) -> Dict[str, Any]:
    member = await FamilyMembers.get_or_none(
        id=member_id, owner_id=user.id
    ).prefetch_related("member")
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    owner_quota = await build_family_quota_snapshot(user)
    family_limit = int(owner_quota.family_limit)
    if payload.allocated_devices > family_limit:
        raise HTTPException(
            status_code=400, detail="Allocated devices exceed family limit"
        )
    next_reserved_devices = (
        owner_quota.reserved_devices
        - int(member.allocated_devices or 0)
        + int(payload.allocated_devices or 0)
    )
    if next_reserved_devices > family_limit:
        raise HTTPException(status_code=409, detail="Family allocation limit exceeded")
    previous_allocated_devices = int(member.allocated_devices or 0)
    member.allocated_devices = payload.allocated_devices
    member.status = "disabled" if payload.allocated_devices == 0 else "active"
    await member.save()
    target_user = member.member
    if not target_user.remnawave_uuid:
        await target_user._ensure_remnawave_user()
        target_user = await Users.get(id=target_user.id)
    await _sync_user_hwid_limit(target_user, payload.allocated_devices)
    try:
        await _sync_owner_effective_remnawave_limit(user)
    except Exception as exc:
        logger.warning(
            "family_owner_effective_limit_sync_failed owner=%s err=%s", user.id, exc
        )
    await _audit(
        owner=user,
        actor=user,
        action="member_limit_updated",
        target_id=str(member.id),
        details={
            "allocated_devices": payload.allocated_devices,
            "status": member.status,
        },
    )
    logger.info(
        "family_member_limit_updated owner=%s member=%s member_record=%s devices=%s status=%s",
        user.id,
        target_user.id,
        member.id,
        payload.allocated_devices,
        member.status,
    )
    try:
        await _notify_family_membership_admin_log(
            owner=user,
            member=target_user,
            event="member_limit_updated",
            allocated_devices=int(payload.allocated_devices),
            previous_allocated_devices=previous_allocated_devices,
        )
    except Exception as exc:
        logger.warning(
            "family_membership_admin_log_failed owner=%s member=%s event=%s err=%s",
            user.id,
            target_user.id,
            "member_limit_updated",
            exc,
        )
    try:
        await notify_family_member_limit_updated(
            member=target_user,
            owner=user,
            allocated_devices=int(payload.allocated_devices),
        )
    except Exception as exc:
        logger.warning(
            "family_limit_notification_failed owner=%s member=%s err=%s",
            user.id,
            target_user.id,
            exc,
        )
    return {"ok": True, "allocated_devices": member.allocated_devices}


@router.delete("/members/{member_id}")
async def delete_member(
    member_id: str, user: Users = Depends(validate)
) -> Dict[str, Any]:
    member = await FamilyMembers.get_or_none(
        id=member_id, owner_id=user.id
    ).prefetch_related("member")
    if not member:
        return {"ok": True, "note": "already_deleted"}
    target_user = member.member
    personal_limit = await _resolve_personal_device_limit(target_user)
    await _sync_user_hwid_limit(target_user, personal_limit)
    await member.delete()
    try:
        await _sync_owner_effective_remnawave_limit(user)
    except Exception as exc:
        logger.warning(
            "family_owner_effective_limit_sync_failed owner=%s err=%s", user.id, exc
        )
    await _audit(owner=user, actor=user, action="member_deleted", target_id=member_id)
    logger.info("family_member_deleted owner=%s member_record=%s", user.id, member_id)
    try:
        await _notify_family_membership_admin_log(
            owner=user,
            member=target_user,
            event="member_deleted",
            restored_limit=int(personal_limit or 0),
        )
    except Exception as exc:
        logger.warning(
            "family_membership_admin_log_failed owner=%s member=%s event=%s err=%s",
            user.id,
            target_user.id,
            "member_deleted",
            exc,
        )
    try:
        await notify_family_member_removed(
            member=target_user,
            owner=user,
            removed_by_owner=True,
            restored_limit=int(personal_limit or 0),
        )
    except Exception as exc:
        logger.warning(
            "family_member_removed_notification_failed owner=%s member=%s err=%s",
            user.id,
            target_user.id,
            exc,
        )
    return {"ok": True}


@router.get("/membership")
async def get_membership(user: Users = Depends(validate)) -> Dict[str, Any]:
    # Returns membership details for current user (member role).
    member = await FamilyMembers.get_or_none(
        member_id=user.id, status="active", allocated_devices__gt=0
    ).prefetch_related("owner")
    if not member:
        return {"is_member": False}
    # Self-heal stale member limits created before sync rollout.
    await _sync_user_hwid_limit(user, int(member.allocated_devices or 0))
    owner = member.owner
    owner_expired_at = owner.expired_at.isoformat() if owner.expired_at else None
    owner_is_active = bool(owner.expired_at and owner.expired_at >= _now().date())
    return {
        "is_member": True,
        "id": str(member.id),
        "owner_id": int(owner.id),
        "owner_username": owner.username,
        "owner_full_name": owner.full_name,
        "allocated_devices": int(member.allocated_devices or 0),
        "status": member.status,
        "family_expires_at": owner_expired_at,
        "family_is_active": owner_is_active,
        "can_leave": True,
    }


@router.post("/members/leave")
async def leave_family(user: Users = Depends(validate)) -> Dict[str, Any]:
    member = await FamilyMembers.get_or_none(member_id=user.id).prefetch_related(
        "owner"
    )
    if not member:
        raise HTTPException(status_code=404, detail="Not a family member")
    owner = member.owner
    member_id = str(member.id)
    personal_limit = await _resolve_personal_device_limit(user)
    await _sync_user_hwid_limit(user, personal_limit)
    await member.delete()
    try:
        await _sync_owner_effective_remnawave_limit(owner)
    except Exception as exc:
        logger.warning(
            "family_owner_effective_limit_sync_failed owner=%s err=%s", owner.id, exc
        )
    await _audit(
        owner=owner,
        actor=user,
        action="member_left",
        target_id=member_id,
        details={"restored_hwid_limit": personal_limit},
    )
    logger.info(
        "family_member_left owner=%s member=%s member_record=%s",
        owner.id,
        user.id,
        member_id,
    )
    try:
        await _notify_family_membership_admin_log(
            owner=owner,
            member=user,
            event="member_left",
            restored_limit=int(personal_limit or 0),
        )
    except Exception as exc:
        logger.warning(
            "family_membership_admin_log_failed owner=%s member=%s event=%s err=%s",
            owner.id,
            user.id,
            "member_left",
            exc,
        )
    try:
        await notify_family_member_removed(
            member=user,
            owner=owner,
            removed_by_owner=False,
            restored_limit=int(personal_limit or 0),
        )
    except Exception as exc:
        logger.warning(
            "family_member_left_notification_failed owner=%s member=%s err=%s",
            owner.id,
            user.id,
            exc,
        )
    return {"ok": True}


@router.get("/invites")
async def list_invites(user: Users = Depends(validate)) -> List[Dict[str, Any]]:
    if not await _has_family_owner_access_async(user):
        raise HTTPException(status_code=403, detail="Family subscription required")
    invites = (
        await FamilyInvites.filter(owner_id=user.id, revoked_at=None)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=_now()))
        .filter(used_count__lt=F("max_uses"))
        .order_by("-created_at")
    )
    return [
        {
            "id": str(item.id),
            "allocated_devices": item.allocated_devices,
            "expires_at": item.expires_at,
            "max_uses": item.max_uses,
            "used_count": item.used_count,
            "used_at": item.used_at,
            "revoked_at": item.revoked_at,
            "created_at": item.created_at,
        }
        for item in invites
    ]


@router.post("/invites/{invite_id}/revoke")
async def revoke_invite(
    invite_id: str, user: Users = Depends(validate)
) -> Dict[str, Any]:
    if not await _has_family_owner_access_async(user):
        raise HTTPException(status_code=403, detail="Family subscription required")
    invite = await FamilyInvites.get_or_none(id=invite_id, owner_id=user.id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.revoked_at:
        return {"ok": True, "note": "already_revoked"}
    invite.revoked_at = _now()
    await invite.save()
    await _audit(
        owner=user, actor=user, action="invite_revoked", target_id=str(invite.id)
    )
    await _check_anomaly(user, user, "invite_revoked")
    logger.info("family_invite_revoked owner=%s invite=%s", user.id, invite.id)
    try:
        await _sync_owner_effective_remnawave_limit(user)
    except Exception as exc:
        logger.warning(
            "family_owner_effective_limit_sync_failed owner=%s err=%s",
            user.id,
            exc,
        )
    try:
        await notify_family_owner_invite_revoked(
            owner=user,
            allocated_devices=int(invite.allocated_devices or 0),
        )
    except Exception as exc:
        logger.warning(
            "family_owner_invite_revoke_notification_failed owner=%s invite=%s err=%s",
            user.id,
            invite.id,
            exc,
        )
    return {"ok": True}


class AuditEntry(BaseModel):
    id: str
    owner_id: int
    actor_id: int
    action: str
    target_id: str | None = None
    details: Dict[str, Any] | None = None
    created_at: datetime


@router.get("/audit")
async def list_audit(
    user: Users = Depends(validate),
    limit: int = 100,
    action: Optional[str] = None,
) -> List[AuditEntry]:
    if not await _has_family_owner_access_async(user):
        raise HTTPException(status_code=403, detail="Family subscription required")
    limit = max(1, min(200, int(limit)))
    query = FamilyAuditLogs.filter(owner_id=user.id).order_by("-created_at")
    if action:
        query = query.filter(action=action)
    rows = await query.limit(limit)
    return [
        AuditEntry(
            id=str(item.id),
            owner_id=item.owner_id,
            actor_id=item.actor_id,
            action=item.action,
            target_id=item.target_id,
            details=item.details,
            created_at=item.created_at,
        )
        for item in rows
    ]
