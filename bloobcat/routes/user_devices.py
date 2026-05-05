from __future__ import annotations

from datetime import date
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bloobcat.db.hwid_local import HwidDeviceLocal
from bloobcat.db.user_devices import DeviceKind, UserDevice
from bloobcat.db.users import Users
from bloobcat.funcs.validate import validate
from bloobcat.logger import get_logger
from bloobcat.routes.remnawave.hwid_utils import list_user_hwid_devices
from bloobcat.services.device_service import (
    create_device_user,
    delete_device,
    delete_legacy_hwid,
    device_limit_state_for_context,
    find_device_collisions,
    get_device_subscription_url,
    is_device_per_user_enabled_for_context,
    owner_device_operation_lock,
    resolve_device_inventory_context,
    sync_device_hwid_from_remnawave,
)
from bloobcat.services.temp_setup_links import (
    build_temp_setup_link_url,
    build_temp_setup_public_payload,
    generate_temp_setup_token,
    get_temp_setup_expires_at,
    is_temp_setup_token_expired,
    utc_now,
)
from bloobcat.settings import app_settings

logger = get_logger("routes.user_devices")
router = APIRouter(prefix="/user", tags=["user-devices"])

NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


class AddDeviceRequest(BaseModel):
    name: str | None = None


class RenameDevicePayload(BaseModel):
    name: str | None = None


class DeleteLegacyDeviceRequest(BaseModel):
    hwid: str


def _json_no_store(content: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=content, status_code=status_code, headers=NO_STORE_HEADERS)


def _resolve_temp_setup_public_base_url() -> str:
    return str(getattr(app_settings, "temp_setup_site_public_base_url", "") or "").strip()


def _require_temp_setup_public_base_url() -> str:
    base_url = _resolve_temp_setup_public_base_url()
    if not base_url:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "temp_setup_public_base_url_not_configured",
                "message": "Temporary setup site is not configured",
            },
        )
    return base_url


def _device_title(row: UserDevice) -> str | None:
    if row.device_name:
        return row.device_name
    parts = [row.platform, row.device_model]
    text = " · ".join(str(p) for p in parts if p)
    return text or None


def _serialize_collision(device: UserDevice) -> Dict[str, Any]:
    return {
        "id": device.id,
        "device_name": device.device_name,
        "hwid": device.hwid,
        "created_at": device.created_at.isoformat() if device.created_at else None,
        "last_online_at": device.last_online_at.isoformat() if device.last_online_at else None,
    }


async def _serialize_inventory(user: Users) -> Dict[str, Any]:
    from bloobcat.tasks.temp_setup_cleanup import cleanup_expired_temp_setup_for_user

    if await cleanup_expired_temp_setup_for_user(user):
        await user.refresh_from_db()
    context = await resolve_device_inventory_context(user)
    feature_enabled = is_device_per_user_enabled_for_context(user, context)
    legacy_items: list[dict[str, Any]] = []
    local_by_hwid: dict[str, Any] = {}
    if context.legacy_user and context.legacy_user.remnawave_uuid:
        try:
            legacy_items = await list_user_hwid_devices(str(context.legacy_user.remnawave_uuid))
        except Exception as exc:
            logger.error("Failed to load legacy devices for user=%s: %s", context.legacy_user.id, exc)
        local_rows = await HwidDeviceLocal.filter(user_uuid=context.legacy_user.remnawave_uuid).all()
        local_by_hwid = {str(row.hwid): row for row in local_rows}

    query = UserDevice.filter(
        user_id=context.owner.id,
        family_member_id=getattr(context.family_member, "id", None),
    ).order_by("created_at")
    rows = await query.all()
    for row in rows:
        if row.kind == DeviceKind.DEVICE_USER and not row.hwid:
            try:
                await sync_device_hwid_from_remnawave(row)
            except Exception as exc:
                logger.warning("Failed to sync pending device=%s: %s", row.id, exc)
    if any(row.id == getattr(user, "temp_setup_device_id", None) for row in rows):
        await user.refresh_from_db()

    devices: list[Dict[str, Any]] = []
    seen_hwids: set[str] = set()
    for item in legacy_items:
        hwid = item.get("hwid") or item.get("deviceId") or item.get("id")
        if not hwid:
            continue
        hwid_text = str(hwid)
        if hwid_text in seen_hwids:
            continue
        seen_hwids.add(hwid_text)
        local_row = local_by_hwid.get(hwid_text)
        devices.append(
            {
                "id": None,
                "kind": DeviceKind.LEGACY_HWID.value,
                "hwid": hwid_text,
                "device_name": None,
                "platform": item.get("platform") or None,
                "os_version": item.get("osVersion") or item.get("os_version") or None,
                "device_model": item.get("deviceModel") or item.get("device_model") or None,
                "user_agent": item.get("userAgent") or item.get("user_agent") or None,
                "first_seen_at": local_row.first_seen_at.isoformat() if local_row and local_row.first_seen_at else None,
                "last_seen_at": local_row.last_seen_at.isoformat() if local_row and local_row.last_seen_at else None,
                "has_subscription_url": False,
                "is_temp_setup": False,
                "temp_setup_url": None,
                "temp_setup_expires_at": None,
            }
        )

    temp_setup_base_url = _resolve_temp_setup_public_base_url()
    for row in rows:
        if row.kind != DeviceKind.DEVICE_USER:
            continue
        is_temp = bool(user.temp_setup_device_id and row.id == user.temp_setup_device_id)
        devices.append(
            {
                "id": row.id,
                "kind": row.kind.value,
                "hwid": row.hwid,
                "device_name": _device_title(row),
                "platform": row.platform,
                "os_version": row.os_version,
                "device_model": row.device_model,
                "user_agent": None,
                "first_seen_at": row.created_at.isoformat() if row.created_at else None,
                "last_seen_at": row.last_online_at.isoformat() if row.last_online_at else None,
                "has_subscription_url": bool(row.remnawave_uuid),
                "is_temp_setup": is_temp,
                "temp_setup_url": build_temp_setup_link_url(temp_setup_base_url, user.temp_setup_token)
                if is_temp and user.temp_setup_token and temp_setup_base_url
                else None,
                "temp_setup_expires_at": user.temp_setup_expires_at.isoformat()
                if is_temp and user.temp_setup_expires_at
                else None,
            }
        )

    state = await device_limit_state_for_context(context)
    return {
        "devices": devices,
        "devices_limit": state["effective_limit"],
        "base_devices_limit": state["base_limit"],
        "delegated_devices": state["delegated_devices"],
        "used": state["used_total"],
        "can_add": bool(feature_enabled and state["can_add"]),
        "legacy_hwid_count": state["legacy_hwid_count"],
        "device_user_count": state["device_user_count"],
        "available_slots": state["available_slots"],
        "over_limit_by": state["over_limit_by"],
        "blocked_reason": state["blocked_reason"] if feature_enabled else "feature_disabled",
        "device_per_user_enabled": feature_enabled,
        "context": "family_member" if context.family_member else "owner",
    }


async def _get_owned_device_or_404(user: Users, device_id: int) -> UserDevice:
    context = await resolve_device_inventory_context(user)
    device = await UserDevice.get_or_none(
        id=device_id,
        user_id=context.owner.id,
        family_member_id=getattr(context.family_member, "id", None),
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.get("/devices")
async def list_user_devices(user: Users = Depends(validate)) -> Dict[str, Any]:
    return await _serialize_inventory(user)


@router.post("/devices/add")
async def add_user_device(payload: AddDeviceRequest | None = None, user: Users = Depends(validate)) -> Dict[str, Any]:
    context = await resolve_device_inventory_context(user)
    if not is_device_per_user_enabled_for_context(user, context):
        raise HTTPException(status_code=404, detail="feature_disabled")
    if not context.subscription_active:
        return _json_no_store({"error": "subscription_expired"}, status_code=403)  # type: ignore[return-value]
    lock = owner_device_operation_lock(context.owner.id)
    async with lock:
        state = await device_limit_state_for_context(context)
        if not state["can_add"]:
            return _json_no_store(
                {
                    "error": "devices_limit_exceeded",
                    "blocked_reason": state["blocked_reason"],
                    "over_limit_by": state["over_limit_by"],
                    "legacy_hwid_count": state["legacy_hwid_count"],
                    "device_user_count": state["device_user_count"],
                },
                status_code=402,
            )  # type: ignore[return-value]
        try:
            device = await create_device_user(user, name=payload.name if payload else None)
        except Exception as exc:
            logger.error("add_user_device failed for user=%s: %s", user.id, exc, exc_info=True)
            raise HTTPException(status_code=502, detail="Failed to create device") from exc
    subscription_url = None
    try:
        subscription_url = await get_device_subscription_url(device)
    except Exception as exc:
        logger.warning("Failed to get device subscription URL device=%s: %s", device.id, exc)
    return {
        "device": {
            "id": device.id,
            "kind": device.kind.value,
            "device_name": device.device_name,
            "created_at": device.created_at.isoformat() if device.created_at else None,
        },
        "subscription_url": subscription_url,
    }


@router.delete("/devices/{device_id}")
async def delete_user_device(device_id: int, user: Users = Depends(validate)) -> Dict[str, Any]:
    if user.temp_setup_device_id == device_id:
        raise HTTPException(status_code=409, detail="Cannot delete temporary setup device")
    context = await resolve_device_inventory_context(user)
    lock = owner_device_operation_lock(context.owner.id)
    async with lock:
        device = await _get_owned_device_or_404(user, device_id)
        try:
            await delete_device(device)
        except Exception as exc:
            logger.error("delete_user_device failed device=%s user=%s: %s", device_id, user.id, exc, exc_info=True)
            raise HTTPException(status_code=502, detail="Failed to delete device") from exc
    return {"status": "ok"}


@router.patch("/devices/{device_id}")
async def rename_user_device(device_id: int, payload: RenameDevicePayload, user: Users = Depends(validate)) -> Dict[str, Any]:
    device = await _get_owned_device_or_404(user, device_id)
    raw = (payload.name or "").strip()
    device.device_name = raw[:128] or None
    await device.save(update_fields=["device_name"])
    return {"status": "ok", "device": {"id": device.id, "kind": device.kind.value, "device_name": device.device_name}}


@router.get("/devices/{device_id}/subscription_url")
async def get_user_device_subscription_url(device_id: int, user: Users = Depends(validate)) -> Dict[str, Any]:
    device = await _get_owned_device_or_404(user, device_id)
    if device.kind != DeviceKind.DEVICE_USER:
        raise HTTPException(status_code=400, detail="Device does not support separate subscription URL")
    try:
        return {"subscription_url": await get_device_subscription_url(device)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Failed to get subscription URL") from exc


@router.get("/devices/{device_id}/status")
async def get_user_device_status(device_id: int, user: Users = Depends(validate)) -> Dict[str, Any]:
    device = await _get_owned_device_or_404(user, device_id)
    if device.kind != DeviceKind.DEVICE_USER:
        raise HTTPException(status_code=400, detail="Status polling is only available for device_user")
    if await sync_device_hwid_from_remnawave(device):
        collisions = await find_device_collisions(device)
    else:
        collisions = []
    return {
        "id": device.id,
        "kind": device.kind.value,
        "device_name": device.device_name,
        "has_hwid": bool(device.hwid),
        "hwid": device.hwid,
        "last_online_at": device.last_online_at.isoformat() if device.last_online_at else None,
        "created_at": device.created_at.isoformat() if device.created_at else None,
        "collision_with": [_serialize_collision(c) for c in collisions],
    }


@router.post("/devices/{device_id}/cancel")
async def cancel_user_device_creation(device_id: int, user: Users = Depends(validate)) -> Dict[str, Any]:
    context = await resolve_device_inventory_context(user)
    lock = owner_device_operation_lock(context.owner.id)
    async with lock:
        device = await _get_owned_device_or_404(user, device_id)
        if device.kind != DeviceKind.DEVICE_USER:
            raise HTTPException(status_code=400, detail="Cancel is only available for device_user")
        if await sync_device_hwid_from_remnawave(device):
            collisions = await find_device_collisions(device)
            return {"deleted": False, "bound": True, "hwid": device.hwid, "collision_with": [_serialize_collision(c) for c in collisions]}
        await delete_device(device)
        return {"deleted": True, "bound": False}


@router.post("/devices/delete")
async def delete_user_legacy_hwid(payload: DeleteLegacyDeviceRequest, user: Users = Depends(validate)) -> Dict[str, Any]:
    hwid = (payload.hwid or "").strip()
    if not hwid:
        raise HTTPException(status_code=400, detail="HWID is required")
    context = await resolve_device_inventory_context(user)
    if not context.legacy_user or not context.legacy_user.remnawave_uuid:
        raise HTTPException(status_code=400, detail="User has no RemnaWave UUID")
    lock = owner_device_operation_lock(context.owner.id)
    async with lock:
        await delete_legacy_hwid(str(context.legacy_user.remnawave_uuid), hwid)
        existing = await UserDevice.get_or_none(user_id=context.owner.id, family_member_id=getattr(context.family_member, "id", None), hwid=hwid)
        if existing:
            await existing.delete()
    return {"status": "ok"}


async def _issue_temp_setup_token(user: Users, device: UserDevice | None = None, base_url: str | None = None) -> dict[str, Any]:
    public_base_url = base_url or _require_temp_setup_public_base_url()
    token = generate_temp_setup_token()
    expires_at = get_temp_setup_expires_at()
    user.temp_setup_token = token
    user.temp_setup_expires_at = expires_at
    await user.save(update_fields=["temp_setup_token", "temp_setup_expires_at"])
    return {
        "token": token,
        "url": build_temp_setup_link_url(public_base_url, token),
        "expires_at": expires_at.isoformat(),
        "ttl_seconds": int((expires_at - utc_now()).total_seconds()),
        "device_id": device.id if device else None,
    }


@router.post("/temp-link")
async def create_temp_setup_link(user: Users = Depends(validate)) -> JSONResponse:
    public_base_url = _require_temp_setup_public_base_url()
    context = await resolve_device_inventory_context(user)
    if not is_device_per_user_enabled_for_context(user, context):
        return _json_no_store(await _issue_temp_setup_token(user, None, public_base_url))
    lock = owner_device_operation_lock(context.owner.id)
    async with lock:
        if user.temp_setup_device_id:
            old = await UserDevice.get_or_none(id=user.temp_setup_device_id)
            if old and not old.hwid:
                await delete_device(old)
            user.temp_setup_device_id = None
        state = await device_limit_state_for_context(context)
        if not state["can_add"]:
            raise HTTPException(status_code=402, detail=f"Device limit exceeded: {state['blocked_reason']}")
        device = await create_device_user(user, name=None)
        user.temp_setup_device_id = device.id
        await user.save(update_fields=["temp_setup_device_id"])
    return _json_no_store(await _issue_temp_setup_token(user, device, public_base_url))


@router.delete("/temp-link")
async def cancel_temp_setup_link(user: Users = Depends(validate)) -> Dict[str, Any]:
    if not user.temp_setup_token and not user.temp_setup_device_id:
        return {"status": "ok", "was_active": False}
    context = await resolve_device_inventory_context(user)
    lock = owner_device_operation_lock(context.owner.id)
    async with lock:
        if user.temp_setup_device_id:
            device = await UserDevice.get_or_none(id=user.temp_setup_device_id)
            if device and not device.hwid:
                await delete_device(device)
        user.temp_setup_token = None
        user.temp_setup_expires_at = None
        user.temp_setup_device_id = None
        await user.save(update_fields=["temp_setup_token", "temp_setup_expires_at", "temp_setup_device_id"])
    return {"status": "ok", "was_active": True}


async def _get_temp_setup_payload(user: Users) -> dict[str, object]:
    subscription_url = None
    subscription_url_error = None
    context = await resolve_device_inventory_context(user)
    if is_device_per_user_enabled_for_context(user, context) and user.temp_setup_device_id:
        device = await UserDevice.get_or_none(id=user.temp_setup_device_id)
        if device:
            try:
                subscription_url = await get_device_subscription_url(device)
            except Exception as exc:
                subscription_url_error = str(exc)
    elif user.remnawave_uuid:
        from bloobcat.routes.user import remnawave_client

        try:
            subscription_url = await remnawave_client.users.get_subscription_url(user)
        except Exception as exc:
            subscription_url_error = str(exc)
    inventory = await _serialize_inventory(user)
    return build_temp_setup_public_payload(
        subscription_url=subscription_url,
        subscription_url_error=subscription_url_error,
        devices_count=int(inventory.get("used") or 0),
        devices_limit=int(inventory.get("devices_limit") or 0),
        expires_at=user.temp_setup_expires_at,
    )


def _raise_temp_setup_error(status_code: int, detail: str, headers: dict[str, str] | None = None) -> None:
    raise HTTPException(status_code=status_code, detail=detail, headers=headers)


@router.get("/temp-link/{token}")
async def get_temp_setup_link_payload(token: str, request: Request) -> JSONResponse:
    _ = request
    user = await Users.get_or_none(temp_setup_token=token)
    if not user:
        _raise_temp_setup_error(status_code=404, detail="Temp setup link not found")
    if is_temp_setup_token_expired(user.temp_setup_expires_at):
        _raise_temp_setup_error(status_code=410, detail="Temp setup link expired")
    context = await resolve_device_inventory_context(user)
    feature_enabled = is_device_per_user_enabled_for_context(user, context)
    if feature_enabled and user.temp_setup_device_id:
        device = await UserDevice.get_or_none(id=user.temp_setup_device_id)
        if not device:
            _raise_temp_setup_error(status_code=410, detail="Temporary device no longer exists")
        if device.hwid:
            await sync_device_hwid_from_remnawave(device)
            _raise_temp_setup_error(status_code=410, detail="Temp setup link already used")
    elif feature_enabled:
        _raise_temp_setup_error(status_code=410, detail="Temp setup link already used")
    return _json_no_store(await _get_temp_setup_payload(user))


@router.get("/temp-link/{token}/device-status")
async def get_temp_device_status(token: str, request: Request) -> JSONResponse:
    _ = request
    user = await Users.get_or_none(temp_setup_token=token)
    if not user:
        _raise_temp_setup_error(status_code=404, detail="Temp setup link not found")
    if is_temp_setup_token_expired(user.temp_setup_expires_at):
        _raise_temp_setup_error(status_code=410, detail="Temp setup link expired")
    if not user.temp_setup_device_id:
        _raise_temp_setup_error(status_code=404, detail="Temp device not configured")
    device = await UserDevice.get_or_none(id=user.temp_setup_device_id)
    if not device:
        _raise_temp_setup_error(status_code=404, detail="Temp device not found")
    if device.kind == DeviceKind.DEVICE_USER:
        await sync_device_hwid_from_remnawave(device)
    return _json_no_store(
        {
            "id": device.id,
            "device_name": device.device_name,
            "hwid": device.hwid,
            "kind": device.kind.value,
            "last_online_at": device.last_online_at.isoformat() if device.last_online_at else None,
            "created_at": device.created_at.isoformat() if device.created_at else None,
            "connected": bool(device.hwid),
            "has_hwid": bool(device.hwid),
        }
    )


@router.post("/devices/{device_id}/temp-link")
async def regenerate_temp_setup_link(device_id: int, user: Users = Depends(validate)) -> JSONResponse:
    device = await _get_owned_device_or_404(user, device_id)
    if user.temp_setup_device_id != device.id:
        raise HTTPException(status_code=400, detail={"code": "not_temp_setup_device", "message": "Device is not a temporary setup device"})
    if device.hwid:
        raise HTTPException(status_code=410, detail={"code": "temp_setup_completed", "message": "Temporary setup already completed"})
    return _json_no_store(await _issue_temp_setup_token(user, device))
