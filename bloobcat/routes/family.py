from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bloobcat.funcs.validate import validate
from bloobcat.db.users import Users
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.family_devices import FamilyDevices
from bloobcat.db.family_members import FamilyMembers
from bloobcat.settings import app_settings
from bloobcat.logger import get_logger

logger = get_logger("routes.family")

router = APIRouter(prefix="/subscription/family", tags=["family"])


class FamilyDeviceCreate(BaseModel):
    title: str
    subtitle: str
    client_id: str | None = None


async def _get_devices_limit(user: Users) -> int:
    devices_limit = 1
    if user.active_tariff_id:
        tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if tariff:
            devices_limit = tariff.hwid_limit
    if user.hwid_limit is not None:
        devices_limit = user.hwid_limit
    return devices_limit


@router.get("")
async def list_family_devices(user: Users = Depends(validate)) -> List[Dict[str, Any]]:
    devices = await FamilyDevices.filter(user_id=user.id).order_by("created_at")
    return [
        {
            "id": str(d.id),
            "client_id": d.client_id,
            "title": d.title,
            "subtitle": d.subtitle,
        }
        for d in devices
    ]


@router.post("")
async def create_family_device(payload: FamilyDeviceCreate, user: Users = Depends(validate)) -> Dict[str, Any]:
    title = (payload.title or "").strip()
    subtitle = (payload.subtitle or "").strip()
    if not title or not subtitle:
        raise HTTPException(status_code=400, detail="Title and subtitle are required")
    if len(title) > 100 or len(subtitle) > 200:
        raise HTTPException(status_code=400, detail="Title or subtitle is too long")

    devices_limit = await _get_devices_limit(user)
    family_max = int(getattr(app_settings, "family_devices_limit", 10) or 10)
    if devices_limit < 10:
        raise HTTPException(status_code=403, detail="Family subscription required")
    count = await FamilyDevices.filter(user_id=user.id).count()
    allocated_to_members = 0
    for member in await FamilyMembers.filter(owner_id=user.id, status="active", allocated_devices__gt=0):
        allocated_to_members += int(member.allocated_devices or 0)
    owner_remaining = max(0, family_max - allocated_to_members)
    if count >= owner_remaining:
        raise HTTPException(status_code=409, detail="Family devices limit reached")

    if payload.client_id:
        existing = await FamilyDevices.get_or_none(user_id=user.id, client_id=payload.client_id)
        if existing:
            return {
                "id": str(existing.id),
                "client_id": existing.client_id,
                "title": existing.title,
                "subtitle": existing.subtitle,
            }

    device = await FamilyDevices.create(
        user=user,
        client_id=payload.client_id,
        title=title,
        subtitle=subtitle,
    )
    return {
        "id": str(device.id),
        "client_id": device.client_id,
        "title": device.title,
        "subtitle": device.subtitle,
    }


@router.delete("/{device_id}")
async def delete_family_device(device_id: str, user: Users = Depends(validate)) -> Dict[str, Any]:
    device = await FamilyDevices.get_or_none(id=device_id, user_id=user.id)
    if not device:
        return {"ok": True, "note": "already_deleted"}
    await device.delete()
    return {"ok": True}
