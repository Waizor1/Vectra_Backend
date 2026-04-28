from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from tortoise.expressions import F, Q

from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.family_invites import FamilyInvites
from bloobcat.db.family_members import FamilyMembers
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.routes.remnawave.hwid_utils import count_active_devices
from bloobcat.settings import app_settings, remnawave_settings
from bloobcat.services.subscription_limits import family_devices_threshold

logger = get_logger("routes.family_quota")


@dataclass(frozen=True, slots=True)
class FamilyAllocationSummary:
    member_allocated_devices: int
    invite_reserved_devices: int
    active_members_count: int
    active_invites_count: int


@dataclass(frozen=True, slots=True)
class FamilyQuotaSnapshot:
    family_limit: int
    owner_base_devices_limit: int
    owner_connected_devices: int
    member_allocated_devices: int
    invite_reserved_devices: int
    reserved_devices: int
    available_devices: int
    owner_quota_limit: int
    active_members_count: int
    active_invites_count: int


def family_devices_limit() -> int:
    # Compatibility name: minimum devices that unlock family features.
    return family_devices_threshold()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_subscription_active(user: Users) -> bool:
    if not user.expired_at:
        return True
    try:
        return user.expired_at >= datetime.now().date()
    except Exception:
        return True


async def resolve_owner_base_devices_limit(owner: Users) -> int:
    base_limit = 1
    if owner.active_tariff_id and is_subscription_active(owner):
        tariff = await ActiveTariffs.get_or_none(id=owner.active_tariff_id)
        if tariff:
            base_limit = max(base_limit, int(getattr(tariff, "hwid_limit", 0) or 0))
    if owner.hwid_limit is not None:
        base_limit = int(owner.hwid_limit or 0)
    return max(1, base_limit)


async def get_family_allocation_summary(
    owner_id: int,
    *,
    now: datetime | None = None,
) -> FamilyAllocationSummary:
    effective_now = now or utc_now()
    active_members = await FamilyMembers.filter(
        owner_id=owner_id,
        status="active",
        allocated_devices__gt=0,
    )
    active_invites = (
        await FamilyInvites.filter(owner_id=owner_id, revoked_at=None)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=effective_now))
        .filter(used_count__lt=F("max_uses"))
    )
    member_allocated_devices = sum(
        int(member.allocated_devices or 0) for member in active_members
    )
    invite_reserved_devices = sum(
        int(invite.allocated_devices or 0) for invite in active_invites
    )
    return FamilyAllocationSummary(
        member_allocated_devices=member_allocated_devices,
        invite_reserved_devices=invite_reserved_devices,
        active_members_count=len(active_members),
        active_invites_count=len(active_invites),
    )


async def get_owner_connected_devices_count(owner: Users) -> int:
    if not owner.remnawave_uuid:
        return 0
    client = RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )
    try:
        raw_resp = await client.users.get_user_hwid_devices(str(owner.remnawave_uuid))
        return count_active_devices(raw_resp)
    except Exception as exc:
        logger.warning(
            "family_owner_connected_devices_fetch_failed owner=%s err=%s",
            owner.id,
            exc,
        )
        return 0
    finally:
        await client.close()


def compute_owner_quota_limit(
    *,
    family_limit: int,
    member_allocated_devices: int,
    invite_reserved_devices: int,
) -> int:
    return max(
        0,
        int(family_limit)
        - int(member_allocated_devices or 0)
        - int(invite_reserved_devices or 0),
    )


def compute_reserved_devices(
    *,
    owner_connected_devices: int,
    member_allocated_devices: int,
    invite_reserved_devices: int,
) -> int:
    return max(0, int(owner_connected_devices or 0)) + max(
        0, int(member_allocated_devices or 0)
    ) + max(0, int(invite_reserved_devices or 0))


async def build_family_quota_snapshot(
    owner: Users,
    *,
    owner_connected_devices: int | None = None,
    owner_base_devices_limit: int | None = None,
    now: datetime | None = None,
) -> FamilyQuotaSnapshot:
    allocation_summary = await get_family_allocation_summary(owner.id, now=now)
    effective_owner_connected_devices = (
        max(0, int(owner_connected_devices or 0))
        if owner_connected_devices is not None
        else await get_owner_connected_devices_count(owner)
    )
    effective_owner_base_devices_limit = (
        max(1, int(owner_base_devices_limit or 0))
        if owner_base_devices_limit is not None
        else await resolve_owner_base_devices_limit(owner)
    )
    effective_family_limit = max(family_devices_threshold(), effective_owner_base_devices_limit)
    owner_quota_limit = compute_owner_quota_limit(
        family_limit=effective_family_limit,
        member_allocated_devices=allocation_summary.member_allocated_devices,
        invite_reserved_devices=allocation_summary.invite_reserved_devices,
    )
    reserved_devices = compute_reserved_devices(
        owner_connected_devices=effective_owner_connected_devices,
        member_allocated_devices=allocation_summary.member_allocated_devices,
        invite_reserved_devices=allocation_summary.invite_reserved_devices,
    )
    return FamilyQuotaSnapshot(
        family_limit=effective_family_limit,
        owner_base_devices_limit=effective_owner_base_devices_limit,
        owner_connected_devices=effective_owner_connected_devices,
        member_allocated_devices=allocation_summary.member_allocated_devices,
        invite_reserved_devices=allocation_summary.invite_reserved_devices,
        reserved_devices=reserved_devices,
        available_devices=max(0, effective_family_limit - reserved_devices),
        owner_quota_limit=owner_quota_limit,
        active_members_count=allocation_summary.active_members_count,
        active_invites_count=allocation_summary.active_invites_count,
    )
