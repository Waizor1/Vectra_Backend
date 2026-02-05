from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from bloobcat.services.admin_integration import (
    sync_user_lte,
    sync_active_tariff_lte,
    sync_user_remnawave_fields,
    delete_user_via_admin,
)
from bloobcat.settings import admin_integration_settings

router = APIRouter(prefix="/admin/integration", tags=["admin-integration"])


async def require_admin_integration_token(
    x_admin_integration_token: Optional[str] = Header(default=None, alias="X-Admin-Integration-Token"),
):
    if not admin_integration_settings.token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Admin integration not configured")
    expected = admin_integration_settings.token.get_secret_value()
    if not x_admin_integration_token or x_admin_integration_token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


class UserSyncPayload(BaseModel):
    lte_gb_total: Optional[int] = None
    expired_at: Optional[date] = None
    hwid_limit: Optional[int] = None


class ActiveTariffSyncPayload(BaseModel):
    lte_gb_total: Optional[int] = None
    lte_gb_used: Optional[float] = None


@router.post("/users/{user_id}/sync", dependencies=[Depends(require_admin_integration_token)])
async def sync_user(user_id: int, payload: UserSyncPayload):
    await sync_user_lte(user_id, payload.lte_gb_total)
    await sync_user_remnawave_fields(user_id, payload.expired_at, payload.hwid_limit)
    return {"ok": True}


@router.post("/active-tariffs/{active_tariff_id}/sync", dependencies=[Depends(require_admin_integration_token)])
async def sync_active_tariff(active_tariff_id: str, payload: ActiveTariffSyncPayload):
    await sync_active_tariff_lte(active_tariff_id, payload.lte_gb_total, payload.lte_gb_used)
    return {"ok": True}


@router.delete("/users/{user_id}", dependencies=[Depends(require_admin_integration_token)])
async def delete_user(user_id: int):
    deleted = await delete_user_via_admin(user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return {"ok": True}
