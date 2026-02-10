from random import randint
from datetime import date, datetime, timedelta, time, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import asyncio
import random
from functools import partial
from urllib.parse import urlparse
import uuid

from fastapi import APIRouter, Depends, HTTPException, Header, Request # type: ignore
from yookassa import Configuration, Payment, Webhook
from yookassa.domain.notification import WebhookNotification, WebhookNotificationEventType
from urllib3.exceptions import ConnectTimeoutError, ReadTimeoutError
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout
from tortoise.expressions import F
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from bloobcat.bot.bot import get_bot_username
from bloobcat.bot.notifications.admin import on_payment, cancel_subscription
from bloobcat.bot.notifications.general.referral import on_referral_payment
from bloobcat.bot.notifications.subscription.renewal import (
    notify_auto_renewal_success_balance,
    notify_auto_renewal_failure,
    notify_renewal_success_yookassa,
    notify_payment_canceled_yookassa,
)
from bloobcat.bot.notifications.prize_wheel import notify_spin_awarded
from bloobcat.db.tariff import Tariffs
from bloobcat.db.users import Users, normalize_date
from bloobcat.funcs.validate import validate
from bloobcat.settings import yookassa_settings, remnawave_settings, telegram_settings
from bloobcat.db.payments import ProcessedPayments
from bloobcat.db.partner_earnings import PartnerEarnings
from bloobcat.db.partner_qr import PartnerQr
from bloobcat.logger import get_payment_logger
from bloobcat.db.active_tariff import ActiveTariffs
from bloobcat.db.notifications import NotificationMarks
from bloobcat.routes.remnawave.client import RemnaWaveClient
from bloobcat.routes.remnawave.hwid_utils import cleanup_user_hwid_devices
from bloobcat.routes.remnawave.lte_utils import set_lte_squad_status
from bloobcat.services.discounts import (
    apply_personal_discount,
    consume_discount_if_needed,
)
from bloobcat.utils.dates import add_months_safe
from bloobcat.db.referral_rewards import ReferralRewards

# Инициализируем клиент ЮKассы
Configuration.account_id = yookassa_settings.shop_id
Configuration.secret_key = yookassa_settings.secret_key.get_secret_value()

router = APIRouter(prefix="/pay", tags=["pay"])
# NOTE: YooKassa webhook URL is sometimes configured without "/pay" prefix.
# We expose the same handler on both paths:
# - /pay/webhook/yookassa/{secret}   (default, under /pay)
# - /webhook/yookassa/{secret}       (alias, no /pay)
webhook_router = APIRouter(tags=["pay"])
logger = get_payment_logger()

BYTES_IN_GB = 1024 ** 3
MSK_TZ = timezone(timedelta(hours=3))

def _calc_referrer_bonus_days(months: int | None, device_count: int | None) -> int:
    """Referral reward for the referrer, based on what the friend bought.

    Matches the Mini App rules (see ReferralsPage modal):
    - 1 month -> +7 days
    - 3 months -> +20 days
    - 6 months -> +36 days
    - 12 months -> +60 days
    - 12 months family (10 devices) -> +120 days
    """
    m = int(months or 0)
    d = int(device_count or 0)
    if m >= 12 and d >= 10:
        return 120
    if m >= 12:
        return 60
    if m >= 6:
        return 36
    if m >= 3:
        return 20
    # Default fallback: treat any "short" purchase as 1 month.
    return 7


async def _is_active_family_subscription(user: Users) -> bool:
    """Best-effort check: active subscription + effective devices limit == 10."""
    try:
        exp = normalize_date(user.expired_at)
        if not exp or exp <= date.today():
            return False
        if not bool(getattr(user, "is_subscribed", False)):
            # `is_subscribed` is used in some flows; keep it as a weak signal.
            pass

        if int(getattr(user, "hwid_limit", 0) or 0) == 10:
            return True
        tariff_id = getattr(user, "active_tariff_id", None)
        if tariff_id:
            tariff = await ActiveTariffs.get_or_none(id=tariff_id)
            if tariff and int(getattr(tariff, "hwid_limit", 0) or 0) == 10:
                return True
    except Exception:
        return False
    return False


async def _apply_referral_first_payment_reward(
    *,
    referred_user_id: int,
    payment_id: str,
    amount_rub: int | None,
    months: int | None,
    device_count: int,
) -> dict:
    """Apply referral rewards in a DB-transaction (ledger + subscription updates).

    Guarantees:
    - idempotent under webhook retries
    - safe under concurrent webhook deliveries (unique ledger key)
    - no money-based rewards for normal users (days-based only)
    """
    today = date.today()
    m = int(months or 0)
    d = int(device_count or 1)

    async with in_transaction() as conn:
        # Lock referred user to avoid races with other operations on the same user.
        referred = await Users.select_for_update().using_db(conn).get(id=referred_user_id)
        if not int(getattr(referred, "referred_by", 0) or 0):
            return {"applied": False}

        # Try to insert a ledger row first (unique on (referred_user_id, kind)).
        # If it already exists -> reward already applied (or in progress previously).
        try:
            # We'll fill the rest after computing; keep defaults now.
            reward = await ReferralRewards.create(
                referred_user_id=referred.id,
                referrer_user_id=int(referred.referred_by),
                kind="first_payment",
                payment_id=payment_id,
                months=m,
                device_count=d,
                amount_rub=int(amount_rub) if amount_rub is not None else None,
                using_db=conn,
            )
        except IntegrityError:
            return {"applied": False}

        referrer = await Users.using_db(conn).get(id=int(referred.referred_by))

        friend_bonus_days = 7
        referrer_bonus_days = _calc_referrer_bonus_days(months=m, device_count=d)

        # Friend subscription extension in DB (no external side effects here).
        start_friend = max(normalize_date(referred.expired_at) or today, today)
        new_friend_expired_at = start_friend + timedelta(days=friend_bonus_days)
        await Users.filter(id=referred.id).using_db(conn).update(
            expired_at=new_friend_expired_at,
            referral_first_payment_rewarded=True,
        )

        # Referrer bonus counter always accumulates (UI uses this).
        if referrer_bonus_days > 0:
            await Users.filter(id=referrer.id).using_db(conn).update(
                referral_bonus_days_total=F("referral_bonus_days_total") + int(referrer_bonus_days)
            )

        applied_to_subscription = False
        try:
            is_family = await _is_active_family_subscription(referrer)
            if not is_family and referrer_bonus_days > 0:
                start_ref = max(normalize_date(referrer.expired_at) or today, today)
                new_ref_expired_at = start_ref + timedelta(days=referrer_bonus_days)
                await Users.filter(id=referrer.id).using_db(conn).update(expired_at=new_ref_expired_at)
                applied_to_subscription = True
        except Exception:
            # Best-effort: even if family check fails, we still keep the earned days counter.
            applied_to_subscription = False

        # Update ledger with actual computed values.
        await ReferralRewards.filter(id=reward.id).using_db(conn).update(
            friend_bonus_days=friend_bonus_days,
            referrer_bonus_days=referrer_bonus_days,
            applied_to_subscription=applied_to_subscription,
        )

        return {
            "applied": True,
            "referrer_id": int(referrer.id),
            "friend_bonus_days": int(friend_bonus_days),
            "referrer_bonus_days": int(referrer_bonus_days),
            "months": int(m),
            "device_count": int(d),
            "applied_to_subscription": bool(applied_to_subscription),
        }


async def _upsert_processed_payment(
    *,
    payment_id: str,
    user_id: int,
    amount: float,
    amount_external: float,
    amount_from_balance: float,
    status: str,
) -> ProcessedPayments:
    """
    Idempotent write to `processed_payments`.
    Webhooks can be retried and we also have a fallback processor, so this must be safe to call multiple times.
    """
    existing = await ProcessedPayments.get_or_none(payment_id=payment_id)
    if existing:
        existing.user_id = int(user_id)
        existing.amount = float(amount)
        existing.amount_external = float(amount_external)
        existing.amount_from_balance = float(amount_from_balance)
        existing.status = str(status)
        await existing.save(
            update_fields=[
                "user_id",
                "amount",
                "amount_external",
                "amount_from_balance",
                "status",
            ]
        )
        return existing
    return await ProcessedPayments.create(
        payment_id=payment_id,
        user_id=int(user_id),
        amount=float(amount),
        amount_external=float(amount_external),
        amount_from_balance=float(amount_from_balance),
        status=str(status),
    )


async def _award_partner_cashback(
    *,
    payment_id: str,
    referral_user: Users,
    amount_rub_total: int,
) -> None:
    """
    Money-based partner rewards (cashback % from each purchase of referred users).
    Idempotent by payment_id via partner_earnings.payment_id unique constraint.
    """
    try:
        referrer_id = int(getattr(referral_user, "referred_by", 0) or 0)
        if not referrer_id:
            return
        referrer = await Users.get_or_none(id=referrer_id)
        if not referrer or not bool(getattr(referrer, "is_partner", False)):
            return

        # Partner percent: can be configured per-user or tiered by referrals count.
        try:
            percent = int(referrer.referral_percent()) if hasattr(referrer, "referral_percent") else int(getattr(referrer, "custom_referral_percent", 0) or 0)
        except Exception:
            percent = int(getattr(referrer, "custom_referral_percent", 0) or 0)
        percent = max(0, percent)
        if percent <= 0:
            return

        # Resolve QR attribution from referral_user.utm if present.
        source = "referral_link"
        qr = None
        try:
            utm = (getattr(referral_user, "utm", None) or "").strip()
            if utm.startswith("qr_"):
                token = utm[3:]
                source = "qr"
                try:
                    qr_uuid = uuid.UUID(token) if len(token) != 32 else uuid.UUID(hex=token)
                    qr = await PartnerQr.get_or_none(id=qr_uuid)
                except Exception:
                    qr = None
                if not qr:
                    qr = await PartnerQr.get_or_none(slug=token)
        except Exception:
            qr = None

        existing = await PartnerEarnings.get_or_none(payment_id=payment_id)
        if existing:
            return

        reward = _round_rub(float(amount_rub_total) * float(percent) / 100.0)
        if reward <= 0:
            return

        await PartnerEarnings.create(
            payment_id=str(payment_id),
            partner=referrer,
            referral_id=int(referral_user.id),
            qr_code=qr,
            source=source,
            amount_total_rub=int(amount_rub_total),
            reward_rub=int(reward),
            percent=int(percent),
        )

        # Update partner available balance (atomic).
        await Users.filter(id=referrer.id).update(balance=F("balance") + int(reward))
    except Exception as e:
        logger.warning("Partner cashback award failed for payment %s: %s", payment_id, e)

def _bot_chat_url_from_webapp_url(webapp_url: str | None) -> str | None:
    """
    Best-effort parse bot username from TELEGRAM_WEBAPP_URL.
    Expected: https://t.me/<bot>/<app>/...
    Returns: https://t.me/<bot>
    """
    raw = (webapp_url or "").strip()
    if not raw:
        return None
    try:
        u = urlparse(raw)
        host = (u.netloc or "").lower()
        if host not in ("t.me", "telegram.me", "www.t.me", "www.telegram.me"):
            return None
        parts = [p for p in (u.path or "").split("/") if p]
        if not parts:
            return None
        bot = parts[0].lstrip("@").strip()
        if not bot:
            return None
        return f"https://t.me/{bot}"
    except Exception:
        return None


def _is_telegram_https_link(url: str) -> bool:
    """
    Accept any Telegram universal link (t.me / telegram.me) over HTTPS.
    We use this as a safe fallback for YooKassa `return_url` so the "back" button is never empty.
    """
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        u = urlparse(raw)
        if (u.scheme or "").lower() != "https":
            return False
        host = (u.netloc or "").lower()
        if host not in ("t.me", "telegram.me", "www.t.me", "www.telegram.me"):
            return False
        parts = [p for p in (u.path or "").split("/") if p]
        return len(parts) >= 1
    except Exception:
        return False


def _is_bot_chat_link(url: str) -> bool:
    """
    Accept only "https://t.me/<username>" (optional trailing slash).
    We intentionally reject Mini App links (/bot/app) and deep links with query params.
    """
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        u = urlparse(raw)
        if (u.scheme or "").lower() != "https":
            return False
        host = (u.netloc or "").lower()
        if host not in ("t.me", "telegram.me", "www.t.me", "www.telegram.me"):
            return False
        parts = [p for p in (u.path or "").split("/") if p]
        if len(parts) != 1:
            return False
        if u.query or u.fragment:
            return False
        return True
    except Exception:
        return False


async def _resolve_payment_return_url() -> str:
    """
    After YooKassa redirect we MUST return the user to the bot chat (not to Mini App),
    because the payment status is delivered by the bot message.
    """
    raw = (telegram_settings.payment_return_url or "").strip()
    if raw and _is_bot_chat_link(raw):
        return raw
    if raw and not _is_bot_chat_link(raw):
        # Historically we allowed only a bot chat link here.
        # In practice, operators often configure a Mini App deep-link like:
        #   https://t.me/<bot>/<miniapp_short_name>?startapp=...
        # If we ignore it completely, YooKassa can end up with a useless return_url fallback.
        if _is_telegram_https_link(raw):
            logger.info("TELEGRAM_PAYMENT_RETURN_URL is not a bot chat link; using as-is for YooKassa return_url")
            return raw
        logger.warning("TELEGRAM_PAYMENT_RETURN_URL ignored: not a Telegram HTTPS link")

    parsed = _bot_chat_url_from_webapp_url(getattr(telegram_settings, "webapp_url", None))
    if parsed:
        return parsed

    try:
        bot_username = (await get_bot_username() or "").strip().lstrip("@")
        if bot_username:
            return f"https://t.me/{bot_username}/"
    except Exception:
        pass

    # Last resort: return any configured HTTPS page so YooKassa button is still clickable.
    try:
        webapp_url = (getattr(telegram_settings, "webapp_url", None) or "").strip()
        if webapp_url and webapp_url.lower().startswith("https://"):
            return webapp_url
        miniapp_url = (getattr(telegram_settings, "miniapp_url", None) or "").strip()
        if miniapp_url and miniapp_url.lower().startswith("https://"):
            return miniapp_url
    except Exception:
        pass
    return "https://t.me/"

def _round_rub(value: float) -> int:
    try:
        dec = Decimal(str(value))
    except Exception:
        return 0
    return int(dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def _format_range_start(start_date: date) -> str:
    start_dt = datetime.combine(start_date, time.min, tzinfo=MSK_TZ)
    return start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _format_range_end(end_dt: datetime) -> str:
    return end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


async def _fetch_today_lte_usage_gb(user_uuid: str) -> float | None:
    marker_upper = (remnawave_settings.lte_node_marker or "").upper()
    if not marker_upper:
        return 0.0
    client = RemnaWaveClient(
        remnawave_settings.url, remnawave_settings.token.get_secret_value()
    )
    try:
        msk_today = datetime.now(MSK_TZ).date()
        start_str = _format_range_start(msk_today)
        end_str = _format_range_end(datetime.now(timezone.utc))
        resp = await client.users.get_user_usage_by_range(user_uuid, start_str, end_str)
        items = resp.get("response") or []
        total_gb = 0.0
        for item in items:
            node_name = str(item.get("nodeName") or "").upper()
            if marker_upper and marker_upper not in node_name:
                continue
            total_bytes = float(item.get("total") or 0)
            total_gb += total_bytes / BYTES_IN_GB
        return total_gb
    except Exception as e:
        logger.error(f"Ошибка получения LTE usage snapshot для {user_uuid}: {e}")
        return None
    finally:
        await client.close()


async def _apply_succeeded_payment_fallback(yk_payment, user: Users, meta: dict) -> bool:
    """
    Fallback processor used by `/pay/status/{payment_id}` when webhook delivery fails.
    It applies the subscription update and creates ProcessedPayments record (idempotent).
    """
    pid = str(getattr(yk_payment, "id", "") or "").strip()
    if not pid:
        return False
    existing = await ProcessedPayments.get_or_none(payment_id=pid)
    if existing and (existing.status or "").strip().lower() != "pending":
        # Already finalized by webhook / previous reconciliation.
        return True

    try:
        months = int(meta.get("month"))
    except Exception:
        return False

    try:
        amount_external = float(getattr(getattr(yk_payment, "amount", None), "value", 0) or 0)
    except Exception:
        amount_external = 0.0

    amount_from_balance = _round_rub(meta.get("amount_from_balance", 0))
    if amount_from_balance > 0:
        user.balance = max(0, int(user.balance or 0) - int(amount_from_balance))

    # Extend subscription days.
    current_date = date.today()
    target_date = add_months_safe(current_date, months)
    days = max(0, (target_date - current_date).days)
    await user.extend_subscription(days)

    # Persist tariff snapshot + device limit when metadata contains tariff_id.
    tariff_id = meta.get("tariff_id")
    device_count = 1
    try:
        device_count = int(meta.get("device_count", 1))
    except Exception:
        device_count = 1
    if device_count < 1:
        device_count = 1

    lte_gb = 0
    try:
        lte_gb = int(meta.get("lte_gb") or 0)
    except Exception:
        lte_gb = 0
    if lte_gb < 0:
        lte_gb = 0

    if tariff_id is not None:
        original = await Tariffs.get_or_none(id=tariff_id)
        if original:
            calculated_price = int(original.calculate_price(device_count))
            if user.active_tariff_id:
                old_active = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
                if old_active:
                    await old_active.delete()

            if "lte_price_per_gb" in meta:
                try:
                    lte_price_snapshot = float(meta.get("lte_price_per_gb") or 0)
                except Exception:
                    lte_price_snapshot = float(original.lte_price_per_gb or 0)
            else:
                lte_price_snapshot = float(original.lte_price_per_gb or 0)

            msk_today = datetime.now(MSK_TZ).date()
            usage_snapshot = None
            if user.remnawave_uuid:
                usage_snapshot = await _fetch_today_lte_usage_gb(str(user.remnawave_uuid))

            active_tariff = await ActiveTariffs.create(
                user=user,
                name=original.name,
                months=original.months,
                price=calculated_price,
                hwid_limit=device_count,
                lte_gb_total=lte_gb,
                lte_gb_used=0.0,
                lte_price_per_gb=lte_price_snapshot,
                lte_usage_last_date=msk_today,
                lte_usage_last_total_gb=usage_snapshot if usage_snapshot is not None else 0.0,
                progressive_multiplier=original.progressive_multiplier,
                residual_day_fraction=0.0,
            )
            user.active_tariff_id = active_tariff.id
            user.hwid_limit = device_count
            user.lte_gb_total = lte_gb

    if user.is_trial:
        user.is_trial = False

    await user.save()

    # Best-effort RemnaWave sync for HWID limit.
    if user.remnawave_uuid and user.hwid_limit:
        remnawave_client = None
        try:
            remnawave_client = RemnaWaveClient(
                remnawave_settings.url,
                remnawave_settings.token.get_secret_value(),
            )
            await remnawave_client.users.update_user(
                uuid=user.remnawave_uuid,
                expireAt=user.expired_at,
                hwidDeviceLimit=int(user.hwid_limit),
            )
        except Exception:
            pass
        finally:
            if remnawave_client:
                try:
                    await remnawave_client.close()
                except Exception:
                    pass

    # Mark processed (idempotent).
    total_amount = float(amount_external) + float(amount_from_balance)
    await _upsert_processed_payment(
        payment_id=pid,
        user_id=user.id,
        amount=total_amount,
        amount_external=float(amount_external),
        amount_from_balance=float(amount_from_balance),
        status="succeeded",
    )

    # Notify user in bot (so they see the outcome even if webhook failed).
    try:
        await notify_renewal_success_yookassa(
            user=user,
            days=days,
            amount_paid_via_yookassa=float(amount_external),
            amount_from_balance=float(amount_from_balance),
        )
    except Exception:
        pass

    return True

@router.get("/tariffs")
async def get_tariffs():
    return await Tariffs.all().order_by("order")

@router.get("/status/{payment_id}")
async def get_payment_status(
    payment_id: str,
    user: Users = Depends(validate),
):
    """
    Point payment status check by YooKassa payment_id.

    Why:
    - Webhook delivery / subscription activation can lag.
    - This endpoint allows the client to confirm the payment result (succeeded/canceled/pending)
      even before `/user` reflects the updated subscription.

    Security:
    - Requires authenticated user (Telegram initData or Bearer token).
    - Verifies YooKassa payment metadata user_id when available.
    - If the payment was already processed by our webhook, we also verify against ProcessedPayments.
    """
    if not payment_id or not str(payment_id).strip():
        raise HTTPException(status_code=400, detail="payment_id is required")

    processed = await ProcessedPayments.get_or_none(payment_id=payment_id)
    if processed and int(processed.user_id) != int(user.id):
        # Don't leak existence of foreign payment IDs.
        raise HTTPException(status_code=404, detail="Payment not found")

    try:
        yk_payment = await asyncio.wait_for(
            asyncio.to_thread(partial(Payment.find_one, payment_id)),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Payment status service timeout")
    except Exception as e:
        logger.error(
            f"Ошибка при получении статуса платежа YooKassa {payment_id}: {e}",
            extra={"payment_id": payment_id, "user_id": user.id},
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Payment status service unavailable")

    if not yk_payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    status = str(getattr(yk_payment, "status", "") or "")
    amount_value: str | None = None
    currency: str | None = None
    try:
        amount_obj = getattr(yk_payment, "amount", None)
        amount_value = str(getattr(amount_obj, "value", "") or "") or None
        currency = str(getattr(amount_obj, "currency", "") or "") or None
    except Exception:
        amount_value = None
        currency = None

    meta_user_id: int | None = None
    try:
        meta = getattr(yk_payment, "metadata", None)
        if isinstance(meta, dict):
            raw = meta.get("user_id")
            if raw is not None:
                meta_user_id = int(raw)
    except Exception:
        meta_user_id = None

    if meta_user_id is not None and int(meta_user_id) != int(user.id):
        raise HTTPException(status_code=403, detail="Payment does not belong to user")

    # Reliability fallback:
    # If YooKassa says the payment is succeeded but our webhook didn't process it,
    # try to apply subscription here (idempotent via ProcessedPayments unique constraint).
    if status == "succeeded" and (not processed or (processed.status or "").strip().lower() == "pending"):
        try:
            meta = getattr(yk_payment, "metadata", None)
            if isinstance(meta, dict) and str(meta.get("user_id", "")).strip():
                if int(meta.get("user_id")) == int(user.id):
                    applied = await _apply_succeeded_payment_fallback(yk_payment, user, meta)
                    if applied:
                        processed = await ProcessedPayments.get_or_none(payment_id=payment_id)
        except Exception as e:
            logger.error(
                f"Fallback processing failed for succeeded payment {payment_id}: {e}",
                extra={"payment_id": payment_id, "user_id": user.id},
                exc_info=True,
            )

    return {
        "payment_id": payment_id,
        "yookassa_status": status,
        "is_final": status in ("succeeded", "canceled"),
        "is_paid": status == "succeeded",
        "amount": amount_value,
        "currency": currency,
        "processed": bool(processed),
        "processed_status": (processed.status if processed else None),
    }



@router.post("/webhook/yookassa/{secret}")
@webhook_router.post("/webhook/yookassa/{secret}")
async def yookassa_webhook(request: Request, secret: str):
    if secret != yookassa_settings.webhook_secret:
        logger.error("Получен webhook с неверным секретным ключом")
        raise HTTPException(status_code=403, detail="Invalid secret")
    
    try:
        # Получаем тело запроса
        body = await request.json()
        # Получаем заголовки
        headers = dict(request.headers)
        
        # Проверяем подпись и создаем объект уведомления
        notification = WebhookNotification(body, headers)
        
        event = notification.event
        payment = notification.object
        
        # Логируем событие
        logger.info(
            f"Получен webhook от YooKassa: {event}",
            extra={
                'payment_id': payment.id if payment else 'unknown',
                'user_id': payment.metadata.get("user_id", "unknown") if payment else "unknown",
                'amount': payment.amount.value if payment else "unknown",
                'status': payment.status if payment else "unknown"
            }
        )
        
        try:
            data = payment.metadata
            user = await Users.get(id=data["user_id"])
        except (KeyError, ValueError) as e:
            logger.error(
                f"Некорректные метаданные в webhook'е YooKassa: {e}",
                extra={
                    'payment_id': payment.id if payment else 'unknown',
                    'user_id': "unknown",
                    'amount': payment.amount.value if payment else "unknown",
                    'status': payment.status if payment else "unknown"
                }
            )
            return {"status": "error", "message": "Invalid metadata"}
        except Exception as e:
            logger.error(
                f"Ошибка при получении пользователя в webhook'е YooKassa: {e}",
                extra={'payment_id': payment.id if payment else 'unknown'}
            )
            return {"status": "error", "message": "User not found"}

        old_expired_at = user.expired_at

        # Вычисляем will_retry для уведомлений об ошибках
        user_expired_at = normalize_date(user.expired_at)
        will_retry = user_expired_at is not None and (user_expired_at - date.today()).days >= 0

        # Проверяем, не обработан ли уже этот платеж
        if not payment.id:
            logger.error(
                "Отсутствует payment_id в webhook'е YooKassa",
                extra={'payment_id': 'missing'}
            )
            return {"status": "error", "message": "Missing payment_id"}

        processed_payment = await ProcessedPayments.get_or_none(payment_id=payment.id)
        if processed_payment and processed_payment.status != "pending":
            logger.info(
                f"Платеж {payment.id} уже был обработан ранее",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value,
                    'status': processed_payment.status
                }
            )
            return {"status": "ok"}
        
        # Обработка разных типов событий
        if event == WebhookNotificationEventType.REFUND_SUCCEEDED:
            # При возврате средств отключаем автопродление
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            # Резервации отключены
            
            # Сохраняем информацию о возврате (идемпотентно)
            await _upsert_processed_payment(
                payment_id=payment.id,
                user_id=user.id,
                amount=float(payment.amount.value),
                amount_external=float(payment.amount.value),
                amount_from_balance=0,
                status="refunded",
            )
            
            logger.info(
                f"Автопродление отключено для пользователя {user.id} из-за возврата средств",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value,
                    'status': "refunded"
                }
            )
            return {"status": "ok"}
        
        if event == WebhookNotificationEventType.PAYMENT_CANCELED:
            # Сохраняем информацию об отмене (идемпотентно)
            await _upsert_processed_payment(
                payment_id=payment.id,
                user_id=user.id,
                amount=float(payment.amount.value),
                amount_external=float(payment.amount.value),
                amount_from_balance=0,
                status="canceled",
            )
            # Резервации отключены
            
            logger.info(
                f"Автопродление отключено для пользователя {user.id} из-за отмены платежа",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value,
                    'status': "canceled"
                }
            )
            # Ручные платежи: сообщаем пользователю outcome в боте,
            # чтобы после return_url он видел “успех/ошибка” без UI-поллинга в Mini App.
            if not data.get("is_auto", False):
                try:
                    await notify_payment_canceled_yookassa(user=user)
                except Exception as notify_exc:
                    logger.error(
                        f"Ошибка при отправке уведомления об отмене оплаты (YooKassa) для {user.id}: {notify_exc}",
                        extra={'payment_id': payment.id, 'user_id': user.id},
                    )
            if data.get("is_auto", False):
                disable = data.get("disable_on_fail", False)
                if disable:
                    user.is_subscribed = False
                    user.renew_id = None
                    await user.save()
                    # Уведомляем админа об отключении автопродления из-за отмены платежа
                    await cancel_subscription(user, reason="Автоплатеж был отменен")
                await notify_auto_renewal_failure(user, reason="Платеж был отменен", will_retry=will_retry)
            return {"status": "ok"}
        
        if event != WebhookNotificationEventType.PAYMENT_SUCCEEDED:
            return {"status": "ok"}
        
        if payment.status != "succeeded":
            logger.warning(f"Автоплатеж {payment.id} для пользователя {user.id} завершился со статусом {payment.status}")
            if data.get("is_auto", False):
                disable = data.get("disable_on_fail", False)
                if disable:
                    user.is_subscribed = False
                    user.renew_id = None
                    await user.save()
                    # Уведомляем админа об отключении автопродления из-за неуспешного платежа
                    await cancel_subscription(user, reason=f"Автоплатеж завершился со статусом: {payment.status}")
                await notify_auto_renewal_failure(user, reason=f"Платеж не прошел (статус: {payment.status})", will_retry=will_retry)
            return {"status": "ok"}

        if data.get("lte_topup"):
            lte_gb_delta = int(data.get("lte_gb_delta") or 0)
            lte_price_per_gb = float(data.get("lte_price_per_gb") or 0)
            amount_from_balance = _round_rub(data.get("amount_from_balance", 0))
            active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id) if user.active_tariff_id else None
            if not active_tariff:
                logger.error(f"LTE пополнение: активный тариф не найден для пользователя {user.id}")
                return {"status": "error", "message": "Active tariff not found"}

            if amount_from_balance > 0:
                initial_balance = user.balance
                user.balance = max(0, user.balance - amount_from_balance)
                await user.save(update_fields=["balance"])
                logger.info(
                    f"LTE пополнение: списано {amount_from_balance:.2f} с бонусного баланса пользователя {user.id}. "
                    f"Баланс до: {initial_balance}, после: {user.balance}"
                )

            update_fields = []
            if lte_gb_delta > 0:
                active_tariff.lte_gb_total = int(active_tariff.lte_gb_total or 0) + lte_gb_delta
                update_fields.append("lte_gb_total")
                user.lte_gb_total = int(active_tariff.lte_gb_total or 0)
                await user.save(update_fields=["lte_gb_total"])

            msk_today = datetime.now(MSK_TZ).date()
            usage_snapshot = None
            if user.remnawave_uuid:
                usage_snapshot = await _fetch_today_lte_usage_gb(
                    str(user.remnawave_uuid)
                )
            if usage_snapshot is not None:
                active_tariff.lte_usage_last_date = msk_today
                active_tariff.lte_usage_last_total_gb = usage_snapshot
                update_fields.extend(["lte_usage_last_date", "lte_usage_last_total_gb"])
            elif active_tariff.lte_usage_last_date != msk_today:
                active_tariff.lte_usage_last_date = msk_today
                active_tariff.lte_usage_last_total_gb = 0.0
                update_fields.extend(["lte_usage_last_date", "lte_usage_last_total_gb"])

            if update_fields:
                await active_tariff.save(update_fields=update_fields)

            await NotificationMarks.filter(user_id=user.id, type="lte_usage").delete()

            pending_device_count = data.get("pending_device_count")
            if pending_device_count is not None:
                try:
                    pending_device_count = int(pending_device_count)
                except (TypeError, ValueError):
                    pending_device_count = None
            if pending_device_count and pending_device_count > 0:
                pending_expired_at = None
                pending_expired_at_raw = data.get("pending_expired_at")
                if pending_expired_at_raw:
                    try:
                        pending_expired_at = date.fromisoformat(str(pending_expired_at_raw))
                    except Exception:
                        pending_expired_at = None

                pending_active_tariff_price = data.get("pending_active_tariff_price")
                try:
                    pending_active_tariff_price = (
                        int(pending_active_tariff_price) if pending_active_tariff_price is not None else None
                    )
                except (TypeError, ValueError):
                    pending_active_tariff_price = None

                pending_progressive_multiplier = data.get("pending_progressive_multiplier")
                try:
                    pending_progressive_multiplier = (
                        float(pending_progressive_multiplier) if pending_progressive_multiplier is not None else None
                    )
                except (TypeError, ValueError):
                    pending_progressive_multiplier = None

                pending_residual_day_fraction = data.get("pending_residual_day_fraction")
                try:
                    pending_residual_day_fraction = (
                        float(pending_residual_day_fraction) if pending_residual_day_fraction is not None else None
                    )
                except (TypeError, ValueError):
                    pending_residual_day_fraction = None

                pending_devices_decrease_count = data.get("pending_devices_decrease_count")
                try:
                    pending_devices_decrease_count = (
                        int(pending_devices_decrease_count) if pending_devices_decrease_count is not None else None
                    )
                except (TypeError, ValueError):
                    pending_devices_decrease_count = None

                user.expired_at = pending_expired_at or user.expired_at
                user.hwid_limit = pending_device_count
                await user.save()

                active_tariff.hwid_limit = pending_device_count
                if pending_active_tariff_price is not None:
                    active_tariff.price = pending_active_tariff_price
                if pending_progressive_multiplier is not None:
                    active_tariff.progressive_multiplier = pending_progressive_multiplier
                if pending_residual_day_fraction is not None:
                    active_tariff.residual_day_fraction = pending_residual_day_fraction
                if pending_devices_decrease_count is not None:
                    active_tariff.devices_decrease_count = pending_devices_decrease_count
                await active_tariff.save()

                if user.remnawave_uuid:
                    remnawave_client = None
                    try:
                        remnawave_client = RemnaWaveClient(
                            remnawave_settings.url,
                            remnawave_settings.token.get_secret_value(),
                        )
                        await remnawave_client.users.update_user(
                            uuid=user.remnawave_uuid,
                            expireAt=user.expired_at,
                            hwidDeviceLimit=pending_device_count
                        )
                    except Exception as e:
                        logger.error(f"LTE пополнение: ошибка обновления RemnaWave при изменении устройств для {user.id}: {e}")
                    finally:
                        if remnawave_client:
                            try:
                                await remnawave_client.close()
                            except Exception:
                                pass

            if user.remnawave_uuid:
                try:
                    effective_lte_total = (
                        user.lte_gb_total
                        if user.lte_gb_total is not None
                        else (active_tariff.lte_gb_total or 0)
                    )
                    should_enable_lte = effective_lte_total > (active_tariff.lte_gb_used or 0)
                    await set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable_lte)
                except Exception as e:
                    logger.error(f"LTE пополнение: ошибка обновления LTE-сквада для {user.id}: {e}")

            amount_external = float(payment.amount.value)
            total_amount = amount_external + amount_from_balance
            await _upsert_processed_payment(
                payment_id=payment.id,
                user_id=user.id,
                amount=total_amount,
                amount_external=amount_external,
                amount_from_balance=amount_from_balance,
                status="succeeded",
            )

            logger.info(
                f"LTE пополнение успешно: user={user.id}, delta={lte_gb_delta}, price={lte_price_per_gb}"
            )
            return {"status": "ok"}
        
        try:
            months = int(data["month"])
        except (KeyError, ValueError) as e:
            logger.error(
                f"Некорректное значение месяцев в webhook'е YooKassa: {e}",
                extra={'payment_id': payment.id}
            )
            return {"status": "error", "message": "Invalid month value"}

        lte_gb = int(data.get("lte_gb") or 0)
        lte_price_per_gb = float(data.get("lte_price_per_gb") or 0)
        lte_cost = _round_rub(data.get("lte_cost") or (lte_gb * lte_price_per_gb))
        discounted_raw = data.get("discounted_price")
        if discounted_raw is None:
            base_paid_price = float(data.get("base_full_price") or 0)
        else:
            base_paid_price = float(discounted_raw)
        
        # Достаём скидку, применённую при создании платежа (если была)
        discount_id = data.get("discount_id")
        discount_percent = int(data.get("discount_percent") or 0)

        # Сразу пытаемся списать скидку: если списалась, пропорциональная коррекция не нужна
        consumed = False
        try:
            consumed = await consume_discount_if_needed(discount_id)
        except Exception:
            consumed = False

        # Новый тариф из webhook
        active_tariff_for_lte = None
        tariff_id = data.get("tariff_id")
        if tariff_id is not None:
            # Получаем новый тариф
            new_tariff = await Tariffs.get_or_none(id=tariff_id)
            if not new_tariff:
                logger.error(f"Не найден тариф {tariff_id} при обработке платежа")
                return {"status": "error", "message": "Tariff not found"}
                
            # Проверяем, есть ли у пользователя активная подписка и активный тариф
            current_date = date.today()
            additional_days = 0
            user_expired_at = normalize_date(user.expired_at)

            if user_expired_at and user_expired_at > current_date and user.active_tariff_id:
                # У пользователя есть действующая подписка
                try:
                    # Получаем активный тариф
                    active_tariff = await ActiveTariffs.get(id=user.active_tariff_id)
                    
                    # Вычисляем оставшиеся дни подписки
                    days_remaining = (user_expired_at - current_date).days
                    logger.info(f"У пользователя {user.id} осталось {days_remaining} дней подписки")
                    
                    # Рассчитываем количество дней, которое давал старый тариф
                    old_months = int(active_tariff.months)
                    old_target_date = add_months_safe(current_date, old_months)
                    old_total_days = (old_target_date - current_date).days
                    
                    # Рассчитываем процент неиспользованной подписки
                    unused_percent = days_remaining / old_total_days if old_total_days > 0 else 0
                    unused_value = unused_percent * active_tariff.price
                    
                    logger.info(
                        f"Неиспользованная часть подписки пользователя {user.id}: " 
                        f"{days_remaining}/{old_total_days} дней ({unused_percent:.2%}), " 
                        f"стоимость: {unused_value:.2f} руб."
                    )
                    
                    # ИСПРАВЛЕННАЯ ЛОГИКА: рассчитываем через пропорцию от общей суммы
                    # Выполняем ТОЛЬКО если скидка не была списана (например, повторная оплата без скидки)
                    if new_tariff.price > 0 and not consumed:
                        # Получаем device_count и рассчитываем правильную цену
                        try:
                            device_count = int(data.get("device_count", 1))
                        except (ValueError, TypeError):
                            device_count = 1
                        if device_count < 1:
                            device_count = 1
                        
                        # Рассчитываем итоговую цену для указанного количества устройств
                        correct_new_tariff_price = new_tariff.calculate_price(device_count)
                        
                        # Общая сумма = заплачено пользователем + компенсация за старый тариф
                        total_paid = max(0.0, base_paid_price)
                        total_amount = total_paid + unused_value
                        
                        # Рассчитываем новый период подписки (стандартный для тарифа)
                        tariff_months = int(new_tariff.months)
                        new_target_date = add_months_safe(current_date, tariff_months)
                        new_total_days = (new_target_date - current_date).days
                        
                        # Пропорция: x дней / общая_сумма = полный_период_тарифа / цена_тарифа
                        # x = общая_сумма * полный_период_тарифа / цена_тарифа
                        calculated_days = int(total_amount * new_total_days / correct_new_tariff_price)
                        
                        logger.info(
                            f"ИСПРАВЛЕННЫЙ расчёт для пользователя {user.id}: "
                            f"Заплачено: {total_paid:.2f} руб + Компенсация: {unused_value:.2f} руб = "
                            f"Общая сумма: {total_amount:.2f} руб. "
                            f"Пропорция: {calculated_days} дней = {total_amount:.2f} * {new_total_days} / {correct_new_tariff_price:.2f}"
                        )
                        
                        # Устанавливаем рассчитанные дни как итоговые (без additional_days)
                        additional_days = 0  # Сбрасываем, так как используем calculated_days
                        days = calculated_days  # Переопределяем days
                except Exception as e:
                    logger.error(f"Ошибка при расчете переноса подписки для {user.id}: {str(e)}")
                    additional_days = 0  # При ошибке не добавляем дополнительные дни
            
            # Рассчитываем точное количество дней для указанного количества месяцев
            # При смене тарифа days уже рассчитано через пропорцию
            if 'calculated_days' not in locals():
                # Обычная покупка нового тарифа без смены
                current_date = date.today()
                target_date = add_months_safe(current_date, months)
                days = (target_date - current_date).days
                logger.info(f"Стандартное количество дней подписки: {days}")
            else:
                # days уже рассчитано через пропорцию при смене тарифа
                logger.info(f"Итоговое количество дней подписки (через пропорцию): {days}")
        else:
            # Если нет tariff_id, значит это автоплатеж или другой тип платежа, просто рассчитываем дни как обычно
            current_date = date.today()
            target_date = add_months_safe(current_date, months)
            days = (target_date - current_date).days
            logger.info(f"Стандартное количество дней подписки: {days}")
        
        # Рассчитываем точное количество дней для указанного количества месяцев
        try:
            if tariff_id is not None and user.active_tariff_id:
                old_active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
                if old_active_tariff:
                    remaining_gb = max(
                        0.0,
                        float(old_active_tariff.lte_gb_total or 0)
                        - float(old_active_tariff.lte_gb_used or 0),
                    )
                    lte_refund_amount = _round_rub(
                        remaining_gb * float(old_active_tariff.lte_price_per_gb or 0)
                    )
                    if lte_refund_amount > 0:
                        user.balance += lte_refund_amount
                        logger.info(
                            f"Начислен бонус за остаток LTE-трафика {remaining_gb:.2f} GB "
                            f"({lte_refund_amount:.2f} руб.) пользователю {user.id}"
                        )

            amount_from_balance = _round_rub(data.get("amount_from_balance", 0))
            if amount_from_balance > 0:
                initial_balance = user.balance
                user.balance = max(0, user.balance - amount_from_balance)
                logger.info(
                    f"Списание с бонусного баланса пользователя {user.id}. "
                    f"Сумма: {amount_from_balance}. Баланс до: {initial_balance}, После: {user.balance}",
                    extra={
                        'payment_id': payment.id,
                        'user_id': user.id,
                        'amount_from_balance': amount_from_balance
                    }
                )
            
            # Устанавливаем новую дату окончания подписки
            # В случае автопродления переходим на новый тариф, сбрасывая старую подписку
            is_auto = data.get("is_auto", False)
            if is_auto:
                # Для автопродления используем extend_subscription вместо прямой установки даты
                await user.extend_subscription(days)
                logger.info(
                    f"Автопродление: подписка пользователя {user.id} продлена на {days} дней, новая дата истечения: {user.expired_at}"
                )
            else:
                # При смене тарифа (когда calculated_days определено) устанавливаем от текущей даты
                # чтобы избежать двойного учёта компенсации
                if 'calculated_days' in locals():
                    # Смена тарифа с компенсацией - устанавливаем от текущей даты
                    user.expired_at = current_date + timedelta(days=days)
                    logger.info(
                        f"Смена тарифа для пользователя {user.id}: установлена дата {user.expired_at} "
                        f"({days} дней от текущей даты, рассчитано через пропорцию)"
                    )
                else:
                    # Обычное продление или новая подписка без смены тарифа
                    await user.extend_subscription(days)
                    logger.info(
                        f"Подписка пользователя {user.id} продлена на {days} дней, новая дата истечения: {user.expired_at} "
                        f"(с учетом оставшихся дней предыдущей подписки/триала)"
                    )
                
            # If a tariff_id is provided in metadata, ensure it's created in ActiveTariffs and assign to user
            if tariff_id is not None:
                original = await Tariffs.get_or_none(id=tariff_id)
                if original:
                    # Получаем device_count из метаданных платежа
                    try:
                        device_count = int(data.get("device_count", 1))
                    except (ValueError, TypeError):
                        device_count = 1
                    if device_count < 1:
                        device_count = 1
                    
                    # Рассчитываем итоговую цену для указанного количества устройств
                    calculated_price = original.calculate_price(device_count)
                    
                    # Удаляем предыдущий активный тариф, если он есть
                    if user.active_tariff_id:
                        old_active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
                        if old_active_tariff:
                            logger.info(f"Удаляем предыдущий активный тариф {user.active_tariff_id} пользователя {user.id}")
                            await old_active_tariff.delete()
                        else:
                            logger.warning(f"Не найден активный тариф {user.active_tariff_id} для удаления у пользователя {user.id}")
                    
                    # код по сбросу HWID временно отключен
                    # if user.remnawave_uuid:
                        # await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)
                    
                    msk_today = datetime.now(MSK_TZ).date()
                    usage_snapshot = None
                    if user.remnawave_uuid:
                        usage_snapshot = await _fetch_today_lte_usage_gb(
                            str(user.remnawave_uuid)
                        )
                    # Create a new active tariff entry with random ID
                    if "lte_price_per_gb" in data:
                        lte_price_snapshot = float(data.get("lte_price_per_gb") or 0)
                    else:
                        lte_price_snapshot = float(original.lte_price_per_gb or 0)
                    active_tariff = await ActiveTariffs.create(
                        user=user,  # Link to this user
                        name=original.name,
                        months=original.months,
                        price=calculated_price,  # Используем рассчитанную цену
                        hwid_limit=device_count,  # Используем выбранное количество устройств
                        lte_gb_total=lte_gb,
                        lte_gb_used=0.0,
                        lte_price_per_gb=lte_price_snapshot,
                        lte_usage_last_date=msk_today,
                        lte_usage_last_total_gb=usage_snapshot if usage_snapshot is not None else 0.0,
                        progressive_multiplier=original.progressive_multiplier,
                        residual_day_fraction=0.0
                    )
                    active_tariff_for_lte = active_tariff
                    # Link user to this active tariff
                    user.active_tariff_id = active_tariff.id
                    user.lte_gb_total = lte_gb

                    # Устанавливаем hwid_limit пользователю из выбранного количества устройств
                    user.hwid_limit = device_count
                    logger.info(f"Created ActiveTariff {active_tariff.id} for user {user.id} based on tariff {original.id}, device_count={device_count}, установлен hwid_limit={device_count}")

                    # ВАЖНО: сохраняем active_tariff_id и hwid_limit в БД как можно раньше
                    # чтобы минимизировать race condition с remnawave_updater
                    try:
                        await user.save(update_fields=["active_tariff_id", "hwid_limit"])
                        logger.debug(f"Ранее сохранены active_tariff_id={active_tariff.id} и hwid_limit={device_count} для пользователя {user.id}")
                    except Exception as persist_exc:
                        logger.warning(f"Не удалось рано сохранить active_tariff_id/hwid_limit для {user.id}: {persist_exc}")
                else:
                    logger.error(f"Original tariff {tariff_id} not found; skipping ActiveTariffs")
            
            # После успешной оплаты сбрасываем счётчик уменьшений лимита устройств
            if user.active_tariff_id:
                await ActiveTariffs.filter(id=user.active_tariff_id).update(devices_decrease_count=0)
            
            # Синхронизируем данные с RemnaWave
            if user.remnawave_uuid:
                # Настройки бесконечных повторных попыток с ограничением по времени
                max_total_time = 60  # Максимальное время в секундах для всех попыток (1 минута)
                start_time = datetime.now()
                retry_count = 0
                remnawave_client = None
                success = False
                
                try:
                    # Подготавливаем параметры для обновления
                    update_params = {}

                    # Передаём дату в формате date; клиент внутри сам форматирует expireAt
                    update_params["expireAt"] = user.expired_at
                    
                    # Определяем hwid_limit ТОЛЬКО для новых подписок (когда есть tariff_id),
                    # при автопродлении hwid_limit не меняем
                    hwid_limit = None
                    if tariff_id is not None and original:
                        try:
                            device_count = int(data.get("device_count", 1))
                        except (ValueError, TypeError):
                            device_count = 1
                        if device_count < 1:
                            device_count = 1
                        hwid_limit = device_count
                        logger.info(f"Новая подписка: устанавливаем hwid_limit={hwid_limit} из device_count для тарифа ID={original.id}")
                        update_params["hwidDeviceLimit"] = hwid_limit
                    else:
                        logger.info(f"Автопродление: hwid_limit не меняем, обновляем только дату истечения")
                    
                    # Цикл повторных попыток обновления информации в RemnaWave
                    while not success:
                        # Проверяем, не превысили ли мы общее время попыток
                        elapsed_time = (datetime.now() - start_time).total_seconds()
                        if elapsed_time > max_total_time:
                            logger.error(
                                f"Превышено максимальное время ({max_total_time} сек) для обновления пользователя {user.id} в RemnaWave. "
                                f"Выполнено {retry_count} попыток за {elapsed_time:.1f} сек."
                            )
                            break
                            
                        try:
                            retry_count += 1
                            
                            # Создаем клиент RemnaWave для каждой попытки
                            if remnawave_client:
                                await remnawave_client.close()
                            remnawave_client = RemnaWaveClient(
                                remnawave_settings.url, 
                                remnawave_settings.token.get_secret_value()
                            )
                            
                            # Обновляем пользователя в RemnaWave
                            logger.info(
                                f"Попытка #{retry_count} [{elapsed_time:.1f} сек]: Обновляем пользователя {user.id} в RemnaWave (UUID: {user.remnawave_uuid}). "
                                f"Новая дата: {user.expired_at}" + 
                                (f", hwid_limit: {hwid_limit}" if hwid_limit is not None else ", hwid_limit без изменений")
                            )
                            
                            try:
                                await remnawave_client.users.update_user(
                                    uuid=user.remnawave_uuid,
                                    **update_params
                                )
                            except Exception as update_err:
                                # Если юзер удален в RemnaWave – пересоздаём и пытаемся снова
                                if any(token in str(update_err) for token in ["User not found", "A039", "Update user error"]):
                                    recreated = await user.recreate_remnawave_user()
                                    if recreated and user.remnawave_uuid:
                                        await remnawave_client.users.update_user(
                                            uuid=user.remnawave_uuid,
                                            **update_params
                                        )
                                else:
                                    raise
                            
                            logger.info(f"УСПЕХ! Пользователь {user.id} обновлен в RemnaWave с попытки #{retry_count} за {elapsed_time:.1f} сек")
                            success = True
                            break  # Успешное обновление, выходим из цикла
                            
                        except Exception as retry_exc:
                            # Ограничиваем экспоненциальный рост задержки
                            backoff_time = min(10, 0.5 * (2 ** min(retry_count, 5)) + random.uniform(0, 0.5))
                            logger.warning(
                                f"Ошибка при обновлении пользователя {user.id} в RemnaWave (попытка {retry_count}, прошло {elapsed_time:.1f} сек): {str(retry_exc)}. "
                                f"Повторная попытка через {backoff_time:.2f} сек."
                            )
                            await asyncio.sleep(backoff_time)
                    
                    # Если не удалось обновить после всех попыток
                    if not success:
                        logger.error(
                            f"НЕ УДАЛОСЬ обновить пользователя {user.id} в RemnaWave даже после {retry_count} попыток. "
                            f"Общее время: {(datetime.now() - start_time).total_seconds():.1f} сек."
                        )
                    
                except Exception as e:
                    logger.error(f"Ошибка при обновлении пользователя {user.id} в RemnaWave: {str(e)}")
                    # Продолжаем обработку платежа, несмотря на ошибку синхронизации с RemnaWave
                finally:
                    # Закрываем клиент в любом случае
                    if remnawave_client:
                        try:
                            await remnawave_client.close()
                        except Exception as close_exc:
                            logger.warning(f"Ошибка при закрытии клиента RemnaWave: {str(close_exc)}")

            # Если это автоплатеж и он успешен, обновляем статус подписки
            if payment.payment_method.saved and not is_auto:
                user.renew_id = payment.payment_method.id
                user.is_subscribed = True
            
            # Если это автоплатеж и он успешен, обновляем статус подписки
            if is_auto and payment.status == "succeeded":
                user.is_subscribed = True
            
            # Если у пользователя был пробный период, сбрасываем флаг
            if user.is_trial:
                user.is_trial = False
                logger.info(
                    f"Сброшен флаг пробного периода для пользователя {user.id} после оплаты подписки",
                    extra={
                        'payment_id': payment.id,
                        'user_id': user.id
                    }
                )
            
            await user.save()  # Сохраняем пользователя (включая обновленный баланс)

            active_tariff_current = active_tariff_for_lte
            if active_tariff_current is None and user.active_tariff_id:
                active_tariff_current = await ActiveTariffs.get_or_none(id=user.active_tariff_id)

            if active_tariff_current:
                if is_auto:
                    active_tariff_current.lte_gb_used = 0.0
                    msk_today = datetime.now(MSK_TZ).date()
                    update_fields = ["lte_gb_used"]
                    usage_snapshot = None
                    if user.remnawave_uuid:
                        usage_snapshot = await _fetch_today_lte_usage_gb(
                            str(user.remnawave_uuid)
                        )
                    if usage_snapshot is not None:
                        active_tariff_current.lte_usage_last_date = msk_today
                        active_tariff_current.lte_usage_last_total_gb = usage_snapshot
                        update_fields.extend(
                            ["lte_usage_last_date", "lte_usage_last_total_gb"]
                        )
                    elif active_tariff_current.lte_usage_last_date != msk_today:
                        active_tariff_current.lte_usage_last_date = msk_today
                        active_tariff_current.lte_usage_last_total_gb = 0.0
                        update_fields.extend(
                            ["lte_usage_last_date", "lte_usage_last_total_gb"]
                        )
                    await active_tariff_current.save(update_fields=update_fields)
                await NotificationMarks.filter(user_id=user.id, type="lte_usage").delete()
                if user.remnawave_uuid:
                    try:
                        effective_lte_total = (
                            user.lte_gb_total
                            if user.lte_gb_total is not None
                            else (active_tariff_current.lte_gb_total or 0)
                        )
                        should_enable_lte = effective_lte_total > (active_tariff_current.lte_gb_used or 0)
                        await set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable_lte)
                    except Exception as e:
                        logger.error(f"Ошибка обновления LTE-сквада после оплаты для {user.id}: {e}")
            
            amount_paid_via_yookassa = float(payment.amount.value)
            full_tariff_price_for_history = amount_paid_via_yookassa + amount_from_balance
            
            # Сохраняем информацию об успешном платеже (идемпотентно)
            await _upsert_processed_payment(
                payment_id=payment.id,
                user_id=user.id,
                amount=full_tariff_price_for_history,  # Используем полную стоимость тарифа
                amount_external=amount_paid_via_yookassa,
                amount_from_balance=amount_from_balance,
                status="succeeded",
            )

            # Partner program: award cashback to partner referrer (money-based).
            try:
                await _award_partner_cashback(
                    payment_id=str(payment.id),
                    referral_user=user,
                    amount_rub_total=int(_round_rub(float(full_tariff_price_for_history))),
                )
            except Exception:
                # best-effort, do not affect payment flow
                pass
            
            logger.info(
                f"Успешно продлена подписка для пользователя {user.id} на {days} дней",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value, # Сумма платежа Yookassa
                    'amount_from_balance': amount_from_balance, # Сумма списания с баланса
                    'status': "succeeded",
                    'is_auto': is_auto,
                    'discount_percent': discount_percent,
                    'discount_id': discount_id,
                }
            )

            # Списываем использование скидки напрямую, если не списали ранее
            if not consumed:
                try:
                    consumed = await consume_discount_if_needed(discount_id)
                except Exception:
                    consumed = False

            # Если скидка не была списана (например, второй платёж без доступной скидки),
            # корректируем дни пропорционально фактически оплаченной сумме
            if not consumed and tariff_id is not None:
                try:
                    original = await Tariffs.get_or_none(id=tariff_id)
                    if original:
                        try:
                            device_count = int(data.get("device_count", 1))
                        except (ValueError, TypeError):
                            device_count = 1
                        if device_count < 1:
                            device_count = 1
                        correct_new_tariff_price = original.calculate_price(device_count)
                        amount_paid_by_user = float(payment.amount.value)
                        amount_from_balance = _round_rub(data.get("amount_from_balance", 0))
                        total_paid_now = amount_paid_by_user + amount_from_balance
                        base_paid_now = max(0.0, total_paid_now - lte_cost)
                        current_date = date.today()
                        original_months = int(original.months)
                        new_target_date = add_months_safe(current_date, original_months)
                        new_total_days = (new_target_date - current_date).days
                        proportional_days = int(base_paid_now * new_total_days / max(1, correct_new_tariff_price))
                        # Берём минимум, чтобы не подарить лишние дни
                        days = min(days, proportional_days)
                except Exception:
                    pass
            
            # IMPORTANT: всегда сообщаем пользователю результат оплаты в боте (и для ручных оплат тоже).
            try:
                await notify_renewal_success_yookassa(
                    user=user,
                    days=days,
                    amount_paid_via_yookassa=amount_paid_via_yookassa,
                    amount_from_balance=amount_from_balance,
                )
            except Exception as notify_exc:
                logger.error(
                    f"Ошибка при отправке уведомления об успешной оплате (YooKassa) для {user.id}: {notify_exc}"
                )

            if is_auto:
                # Начисление круток за автосписание: 1 крутка за каждый месяц
                try:
                    attempts_before = int(getattr(user, "prize_wheel_attempts", 0) or 0)
                    if months and months > 0:
                        user.prize_wheel_attempts = attempts_before + int(months)
                        await user.save()
                        logger.info(
                            f"Начислено {months} круток за автосписание пользователю {user.id}. Было: {attempts_before}, стало: {user.prize_wheel_attempts}"
                        )
                except Exception as award_exc:
                    logger.error(f"Не удалось начислить крутки за автосписание для {user.id}: {award_exc}")
                # Сообщение пользователю о начислении круток
                try:
                    await notify_spin_awarded(
                        user=user,
                        added_attempts=int(months),
                        total_attempts=int(user.prize_wheel_attempts or 0),
                    )
                except Exception as e_notify_spins:
                    logger.error(f"Ошибка уведомления о крутках (вебхук) для {user.id}: {e_notify_spins}")
            
        except Exception as e:
            logger.error(
                f"Ошибка при продлении подписки в webhook'е YooKassa: {e}",
                extra={'payment_id': payment.id}
            )
            return {"status": "error", "message": "Error extending subscription"}

        amount = payment.amount.value
        referrer = None

        # Referral rewards (days-based, not money): apply ONLY once per referred user.
        if user.referred_by:
            try:
                device_count = 1
                if isinstance(data, dict):
                    try:
                        device_count = int(data.get("device_count", 1) or 1)
                    except (TypeError, ValueError):
                        device_count = 1

                reward_res = await _apply_referral_first_payment_reward(
                    referred_user_id=user.id,
                    payment_id=str(payment.id),
                    amount_rub=int(float(amount)) if amount is not None else None,
                    months=int(months or 0),
                    device_count=device_count,
                )

                if reward_res.get("applied"):
                    referrer = await Users.get(id=int(reward_res["referrer_id"]))
                    try:
                        await on_referral_payment(
                            user=referrer,
                            referral=user,
                            amount=int(float(amount)) if amount is not None else 0,
                            bonus_days=int(reward_res["referrer_bonus_days"]),
                            friend_bonus_days=int(reward_res["friend_bonus_days"]),
                            months=int(reward_res["months"]),
                            device_count=int(reward_res["device_count"]),
                            applied_to_subscription=bool(reward_res["applied_to_subscription"]),
                        )
                    except Exception as e_notify:
                        logger.error(f"Ошибка уведомления реферера {referrer.id} о бонусных днях: {e_notify}")
            except Exception as e:
                logger.error(
                    f"Ошибка при обработке реферала (ledger) в webhook'е YooKassa: {e}",
                    extra={'payment_id': payment.id}
                )

        try:
            lte_gb_for_log = None
            if isinstance(data, dict) and data.get("lte_gb") is not None:
                try:
                    lte_gb_for_log = int(data.get("lte_gb"))
                except (TypeError, ValueError):
                    lte_gb_for_log = None
            if lte_gb_for_log is None:
                lte_gb_for_log = user.lte_gb_total if hasattr(user, "lte_gb_total") else None

            await on_payment(
                user_id=user.id,
                is_sub=user.is_subscribed,
                referrer=referrer.name() if referrer else None,
                amount=amount,
                months=months,
                method="yookassa",
                payment_id=payment.id,
                is_auto=is_auto,
                utm=user.utm if hasattr(user, "utm") else None,
                discount_percent=discount_percent,
                device_count=(int(data.get("device_count", 1)) if isinstance(data.get("device_count"), (int, str)) else None),
                old_expired_at=old_expired_at,
                new_expired_at=user.expired_at,
                lte_gb_total=lte_gb_for_log,
            )
        except Exception as e:
            logger.error(
                f"Ошибка при отправке уведомления о платеже: {e}",
                extra={'payment_id': payment.id}
            )

        return {"status": "ok"}
    except Exception as e:
        logger.error(
            f"Непредвиденная ошибка в webhook'е YooKassa: {e}",
            extra={'payment_id': payment.id if payment else 'unknown'}
        )
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/{tariff_id}")
async def pay(
    tariff_id: int,
    email: str,
    device_count: int = 1,
    lte_gb: int = 0,
    user: Users = Depends(validate),
):
    tariff = await Tariffs.get_or_none(id=tariff_id)
    if tariff is None:
        raise HTTPException(status_code=404, detail="Tariff not found")

    # Проверяем количество устройств
    if device_count < 1:
        device_count = 1
    
    months = int(tariff.months)
    if lte_gb is None:
        lte_gb = 0
    try:
        lte_gb = int(lte_gb)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Некорректное значение LTE лимита")
    if lte_gb < 0:
        raise HTTPException(status_code=400, detail="Некорректное значение LTE лимита")
    if lte_gb > 0 and not tariff.lte_enabled:
        raise HTTPException(status_code=400, detail="LTE недоступен для выбранного тарифа")

    lte_price_per_gb = float(tariff.lte_price_per_gb or 0) if tariff.lte_enabled else 0.0
    lte_cost = _round_rub(lte_gb * lte_price_per_gb)

    # Рассчитываем цену для указанного количества устройств (без LTE)
    base_full_price = int(tariff.calculate_price(device_count))
    discounted_price, discount_id, discount_percent = await apply_personal_discount(
        user.id, base_full_price, months
    )
    full_price = int(discounted_price) + lte_cost
    user_balance = float(user.balance)
    old_expired_at = user.expired_at
    old_active_tariff = None
    lte_refund_amount = 0
    if user.active_tariff_id:
        old_active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if old_active_tariff:
            remaining_gb = max(
                0.0,
                float(old_active_tariff.lte_gb_total or 0)
                - float(old_active_tariff.lte_gb_used or 0),
            )
            lte_refund_amount = _round_rub(
                remaining_gb * float(old_active_tariff.lte_price_per_gb or 0)
            )

    try:
        current_date = date.today()
        target_date = add_months_safe(current_date, months)
        days = (target_date - current_date).days
    except Exception as e:
        logger.error(
            f"Ошибка при расчете дней подписки для пользователя {user.id} и тарифа {tariff_id}: {e}",
            extra={'user_id': user.id, 'tariff_id': tariff_id, 'months': months}
        )
        raise HTTPException(status_code=500, detail="Error calculating subscription days")

    # Проверка полной оплаты с баланса
    effective_balance = user_balance + lte_refund_amount
    if effective_balance >= full_price:
        logger.info(
            f"Оплата тарифа {tariff_id} для пользователя {user.id} полностью с баланса. "
            f"Цена: {full_price}, Баланс: {user_balance}, Скидка: {discount_percent}% (id={discount_id}), "
            f"LTE: {lte_gb} GB"
        )
        if lte_refund_amount > 0:
            user.balance += lte_refund_amount
            logger.info(
                f"Начислен бонус за остаток LTE-трафика "
                f"({lte_refund_amount:.2f} руб.) пользователю {user.id} перед списанием"
            )

        current_date = date.today()
        additional_days = 0
        user_expired_at = normalize_date(user.expired_at)
        # --- NEW: перерасчёт остатка по старому тарифу ---
        if user_expired_at and user_expired_at > current_date and user.active_tariff_id:
            try:
                active_tariff = await ActiveTariffs.get(id=user.active_tariff_id)
                days_remaining = (user_expired_at - current_date).days
                logger.info(f"У пользователя {user.id} осталось {days_remaining} дней подписки")
                old_months = int(active_tariff.months)
                old_target_date = add_months_safe(current_date, old_months)
                old_total_days = (old_target_date - current_date).days
                unused_percent = days_remaining / old_total_days if old_total_days > 0 else 0
                unused_value = unused_percent * active_tariff.price
                logger.info(
                    f"Неиспользованная часть подписки пользователя {user.id}: "
                    f"{days_remaining}/{old_total_days} дней (стоимость: {unused_value:.2f} руб.)"
                )
                if discounted_price > 0:
                    # ИСПРАВЛЕННАЯ ЛОГИКА: рассчитываем через пропорцию от общей суммы
                    # Общая сумма = оплата базового тарифа + компенсация за старый тариф
                    total_amount = float(discounted_price) + unused_value
                    
                    # Рассчитываем новый период подписки (стандартный для тарифа)
                    tariff_months = int(tariff.months)
                    new_target_date = add_months_safe(current_date, tariff_months)
                    new_total_days = (new_target_date - current_date).days
                    
                    # Пропорция: x дней / общая_сумма = полный_период_тарифа / цена_тарифа
                    # x = общая_сумма * полный_период_тарифа / цена_тарифа
                    calculated_days = int(total_amount * new_total_days / float(discounted_price))
                    
                    logger.info(
                        f"ИСПРАВЛЕННЫЙ расчёт (баланс) для пользователя {user.id}: "
                        f"Заплачено: {float(discounted_price):.2f} руб + Компенсация: {unused_value:.2f} руб = "
                        f"Общая сумма: {total_amount:.2f} руб. "
                        f"Пропорция: {calculated_days} дней = {total_amount:.2f} * {new_total_days} / {float(discounted_price):.2f}"
                    )
                    
                    # Устанавливаем рассчитанные дни как итоговые
                    additional_days = 0  # Сбрасываем, так как используем calculated_days
                    days = calculated_days  # Переопределяем days
            except Exception as e:
                logger.error(f"Ошибка при расчете переноса подписки для {user.id}: {str(e)}")
                additional_days = 0
        # При смене тарифа days уже рассчитано через пропорцию
        if 'calculated_days' not in locals():
            # Обычная покупка без смены тарифа - days уже рассчитано выше
            logger.info(f"Стандартное количество дней подписки: {days}")
        else:
            # days уже рассчитано через пропорцию при смене тарифа
            logger.info(f"Итоговое количество дней подписки (через пропорцию): {days}")

        user.balance -= full_price
        
        # При смене тарифа компенсация уже учтена в calculated_days
        # Поэтому устанавливаем дату от текущего дня, чтобы избежать двойного учёта
        if 'calculated_days' in locals():
            # Смена тарифа с компенсацией - устанавливаем от текущей даты
            user.expired_at = current_date + timedelta(days=days)
            logger.info(
                f"Смена тарифа (баланс) для пользователя {user.id}: установлена дата {user.expired_at} "
                f"({days} дней от текущей даты, рассчитано через пропорцию)"
            )
        else:
            # Обычное продление без смены тарифа
            await user.extend_subscription(days)
            logger.info(
                f"Продление (баланс) для пользователя {user.id}: дата {user.expired_at} "
                f"(с учетом оставшихся дней предыдущей подписки/триала)"
            )

        # Если у пользователя был пробный период, сбрасываем флаг
        if user.is_trial:
            user.is_trial = False
            logger.info(f"Сброшен флаг пробного периода для пользователя {user.id} после оплаты с баланса")

        # --- NEW: Создаём/обновляем ActiveTariffs и лимит устройств ---
        # Удаляем предыдущий активный тариф, если есть
        if user.active_tariff_id:
            if old_active_tariff is None:
                old_active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
            if old_active_tariff:
                logger.info(f"Удаляем предыдущий активный тариф {user.active_tariff_id} пользователя {user.id}")
                await old_active_tariff.delete()
            else:
                logger.warning(f"Не найден активный тариф {user.active_tariff_id} для удаления у пользователя {user.id}")

        # код по сбросу HWID временно отключен
        # if user.remnawave_uuid:
            # await cleanup_user_hwid_devices(user.id, user.remnawave_uuid)

        msk_today = datetime.now(MSK_TZ).date()
        usage_snapshot = None
        if user.remnawave_uuid:
            usage_snapshot = await _fetch_today_lte_usage_gb(
                str(user.remnawave_uuid)
            )
        # Создаём новый активный тариф
        # ВАЖНО: сохраняем в price базовую стоимость тарифа без персональной скидки,
        # чтобы автоплатежи не применяли скидку дважды
        base_calculated_price = tariff.calculate_price(device_count)
        active_tariff = await ActiveTariffs.create(
            user=user,
            name=tariff.name,
            months=tariff.months,
            price=base_calculated_price,  # Цена без персональной скидки
            hwid_limit=device_count,  # Используем выбранное количество устройств
            lte_gb_total=lte_gb,
            lte_gb_used=0.0,
            lte_price_per_gb=lte_price_per_gb,
            lte_usage_last_date=msk_today,
            lte_usage_last_total_gb=usage_snapshot if usage_snapshot is not None else 0.0,
            progressive_multiplier=tariff.progressive_multiplier,
            residual_day_fraction=0.0
        )
        user.active_tariff_id = active_tariff.id
        user.lte_gb_total = lte_gb

        # Устанавливаем hwid_limit пользователю из выбранного количества устройств
        user.hwid_limit = device_count
        logger.info(f"При покупке с баланса установлен hwid_limit={device_count} для пользователя {user.id}")

        # ВАЖНО: сохраняем active_tariff_id и hwid_limit в БД как можно раньше
        # чтобы минимизировать race condition с remnawave_updater
        try:
            await user.save(update_fields=["active_tariff_id", "hwid_limit"])
            logger.debug(f"Ранее сохранены active_tariff_id={active_tariff.id} и hwid_limit={device_count} для пользователя {user.id}")
        except Exception as persist_exc:
            logger.warning(f"Не удалось рано сохранить active_tariff_id/hwid_limit для {user.id}: {persist_exc}")

        # Сохраняем ВСЕ изменения пользователя (баланс, дата, is_trial и т.д.)
        await user.save()
        await NotificationMarks.filter(user_id=user.id, type="lte_usage").delete()

        # После оплаты с баланса также обнуляем счётчик уменьшений
        if user.active_tariff_id:
            await ActiveTariffs.filter(id=user.active_tariff_id).update(devices_decrease_count=0)

        # Синхронизируем лимит устройств и дату окончания с RemnaWave
        if user.remnawave_uuid:
            remnawave_client = None
            try:
                remnawave_client = RemnaWaveClient(
                    remnawave_settings.url,
                    remnawave_settings.token.get_secret_value()
                )
                await remnawave_client.users.update_user(
                    uuid=user.remnawave_uuid,
                    expireAt=user.expired_at,
                    hwidDeviceLimit=device_count
                )
                logger.info(f"Синхронизирован hwid_limit={device_count} и expireAt={user.expired_at} для пользователя {user.id} в RemnaWave")
            except Exception as e:
                logger.error(f"Ошибка при синхронизации hwid_limit/expireAt с RemnaWave для пользователя {user.id}: {e}")
            finally:
                if remnawave_client:
                    try:
                        await remnawave_client.close()
                    except Exception as close_exc:
                        logger.warning(f"Ошибка при закрытии клиента RemnaWave: {close_exc}")
            try:
                should_enable_lte = lte_gb > 0
                await set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable_lte)
            except Exception as e:
                logger.error(f"Ошибка обновления LTE-сквада для {user.id}: {e}")

        payment_id = f"balance_{user.id}_{int(datetime.now().timestamp())}_{randint(100, 999)}"

        await _upsert_processed_payment(
            payment_id=payment_id,
            user_id=user.id,
            amount=full_price,  # Итоговая стоимость (с учетом скидки)
            amount_external=0,
            amount_from_balance=full_price,
            status="succeeded",  # Статус как при обычной успешной оплате
        )

        # Partner program: award cashback on "balance" payments too.
        try:
            await _award_partner_cashback(
                payment_id=str(payment_id),
                referral_user=user,
                amount_rub_total=int(_round_rub(float(full_price))),
            )
        except Exception:
            pass

        # Списываем одно использование скидки (если не постоянная)
        await consume_discount_if_needed(discount_id)

        referrer = await user.referrer() # Получаем реферера для уведомления админу
        try:
            await on_payment(
                user_id=user.id,
                is_sub=user.is_subscribed, # Передаем текущий статус автопродления
                referrer=referrer.name() if referrer else None,
                amount=full_price, # Сумма уведомления - итоговая цена с учетом скидки
                months=months,
                method="balance", # Указываем метод оплаты
                payment_id=payment_id,
                utm=user.utm if hasattr(user, "utm") else None,
                discount_percent=discount_percent,
                device_count=device_count,
                old_expired_at=old_expired_at,
                new_expired_at=user.expired_at,
                lte_gb_total=lte_gb,
            )
        except Exception as e:
            logger.error(
                f"Ошибка при отправке уведомления о платеже с баланса: {e}",
                extra={'payment_id': payment_id, 'user_id': user.id}
            )
            
        return {"status": "success", "message": "Оплачено с бонусного баланса"}

    else:
        # Логика частичной оплаты
        amount_to_pay = max(1.0, full_price - user_balance) # Минимум 1 рубль для Yookassa
        amount_from_balance = full_price - amount_to_pay

        logger.info(
            f"Создание платежа для пользователя {user.id}. "
            f"Тариф: {tariff_id}, Полная цена: {full_price}, Баланс: {user_balance}, "
            f"К оплате: {amount_to_pay}, С баланса: {amount_from_balance}"
        )

        metadata = {
            "user_id": user.id,
            "month": months,
            "amount_from_balance": amount_from_balance, # Добавляем сумму списания с баланса
            "tariff_id": tariff.id,
            "device_count": device_count,  # Добавляем количество устройств
            "base_full_price": base_full_price,
            "discounted_price": discounted_price,
            "discount_percent": discount_percent,
            "discount_id": discount_id,
            "lte_gb": lte_gb,
            "lte_price_per_gb": lte_price_per_gb,
            "lte_cost": lte_cost,
        }

        # Build return_url safely:
        # Always return to bot chat (not Mini App). Status is delivered by bot message.
        return_url = await _resolve_payment_return_url()

        # Обернуть синхронный вызов YooKassa в async с таймаутом
        try:
            payment_data = {
                "amount": {
                    "value": str(amount_to_pay),
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    # Return URL after the payment is completed in YooKassa.
                    #
                    # Why:
                    # - If the payment is opened in an external browser, returning to the hosted Mini App URL
                    #   keeps the user in that browser (doesn't bring them back to Telegram).
                    # - A Telegram deep link (t.me/...) can reopen Telegram from the external browser.
                    #
                    # Priority:
                    # 1) TELEGRAM_PAYMENT_RETURN_URL, but only if it's a bot chat link
                    # 2) fallback to bot chat derived from TELEGRAM_WEBAPP_URL / bot username
                    "return_url": return_url,
                },
                "metadata": metadata,
                "capture": True,
                "description": f"Оплата подписки пользователя {user.id} (Тариф: {tariff.name})",
                "save_payment_method": True,
                "receipt": {
                    "customer": {"email": email},
                    "items": [{
                        "description": f"Подписка пользователя {user.id} ({tariff.name})",
                        "quantity": "1",
                        "amount": {
                            "value": str(amount_to_pay),
                            "currency": "RUB"
                        },
                        "vat_code": 1, # TODO: Проверить НДС
                        "payment_subject": "service",
                        "payment_mode": "full_payment"
                    }]
                }
            }

            idempotence_key = str(randint(100000, 999999999999))

            # Используем asyncio.to_thread для неблокирующего вызова с таймаутом
            payment = await asyncio.wait_for(
                asyncio.to_thread(partial(Payment.create, payment_data, idempotence_key)),
                timeout=30.0
            )

        except asyncio.TimeoutError:
            logger.error(
                f"Таймаут при создании платежа YooKassa для пользователя {user.id}. "
                f"Тариф: {tariff_id}, Сумма: {amount_to_pay}",
                extra={'user_id': user.id, 'tariff_id': tariff_id, 'amount': amount_to_pay}
            )
            raise HTTPException(
                status_code=503,
                detail="Сервис оплаты временно недоступен. Пожалуйста, попробуйте позже."
            )
        except (ConnectTimeoutError, ReadTimeoutError, RequestsConnectionError, RequestsTimeout) as network_err:
            logger.error(
                f"Сетевая ошибка при создании платежа YooKassa для пользователя {user.id}: {network_err}",
                extra={'user_id': user.id, 'tariff_id': tariff_id, 'amount': amount_to_pay, 'error_type': type(network_err).__name__}
            )
            raise HTTPException(
                status_code=503,
                detail="Сервис оплаты временно недоступен. Пожалуйста, попробуйте позже."
            )
        except Exception as e:
            logger.error(
                f"Неожиданная ошибка при создании платежа YooKassa для пользователя {user.id}: {e}",
                extra={'user_id': user.id, 'tariff_id': tariff_id, 'amount': amount_to_pay, 'error_type': type(e).__name__},
                exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail="Ошибка при создании платежа. Пожалуйста, попробуйте позже."
            )

        # Резервации отключены

        # Persist as "pending" so the server can reconcile even if webhook delivery fails
        # (e.g. user device was offline after payment).
        try:
            if payment and payment.id:
                await _upsert_processed_payment(
                    payment_id=payment.id,
                    user_id=user.id,
                    amount=float(amount_to_pay) + float(amount_from_balance),
                    amount_external=float(amount_to_pay),
                    amount_from_balance=float(amount_from_balance),
                    status="pending",
                )
        except Exception as e:
            logger.warning(
                f"Не удалось сохранить pending payment {getattr(payment, 'id', None)}: {e}",
                extra={'user_id': user.id, 'tariff_id': tariff_id},
            )

        return {"redirect_to": payment.confirmation.confirmation_url, "payment_id": payment.id}

async def create_auto_payment(user: Users, disable_on_fail: bool = True) -> bool:
    """
    Создает автоматический платеж для продления подписки
    Returns:
        bool: True если платеж успешно создан, False в случае ошибки
    """
    # Вычисляем will_retry один раз для всех уведомлений об ошибках
    user_expired_at = normalize_date(user.expired_at)
    will_retry = user_expired_at is not None and (user_expired_at - date.today()).days >= 0

    try:
        # --- Modify auto-payment logic to use active_tariff_id ---
        if not user.active_tariff_id:
            logger.error(f"У пользователя {user.id} не установлен active_tariff_id. Автопродление невозможно.")
            await notify_auto_renewal_failure(user, reason="Отсутствует информация о последнем активном тарифе", will_retry=will_retry)
            # Отключаем подписку, если нет активного тарифа
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            # Уведомляем админа об отключении автопродления из-за отсутствия тарифа
            await cancel_subscription(user, reason="Отсутствует информация о последнем активном тарифе")
            return False

        # Получаем детали тарифа из ActiveTariffs
        active_tariff = await ActiveTariffs.get_or_none(id=user.active_tariff_id)
        if not active_tariff:
            logger.error(
                f"Не найден активный тариф с ID {user.active_tariff_id} для пользователя {user.id}",
                extra={'user_id': user.id, 'active_tariff_id': user.active_tariff_id}
            )
            # Отключаем автопродление, если активный тариф не найден
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            logger.warning(f"Автопродление отключено для {user.id} из-за отсутствия активного тарифа ID={user.active_tariff_id} в базе.")
            await notify_auto_renewal_failure(user, reason=f"Не найден активный тариф (ID: {user.active_tariff_id}) для автопродления", will_retry=will_retry)
            # Уведомляем админа об отключении автопродления из-за отсутствия тарифа в базе
            await cancel_subscription(user, reason=f"Не найден активный тариф (ID: {user.active_tariff_id}) в базе")
            return False

        logger.info(f"Автопродление для пользователя {user.id}. Используется активный тариф ID={active_tariff.id} (Name: {active_tariff.name}, Price: {active_tariff.price})")

        months = int(active_tariff.months)
        base_full_price = int(active_tariff.price)
        lte_gb_total = int(active_tariff.lte_gb_total or 0)
        lte_price_per_gb = float(active_tariff.lte_price_per_gb or 0)
        lte_cost = 0
        if not bool(getattr(active_tariff, "lte_autopay_free", False)):
            lte_cost = _round_rub(lte_gb_total * lte_price_per_gb)
        # Применяем персональную скидку (если есть)
        discounted_price, discount_id, discount_percent = await apply_personal_discount(user.id, base_full_price, months)
        full_price = int(discounted_price) + lte_cost
        user_balance = float(user.balance)

        try:
            current_date = date.today()
            target_date = add_months_safe(current_date, months)
            days = (target_date - current_date).days
        except Exception as e:
            logger.error(
                f"Ошибка при расчете дней подписки для автоплатежа {user.id}, тариф {active_tariff.id}: {e}",
                extra={'user_id': user.id, 'tariff_id': active_tariff.id, 'months': months}
            )
            # Уведомляем пользователя о неудаче (здесь маловероятно, но все же)
            await notify_auto_renewal_failure(user, reason="Ошибка при расчете срока продления", will_retry=will_retry)
            return False

        # Проверка полной оплаты с баланса
        if user_balance >= full_price:
            old_expired_at = user.expired_at
            logger.info(
                f"Автопродление тарифа {active_tariff.id} для пользователя {user.id} полностью с баланса. "
                f"Цена: {full_price}, Баланс: {user_balance}, LTE: {lte_gb_total} GB"
            )

            initial_balance = user.balance
            user.balance -= full_price
            await user.extend_subscription(days)
            active_tariff.lte_gb_used = 0.0
            update_fields = ["lte_gb_used"]
            usage_snapshot = None
            msk_today = datetime.now(MSK_TZ).date()
            if user.remnawave_uuid:
                usage_snapshot = await _fetch_today_lte_usage_gb(
                    str(user.remnawave_uuid)
                )
            if usage_snapshot is not None:
                active_tariff.lte_usage_last_date = msk_today
                active_tariff.lte_usage_last_total_gb = usage_snapshot
                update_fields.extend(["lte_usage_last_date", "lte_usage_last_total_gb"])
            elif active_tariff.lte_usage_last_date != msk_today:
                active_tariff.lte_usage_last_date = msk_today
                active_tariff.lte_usage_last_total_gb = 0.0
                update_fields.extend(["lte_usage_last_date", "lte_usage_last_total_gb"])
            await active_tariff.save(update_fields=update_fields)
            await NotificationMarks.filter(user_id=user.id, type="lte_usage").delete()
            if user.remnawave_uuid:
                try:
                    should_enable_lte = lte_gb_total > 0
                    await set_lte_squad_status(str(user.remnawave_uuid), enable=should_enable_lte)
                except Exception as e:
                    logger.error(f"Ошибка обновления LTE-сквада при автопродлении для {user.id}: {e}")
            # Сбрасываем триал, если был (маловероятно для автоплатежа, но на всякий случай)
            if user.is_trial:
                user.is_trial = False
                logger.info(f"Сброшен флаг пробного периода для {user.id} при автооплате с баланса")
            await user.save()

            payment_id = f"balance_auto_{user.id}_{int(datetime.now().timestamp())}_{randint(100, 999)}"

            await _upsert_processed_payment(
                payment_id=payment_id,
                user_id=user.id,
                amount=full_price,  # Итоговая стоимость с учетом скидки
                amount_external=0,
                amount_from_balance=full_price,
                status="succeeded",  # Статус как при обычной успешной оплате
            )

            # Partner program: award cashback on balance auto-pay too.
            try:
                await _award_partner_cashback(
                    payment_id=str(payment_id),
                    referral_user=user,
                    amount_rub_total=int(_round_rub(float(full_price))),
                )
            except Exception:
                pass
            
            logger.info(
                 f"Автоплатеж для пользователя {user.id} успешно выполнен с баланса. "
                 f"Списано: {full_price}. Баланс до: {initial_balance}, После: {user.balance}, Скидка: {discount_percent}% (id={discount_id})"
            )

            # Уведомления (админу)
            referrer = await user.referrer()
            try:
                await on_payment(
                    user_id=user.id,
                    is_sub=user.is_subscribed,
                    referrer=referrer.name() if referrer else None,
                    amount=full_price,
                    months=months,
                    method="balance_auto", # Указываем метод
                    payment_id=payment_id,
                    is_auto=True,
                    utm=user.utm if hasattr(user, "utm") else None,
                    discount_percent=discount_percent,
                    device_count=active_tariff.hwid_limit if hasattr(active_tariff, "hwid_limit") else None,
                    old_expired_at=old_expired_at,
                    new_expired_at=user.expired_at,
                    lte_gb_total=lte_gb_total,
                )
            except Exception as e:
                logger.error(
                    f"Ошибка при отправке уведомления об автоплатеже с баланса: {e}",
                    extra={'payment_id': payment_id, 'user_id': user.id}
                )
            
            # Списываем использование скидки (если не постоянная)
            await consume_discount_if_needed(discount_id)

            # Уведомляем пользователя об успешном автопродлении с баланса
            await notify_auto_renewal_success_balance(user, days=days, amount=full_price)
            # Сообщение пользователю о начислении круток
            try:
                await notify_spin_awarded(user=user, added_attempts=int(months), total_attempts=int(user.prize_wheel_attempts or 0))
            except Exception as e_notify_spins:
                logger.error(f"Ошибка уведомления о крутках (баланс) для {user.id}: {e_notify_spins}")

            # Начисление круток за автосписание с баланса: 1 крутка за каждый месяц
            try:
                attempts_before = int(getattr(user, "prize_wheel_attempts", 0) or 0)
                if months and months > 0:
                    user.prize_wheel_attempts = attempts_before + int(months)
                    await user.save()
                    logger.info(
                        f"Начислено {months} круток за автосписание (баланс) пользователю {user.id}. Было: {attempts_before}, стало: {user.prize_wheel_attempts}"
                    )
            except Exception as award_exc:
                logger.error(f"Не удалось начислить крутки за автосписание (баланс) для {user.id}: {award_exc}")
            
            return True # Автоплатеж успешен

        else:
            # Логика частичной оплаты
            amount_to_pay = max(1.0, full_price - user_balance)
            amount_from_balance = full_price - amount_to_pay

            logger.info(
                f"Создание автоплатежа Yookassa для пользователя {user.id}. "
                f"Тариф: {active_tariff.id}, Полная цена: {full_price}, Баланс: {user_balance}, "
                f"К оплате: {amount_to_pay}, С баланса: {amount_from_balance}"
            )

            metadata = {
                "user_id": user.id,
                "month": months,
                "is_auto": True,
                "amount_from_balance": amount_from_balance,
                "disable_on_fail": disable_on_fail,
                "base_full_price": base_full_price,
                "discounted_price": discounted_price,
                "discount_percent": discount_percent,
                "discount_id": discount_id,
                "lte_gb": lte_gb_total,
                "lte_price_per_gb": lte_price_per_gb,
                "lte_cost": lte_cost,
            }

            # Создаем автоплатеж Yookassa с таймаутом
            try:
                payment_data = {
                    "amount": {
                        "value": str(amount_to_pay),
                        "currency": "RUB"
                    },
                    "payment_method_id": user.renew_id,
                    "metadata": metadata,
                    "capture": True,
                    "description": f"Автопродление подписки пользователя {user.id} ({active_tariff.name})",
                    "receipt": {
                        "customer": {"email": user.email if user.email else "auto@bloopcat.ru"},
                        "items": [{
                            "description": f"Автопродление подписки пользователя {user.id} ({active_tariff.name})",
                            "quantity": "1",
                            "amount": {
                                "value": str(amount_to_pay),
                                "currency": "RUB"
                            },
                            "vat_code": 1, # TODO: Проверить НДС
                            "payment_subject": "service",
                            "payment_mode": "full_payment"
                        }]
                    }
                }

                idempotence_key = str(randint(100000, 999999999999))

                # Используем asyncio.to_thread для неблокирующего вызова с таймаутом
                payment = await asyncio.wait_for(
                    asyncio.to_thread(partial(Payment.create, payment_data, idempotence_key)),
                    timeout=30.0
                )

            except asyncio.TimeoutError:
                logger.error(
                    f"Таймаут при создании автоплатежа YooKassa для пользователя {user.id}. "
                    f"Тариф: {active_tariff.id}, Сумма: {amount_to_pay}",
                    extra={'user_id': user.id, 'tariff_id': active_tariff.id, 'amount': amount_to_pay}
                )
                await notify_auto_renewal_failure(
                    user,
                    reason="Сервис оплаты временно недоступен (таймаут)",
                    will_retry=will_retry
                )
                return False
            except (ConnectTimeoutError, ReadTimeoutError, RequestsConnectionError, RequestsTimeout) as network_err:
                logger.error(
                    f"Сетевая ошибка при создании автоплатежа YooKassa для пользователя {user.id}: {network_err}",
                    extra={'user_id': user.id, 'tariff_id': active_tariff.id, 'amount': amount_to_pay, 'error_type': type(network_err).__name__}
                )
                await notify_auto_renewal_failure(
                    user,
                    reason="Сервис оплаты временно недоступен (ошибка сети)",
                    will_retry=will_retry
                )
                return False
            except Exception as create_exc:
                logger.error(
                    f"Неожиданная ошибка при создании автоплатежа YooKassa для пользователя {user.id}: {create_exc}",
                    extra={'user_id': user.id, 'tariff_id': active_tariff.id, 'amount': amount_to_pay, 'error_type': type(create_exc).__name__},
                    exc_info=True
                )
                # Для непредвиденных ошибок пробрасываем дальше
                raise

            # Сбрасываем триал, если пользователь платит первый раз (даже автоплатежом)
            if user.is_trial:
                user.is_trial = False
                await user.save()
                logger.info(
                    f"Сброшен флаг пробного периода для {user.id} при создании автоплатежа Yookassa",
                    extra={
                        'payment_id': payment.id,
                        'user_id': user.id
                    }
                )

            logger.info(
                f"Создан автоплатеж Yookassa для пользователя {user.id}",
                extra={
                    'payment_id': payment.id,
                    'user_id': user.id,
                    'amount': payment.amount.value,
                    'status': payment.status
                }
            )
            # Persist as "pending" so the server can reconcile even if webhook delivery fails.
            try:
                if payment and payment.id:
                    await _upsert_processed_payment(
                        payment_id=payment.id,
                        user_id=user.id,
                        amount=float(amount_to_pay) + float(amount_from_balance),
                        amount_external=float(amount_to_pay),
                        amount_from_balance=float(amount_from_balance),
                        status="pending",
                    )
            except Exception as e:
                logger.warning(
                    f"Не удалось сохранить pending auto payment {getattr(payment, 'id', None)}: {e}",
                    extra={'user_id': user.id},
                )
            return True # Автоплатеж создан (результат будет в вебхуке)

    except Exception as e:
        logger.error(
            f"Ошибка при создании автоплатежа для пользователя {user.id}: {e}",
            extra={'user_id': user.id}
        )
        # Отключаем автопродление только если это последняя попытка
        if disable_on_fail:
            user.is_subscribed = False
            user.renew_id = None
            await user.save()
            # Уведомляем админа об отключении автопродления из-за ошибки
            await cancel_subscription(user, reason=f"Ошибка при создании автоплатежа: {str(e)}")
        logger.warning(f"Автопродление отключено для {user.id} из-за ошибки при создании автоплатежа: {e}")
        await notify_auto_renewal_failure(user, reason=f"Внутренняя ошибка сервера при попытке автопродления", will_retry=will_retry)
        return False
