from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bloobcat.funcs.validate import validate
from bloobcat.db.users import Users
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.settings import remnawave_settings
from bloobcat.logger import get_logger

logger = get_logger("routes.devices")

router = APIRouter(prefix="/devices", tags=["devices"])


def _extract_devices(raw_resp: Any) -> List[Dict[str, Any]]:
    devices: List[Dict[str, Any]] = []
    if isinstance(raw_resp, list):
        devices = raw_resp
    elif isinstance(raw_resp, dict):
        resp = raw_resp.get("response")
        if isinstance(resp, list):
            devices = resp
        elif isinstance(resp, dict) and isinstance(resp.get("devices"), list):
            devices = resp.get("devices")
    return devices


def _format_device(device: Dict[str, Any]) -> Dict[str, Any]:
    hwid = device.get("hwid") or device.get("deviceId") or device.get("id")
    platform = device.get("platform") or device.get("os") or device.get("osName")
    os_version = device.get("osVersion") or device.get("os_version")
    model = device.get("deviceModel") or device.get("model") or device.get("device")
    user_agent = device.get("userAgent") or device.get("user_agent")
    last_seen = device.get("lastSeenAt") or device.get("last_seen") or device.get("updatedAt")

    status_text = None
    if last_seen:
        status_text = f"Последняя активность: {last_seen}"

    return {
        "hwid": hwid,
        "platform": platform,
        "osVersion": os_version,
        "deviceModel": model,
        "userAgent": user_agent,
        "lastSeenAt": last_seen,
        "statusText": status_text,
    }


class DeviceCreateRequest(BaseModel):
    hwid: Optional[str] = None
    name: Optional[str] = None
    platform: Optional[str] = None
    osVersion: Optional[str] = None
    deviceModel: Optional[str] = None
    userAgent: Optional[str] = None


@router.get("")
async def list_devices(user: Users = Depends(validate)) -> List[Dict[str, Optional[str]]]:
    if not user.remnawave_uuid:
        logger.warning(f"User {user.id} has no RemnaWave UUID, returning empty devices list")
        return []

    client = RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )
    try:
        raw_resp = await client.users.get_user_hwid_devices(str(user.remnawave_uuid))
        devices = _extract_devices(raw_resp)
        return [_format_device(d) for d in devices if isinstance(d, dict)]
    except Exception as exc:
        logger.error(f"Failed to fetch devices for user {user.id}: {exc}")
        raise HTTPException(status_code=502, detail="Failed to load devices")
    finally:
        await client.close()


@router.delete("/{hwid}")
async def delete_device(hwid: str, user: Users = Depends(validate)) -> Dict[str, Any]:
    if not user.remnawave_uuid:
        raise HTTPException(status_code=400, detail="User has no RemnaWave UUID")

    client = RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )
    try:
        await client.users.delete_user_hwid_device(str(user.remnawave_uuid), hwid)
        return {"ok": True}
    except Exception as exc:
        if "A101" in str(exc) or "Delete hwid user device error" in str(exc):
            # Treat as idempotent: device already removed
            return {"ok": True, "note": "already_deleted"}
        logger.error(f"Failed to delete device {hwid} for user {user.id}: {exc}")
        raise HTTPException(status_code=502, detail="Failed to delete device")
    finally:
        await client.close()


@router.post("")
async def create_device(payload: DeviceCreateRequest, user: Users = Depends(validate)) -> Dict[str, Any]:
    if not user.remnawave_uuid:
        raise HTTPException(status_code=400, detail="User has no RemnaWave UUID")

    hwid = (payload.hwid or "").strip()
    if not hwid:
        raise HTTPException(status_code=400, detail="HWID is required")

    device_model = payload.deviceModel or payload.name

    client = RemnaWaveClient(
        remnawave_settings.url,
        remnawave_settings.token.get_secret_value(),
    )
    try:
        resp = await client.users.add_user_hwid_device(
            str(user.remnawave_uuid),
            hwid,
            platform=payload.platform,
            os_version=payload.osVersion,
            device_model=device_model,
            user_agent=payload.userAgent,
        )
        device = None
        if isinstance(resp, dict):
            device = resp.get("response") or resp.get("device") or resp
        if isinstance(device, dict):
            return _format_device(device)
        return {
            "hwid": hwid,
            "platform": payload.platform,
            "deviceModel": device_model,
            "osVersion": payload.osVersion,
            "userAgent": payload.userAgent,
            "statusText": None,
        }
    except Exception as exc:
        logger.error(f"Failed to create device for user {user.id}: {exc}")
        raise HTTPException(status_code=502, detail="Failed to create device")
    finally:
        await client.close()
