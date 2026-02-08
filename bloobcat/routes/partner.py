from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from bloobcat.db.users import Users
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.db.partner_withdrawals import PartnerWithdrawals
from bloobcat.funcs.validate import validate
from bloobcat.bot.bot import get_bot_username
from bloobcat.logger import get_logger

logger = get_logger("routes.partner")

router = APIRouter(prefix="/partner", tags=["partner"])


class PartnerBalanceResponse(BaseModel):
    balanceRub: int


class PartnerQrCreateRequest(BaseModel):
    title: str = Field(..., max_length=120)


class PartnerQrPatchRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    isActive: Optional[bool] = None


class PartnerQrResponse(BaseModel):
    id: str
    title: str
    subtitle: Optional[str] = None
    link: Optional[str] = None
    viewsCount: Optional[int] = None
    activationsCount: Optional[int] = None
    isActive: Optional[bool] = None


class PartnerWithdrawRequest(BaseModel):
    amountRub: int = Field(..., ge=1)
    method: str
    details: Optional[str] = None


class PartnerWithdrawStatus(BaseModel):
    txId: str
    status: str
    amountRub: int
    createdAtMs: int
    error: Optional[str] = None


def _slugify_title(title: str) -> str:
    s = title.strip().lower()
    out = []
    prev_underscore = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_underscore = False
            continue
        if ch in ("_", "-") or ch.isspace():
            if not prev_underscore:
                out.append("_")
                prev_underscore = True
    slug = "".join(out).strip("_")
    return (slug or "qr")[:40]


async def _build_qr_link(title: str) -> str:
    slug = _slugify_title(title)
    try:
        bot_name = await get_bot_username()
    except Exception:
        bot_name = "TriadVPN_bot"
    return f"https://t.me/{bot_name}?start=qr_{slug}"


def _format_qr(qr: PartnerQr) -> PartnerQrResponse:
    subtitle = f"{qr.views_count} переходов | {qr.activations_count} активаций"
    return PartnerQrResponse(
        id=str(qr.id),
        title=qr.title,
        subtitle=subtitle,
        link=qr.link,
        viewsCount=qr.views_count,
        activationsCount=qr.activations_count,
        isActive=qr.is_active,
    )


@router.get("/balance", response_model=PartnerBalanceResponse)
async def get_balance(user: Users = Depends(validate)) -> PartnerBalanceResponse:
    return PartnerBalanceResponse(balanceRub=int(user.balance or 0))


@router.get("/qr", response_model=List[PartnerQrResponse])
async def list_qr_codes(user: Users = Depends(validate)) -> List[PartnerQrResponse]:
    items = await PartnerQr.filter(owner_id=user.id).order_by("-created_at")
    return [_format_qr(item) for item in items]


@router.post("/qr", response_model=PartnerQrResponse)
async def create_qr_code(payload: PartnerQrCreateRequest, user: Users = Depends(validate)) -> PartnerQrResponse:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    link = await _build_qr_link(title)
    slug = _slugify_title(title)
    qr = await PartnerQr.create(
        owner=user,
        title=title,
        slug=slug,
        link=link,
        is_active=True,
        views_count=0,
        activations_count=0,
    )
    return _format_qr(qr)


@router.patch("/qr/{qr_id}", response_model=PartnerQrResponse)
async def update_qr_code(qr_id: str, payload: PartnerQrPatchRequest, user: Users = Depends(validate)) -> PartnerQrResponse:
    qr = await PartnerQr.get_or_none(id=qr_id, owner_id=user.id)
    if not qr:
        raise HTTPException(status_code=404, detail="QR not found")
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        qr.title = title
        qr.slug = _slugify_title(title)
        qr.link = await _build_qr_link(title)
    if payload.isActive is not None:
        qr.is_active = payload.isActive
    await qr.save()
    return _format_qr(qr)


@router.delete("/qr/{qr_id}")
async def delete_qr_code(qr_id: str, user: Users = Depends(validate)) -> Dict[str, Any]:
    deleted = await PartnerQr.filter(id=qr_id, owner_id=user.id).delete()
    if not deleted:
        raise HTTPException(status_code=404, detail="QR not found")
    return {"ok": True}


@router.post("/withdraw", response_model=PartnerWithdrawStatus)
async def request_withdraw(payload: PartnerWithdrawRequest, user: Users = Depends(validate)) -> PartnerWithdrawStatus:
    amount = int(payload.amountRub or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    balance = int(user.balance or 0)
    if amount > balance:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    user.balance = balance - amount
    await user.save(update_fields=["balance"])

    withdraw = await PartnerWithdrawals.create(
        owner=user,
        amount_rub=amount,
        method=str(payload.method),
        details=payload.details,
        status="created",
    )
    created_ms = int(withdraw.created_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return PartnerWithdrawStatus(
        txId=str(withdraw.id),
        status=withdraw.status,
        amountRub=withdraw.amount_rub,
        createdAtMs=created_ms,
        error=withdraw.error,
    )


@router.get("/withdraw/{tx_id}", response_model=PartnerWithdrawStatus)
async def get_withdraw_status(tx_id: str, user: Users = Depends(validate)) -> PartnerWithdrawStatus:
    withdraw = await PartnerWithdrawals.get_or_none(id=tx_id, owner_id=user.id)
    if not withdraw:
        raise HTTPException(status_code=404, detail="Withdrawal not found")
    created_ms = int(withdraw.created_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return PartnerWithdrawStatus(
        txId=str(withdraw.id),
        status=withdraw.status,
        amountRub=withdraw.amount_rub,
        createdAtMs=created_ms,
        error=withdraw.error,
    )


@router.get("/profit")
async def get_profit(
    range: str = Query("week", pattern="^(week|month|year)$"),
    user: Users = Depends(validate),
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    points: List[Dict[str, Any]] = []
    balance_value = float(user.balance or 0)

    if range == "year":
        for i in range(11, -1, -1):
            month_start = (now.replace(day=1) - timedelta(days=30 * i)).replace(day=1)
            ts = int(month_start.timestamp() * 1000)
            points.append({"ts": ts, "value": balance_value})
    else:
        days = 7 if range == "week" else 30
        for i in range(days - 1, -1, -1):
            day = now - timedelta(days=i)
            ts = int(day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            points.append({"ts": ts, "value": balance_value})

    return {"points": points}
