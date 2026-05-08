from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple
from urllib.parse import quote
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
from bloobcat.bot.bot import get_bot_username
from bloobcat.funcs.referral_attribution import (
    DEFAULT_PARTNER_LINK_MODE,
    PartnerLinkMode,
    build_partner_link,
    normalize_partner_link_mode,
)
from bloobcat.funcs.validate import validate
from bloobcat.logger import get_logger
from bloobcat.settings import telegram_settings

logger = get_logger("routes.partner")

router = APIRouter(prefix="/partner", tags=["partner"])


class PartnerBalanceResponse(BaseModel):
    balanceRub: int


class PartnerStatusResponse(BaseModel):
    isPartner: bool
    cashbackPercent: int = 0
    referralLink: str
    linkMode: Literal["bot", "app"] = "bot"


class PartnerLinkModeRequest(BaseModel):
    mode: Literal["bot", "app"]


class PartnerLinkModeResponse(BaseModel):
    linkMode: Literal["bot", "app"]
    referralLink: str


_UTM_SAFE_RE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"


def _sanitize_utm_value(raw: Optional[str], *, max_length: int) -> Optional[str]:
    """Keep UTM values to the conservative analytics-friendly subset.

    Reason: the value is rendered into a public URL and into landing-page
    analytics events; stripping anything outside `[A-Za-z0-9._-]` avoids
    surprises with whitespace / unicode in tracking dashboards.
    """
    if raw is None:
        return None
    cleaned = "".join(ch for ch in raw.strip() if ch in _UTM_SAFE_RE)
    if not cleaned:
        return None
    return cleaned[:max_length]


class PartnerQrCreateRequest(BaseModel):
    title: str = Field(..., max_length=120)
    utmSource: Optional[str] = Field(None, max_length=64)
    utmMedium: Optional[str] = Field(None, max_length=64)
    utmCampaign: Optional[str] = Field(None, max_length=120)


class PartnerQrPatchRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    isActive: Optional[bool] = None
    utmSource: Optional[str] = Field(None, max_length=64)
    utmMedium: Optional[str] = Field(None, max_length=64)
    utmCampaign: Optional[str] = Field(None, max_length=120)


class PartnerQrResponse(BaseModel):
    id: str
    title: str
    subtitle: Optional[str] = None
    link: Optional[str] = None
    viewsCount: Optional[int] = None
    activationsCount: Optional[int] = None
    earningsRub: Optional[int] = None
    isActive: Optional[bool] = None
    utmSource: Optional[str] = None
    utmMedium: Optional[str] = None
    utmCampaign: Optional[str] = None


class PartnerWithdrawRequest(BaseModel):
    amountRub: int = Field(..., ge=1)
    method: str
    details: Optional[str] = None


class PartnerWithdrawStatus(BaseModel):
    txId: str
    status: Literal["created", "paid"]
    amountRub: int
    paidAmountRub: Optional[int] = None
    createdAtMs: int
    error: Optional[str] = None


_WITHDRAW_STATUS_CREATED = "created"
_WITHDRAW_STATUS_PAID = "paid"
_WITHDRAW_LEGACY_PAID = {"success"}
_WITHDRAW_LEGACY_PENDING = {"processing"}


def _normalize_withdraw_status(raw_status: Optional[str]) -> Literal["created", "paid"]:
    status = (raw_status or "").strip().lower()
    if status == _WITHDRAW_STATUS_PAID or status in _WITHDRAW_LEGACY_PAID:
        return _WITHDRAW_STATUS_PAID
    return _WITHDRAW_STATUS_CREATED


def _resolve_paid_amount_rub(withdraw: PartnerWithdrawals, normalized_status: Literal["created", "paid"]) -> Optional[int]:
    paid_raw = getattr(withdraw, "paid_amount_rub", None)
    paid_amount = int(paid_raw) if paid_raw is not None else None
    if normalized_status == _WITHDRAW_STATUS_PAID:
        return paid_amount if paid_amount is not None else int(withdraw.amount_rub or 0)
    return paid_amount


def _sql_param(index: int, *, dialect: str | None) -> str:
    if (dialect or "").strip().lower() == "sqlite":
        return "?"
    return f"${index}"


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
    return await _build_qr_link_from_payload(f"qr_{slug}")


def _append_utm_query(base: str, utm_pairs: List[Tuple[str, str]]) -> str:
    if not utm_pairs:
        return base
    sep = "&" if "?" in base else "?"
    extra = "&".join(f"{k}={quote(v, safe='')}" for k, v in utm_pairs)
    return f"{base}{sep}{extra}"


async def _build_qr_link_from_payload(
    payload: str,
    *,
    mode: PartnerLinkMode = DEFAULT_PARTNER_LINK_MODE,
    utm_source: Optional[str] = None,
    utm_medium: Optional[str] = None,
    utm_campaign: Optional[str] = None,
) -> str:
    try:
        bot_name = await get_bot_username()
    except Exception:
        bot_name = "VectraConnect_bot"
    resolved_mode = normalize_partner_link_mode(mode)
    if resolved_mode == "app":
        webapp_url = (getattr(telegram_settings, "webapp_url", None) or "").strip()
        if webapp_url and webapp_url.lower().startswith("https://"):
            sep = "&" if "?" in webapp_url else "?"
            base = f"{webapp_url.rstrip('/')}{sep}startapp={quote(payload, safe='')}"
        else:
            base = f"https://t.me/{bot_name}/start?startapp={quote(payload, safe='')}"
    else:
        # 'bot' mode: open chat with START button. Same start_param flows to /start handler.
        base = f"https://t.me/{bot_name}?start={quote(payload, safe='')}"
    pairs: List[Tuple[str, str]] = []
    if utm_source:
        pairs.append(("utm_source", utm_source))
    if utm_medium:
        pairs.append(("utm_medium", utm_medium))
    if utm_campaign:
        pairs.append(("utm_campaign", utm_campaign))
    return _append_utm_query(base, pairs)


def _format_qr(qr: PartnerQr, *, earnings_rub: int = 0) -> PartnerQrResponse:
    subtitle = (
        f"{qr.views_count} переходов | "
        f"{qr.activations_count} активаций | "
        f"{earnings_rub} ₽"
    )
    return PartnerQrResponse(
        id=str(qr.id),
        title=qr.title,
        subtitle=subtitle,
        link=qr.link,
        viewsCount=qr.views_count,
        activationsCount=qr.activations_count,
        earningsRub=int(earnings_rub or 0),
        isActive=qr.is_active,
        utmSource=getattr(qr, "utm_source", None),
        utmMedium=getattr(qr, "utm_medium", None),
        utmCampaign=getattr(qr, "utm_campaign", None),
    )


def _require_partner(user: Users) -> None:
    if not getattr(user, "is_partner", False):
        raise HTTPException(status_code=403, detail="Partner access required")


@router.get("/balance", response_model=PartnerBalanceResponse)
async def get_balance(user: Users = Depends(validate)) -> PartnerBalanceResponse:
    _require_partner(user)
    return PartnerBalanceResponse(balanceRub=int(user.balance or 0))


@router.patch("/link-mode", response_model=PartnerLinkModeResponse)
async def update_link_mode(
    payload: PartnerLinkModeRequest,
    user: Users = Depends(validate),
) -> PartnerLinkModeResponse:
    """Switch between bot-chat (`?start=`) and Mini App (`?startapp=`) link forms.

    Applies to both the personal partner referral link and every QR link the partner owns.
    The Telegram `start_param` payload is preserved (e.g. `qr_<uuid>`, `partner-<uid>`),
    so attribution survives the rebuild and any URLs the partner already shared keep working.
    """
    _require_partner(user)
    new_mode = normalize_partner_link_mode(payload.mode)
    user.partner_link_mode = new_mode
    await user.save(update_fields=["partner_link_mode"])

    # Rebuild every QR link the partner owns so the cabinet/list reflects the new form
    # immediately (cheap: O(N) string work per partner — partners rarely have >50 QR codes).
    try:
        owned = await PartnerQr.filter(owner_id=user.id)
        for qr in owned:
            qr.link = await _build_qr_link_from_payload(
                f"qr_{qr.id.hex}",
                mode=new_mode,
                utm_source=qr.utm_source,
                utm_medium=qr.utm_medium,
                utm_campaign=qr.utm_campaign,
            )
            await qr.save(update_fields=["link"])
    except Exception as exc:
        logger.warning(
            "Partner %s link-mode rebuild partially failed: %s", user.id, exc
        )

    referral_link = await build_partner_link(int(user.id), new_mode)
    return PartnerLinkModeResponse(linkMode=new_mode, referralLink=referral_link)


@router.get("/status", response_model=PartnerStatusResponse)
async def get_status(user: Users = Depends(validate)) -> PartnerStatusResponse:
    # This endpoint is intentionally accessible for non-partners too
    # (so the Mini App can show a clear "not activated" state).
    cashback = int(user.referral_percent()) if hasattr(user, "referral_percent") else int(getattr(user, "custom_referral_percent", 0) or 0)
    link_mode = normalize_partner_link_mode(getattr(user, "partner_link_mode", None))
    referral_link = await build_partner_link(int(user.id), link_mode)
    return PartnerStatusResponse(
        isPartner=bool(getattr(user, "is_partner", False)),
        cashbackPercent=max(0, cashback),
        referralLink=referral_link,
        linkMode=link_mode,
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
    linkMode: Literal["bot", "app"] = "bot"


@router.get("/summary", response_model=PartnerSummaryResponse)
async def get_summary(user: Users = Depends(validate)) -> PartnerSummaryResponse:
    cashback = int(user.referral_percent()) if hasattr(user, "referral_percent") else int(getattr(user, "custom_referral_percent", 0) or 0)
    link_mode = normalize_partner_link_mode(getattr(user, "partner_link_mode", None))
    referral_link = await build_partner_link(int(user.id), link_mode)

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
            linkMode=link_mode,
        )

    # Frozen: pending withdrawals.
    pending_statuses = [_WITHDRAW_STATUS_CREATED, *_WITHDRAW_LEGACY_PENDING]
    frozen_rows = await PartnerWithdrawals.filter(owner_id=user.id, status__in=pending_statuses).values_list("amount_rub", flat=True)
    frozen = int(sum(int(x or 0) for x in frozen_rows))

    # Count all referred users, even before first device activation.
    invited_count = await Users.filter(referred_by=user.id).count()

    # Count referred users who have at least one succeeded payment.
    # Use a raw query for "count distinct" + join (fast & exact on Postgres).
    subscribed_count = 0
    try:
        conn = connections.get("default")
        dialect = getattr(getattr(conn, "capabilities", None), "dialect", None)
        ref_param = _sql_param(1, dialect=dialect)
        rows = await conn.execute_query_dict(
            """
            SELECT COUNT(DISTINCT p.user_id) AS cnt
            FROM processed_payments p
            JOIN users u ON u.id = p.user_id
            WHERE u.referred_by = """
            + ref_param
            + """
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
        linkMode=link_mode,
    )


async def _earnings_by_qr(partner_id: int, qr_ids: List[uuid.UUID]) -> Dict[str, int]:
    if not qr_ids:
        return {}
    try:
        rows = await PartnerEarnings.filter(
            partner_id=partner_id,
            qr_code_id__in=qr_ids,
        ).values("qr_code_id", "reward_rub")
    except Exception as exc:
        logger.warning("Failed to aggregate per-QR earnings for partner %s: %s", partner_id, exc)
        return {}
    totals: Dict[str, int] = {}
    for row in rows:
        qr_id = row.get("qr_code_id")
        if not qr_id:
            continue
        totals[str(qr_id)] = totals.get(str(qr_id), 0) + int(row.get("reward_rub") or 0)
    return totals


@router.get("/qr", response_model=List[PartnerQrResponse])
async def list_qr_codes(user: Users = Depends(validate)) -> List[PartnerQrResponse]:
    _require_partner(user)
    items = await PartnerQr.filter(owner_id=user.id).order_by("-created_at")
    earnings = await _earnings_by_qr(int(user.id), [item.id for item in items])
    return [_format_qr(item, earnings_rub=earnings.get(str(item.id), 0)) for item in items]


@router.post("/qr", response_model=PartnerQrResponse)
async def create_qr_code(payload: PartnerQrCreateRequest, user: Users = Depends(validate)) -> PartnerQrResponse:
    _require_partner(user)
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    utm_source = _sanitize_utm_value(payload.utmSource, max_length=64)
    utm_medium = _sanitize_utm_value(payload.utmMedium, max_length=64)
    utm_campaign = _sanitize_utm_value(payload.utmCampaign, max_length=120)
    slug = _slugify_title(title)
    qr_id = uuid.uuid4()
    link_mode = normalize_partner_link_mode(getattr(user, "partner_link_mode", None))
    # Stable token based on UUID hex (fits into Telegram start param restrictions).
    link = await _build_qr_link_from_payload(
        f"qr_{qr_id.hex}",
        mode=link_mode,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
    )
    qr = await PartnerQr.create(
        id=qr_id,
        owner=user,
        title=title,
        slug=slug,
        link=link,
        is_active=True,
        views_count=0,
        activations_count=0,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
    )
    return _format_qr(qr, earnings_rub=0)


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

    utm_changed = False
    if payload.utmSource is not None:
        qr.utm_source = _sanitize_utm_value(payload.utmSource, max_length=64)
        utm_changed = True
    if payload.utmMedium is not None:
        qr.utm_medium = _sanitize_utm_value(payload.utmMedium, max_length=64)
        utm_changed = True
    if payload.utmCampaign is not None:
        qr.utm_campaign = _sanitize_utm_value(payload.utmCampaign, max_length=120)
        utm_changed = True

    if utm_changed:
        # Keep the `qr_<uuidhex>` payload stable so attribution does not break;
        # only the visible UTM tail of the link is rebuilt.
        qr.link = await _build_qr_link_from_payload(
            f"qr_{qr.id.hex}",
            mode=normalize_partner_link_mode(getattr(user, "partner_link_mode", None)),
            utm_source=qr.utm_source,
            utm_medium=qr.utm_medium,
            utm_campaign=qr.utm_campaign,
        )

    await qr.save()
    earnings = await _earnings_by_qr(int(user.id), [qr.id])
    return _format_qr(qr, earnings_rub=earnings.get(str(qr.id), 0))


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
        status=_WITHDRAW_STATUS_CREATED,
        paid_amount_rub=None,
    )
    created_ms = int(withdraw.created_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
    normalized_status = _normalize_withdraw_status(withdraw.status)
    return PartnerWithdrawStatus(
        txId=str(withdraw.id),
        status=normalized_status,
        amountRub=withdraw.amount_rub,
        paidAmountRub=_resolve_paid_amount_rub(withdraw, normalized_status),
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
    normalized_status = _normalize_withdraw_status(withdraw.status)
    return PartnerWithdrawStatus(
        txId=str(withdraw.id),
        status=normalized_status,
        amountRub=withdraw.amount_rub,
        paidAmountRub=_resolve_paid_amount_rub(withdraw, normalized_status),
        createdAtMs=created_ms,
        error=withdraw.error,
    )


@router.get("/profit")
async def get_profit(
    range_param: str = Query("week", pattern="^(week|month|year)$", alias="range"),
    qrIds: Optional[List[str]] = Query(None),
    user: Users = Depends(validate),
) -> Dict[str, Any]:
    _require_partner(user)

    now = datetime.now(timezone.utc)
    days = 7 if range_param == "week" else 30 if range_param == "month" else 365
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
