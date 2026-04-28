import asyncio
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

from bloobcat.bot.notifications.subscription.expiration import notify_auto_payment
from bloobcat.db.notifications import NotificationMarks
from bloobcat.db.users import Users, normalize_date
from bloobcat.logger import get_logger
from bloobcat.routes.payment import build_auto_payment_preview
from bloobcat.settings import payment_settings


logger = get_logger("tasks.auto_payment_reminders")
MOSCOW = ZoneInfo("Europe/Moscow")
AUTO_PAYMENT_REMINDER_TIME = time(20, 0)
AUTO_PAYMENT_REMINDER_MARK_TYPE = "auto_payment_reminder"
_AUTO_PAYMENT_REMINDER_PENDING_META = "pending"
_AUTO_PAYMENT_REMINDER_PENDING_STALE_SECONDS = 900


def build_auto_payment_reminder_key(*, planned_expired: date, days_before: int) -> str:
    return f"{int(days_before)}d:{planned_expired.isoformat()}"


def build_auto_payment_reminder_eta(
    *, planned_expired: date, days_before: int
) -> datetime:
    reminder_date = planned_expired - timedelta(days=int(days_before) + 1)
    return datetime.combine(reminder_date, AUTO_PAYMENT_REMINDER_TIME, tzinfo=MOSCOW)


def _is_in_evening_window(now_msk: datetime) -> bool:
    start = datetime.combine(now_msk.date(), AUTO_PAYMENT_REMINDER_TIME, tzinfo=MOSCOW)
    end = datetime.combine(
        now_msk.date() + timedelta(days=1),
        time.min,
        tzinfo=MOSCOW,
    )
    return start <= now_msk < end


async def _claim_auto_payment_reminder_once(*, user_id: int, key: str) -> bool:
    now_utc = datetime.now(timezone.utc)
    async with in_transaction() as conn:
        existing_marks = (
            await NotificationMarks.filter(
                user_id=int(user_id),
                type=AUTO_PAYMENT_REMINDER_MARK_TYPE,
                key=key,
            )
            .using_db(conn)
            .all()
        )
        for mark in existing_marks:
            meta = str(getattr(mark, "meta", "") or "").strip().lower()
            if meta != _AUTO_PAYMENT_REMINDER_PENDING_META:
                return False

            sent_at = getattr(mark, "sent_at", None)
            if sent_at is not None and getattr(sent_at, "tzinfo", None) is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            if (
                sent_at is not None
                and (now_utc - sent_at).total_seconds()
                < _AUTO_PAYMENT_REMINDER_PENDING_STALE_SECONDS
            ):
                return False

            await NotificationMarks.filter(id=mark.id).using_db(conn).delete()

        try:
            await NotificationMarks.create(
                user_id=int(user_id),
                type=AUTO_PAYMENT_REMINDER_MARK_TYPE,
                key=key,
                meta=_AUTO_PAYMENT_REMINDER_PENDING_META,
                using_db=conn,
            )
        except IntegrityError:
            return False
    return True


async def _finalize_auto_payment_reminder_mark(*, user_id: int, key: str) -> None:
    await NotificationMarks.filter(
        user_id=int(user_id),
        type=AUTO_PAYMENT_REMINDER_MARK_TYPE,
        key=key,
        meta=_AUTO_PAYMENT_REMINDER_PENDING_META,
    ).update(meta=None)


async def _release_auto_payment_reminder_mark(*, user_id: int, key: str) -> None:
    await NotificationMarks.filter(
        user_id=int(user_id),
        type=AUTO_PAYMENT_REMINDER_MARK_TYPE,
        key=key,
        meta=_AUTO_PAYMENT_REMINDER_PENDING_META,
    ).delete()


async def send_auto_payment_reminder_if_needed(
    user_id: int,
    planned_expired: date,
    days_before: int,
) -> bool:
    user = await Users.get_or_none(id=user_id)
    if not user:
        logger.debug("Skipping auto-payment reminder for missing user %s", user_id)
        return False

    if payment_settings.auto_renewal_mode != "yookassa":
        logger.debug(
            "Skipping auto-payment reminder for user %s: auto-renewal disabled",
            user_id,
        )
        return False

    if (
        normalize_date(user.expired_at) != planned_expired
        or not bool(user.is_subscribed)
        or not bool(user.renew_id)
    ):
        logger.debug(
            "Skipping auto-payment reminder for user %s: state changed",
            user_id,
        )
        return False

    try:
        preview = await build_auto_payment_preview(user)
    except Exception as exc:
        logger.warning(
            "Failed to build auto-payment preview for reminder user=%s key=%s err=%s",
            user_id,
            build_auto_payment_reminder_key(
                planned_expired=planned_expired, days_before=days_before
            ),
            exc,
        )
        return False

    if preview is None:
        logger.warning(
            "Skipping auto-payment reminder for user %s: preview unavailable",
            user_id,
        )
        return False

    key = build_auto_payment_reminder_key(
        planned_expired=planned_expired,
        days_before=days_before,
    )
    claimed = await _claim_auto_payment_reminder_once(user_id=int(user.id), key=key)
    if not claimed:
        return False

    charge_date = planned_expired - timedelta(days=int(days_before))
    try:
        delivered = await notify_auto_payment(
            user,
            total_amount=preview.total_amount,
            amount_external=preview.amount_external,
            amount_from_balance=preview.amount_from_balance,
            charge_date=charge_date,
        )
        if not delivered:
            await _release_auto_payment_reminder_mark(user_id=int(user.id), key=key)
            logger.warning(
                "Auto-payment reminder delivery not confirmed for user=%s key=%s",
                user.id,
                key,
            )
            return False
    except Exception as exc:
        await _release_auto_payment_reminder_mark(user_id=int(user.id), key=key)
        logger.warning(
            "Auto-payment reminder send failed for user=%s key=%s err=%s",
            user.id,
            key,
            exc,
        )
        return False

    await _finalize_auto_payment_reminder_mark(user_id=int(user.id), key=key)
    logger.info(
        "Auto-payment reminder sent for user=%s planned_expired=%s days_before=%s",
        user.id,
        planned_expired,
        days_before,
    )
    return True


async def auto_payment_reminders_once(now_msk: datetime | None = None) -> int:
    current = now_msk or datetime.now(MOSCOW)
    if not _is_in_evening_window(current):
        return 0

    sent = 0
    for days_before in (4, 3, 2):
        planned_expired = current.date() + timedelta(days=days_before + 1)
        users = await Users.filter(
            is_subscribed=True,
            is_blocked=False,
            renew_id__not_isnull=True,
            expired_at=planned_expired,
        )
        for user in users:
            if await send_auto_payment_reminder_if_needed(
                int(user.id),
                planned_expired,
                days_before,
            ):
                sent += 1

    if sent:
        logger.info("Auto-payment reminder catch-up sent: %s", sent)
    return sent


async def run_auto_payment_reminders_scheduler(interval_seconds: int = 600) -> None:
    logger.info(
        "Starting auto-payment reminder scheduler (interval: %ss)",
        interval_seconds,
    )
    while True:
        try:
            await auto_payment_reminders_once()
        except Exception as exc:
            logger.error("Error in auto-payment reminder scheduler: %s", exc)
        await asyncio.sleep(interval_seconds)
