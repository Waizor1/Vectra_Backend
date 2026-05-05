"""Service layer for the optional device-per-user inventory.

The current legacy flow keeps working while the feature flag is off.  When it is
on, each newly-added device can get a dedicated RemnaWave user with
``hwidDeviceLimit=1`` while existing legacy HWIDs stay readable/deletable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from bloobcat.db.user_devices import DeviceKind, UserDevice
from bloobcat.logger import get_logger

logger = get_logger("device_service")
_DEVICE_SCOPE_LOCKS: dict[str, asyncio.Lock] = {}
_DEVICE_NAME_PLACEHOLDERS = {"Неизвестное устройство", "Временная подписка", "Unknown device", "Unknown Device"}


@dataclass(slots=True)
class DeviceInventoryContext:
    actor_user: Any
    owner: Any
    family_member: Any | None
    legacy_user: Any | None
    effective_limit: int
    base_limit: int
    delegated_devices: int
    subscription_active: bool


def _rw_client():
    from bloobcat.routes.remnawave.client import RemnaWaveClient
    from bloobcat.settings import remnawave_settings

    return RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )


def owner_device_operation_lock(owner_id: int) -> asyncio.Lock:
    key = f"owner:{owner_id}"
    lock = _DEVICE_SCOPE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _DEVICE_SCOPE_LOCKS[key] = lock
    return lock


def is_device_per_user_enabled_for_context(user: Any, context: DeviceInventoryContext) -> bool:
    """Return the rollout flag for the actor's effective subscription context."""

    owner = getattr(context, "owner", None)
    if context.family_member:
        return bool(owner and owner.is_device_per_user_enabled())
    return bool(user.is_device_per_user_enabled())


def _normalize_hwid(item: dict[str, Any]) -> str | None:
    hwid = item.get("hwid") or item.get("deviceId") or item.get("id")
    if hwid is None:
        return None
    text = str(hwid).strip()
    return text or None


def _subscription_active(expired_at: Any) -> bool:
    if not expired_at:
        return False
    try:
        if isinstance(expired_at, datetime):
            return expired_at.date() >= date.today()
        return expired_at >= date.today()
    except Exception:
        return False


async def _base_devices_limit(user: Any) -> int:
    from bloobcat.db.active_tariff import ActiveTariffs

    if getattr(user, "hwid_limit", None) is not None:
        return max(1, int(user.hwid_limit or 1))
    active_tariff_id = getattr(user, "active_tariff_id", None)
    if active_tariff_id:
        tariff = await ActiveTariffs.get_or_none(id=active_tariff_id)
        if tariff and getattr(tariff, "hwid_limit", None) is not None:
            return max(1, int(tariff.hwid_limit or 1))
    return 1


async def resolve_device_inventory_context(user: Any) -> DeviceInventoryContext:
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.users import Users
    from bloobcat.routes.family_quota import build_family_quota_snapshot
    from bloobcat.services.subscription_limits import family_devices_threshold

    membership = await FamilyMembers.get_or_none(
        member_id=user.id,
        status="active",
        allocated_devices__gt=0,
    ).prefetch_related("owner")
    if membership:
        owner = membership.owner
        return DeviceInventoryContext(
            actor_user=user,
            owner=owner,
            family_member=membership,
            legacy_user=user,
            effective_limit=max(0, int(membership.allocated_devices or 0)),
            base_limit=max(0, int(membership.allocated_devices or 0)),
            delegated_devices=0,
            subscription_active=_subscription_active(owner.expired_at),
        )

    owner = await Users.get_or_none(id=user.id) or user
    base_limit = await _base_devices_limit(owner)
    effective_limit = base_limit
    delegated_devices = 0
    if _subscription_active(getattr(owner, "expired_at", None)) and base_limit >= family_devices_threshold():
        try:
            quota = await build_family_quota_snapshot(owner, owner_base_devices_limit=base_limit)
            effective_limit = max(0, int(quota.owner_quota_limit))
            delegated_devices = max(0, int(quota.reserved_devices))
        except Exception as exc:
            logger.warning("family quota snapshot failed for device inventory owner=%s: %s", owner.id, exc)
    return DeviceInventoryContext(
        actor_user=user,
        owner=owner,
        family_member=None,
        legacy_user=owner,
        effective_limit=max(0, int(effective_limit or 0)),
        base_limit=base_limit,
        delegated_devices=delegated_devices,
        subscription_active=_subscription_active(getattr(owner, "expired_at", None)),
    )


async def _list_legacy_hwid_items(legacy_user: Any | None) -> list[dict[str, Any]]:
    if not legacy_user or not getattr(legacy_user, "remnawave_uuid", None):
        return []
    from bloobcat.routes.remnawave.hwid_utils import list_user_hwid_devices

    try:
        return await list_user_hwid_devices(str(legacy_user.remnawave_uuid))
    except Exception as exc:
        logger.warning("Failed to fetch legacy HWIDs for user=%s: %s", getattr(legacy_user, "id", None), exc)
        return []


def _build_limit_state(
    *,
    effective_limit: int,
    legacy_hwid_count: int,
    device_user_count: int,
    subscription_active: bool,
    base_limit: int,
    delegated_devices: int,
) -> dict[str, Any]:
    used_total = legacy_hwid_count + device_user_count
    available_slots = max(0, effective_limit - used_total)
    over_limit_by = max(0, used_total - effective_limit)
    blocked_reason: str | None = None
    if not subscription_active:
        blocked_reason = "subscription_expired"
    elif over_limit_by > 0:
        blocked_reason = "over_limit_existing_devices"
    elif used_total >= effective_limit:
        if legacy_hwid_count > 0 and delegated_devices > 0:
            blocked_reason = "mixed_legacy_and_delegated_slots"
        elif legacy_hwid_count > 0:
            blocked_reason = "mixed_legacy_slots_occupied"
        elif delegated_devices > 0:
            blocked_reason = "family_delegation_consumes_slots"
        else:
            blocked_reason = "devices_limit_exceeded"
    recommended_legacy_hwid_limit = max(
        legacy_hwid_count,
        max(0, effective_limit - device_user_count),
    )
    return {
        "effective_limit": int(effective_limit),
        "base_limit": int(base_limit),
        "delegated_devices": int(delegated_devices),
        "legacy_hwid_count": int(legacy_hwid_count),
        "device_user_count": int(device_user_count),
        "used_total": int(used_total),
        "available_slots": int(available_slots),
        "over_limit_by": int(over_limit_by),
        "blocked_reason": blocked_reason,
        "can_add": bool(subscription_active and used_total < effective_limit),
        "recommended_legacy_hwid_limit": int(recommended_legacy_hwid_limit),
    }


async def device_limit_state_for_context(context: DeviceInventoryContext) -> dict[str, Any]:
    legacy_items = await _list_legacy_hwid_items(context.legacy_user)
    device_user_count = await UserDevice.filter(
        user_id=context.owner.id,
        family_member_id=getattr(context.family_member, "id", None),
        kind=DeviceKind.DEVICE_USER,
    ).count()
    return _build_limit_state(
        effective_limit=context.effective_limit,
        legacy_hwid_count=len({_normalize_hwid(item) for item in legacy_items if _normalize_hwid(item)}),
        device_user_count=device_user_count,
        subscription_active=context.subscription_active,
        base_limit=context.base_limit,
        delegated_devices=context.delegated_devices,
    )


async def owner_device_limit_state(user: Any) -> dict[str, Any]:
    return await device_limit_state_for_context(await resolve_device_inventory_context(user))


async def get_user_add_device_state(user: Any) -> dict[str, Any]:
    context = await resolve_device_inventory_context(user)
    feature_enabled = is_device_per_user_enabled_for_context(user, context)
    if not feature_enabled:
        return {
            "device_per_user_enabled": False,
            "can_add_device": False,
            "device_add_block_reason": "feature_disabled",
        }
    state = await device_limit_state_for_context(context)
    return {
        "device_per_user_enabled": True,
        "can_add_device": bool(state["can_add"]),
        "device_add_block_reason": state["blocked_reason"],
    }


def _make_device_username(owner: Any, device_id: int, family_member: Any | None) -> str:
    from bloobcat.db.users import (
        REMNAWAVE_USERNAME_MAX_LENGTH,
        REMNAWAVE_USERNAME_PREFIX,
        build_vectra_remnawave_username,
    )
    from bloobcat.settings import test_mode

    owner_id = str(getattr(owner, "id", "0"))
    suffix = "T" if test_mode else ""
    raw_budget = REMNAWAVE_USERNAME_MAX_LENGTH - len(REMNAWAVE_USERNAME_PREFIX)
    if family_member is not None:
        token = str(getattr(family_member, "id", ""))[:8]
        candidate = f"u{owner_id}_f{token}_{device_id}{suffix}"
    else:
        candidate = f"u{owner_id}_{device_id}{suffix}"
    if len(candidate) <= raw_budget:
        return build_vectra_remnawave_username(candidate)
    short_owner = owner_id[-10:]
    if family_member is not None:
        token = str(getattr(family_member, "id", ""))[:8]
        candidate = f"u{short_owner}_f{token}_{device_id}{suffix}"
    else:
        candidate = f"u{short_owner}_{device_id}{suffix}"
    return build_vectra_remnawave_username(candidate[:raw_budget])


async def _collect_internal_squads(owner: Any, family_member: Any | None = None) -> list[str]:
    from bloobcat.db.active_tariff import ActiveTariffs
    from bloobcat.settings import remnawave_settings

    squads: list[str] = []
    if remnawave_settings.default_internal_squad_uuid:
        squads.append(remnawave_settings.default_internal_squad_uuid)
    lte_uuid = remnawave_settings.lte_internal_squad_uuid
    if not lte_uuid:
        return squads
    lte_allowed = False
    if family_member is not None:
        # Current Vectra family memberships do not carry their own LTE bucket.
        # Keep LTE scoped to the owner/base subscription instead of importing the
        # archive family model wholesale.
        lte_allowed = False
    elif getattr(owner, "is_trial", False):
        lte_allowed = True
    elif getattr(owner, "active_tariff_id", None):
        tariff = await ActiveTariffs.get_or_none(id=owner.active_tariff_id)
        effective_lte_total = owner.lte_gb_total if owner.lte_gb_total is not None else (tariff.lte_gb_total or 0 if tariff else 0)
        effective_lte_used = tariff.lte_gb_used if tariff else 0
        lte_allowed = (effective_lte_total or 0) > (effective_lte_used or 0)
    if lte_allowed:
        squads.append(lte_uuid)
    return squads


async def create_device_user(user: Any, *, name: str | None = None) -> UserDevice:
    from bloobcat.settings import remnawave_settings

    context = await resolve_device_inventory_context(user)
    display_name = (name or "").strip()[:128] or None
    device = await UserDevice.create(
        user_id=context.owner.id,
        family_member_id=getattr(context.family_member, "id", None),
        kind=DeviceKind.DEVICE_USER,
        device_name=display_name,
    )
    rw = _rw_client()
    try:
        username = _make_device_username(context.owner, device.id, context.family_member)
        response = await rw.users.create_user(
            username=username,
            expire_at=getattr(context.owner, "expired_at", None) or date.today(),
            hwid_device_limit=1,
            active_internal_squads=(await _collect_internal_squads(context.owner, context.family_member)) or None,
            external_squad_uuid=remnawave_settings.default_external_squad_uuid,
            description=f"Device of {context.owner.id} / {display_name or 'unnamed'}",
        )
        device.remnawave_uuid = (response.get("response") or {}).get("uuid")
        if not device.remnawave_uuid:
            raise ValueError("RemnaWave create_user did not return uuid")
        await device.save(update_fields=["remnawave_uuid"])
        await sync_legacy_hwid_limit_for_context(context)
    except Exception:
        try:
            await device.delete()
        except Exception as del_exc:  # pragma: no cover
            logger.error("Failed to roll back device row %s: %s", device.id, del_exc)
        raise
    finally:
        await rw.close()
    return device


async def delete_device(device: UserDevice) -> None:
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.users import Users

    owner = await Users.get_or_none(id=device.user_id)
    family_member = await FamilyMembers.get_or_none(id=device.family_member_id) if device.family_member_id else None
    if device.kind == DeviceKind.DEVICE_USER and device.remnawave_uuid:
        rw = _rw_client()
        try:
            try:
                await rw.users.delete_user(str(device.remnawave_uuid))
            except Exception as exc:
                text = str(exc)
                if "User not found" not in text and "A039" not in text and "Delete user error" not in text:
                    raise
        finally:
            await rw.close()
    elif device.kind == DeviceKind.LEGACY_HWID and owner and device.hwid:
        legacy_user = await _legacy_user_for(owner, family_member)
        if legacy_user and getattr(legacy_user, "remnawave_uuid", None):
            await delete_legacy_hwid(str(legacy_user.remnawave_uuid), str(device.hwid))
    await device.delete()
    if owner:
        await sync_legacy_hwid_limit_for(owner, family_member)


async def _legacy_user_for(owner: Any, family_member: Any | None) -> Any | None:
    if family_member is None:
        return owner
    from bloobcat.db.users import Users

    return await Users.get_or_none(id=family_member.member_id)


async def delete_legacy_hwid(user_uuid: str, hwid: str) -> bool:
    rw = _rw_client()
    try:
        try:
            await rw.users.delete_user_hwid_device(str(user_uuid), hwid)
            return True
        except Exception as exc:
            text = str(exc)
            if "A101" in text or "Delete hwid user device error" in text:
                return True
            raise
    finally:
        await rw.close()


async def sync_legacy_hwid_limit_for_context(context: DeviceInventoryContext) -> None:
    await sync_legacy_hwid_limit_for(context.owner, context.family_member)


async def sync_legacy_hwid_limit_for(owner: Any, family_member: Any | None = None) -> None:
    legacy_user = await _legacy_user_for(owner, family_member)
    if not legacy_user or not getattr(legacy_user, "remnawave_uuid", None):
        return
    context = await resolve_device_inventory_context(legacy_user if family_member else owner)
    state = await device_limit_state_for_context(context)
    update_params: dict[str, Any] = {
        "hwidDeviceLimit": int(state["recommended_legacy_hwid_limit"]),
    }
    if getattr(owner, "expired_at", None):
        update_params["expireAt"] = owner.expired_at
    rw = _rw_client()
    try:
        await rw.users.update_user(str(legacy_user.remnawave_uuid), **update_params)
    except Exception as exc:
        logger.warning("Failed to sync legacy hwid limit for user=%s: %s", getattr(legacy_user, "id", None), exc)
    finally:
        await rw.close()


async def sync_device_entitlements(user: Any) -> None:
    """Synchronize RemnaWave state owned by the device-per-user layer.

    The legacy RemnaWave user still exists for compatibility/legacy HWIDs, but
    its HWID limit should become the remaining non-device-user slots. Dedicated
    device-users also inherit the owner's expiration date.
    """

    await cascade_expire_at(user)
    await sync_legacy_hwid_limit_for(user)
    try:
        from bloobcat.db.family_members import FamilyMembers

        members = await FamilyMembers.filter(
            owner_id=user.id,
            status="active",
            allocated_devices__gt=0,
        ).all()
        for member in members:
            await sync_legacy_hwid_limit_for(user, member)
    except Exception as exc:
        logger.warning("Failed to sync family member legacy entitlements for owner=%s: %s", getattr(user, "id", None), exc)


async def count_device_users_for_family_member(member: Any) -> int:
    return await UserDevice.filter(
        user_id=member.owner_id,
        family_member_id=member.id,
        kind=DeviceKind.DEVICE_USER,
    ).count()


async def count_used_devices_for_family_member(member: Any) -> int:
    legacy_user = await _legacy_user_for(None, member)
    legacy_items = await _list_legacy_hwid_items(legacy_user)
    legacy_count = len({_normalize_hwid(item) for item in legacy_items if _normalize_hwid(item)})
    return legacy_count + await count_device_users_for_family_member(member)


async def cascade_expire_at(user: Any) -> None:
    if not getattr(user, "expired_at", None):
        return
    devices = await UserDevice.filter(user_id=user.id, kind=DeviceKind.DEVICE_USER, remnawave_uuid__isnull=False).all()
    if not devices:
        return
    rw = _rw_client()
    try:
        await asyncio.gather(
            *[rw.users.update_user(str(d.remnawave_uuid), expireAt=user.expired_at) for d in devices],
            return_exceptions=True,
        )
    finally:
        await rw.close()


async def cascade_delete_family_member_devices(member: Any) -> int:
    devices = await UserDevice.filter(family_member_id=member.id).all()
    deleted = 0
    for device in devices:
        try:
            await delete_device(device)
            deleted += 1
        except Exception as exc:
            logger.warning("Failed to delete family member device id=%s member=%s: %s", device.id, member.id, exc)
    return deleted


async def cascade_delete(user: Any) -> None:
    devices = await UserDevice.filter(user_id=user.id).all()
    for device in devices:
        try:
            await delete_device(device)
        except Exception as exc:
            logger.warning("Failed to cascade delete device id=%s owner=%s: %s", device.id, user.id, exc)


async def fetch_first_hwid_device_info(device: UserDevice) -> dict[str, Any] | None:
    if not device.remnawave_uuid:
        return None
    from bloobcat.routes.remnawave.hwid_utils import list_user_hwid_devices

    try:
        for item in await list_user_hwid_devices(str(device.remnawave_uuid)):
            if isinstance(item, dict) and _normalize_hwid(item):
                return item
    except Exception as exc:
        logger.warning("Failed to fetch hwid metadata for device=%s: %s", device.id, exc)
    return None


def apply_hwid_meta_to_device(device: UserDevice, hwid_meta: dict[str, Any] | None, now: datetime) -> list[str]:
    fields_to_update: list[str] = []
    if not hwid_meta:
        return fields_to_update
    mappings = {
        "platform": (hwid_meta.get("platform") or "").strip() or None,
        "device_model": (hwid_meta.get("deviceModel") or hwid_meta.get("device_model") or "").strip() or None,
        "os_version": (hwid_meta.get("osVersion") or hwid_meta.get("os_version") or "").strip() or None,
    }
    for field, value in mappings.items():
        if value and getattr(device, field) != value:
            setattr(device, field, value)
            fields_to_update.append(field)
    device.meta_refreshed_at = now
    fields_to_update.append("meta_refreshed_at")
    return fields_to_update


async def invalidate_temp_link_if_bound(device: UserDevice) -> bool:
    if not device.hwid:
        return False
    from bloobcat.db.family_members import FamilyMembers
    from bloobcat.db.users import Users

    actor_user_id = device.user_id
    if device.family_member_id:
        member = await FamilyMembers.get_or_none(id=device.family_member_id)
        if member:
            actor_user_id = member.member_id
    actor_user = await Users.get_or_none(id=actor_user_id)
    if not actor_user or actor_user.temp_setup_device_id != device.id:
        return False
    actor_user.temp_setup_token = None
    actor_user.temp_setup_expires_at = None
    actor_user.temp_setup_device_id = None
    await actor_user.save(update_fields=["temp_setup_token", "temp_setup_expires_at", "temp_setup_device_id"])
    return True


async def sync_device_hwid_from_remnawave(device: UserDevice) -> bool:
    if device.kind != DeviceKind.DEVICE_USER:
        return False
    if device.hwid:
        await invalidate_temp_link_if_bound(device)
        return True
    hwid_meta = await fetch_first_hwid_device_info(device)
    hwid = _normalize_hwid(hwid_meta or {})
    if not hwid:
        return False
    now = datetime.now(timezone.utc)
    device.hwid = hwid
    device.last_online_at = now
    fields_to_update = ["hwid", "last_online_at"]
    fields_to_update.extend(apply_hwid_meta_to_device(device, hwid_meta, now))
    if getattr(device, "device_name", None) in _DEVICE_NAME_PLACEHOLDERS:
        device.device_name = None
        fields_to_update.append("device_name")
    await device.save(update_fields=list(dict.fromkeys(fields_to_update)))
    await invalidate_temp_link_if_bound(device)
    return True


async def find_device_collisions(device: UserDevice) -> list[UserDevice]:
    if not device.hwid:
        return []
    return await UserDevice.filter(user_id=device.user_id, hwid=device.hwid).exclude(id=device.id).all()


async def get_device_subscription_url(device: UserDevice) -> str:
    if device.kind != DeviceKind.DEVICE_USER:
        raise ValueError(f"Device {device.id} is not a device_user")
    if not device.remnawave_uuid:
        raise ValueError(f"Device {device.id} has no remnawave_uuid")
    rw = _rw_client()
    try:
        user_data = await rw.users.get_user_by_uuid(str(device.remnawave_uuid))
        resp = user_data.get("response") or {}
        raw_sub_url = resp.get("subscriptionUrl") or ""
        if raw_sub_url:
            return await rw.tools.encrypt_happ_crypto_link(raw_sub_url)
        crypto_link = (resp.get("happ") or {}).get("cryptoLink") or ""
        if crypto_link:
            from bloobcat.routes.remnawave.happ_crypto import normalize_happ_crypto_link

            return normalize_happ_crypto_link(crypto_link)
        raise ValueError("subscription URL not found")
    finally:
        await rw.close()
