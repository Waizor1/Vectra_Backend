from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from tortoise.expressions import F
from tortoise import connections

from bloobcat.db.users import Users
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.db.partner_withdrawals import PartnerWithdrawals
from bloobcat.db.partner_earnings import PartnerEarnings
from bloobcat.db.payments import ProcessedPayments
from bloobcat.funcs.validate import validate
from bloobcat.bot.bot import get_bot_username
from bloobcat.logger import get_logger

logger = get_logger("routes.partner")

router = APIRouter(prefix="/partner", tags=["partner"])


class PartnerBalanceResponse(BaseModel):
    balanceRub: int


class PartnerStatusResponse(BaseModel):
    isPartner: bool
    cashbackPercent: int = 0
    referralLink: str


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
    # NOTE: QR link must be stable: renaming a QR code should not break already printed materials.
    # For new records we build the link from the QR UUID (set after create).
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


def _require_partner(user: Users) -> None:
    if not getattr(user, "is_partner", False):
        raise HTTPException(status_code=403, detail="Partner access required")


async def _build_referral_link(user: Users) -> str:
    try:
        bot_name = await get_bot_username()
    except Exception:
        bot_name = "TriadVPN_bot"
    return f"https://t.me/{bot_name}?start={user.id}"


@router.get("/balance", response_model=PartnerBalanceResponse)
async def get_balance(user: Users = Depends(validate)) -> PartnerBalanceResponse:
    _require_partner(user)
    return PartnerBalanceResponse(balanceRub=int(user.balance or 0))


@router.get("/status", response_model=PartnerStatusResponse)
async def get_status(user: Users = Depends(validate)) -> PartnerStatusResponse:
    # This endpoint is intentionally accessible for non-partners too
    # (so the Mini App can show a clear "not activated" state).
    cashback = int(user.referral_percent()) if hasattr(user, "referral_percent") else int(getattr(user, "custom_referral_percent", 0) or 0)
    referral_link = await _build_referral_link(user)
    return PartnerStatusResponse(
        isPartner=bool(getattr(user, "is_partner", False)),
        cashbackPercent=max(0, cashback),
        referralLink=referral_link,
    )


class PartnerSummaryResponse(BaseModel):
    isPartner: bool
    cashbackPercent: int
    balanceRub: int
    frozenRub: int
    invitedCount: int
    subscribedCount: int
    totalIncomeRub: int
    referralLink: str


@router.get("/summary", response_model=PartnerSummaryResponse)
async def get_summary(user: Users = Depends(validate)) -> PartnerSummaryResponse:
    cashback = int(user.referral_percent()) if hasattr(user, "referral_percent") else int(getattr(user, "custom_referral_percent", 0) or 0)
    referral_link = await _build_referral_link(user)

    if not getattr(user, "is_partner", False):
        return PartnerSummaryResponse(
            isPartner=False,
            cashbackPercent=max(0, cashback),
            balanceRub=int(user.balance or 0),
            frozenRub=0,
            invitedCount=0,
            subscribedCount=0,
            totalIncomeRub=0,
            referralLink=referral_link,
        )

    # Frozen: pending withdrawals.
    pending_statuses = ["created", "processing"]
    frozen_rows = await PartnerWithdrawals.filter(owner_id=user.id, status__in=pending_statuses).values_list("amount_rub", flat=True)
    frozen = int(sum(int(x or 0) for x in frozen_rows))

    # Count all referred users, even before first device activation.
    invited_count = await Users.filter(referred_by=user.id).count()

    # Count referred users who have at least one succeeded payment.
    # Use a raw query for "count distinct" + join (fast & exact on Postgres).
    subscribed_count = 0
    try:
        conn = connections.get("default")
        rows = await conn.execute_query_dict(
            """
            SELECT COUNT(DISTINCT p.user_id) AS cnt
            FROM processed_payments p
            JOIN users u ON u.id = p.user_id
            WHERE u.referred_by = $1
              AND p.status = 'succeeded'
            """,
            [int(user.id)],
        )
        subscribed_count = int((rows[0] or {}).get("cnt") or 0) if rows else 0
    except Exception as e_cnt:
        logger.warning("Failed to compute subscribedCount for partner %s: %s", user.id, e_cnt)

    total_income = 0
    try:
        total_income_rows = await PartnerEarnings.filter(partner_id=user.id).values_list("reward_rub", flat=True)
        total_income = int(sum(int(x or 0) for x in total_income_rows))
    except Exception as e_sum:
        logger.warning("Failed to compute totalIncome for partner %s: %s", user.id, e_sum)

    return PartnerSummaryResponse(
        isPartner=True,
        cashbackPercent=max(0, cashback),
        balanceRub=int(user.balance or 0),
        frozenRub=frozen,
        invitedCount=int(invited_count),
        subscribedCount=int(subscribed_count),
        totalIncomeRub=int(total_income),
        referralLink=referral_link,
    )


@router.get("/qr", response_model=List[PartnerQrResponse])
async def list_qr_codes(user: Users = Depends(validate)) -> List[PartnerQrResponse]:
    _require_partner(user)
    items = await PartnerQr.filter(owner_id=user.id).order_by("-created_at")
    return [_format_qr(item) for item in items]


@router.post("/qr", response_model=PartnerQrResponse)
async def create_qr_code(payload: PartnerQrCreateRequest, user: Users = Depends(validate)) -> PartnerQrResponse:
    _require_partner(user)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    slug = _slugify_title(title)
    qr = await PartnerQr.create(
        owner=user,
        title=title,
        slug=slug,
        link=None,
        is_active=True,
        views_count=0,
        activations_count=0,
    )
    # Stable token based on UUID hex (fits into Telegram start param restrictions).
    try:
        bot_name = await get_bot_username()
    except Exception:
        bot_name = "TriadVPN_bot"
    token = str(qr.id).replace("-", "")
    qr.link = f"https://t.me/{bot_name}?start=qr_{token}"
    await qr.save(update_fields=["link"])
    return _format_qr(qr)


@router.patch("/qr/{qr_id}", response_model=PartnerQrResponse)
async def update_qr_code(qr_id: str, payload: PartnerQrPatchRequest, user: Users = Depends(validate)) -> PartnerQrResponse:
    _require_partner(user)
    qr = await PartnerQr.get_or_none(id=qr_id, owner_id=user.id)
    if not qr:
        raise HTTPException(status_code=404, detail="QR not found")
    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        qr.title = title
        # Keep `link` stable; update only display slug/title.
        qr.slug = _slugify_title(title)
    if payload.isActive is not None:
        qr.is_active = payload.isActive
    await qr.save()
    return _format_qr(qr)


@router.delete("/qr/{qr_id}")
async def delete_qr_code(qr_id: str, user: Users = Depends(validate)) -> Dict[str, Any]:
    _require_partner(user)
    deleted = await PartnerQr.filter(id=qr_id, owner_id=user.id).delete()
    if not deleted:
        raise HTTPException(status_code=404, detail="QR not found")
    return {"ok": True}


@router.post("/withdraw", response_model=PartnerWithdrawStatus)
async def request_withdraw(payload: PartnerWithdrawRequest, user: Users = Depends(validate)) -> PartnerWithdrawStatus:
    _require_partner(user)
    amount = int(payload.amountRub or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    method = str(payload.method or "").strip()
    details = (payload.details or "").strip()
    if method not in ("sbp", "card"):
        raise HTTPException(status_code=400, detail="Invalid method")
    if not details:
        raise HTTPException(status_code=400, detail="Details are required")
    # Minimal validation: keep it permissive, format will be handled by admin payout.
    if method == "sbp":
        phone_digits = "".join(ch for ch in details if ch.isdigit())
        if len(phone_digits) < 10:
            raise HTTPException(status_code=400, detail="Invalid phone number")
    if method == "card":
        card_digits = "".join(ch for ch in details if ch.isdigit())
        if len(card_digits) < 16:
            raise HTTPException(status_code=400, detail="Invalid card number")

    balance = int(user.balance or 0)
    if amount > balance:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    user.balance = balance - amount
    await user.save(update_fields=["balance"])

    withdraw = await PartnerWithdrawals.create(
        owner=user,
        amount_rub=amount,
        method=method,
        details=details,
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
    _require_partner(user)
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
    qrIds: Optional[List[str]] = Query(None),
    user: Users = Depends(validate),
) -> Dict[str, Any]:
    _require_partner(user)

    now = datetime.now(timezone.utc)
    days = 7 if range == "week" else 30 if range == "month" else 365
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    include_ref = False
    qr_uuid_list: List[str] = []
    # Defensive: ignore stale/invalid ids (e.g. old demo ids from localStorage).
    for raw in (qrIds or []):
        s = (raw or "").strip()
        if not s:
            continue
        if s == "referral_link":
            include_ref = True
            continue
        try:
            u = uuid.UUID(s) if len(s) != 32 else uuid.UUID(hex=s)
            qr_uuid_list.append(str(u))
        except Exception:
            continue

    where = ["partner_id = $1", "created_at >= $2", "created_at < $3"]
    params: List[Any] = [int(user.id), start, end]

    if include_ref or qr_uuid_list:
        ors: List[str] = []
        if include_ref:
            ors.append("source = 'referral_link'")
        if qr_uuid_list:
            # $4 is an array of UUIDs (Postgres).
            ors.append("qr_code_id = ANY($4::uuid[])")
            params.append(qr_uuid_list)
        where.append("(" + " OR ".join(ors) + ")")

    sql = f"""
        SELECT date_trunc('day', created_at AT TIME ZONE 'utc') AS day_utc,
               SUM(reward_rub)::int AS value
        FROM partner_earnings
        WHERE {' AND '.join(where)}
        GROUP BY day_utc
        ORDER BY day_utc ASC
    """

    rows: List[Dict[str, Any]] = []
    try:
        conn = connections.get("default")
        rows = await conn.execute_query_dict(sql, params)
    except Exception as e_sql:
        logger.warning("Partner profit query failed (partner=%s): %s", user.id, e_sql)
        rows = []

    by_day: Dict[int, int] = {}
    for r in rows:
        day = r.get("day_utc")
        value = r.get("value")
        if not day:
            continue
        try:
            ts = int(day.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except Exception:
            continue
        by_day[ts] = int(value or 0)

    points: List[Dict[str, Any]] = []
    for i in range(days):
        d = (start + timedelta(days=i))
        ts = int(d.timestamp() * 1000)
        points.append({"ts": ts, "value": by_day.get(ts, 0)})

    return {"points": points}
