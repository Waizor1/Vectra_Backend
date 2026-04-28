import asyncio
from datetime import date, datetime, timedelta

from bloobcat.bot.notifications.subscription.key import on_disabled
from bloobcat.bot.notifications.subscription.renewal import (
    notify_subscription_cancelled_after_failures,
)
from bloobcat.bot.notifications.trial.end import notify_trial_ended
from bloobcat.bot.notifications.trial.pre_expiring_3d import notify_trial_three_days_left
from bloobcat.bot.notifications.winback.discount_offer import (
    notify_winback_discount_offer,
)
from bloobcat.db.notifications import NotificationMarks
from bloobcat.db.users import Users, normalize_date
from bloobcat.logger import get_logger
from bloobcat.tasks.quiet_hours import (
    MOSCOW,
    PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
    PENDING_TRIAL_ENDED_MARK_TYPE,
    PENDING_WINBACK_DISCOUNT_MARK_TYPE,
    SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
    SUBSCRIPTION_EXPIRED_MARK_TYPE,
    build_pending_meta,
    build_winback_notification_key,
    ensure_notification_mark,
    is_in_morning_window,
    is_quiet_hours,
    parse_pending_meta,
)

logger = get_logger("tasks.quiet_hours_notifications")


async def _delete_mark(mark: NotificationMarks) -> None:
    try:
        await mark.delete()
    except Exception as exc:
        logger.warning(f"Failed to delete notification mark id={mark.id}: {exc}")


def _parse_mark_date(raw_value: str | None) -> date | None:
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value)
    except ValueError:
        return None


async def _dispatch_pending_trial_endings() -> int:
    dispatched = 0
    marks = await NotificationMarks.filter(type=PENDING_TRIAL_ENDED_MARK_TYPE).all()
    for mark in marks:
        planned_expired = _parse_mark_date(mark.key)
        if planned_expired is None:
            await _delete_mark(mark)
            continue

        already_sent = await NotificationMarks.filter(
            user_id=mark.user_id,
            type="trial_ended",
            key=mark.key,
        ).exists()
        if already_sent:
            await _delete_mark(mark)
            continue

        user = await Users.get_or_none(id=mark.user_id)
        if user is None:
            await _delete_mark(mark)
            continue
        if user.is_blocked:
            await _delete_mark(mark)
            continue

        current_expired = normalize_date(user.expired_at)
        if user.is_subscribed or (
            current_expired is not None and current_expired > planned_expired
        ):
            logger.info(
                f"Skipping pending trial-ended notification for user {user.id}: state changed"
            )
            await _delete_mark(mark)
            continue

        sent_ok = await notify_trial_ended(user)
        if not sent_ok:
            refreshed_user = await Users.get_or_none(id=mark.user_id)
            if refreshed_user is None or refreshed_user.is_blocked:
                await _delete_mark(mark)
            continue

        await ensure_notification_mark(
            user_id=user.id,
            mark_type="trial_ended",
            key=mark.key,
        )
        await _delete_mark(mark)
        dispatched += 1
    return dispatched


async def _dispatch_pending_subscription_cancellations() -> int:
    dispatched = 0
    marks = await NotificationMarks.filter(
        type=PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE
    ).all()
    for mark in marks:
        already_sent = await NotificationMarks.filter(
            user_id=mark.user_id,
            type=SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
            key=mark.key,
        ).exists()
        if already_sent:
            await _delete_mark(mark)
            continue

        user = await Users.get_or_none(id=mark.user_id)
        if user is None:
            await _delete_mark(mark)
            continue
        if user.is_blocked:
            await _delete_mark(mark)
            continue

        if user.is_subscribed or user.renew_id:
            logger.info(
                f"Skipping pending cancellation notification for user {user.id}: subscription already changed"
            )
            await _delete_mark(mark)
            continue

        sent_ok = await notify_subscription_cancelled_after_failures(user)
        if not sent_ok:
            refreshed_user = await Users.get_or_none(id=mark.user_id)
            if refreshed_user is None or refreshed_user.is_blocked:
                await _delete_mark(mark)
            continue

        await ensure_notification_mark(
            user_id=user.id,
            mark_type=SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
            key=mark.key,
        )
        await _delete_mark(mark)
        dispatched += 1
    return dispatched


async def _dispatch_pending_winback_notifications(now_msk: datetime) -> int:
    dispatched = 0
    marks = await NotificationMarks.filter(type=PENDING_WINBACK_DISCOUNT_MARK_TYPE).all()
    for mark in marks:
        user = await Users.get_or_none(id=mark.user_id)
        if user is None:
            await _delete_mark(mark)
            continue
        if user.is_blocked:
            await _delete_mark(mark)
            continue

        if user.is_subscribed:
            logger.info(
                f"Skipping pending winback notification for user {user.id}: user subscribed again"
            )
            await _delete_mark(mark)
            continue

        meta = parse_pending_meta(mark.meta)
        try:
            percent = int(meta["discount_percent"])
            expires_at = date.fromisoformat(str(meta["expires_at"]))
        except (KeyError, TypeError, ValueError):
            logger.warning(
                f"Skipping pending winback notification for user {mark.user_id}: invalid meta={mark.meta}"
            )
            await _delete_mark(mark)
            continue

        recent_notification = await NotificationMarks.filter(
            user_id=user.id,
            type="winback_discount",
            sent_at__gte=now_msk - timedelta(days=30),
        ).exists()
        if recent_notification:
            await _delete_mark(mark)
            continue

        sent_ok = await notify_winback_discount_offer(user, percent, expires_at)
        if not sent_ok:
            refreshed_user = await Users.get_or_none(id=mark.user_id)
            if refreshed_user is None or refreshed_user.is_blocked:
                await _delete_mark(mark)
            continue

        await ensure_notification_mark(
            user_id=user.id,
            mark_type="winback_discount",
            key=build_winback_notification_key(expires_at),
        )
        await _delete_mark(mark)
        dispatched += 1
    return dispatched


async def _send_subscription_expired_catchup(now_msk: datetime) -> int:
    today = now_msk.date()
    users = await Users.filter(
        is_subscribed=True,
        is_blocked=False,
        renew_id__isnull=True,
        expired_at=today,
    )
    sent = 0
    for user in users:
        exists = await NotificationMarks.filter(
            user_id=user.id,
            type=SUBSCRIPTION_EXPIRED_MARK_TYPE,
            key=str(today),
        ).exists()
        if exists:
            continue
        sent_ok = await on_disabled(user)
        if sent_ok:
            await ensure_notification_mark(
                user_id=user.id,
                mark_type=SUBSCRIPTION_EXPIRED_MARK_TYPE,
                key=str(today),
            )
            sent += 1
    return sent


async def _send_trial_pre_expiring_catchup(now_msk: datetime) -> int:
    tomorrow = now_msk.date() + timedelta(days=1)
    key = f"1d:{tomorrow}"
    users = await Users.filter(
        is_trial=True,
        is_subscribed=False,
        is_blocked=False,
        expired_at=tomorrow,
    )
    sent = 0
    for user in users:
        exists = await NotificationMarks.filter(
            user_id=user.id,
            type="trial_pre_expiring",
            key=key,
        ).exists()
        if exists:
            continue
        sent_ok = await notify_trial_three_days_left(user)
        if sent_ok:
            await ensure_notification_mark(
                user_id=user.id,
                mark_type="trial_pre_expiring",
                key=key,
            )
            sent += 1
    return sent


async def quiet_hours_notifications_once(now_msk: datetime | None = None) -> int:
    current = now_msk or datetime.now(MOSCOW)
    total = 0

    if not is_quiet_hours(current):
        total += await _dispatch_pending_trial_endings()
        total += await _dispatch_pending_subscription_cancellations()
        total += await _dispatch_pending_winback_notifications(current)

    if is_in_morning_window(current):
        total += await _send_subscription_expired_catchup(current)
        total += await _send_trial_pre_expiring_catchup(current)

    if total:
        logger.info(f"Quiet-hours notifications processed: {total}")
    return total


async def run_quiet_hours_notifications_scheduler(interval_seconds: int = 600) -> None:
    logger.info(
        f"Starting quiet-hours notifications scheduler (interval: {interval_seconds}s)"
    )
    while True:
        try:
            await quiet_hours_notifications_once()
        except Exception as exc:
            logger.error(f"Error in quiet-hours notifications scheduler: {exc}")
        await asyncio.sleep(interval_seconds)
