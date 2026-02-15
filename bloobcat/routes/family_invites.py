from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException
from httpx import AsyncClient
from pydantic import BaseModel, Field

from bloobcat.bot.notifications.admin import send_admin_message
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
from bloobcat.routes.remnawave.client import RemnaWaveClient
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
    return hmac.new(_token_secret().encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
            await client.users.update_user(user.remnawave_uuid, hwidDeviceLimit=normalized_limit)
        finally:
            await client.close()


async def _sum_active_family_allocations(owner_id: int) -> int:
    total_alloc = 0
    for member in await FamilyMembers.filter(owner_id=owner_id, status="active", allocated_devices__gt=0):
        total_alloc += int(member.allocated_devices or 0)
    return total_alloc


async def _owner_effective_devices_limit(owner: Users) -> int:
    base_limit = int(owner.hwid_limit or 0)
    if owner.active_tariff_id and _is_subscription_active(owner):
        tariff = await ActiveTariffs.get_or_none(id=owner.active_tariff_id)
        if tariff:
            base_limit = max(base_limit, int(getattr(tariff, "hwid_limit", 0) or 0))
    allocated = await _sum_active_family_allocations(owner.id)
    return max(0, base_limit - allocated)


async def _sync_owner_effective_remnawave_limit(owner: Users) -> None:
    # Keep owner entitlement source in DB intact, but enforce effective remaining
    # device quota in RemnaWave based on active member allocations.
    if not owner.remnawave_uuid:
        return
    effective_limit = await _owner_effective_devices_limit(owner)
    client = RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )
    try:
        await client.users.update_user(owner.remnawave_uuid, hwidDeviceLimit=int(effective_limit))
    finally:
        await client.close()


def _append_startapp_payload(base_url: str, payload: str) -> str:
    parsed = urlsplit(base_url.strip())
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("Invalid base URL")
    query = parsed.query
    next_query = f"{query}&startapp={quote(payload, safe='')}" if query else f"startapp={quote(payload, safe='')}"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), next_query, parsed.fragment))


async def _build_family_invite_link(token: str) -> str:
    payload = f"family_{token}"
    webapp_url = (getattr(telegram_settings, "webapp_url", None) or "").strip()
    if webapp_url:
        try:
            return _append_startapp_payload(webapp_url, payload)
        except Exception:
            logger.warning("Invalid TELEGRAM_WEBAPP_URL for family invite link: {}", webapp_url)
    # Fallback for misconfigured environments.
    from bloobcat.bot.bot import get_bot_username

    try:
        bot_name = await get_bot_username()
    except Exception:
        bot_name = "TriadVPN_bot"
    return f"https://t.me/{bot_name}?start={payload}"


def _family_limit() -> int:
    return int(getattr(app_settings, "family_devices_limit", 10) or 10)


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
    if not user.expired_at:
        return True
    try:
        return user.expired_at >= datetime.now().date()
    except Exception:
        return True


async def _audit(owner: Users, actor: Users, action: str, target_id: str | None = None, details: Dict[str, Any] | None = None) -> None:
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


async def _audit_anomaly(owner: Users, actor: Users, kind: str, details: Dict[str, Any]) -> None:
    await _audit(owner=owner, actor=actor, action="anomaly", target_id=None, details={"kind": kind, **details})
    logger.warning("family_anomaly_detected owner=%s kind=%s details=%s", owner.id, kind, details)


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
    last_block = await FamilyAuditLogs.filter(owner_id=owner.id, action="invite_blocked").order_by("-created_at").first()
    if not last_block or not last_block.details:
        return None
    last_unblock = await FamilyAuditLogs.filter(owner_id=owner.id, action="invite_unblocked").order_by("-created_at").first()
    if last_unblock and last_unblock.created_at and last_block.created_at and last_unblock.created_at >= last_block.created_at:
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
        logger.warning("family_alert_telegram_failed owner=%s kind=%s err=%s", owner.id, kind, exc)

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
        logger.warning("family_alert_webhook_failed owner=%s kind=%s err=%s", owner.id, kind, exc)


async def _maybe_block_invites(owner: Users, actor: Users, kind: str) -> None:
    if await _get_active_invite_block(owner):
        return
    window_start = _now() - timedelta(hours=_anomaly_block_window_hours())
    anomaly_count = await FamilyAuditLogs.filter(owner_id=owner.id, action="anomaly", created_at__gte=window_start).count()
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
        {"blocked_until": blocked_until.isoformat(), "anomaly_count": anomaly_count, "window_hours": _anomaly_block_window_hours()},
    )
    try:
        await notify_family_owner_invites_blocked(
            owner=owner,
            blocked_until=blocked_until,
            reason=f"repeated_anomalies:{kind}",
        )
    except Exception as exc:
        logger.warning("family_owner_block_notification_failed owner=%s err=%s", owner.id, exc)


async def _maybe_notify_owner_invites_unblocked(owner: Users, actor: Users) -> None:
    """
    Sends a one-time "unblocked" message when a previous temporary block has expired.
    """
    last_block = await FamilyAuditLogs.filter(owner_id=owner.id, action="invite_blocked").order_by("-created_at").first()
    if not last_block or not last_block.details:
        return
    last_unblock = await FamilyAuditLogs.filter(owner_id=owner.id, action="invite_unblocked").order_by("-created_at").first()
    if last_unblock and last_block.created_at and last_unblock.created_at and last_unblock.created_at >= last_block.created_at:
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
        logger.warning("family_owner_unblock_notification_failed owner=%s err=%s", owner.id, exc)


async def _check_anomaly(owner: Users, actor: Users, action: str) -> None:
    window_start = _now() - timedelta(hours=1)
    count = await FamilyAuditLogs.filter(owner_id=owner.id, action=action, created_at__gte=window_start).count()
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
    allocated_devices: int = Field(ge=1, le=10)
    ttl_hours: int | None = Field(default=None, ge=1, le=168)


class InviteValidateResponse(BaseModel):
    ok: bool
    expires_at: datetime | None = None


@router.post("/invites")
async def create_invite(payload: InviteCreateRequest, user: Users = Depends(validate)) -> Dict[str, Any]:
    if (user.hwid_limit or 0) < _family_limit():
        raise HTTPException(status_code=403, detail="Family subscription required")
    if not _is_subscription_active(user):
        raise HTTPException(status_code=403, detail="Subscription expired")
    await _maybe_notify_owner_invites_unblocked(owner=user, actor=user)
    blocked_until = await _get_active_invite_block(user)
    if blocked_until:
        retry_after = max(1, int((blocked_until - _now()).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail="Invite creation temporarily blocked",
            headers={"Retry-After": str(retry_after)},
        )
    if payload.allocated_devices > _family_limit():
        raise HTTPException(status_code=400, detail="Allocated devices exceed family limit")
    # enforce total allocations (sum of members + new invite) <= family limit
    total_alloc = 0
    for m in await FamilyMembers.filter(owner_id=user.id):
        total_alloc += int(m.allocated_devices or 0)
    if total_alloc + payload.allocated_devices > _family_limit():
        raise HTTPException(status_code=409, detail="Family allocation limit exceeded")
    # cooldown: минимальный интервал между созданием инвайтов
    last_invite = await FamilyInvites.filter(owner_id=user.id).order_by("-created_at").first()
    if last_invite and last_invite.created_at:
        seconds_since_last = max(0, int((_now() - last_invite.created_at).total_seconds()))
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
    created_recent = await FamilyInvites.filter(owner_id=user.id, created_at__gte=window_start).count()
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
        created_hard = await FamilyInvites.filter(owner_id=user.id, created_at__gte=hard_window_start).count()
        if created_hard >= _invite_rate_limit_per_hour() * hard_multiplier:
            retry_after = int((_now() - hard_window_start).total_seconds())
            retry_after = max(1, 3600 * hard_multiplier - retry_after)
            raise HTTPException(
                status_code=429,
                detail="Invite creation temporarily blocked",
                headers={"Retry-After": str(retry_after)},
            )
    # cap active invites to avoid abuse
    active_count = await FamilyInvites.filter(
        owner_id=user.id,
        revoked_at=None,
        used_count__lt=F("max_uses"),
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gte=_now())).count()
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
        details={"allocated_devices": payload.allocated_devices, "expires_at": invite.expires_at.isoformat() if invite.expires_at else None},
    )
    await _check_anomaly(user, user, "invite_created")
    logger.info(
        "family_invite_created owner=%s invite=%s devices=%s expires_at=%s",
        user.id,
        invite.id,
        payload.allocated_devices,
        invite.expires_at,
    )
    return {
        "id": str(invite.id),
        "token": token,
        "invite_url": await _build_family_invite_link(token),
        "expires_at": invite.expires_at,
    }


@router.get("/invites/{token}")
async def validate_invite(token: str) -> InviteValidateResponse:
    token_hash = _hash_token(token)
    invite = await FamilyInvites.get_or_none(token_hash=token_hash)
    if not invite or invite.revoked_at:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.expires_at and invite.expires_at < _now():
        raise HTTPException(status_code=410, detail="Invite expired")
    if invite.used_count >= invite.max_uses:
        raise HTTPException(status_code=409, detail="Invite already used")
    return InviteValidateResponse(ok=True, expires_at=invite.expires_at)


@router.post("/invites/{token}/accept")
async def accept_invite(token: str, user: Users = Depends(validate)) -> Dict[str, Any]:
    token_hash = _hash_token(token)
    invite = await FamilyInvites.get_or_none(token_hash=token_hash)
    if not invite or invite.revoked_at:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.expires_at and invite.expires_at < _now():
        raise HTTPException(status_code=410, detail="Invite expired")
    if invite.used_count >= invite.max_uses:
        raise HTTPException(status_code=409, detail="Invite already used")

    owner = await Users.get(id=invite.owner_id)
    if owner.id == user.id:
        raise HTTPException(status_code=400, detail="Owner cannot accept own invite")
    if not _is_subscription_active(owner):
        raise HTTPException(status_code=403, detail="Owner subscription expired")

    # ensure member isn't already in another family
    member_family = await FamilyMembers.get_or_none(member_id=user.id, status="active", allocated_devices__gt=0)
    if member_family and member_family.owner_id != owner.id:
        raise HTTPException(status_code=409, detail="Member already in another family")

    existing = await FamilyMembers.get_or_none(owner_id=owner.id, member_id=user.id)
    if existing:
        existing_allocated = int(existing.allocated_devices or 0)
        should_reactivate = existing.status == "disabled" or existing_allocated <= 0
        if should_reactivate:
            total_alloc = await _sum_active_family_allocations(owner.id)
            total_alloc = total_alloc - existing_allocated + int(invite.allocated_devices or 0)
            if total_alloc > _family_limit():
                raise HTTPException(status_code=409, detail="Family allocation limit exceeded")
            if not user.remnawave_uuid:
                await user._ensure_remnawave_user()
                user = await Users.get(id=user.id)
            await _sync_user_hwid_limit(user, int(invite.allocated_devices or 0))
            existing.allocated_devices = int(invite.allocated_devices or 0)
            existing.status = "active"
            await existing.save(update_fields=["allocated_devices", "status"])
            try:
                await _sync_owner_effective_remnawave_limit(owner)
            except Exception as exc:
                logger.warning("family_owner_effective_limit_sync_failed owner=%s err=%s", owner.id, exc)
            await _audit(
                owner=owner,
                actor=user,
                action="member_reactivated",
                target_id=str(existing.id),
                details={"allocated_devices": existing.allocated_devices, "previous_allocated_devices": existing_allocated},
            )
            logger.info(
                "family_invite_accept_reactivated owner=%s member=%s member_record=%s devices=%s",
                owner.id,
                user.id,
                existing.id,
                existing.allocated_devices,
            )
            try:
                await notify_family_owner_member_joined(
                    owner=owner,
                    member=user,
                    allocated_devices=int(existing.allocated_devices or 0),
                    reactivated=True,
                )
                await notify_family_member_joined(
                    member=user,
                    owner=owner,
                    allocated_devices=int(existing.allocated_devices or 0),
                    reactivated=True,
                )
            except Exception as exc:
                logger.warning("family_join_notifications_failed owner=%s member=%s err=%s", owner.id, user.id, exc)
            return {
                "ok": True,
                "member_id": str(existing.id),
                "allocated_devices": existing.allocated_devices,
            }
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
    if (owner.hwid_limit or 0) < _family_limit():
        raise HTTPException(status_code=403, detail="Owner has no family subscription")
    # Ensure owner has remaining allocation at accept time
    total_alloc = await _sum_active_family_allocations(owner.id)
    if total_alloc + invite.allocated_devices > _family_limit():
        raise HTTPException(status_code=409, detail="Family allocation limit exceeded")

    # Reserve invite usage (anti‑race). Roll back if provisioning fails.
    reserved = await FamilyInvites.filter(
        id=invite.id,
        revoked_at=None,
        used_count__lt=invite.max_uses,
    ).update(used_count=F("used_count") + 1, used_at=_now())
    if reserved == 0:
        raise HTTPException(status_code=409, detail="Invite already used")

    try:
        # Create RemnaWave user if missing
        if not user.remnawave_uuid:
            await user._ensure_remnawave_user()
            user = await Users.get(id=user.id)

        # Update member limit both in local DB and RemnaWave.
        await _sync_user_hwid_limit(user, invite.allocated_devices)

        member = await FamilyMembers.create(
            owner=owner,
            member=user,
            allocated_devices=invite.allocated_devices,
            status="active",
        )
        try:
            await _sync_owner_effective_remnawave_limit(owner)
        except Exception as exc:
            logger.warning("family_owner_effective_limit_sync_failed owner=%s err=%s", owner.id, exc)
        await _audit(
            owner=owner,
            actor=user,
            action="member_added",
            target_id=str(member.id),
            details={"allocated_devices": invite.allocated_devices},
        )
        await _check_anomaly(owner, user, "member_added")
        logger.info(
            "family_invite_accept_success owner=%s member=%s member_record=%s devices=%s",
            owner.id,
            user.id,
            member.id,
            invite.allocated_devices,
        )
        try:
            await notify_family_owner_member_joined(
                owner=owner,
                member=user,
                allocated_devices=int(invite.allocated_devices or 0),
                reactivated=False,
            )
            await notify_family_member_joined(
                member=user,
                owner=owner,
                allocated_devices=int(invite.allocated_devices or 0),
                reactivated=False,
            )
        except Exception as exc:
            logger.warning("family_join_notifications_failed owner=%s member=%s err=%s", owner.id, user.id, exc)
        return {
            "ok": True,
            "member_id": str(member.id),
            "allocated_devices": member.allocated_devices,
        }
    except Exception as exc:
        logger.warning("family invite accept failed, reverting reservation: %s", exc)
        await FamilyInvites.filter(id=invite.id).update(used_count=F("used_count") - 1, used_at=None)
        raise HTTPException(status_code=502, detail="Failed to provision family member") from exc


@router.get("/members")
async def list_members(user: Users = Depends(validate)) -> List[Dict[str, Any]]:
    if (user.hwid_limit or 0) < _family_limit():
        raise HTTPException(status_code=403, detail="Family subscription required")
    members = await FamilyMembers.filter(owner_id=user.id, status="active", allocated_devices__gt=0).prefetch_related("member")
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
    allocated_devices: int = Field(ge=0, le=10)


@router.patch("/members/{member_id}")
async def update_member(member_id: str, payload: MemberLimitPatch, user: Users = Depends(validate)) -> Dict[str, Any]:
    member = await FamilyMembers.get_or_none(id=member_id, owner_id=user.id).prefetch_related("member")
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    family_limit = _family_limit()
    if payload.allocated_devices > family_limit:
        raise HTTPException(status_code=400, detail="Allocated devices exceed family limit")
    total_alloc = await _sum_active_family_allocations(user.id)
    total_alloc = total_alloc - int(member.allocated_devices or 0) + payload.allocated_devices
    if total_alloc > family_limit:
        raise HTTPException(status_code=409, detail="Family allocation limit exceeded")
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
        logger.warning("family_owner_effective_limit_sync_failed owner=%s err=%s", user.id, exc)
    await _audit(
        owner=user,
        actor=user,
        action="member_limit_updated",
        target_id=str(member.id),
        details={"allocated_devices": payload.allocated_devices, "status": member.status},
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
        await notify_family_member_limit_updated(
            member=target_user,
            owner=user,
            allocated_devices=int(payload.allocated_devices),
        )
    except Exception as exc:
        logger.warning("family_limit_notification_failed owner=%s member=%s err=%s", user.id, target_user.id, exc)
    return {"ok": True, "allocated_devices": member.allocated_devices}


@router.delete("/members/{member_id}")
async def delete_member(member_id: str, user: Users = Depends(validate)) -> Dict[str, Any]:
    member = await FamilyMembers.get_or_none(id=member_id, owner_id=user.id).prefetch_related("member")
    if not member:
        return {"ok": True, "note": "already_deleted"}
    target_user = member.member
    personal_limit = await _resolve_personal_device_limit(target_user)
    await _sync_user_hwid_limit(target_user, personal_limit)
    await member.delete()
    try:
        await _sync_owner_effective_remnawave_limit(user)
    except Exception as exc:
        logger.warning("family_owner_effective_limit_sync_failed owner=%s err=%s", user.id, exc)
    await _audit(owner=user, actor=user, action="member_deleted", target_id=member_id)
    logger.info("family_member_deleted owner=%s member_record=%s", user.id, member_id)
    try:
        await notify_family_member_removed(
            member=target_user,
            owner=user,
            removed_by_owner=True,
            restored_limit=int(personal_limit or 0),
        )
    except Exception as exc:
        logger.warning("family_member_removed_notification_failed owner=%s member=%s err=%s", user.id, target_user.id, exc)
    return {"ok": True}


@router.get("/membership")
async def get_membership(user: Users = Depends(validate)) -> Dict[str, Any]:
    # Returns membership details for current user (member role).
    member = await FamilyMembers.get_or_none(member_id=user.id, status="active", allocated_devices__gt=0).prefetch_related("owner")
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
    member = await FamilyMembers.get_or_none(member_id=user.id).prefetch_related("owner")
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
        logger.warning("family_owner_effective_limit_sync_failed owner=%s err=%s", owner.id, exc)
    await _audit(
        owner=owner,
        actor=user,
        action="member_left",
        target_id=member_id,
        details={"restored_hwid_limit": personal_limit},
    )
    logger.info("family_member_left owner=%s member=%s member_record=%s", owner.id, user.id, member_id)
    try:
        await notify_family_member_removed(
            member=user,
            owner=owner,
            removed_by_owner=False,
            restored_limit=int(personal_limit or 0),
        )
    except Exception as exc:
        logger.warning("family_member_left_notification_failed owner=%s member=%s err=%s", owner.id, user.id, exc)
    return {"ok": True}


@router.get("/invites")
async def list_invites(user: Users = Depends(validate)) -> List[Dict[str, Any]]:
    if (user.hwid_limit or 0) < _family_limit():
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
async def revoke_invite(invite_id: str, user: Users = Depends(validate)) -> Dict[str, Any]:
    if (user.hwid_limit or 0) < _family_limit():
        raise HTTPException(status_code=403, detail="Family subscription required")
    invite = await FamilyInvites.get_or_none(id=invite_id, owner_id=user.id)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.revoked_at:
        return {"ok": True, "note": "already_revoked"}
    invite.revoked_at = _now()
    await invite.save()
    await _audit(owner=user, actor=user, action="invite_revoked", target_id=str(invite.id))
    await _check_anomaly(user, user, "invite_revoked")
    logger.info("family_invite_revoked owner=%s invite=%s", user.id, invite.id)
    try:
        await notify_family_owner_invite_revoked(
            owner=user,
            allocated_devices=int(invite.allocated_devices or 0),
        )
    except Exception as exc:
        logger.warning("family_owner_invite_revoke_notification_failed owner=%s invite=%s err=%s", user.id, invite.id, exc)
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
    if (user.hwid_limit or 0) < _family_limit():
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
