from __future__ import annotations

from datetime import date
import hmac
from typing import Literal, Optional

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from bloobcat.services.admin_integration import (
    sync_user_lte,
    sync_active_tariff_lte,
    sync_user_remnawave_fields,
    prepare_user_delete_via_admin,
    delete_user_via_admin,
    compute_tariff_effective_pricing,
    preview_tariff_quote_rows,
    HwidPurgePreconditionError,
    preview_hwid_purge,
    purge_hwid_everywhere,
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
    if not x_admin_integration_token or not hmac.compare_digest(
        str(x_admin_integration_token),
        str(expected),
    ):
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


class HwidPreviewPayload(BaseModel):
    hwid: str


class HwidPurgeActorPayload(BaseModel):
    directus_user_id: Optional[str] = None
    directus_role_id: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    is_admin: Optional[bool] = None


class HwidPurgePayload(BaseModel):
    hwid: str
    reason: Optional[str] = None
    actor: Optional[HwidPurgeActorPayload] = None


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
    result = await compute_tariff_effective_pricing(
        tariff_id=payload.tariff_id,
        patch=payload.patch or {},
    )
    return result


@router.post("/tariffs/quote-preview", dependencies=[Depends(require_admin_integration_token)])
async def preview_tariff_pricing(payload: TariffPricingComputePayload):
    return await preview_tariff_quote_rows(
        tariff_id=payload.tariff_id,
        patch=payload.patch or {},
    )


@router.post("/hwid/preview", dependencies=[Depends(require_admin_integration_token)])
async def preview_hwid(payload: HwidPreviewPayload):
    try:
        preview = await preview_hwid_purge(payload.hwid)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"ok": True, "preview": preview}


@router.post("/hwid/purge", dependencies=[Depends(require_admin_integration_token)])
async def purge_hwid(payload: HwidPurgePayload):
    try:
        result = await purge_hwid_everywhere(
            payload.hwid,
            reason=payload.reason,
            actor=payload.actor.model_dump(exclude_none=True) if payload.actor else None,
        )
    except HwidPurgePreconditionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"ok": bool(result.get("ok")), "result": result}


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


class SendUserMessagePayload(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    parse_mode: Literal["HTML", "Markdown", "MarkdownV2"] | None = None


@router.post("/users/{user_id}/send-message", dependencies=[Depends(require_admin_integration_token)])
async def send_user_message(user_id: int, payload: SendUserMessagePayload):
    user = await Users.get_or_none(id=user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    from bloobcat.bot.bot import bot
    try:
        msg = await bot.send_message(user_id, payload.text, parse_mode=payload.parse_mode)
        return {"status": "sent", "message_id": msg.message_id}
    except TelegramForbiddenError:
        return {"status": "blocked"}
    except TelegramBadRequest as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


class PushBroadcastPayload(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=1000)
    url: Optional[str] = Field(default=None, max_length=2048)
    tag: Optional[str] = Field(default=None, max_length=100)
    icon: Optional[str] = Field(default=None, max_length=2048)


@router.post("/push/broadcast", dependencies=[Depends(require_admin_integration_token)])
async def push_broadcast(payload: PushBroadcastPayload):
    from bloobcat.bot.notifications.web_push import send_push_to_users, is_configured
    from bloobcat.db.push_subscriptions import PushSubscription

    if not is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Web push not configured")

    user_ids = await PushSubscription.filter(is_active=True).distinct().values_list("user_id", flat=True)
    result = await send_push_to_users(
        user_ids,
        title=payload.title,
        body=payload.body,
        url=payload.url,
        tag=payload.tag,
        icon=payload.icon,
    )
    return {"ok": True, "result": result}


# ── Home-screen install reward: orphan repair ──────────────────────────
# Frontend `HomeScreenInstallCard` and `PwaInstalledRewardModal` flip
# `users.home_screen_reward_granted_at` after `POST /referrals/home-screen-claim`
# succeeds. The pre-1.79.0 discount path could leave the flag set without a
# `PersonalDiscount(source='home_screen_install')` row when create()
# raised post-commit. This endpoint (and `scripts/repair_home_screen_orphans.py`)
# is the operational tool to detect and repair those stuck states without
# manual SQL.


class HomeScreenRewardRepairPayload(BaseModel):
    mode: Literal["clear", "credit"] = Field(
        ...,
        description=(
            "clear: drop home_screen_reward_granted_at so user can retry; "
            "credit: deliver the reward now (reward_kind required)"
        ),
    )
    reward_kind: Optional[Literal["balance", "discount"]] = Field(
        default=None,
        description="Required for mode=credit. balance forces +50 ₽; discount creates a PersonalDiscount row if absent.",
    )
    actor: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Free-text actor identifier for the audit log (admin email, script name).",
    )


class HomeScreenOrphanScanResponse(BaseModel):
    orphans: list[dict]
    scanned_user_id: Optional[int] = None
    total: int


@router.get(
    "/home-screen-rewards/orphans",
    dependencies=[Depends(require_admin_integration_token)],
)
async def list_home_screen_orphans(
    user_id: Optional[int] = None,
    limit: int = 200,
    include_balance_suspects: bool = False,
) -> HomeScreenOrphanScanResponse:
    from bloobcat.services.home_screen_rewards import scan_home_screen_orphans

    orphans = await scan_home_screen_orphans(
        user_id=user_id,
        limit=max(1, min(int(limit or 200), 1000)),
        include_balance_suspects=include_balance_suspects,
    )
    return HomeScreenOrphanScanResponse(
        orphans=list(orphans),
        scanned_user_id=user_id,
        total=len(orphans),
    )


@router.post(
    "/users/{user_id}/home-screen-reward/repair",
    dependencies=[Depends(require_admin_integration_token)],
)
async def repair_home_screen_reward_endpoint(
    user_id: int,
    payload: HomeScreenRewardRepairPayload,
):
    from bloobcat.services.home_screen_rewards import repair_home_screen_reward

    try:
        result = await repair_home_screen_reward(
            user_id=int(user_id),
            mode=payload.mode,
            reward_kind=payload.reward_kind,
            actor=payload.actor,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"ok": True, "result": result}
