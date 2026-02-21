from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from bloobcat.services.admin_integration import (
    sync_user_lte,
    sync_active_tariff_lte,
    sync_user_remnawave_fields,
    prepare_user_delete_via_admin,
    delete_user_via_admin,
    compute_tariff_effective_pricing,
)
from bloobcat.settings import admin_integration_settings
from bloobcat.db.users import Users
from bloobcat.db.family_audit_logs import FamilyAuditLogs

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


class TariffPricingComputePayload(BaseModel):
    tariff_id: Optional[int] = None
    patch: dict = {}


@router.post("/users/{user_id}/sync", dependencies=[Depends(require_admin_integration_token)])
async def sync_user(user_id: int, payload: UserSyncPayload):
    await sync_user_lte(user_id, payload.lte_gb_total)
    await sync_user_remnawave_fields(user_id, payload.expired_at, payload.hwid_limit)
    return {"ok": True}


@router.post("/active-tariffs/{active_tariff_id}/sync", dependencies=[Depends(require_admin_integration_token)])
async def sync_active_tariff(active_tariff_id: str, payload: ActiveTariffSyncPayload):
    await sync_active_tariff_lte(active_tariff_id, payload.lte_gb_total, payload.lte_gb_used)
    return {"ok": True}


@router.post("/users/{user_id}/pre-delete", dependencies=[Depends(require_admin_integration_token)])
async def pre_delete_user(user_id: int):
    result = await prepare_user_delete_via_admin(user_id)
    return {"ok": True, "result": result}


@router.delete("/users/{user_id}", dependencies=[Depends(require_admin_integration_token)])
async def delete_user(user_id: int):
    await delete_user_via_admin(user_id)
    return {"ok": True}


@router.post("/tariffs/compute-pricing", dependencies=[Depends(require_admin_integration_token)])
async def compute_tariff_pricing(payload: TariffPricingComputePayload):
    computed = await compute_tariff_effective_pricing(
        tariff_id=payload.tariff_id,
        patch=payload.patch or {},
    )
    return {"ok": True, "computed": computed}


@router.post("/family/{owner_id}/unblock-invites", dependencies=[Depends(require_admin_integration_token)])
async def unblock_family_invites(owner_id: int):
    owner = await Users.get_or_none(id=owner_id)
    if not owner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Owner not found")
    await FamilyAuditLogs.create(
        owner=owner,
        actor=owner,
        action="invite_unblocked",
        target_id=None,
        details={"reason": "admin_unblock"},
    )
    return {"ok": True}
