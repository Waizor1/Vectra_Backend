import asyncio
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users, normalize_date
from bloobcat.db.notifications import NotificationMarks
from bloobcat.routes.payment import create_auto_payment
from bloobcat.bot.notifications.subscription.expiration import notify_expiring_subscription
from bloobcat.bot.notifications.subscription.renewal import notify_subscription_cancelled_after_failures
from bloobcat.bot.notifications.subscription.key import on_disabled
from bloobcat.bot.notifications.trial.extended import notify_trial_extended
from bloobcat.bot.notifications.trial.end import notify_trial_ended
from bloobcat.bot.notifications.trial.expiring import notify_expiring_trial
from bloobcat.bot.notifications.trial.pre_expiring_3d import notify_trial_three_days_left
from bloobcat.tasks.referral_prompts import run_referral_prompts_scheduler
from bloobcat.tasks.home_screen_install_promo import (
    run_home_screen_install_promo_scheduler,
)
from bloobcat.logger import get_logger
from bloobcat.settings import app_settings, payment_settings
from bloobcat.tasks.remnawave_updater import run_remnawave_scheduler
from bloobcat.tasks.retry_trial_notifications import run_retry_trial_notifications_scheduler
from bloobcat.tasks.retry_trial_extensions import run_retry_trial_extensions_scheduler
from bloobcat.tasks.retry_trial_endings import run_retry_trial_endings_scheduler
from bloobcat.tasks.retry_trial_extension_notifications import run_retry_trial_extension_notifications_scheduler
from bloobcat.tasks.cleanup_missed_cancellations import run_cleanup_missed_cancellations_scheduler
from bloobcat.tasks.quiet_hours import (
    PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
    PENDING_TRIAL_ENDED_MARK_TYPE,
    SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
    SUBSCRIPTION_EXPIRED_MARK_TYPE,
    ensure_notification_mark,
    is_quiet_hours,
    normalize_user_notification_eta,
)
from bloobcat.tasks.quiet_hours_notifications import (
    run_quiet_hours_notifications_scheduler,
)
from bloobcat.tasks.trial_expiring_catchup import run_trial_expiring_catchup_scheduler
from bloobcat.tasks.subscription_expiring_catchup import run_subscription_expiring_catchup_scheduler
from bloobcat.tasks.winback_discounts import run_winback_discounts_scheduler
from bloobcat.tasks.trial_active_tariff_fix import run_trial_active_tariff_fix_scheduler
from bloobcat.tasks.lte_usage_limiter import (
    run_lte_usage_limiter_scheduler,
    run_lte_usage_limiter_quick_scheduler,
)
from bloobcat.tasks.payment_reconcile import run_payment_reconcile_scheduler
from bloobcat.tasks.remnawave_delete_retry import run_remnawave_delete_retry_scheduler
from bloobcat.tasks.subscription_resume import run_subscription_resume_scheduler
from bloobcat.tasks.temp_setup_cleanup import run_temp_setup_cleanup_scheduler
from bloobcat.tasks.service_growth_analytics import run_service_growth_analytics_scheduler
from bloobcat.tasks.auto_payment_reminders import (
    build_auto_payment_reminder_eta,
    run_auto_payment_reminders_scheduler,
    send_auto_payment_reminder_if_needed,
)

logger = get_logger("scheduler")

MOSCOW = ZoneInfo("Europe/Moscow")
# Global mapping of user_id to scheduled asyncio tasks for cancellation
scheduled_tasks = {}

# Rate limiting для trial extension notifications
_last_trial_notification_time = None
# ОПТИМИЗИРОВАНО: Telegram Bot API позволяет 30 сообщений/сек
# Используем 0.035с = ~28.5 сообщений/сек (с запасом безопасности)
_trial_notification_delay = 0.035  # секунд между уведомлениями (было 0.5)

# Статистика уведомлений для мониторинга
_trial_extension_stats = {
    "total_attempts": 0,
    "successful_notifications": 0,
    "failed_notifications": 0,
    "timeouts": 0,
    "rate_limited": 0,
    "telegram_429_errors": 0,  # НОВОЕ: счётчик 429 ошибок
    "performance": {
        "start_time": None,
        "messages_per_second": 0.0,
        "eta_completion": None,
        "processed_count": 0
    },
    "last_reset": datetime.now(MOSCOW)
}

def reset_trial_extension_stats():
    """Сброс статистики trial extension уведомлений"""
    global _trial_extension_stats
    _trial_extension_stats = {
        "total_attempts": 0,
        "successful_notifications": 0,
        "failed_notifications": 0,
        "timeouts": 0,
        "rate_limited": 0,
        "telegram_429_errors": 0,  # НОВОЕ: счётчик 429 ошибок
        "performance": {
            "start_time": None,
            "messages_per_second": 0.0,
            "eta_completion": None,
            "processed_count": 0
        },
        "last_reset": datetime.now(MOSCOW)
    }
    logger.debug("Trial extension statistics reset")

def get_trial_extension_stats() -> dict:
    """Возвращает статистику trial extension уведомлений"""
    current_time = datetime.now(MOSCOW)
    uptime = current_time - _trial_extension_stats["last_reset"]
    uptime_hours = uptime.total_seconds() / 3600
    
    stats = _trial_extension_stats.copy()
    stats["uptime_hours"] = uptime_hours
    
    # Дополнительные метрики
    total_processed = stats.get("successful_notifications", 0) + stats.get("failed_notifications", 0)
    if total_processed > 0:
        stats["success_rate"] = (stats.get("successful_notifications", 0) / total_processed) * 100
    else:
        stats["success_rate"] = 0.0
    
    # НОВОЕ: Форматированная информация о производительности
    performance = stats.get("performance", {})
    if performance.get("eta_completion"):
        eta_minutes = performance["eta_completion"] / 60
        stats["eta_formatted"] = f"{eta_minutes:.1f} минут"
    else:
        stats["eta_formatted"] = "Неизвестно"
    
    # Telegram API ошибки
    stats["telegram_health"] = {
        "rate_limit_hits": stats.get("telegram_429_errors", 0),
        "api_error_rate": (stats.get("telegram_429_errors", 0) / max(1, stats.get("total_attempts", 1))) * 100
    }
    
    return stats

async def log_trial_extension_summary():
    """Логирует сводку по trial extension уведомлениям"""
    stats = get_trial_extension_stats()
    logger.info(f"Trial extension stats: {stats['total_attempts']} attempts, {stats['successful_notifications']} successful, {stats['failed_notifications']} failed, {stats['timeouts']} timeouts, {stats['success_rate']:.1f}% success rate")

def schedule_coro(at_time: datetime, coro, *args, skip_if_past=False):
    """Schedule coroutine execution at a specific datetime."""
    now = datetime.now(MOSCOW)
    delay = (at_time - now).total_seconds()
    
    if delay <= 0:
        if skip_if_past:
            # Don't execute if time has passed and skip_if_past is True
            logger.debug(f"Skipping task '{coro.__name__}' (scheduled for {at_time.isoformat()}) because time has passed")
            # Return a dummy completed task
            async def dummy():
                pass
            task = asyncio.create_task(dummy())
            return task
        else:
            # If task was scheduled for the past or now, execute immediately
            logger.debug(f"Executing task '{coro.__name__}' immediately (scheduled for {at_time.isoformat()}) args={args}")
            task = asyncio.create_task(coro(*args))
            return task
    else:
        # Schedule normally
        logger.debug(f"Scheduling task '{coro.__name__}' args={args} at {at_time.isoformat()}")

    async def runner():
        now = datetime.now(MOSCOW)
        delay = (at_time - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        await coro(*args)
    task = asyncio.create_task(runner())
    return task

def cancel_user_tasks(user_id: int):
    """Cancel all scheduled tasks for a given user."""
    tasks = scheduled_tasks.get(user_id, [])
    if not tasks:
        logger.debug(f"No scheduled tasks to cancel for user {user_id}")
        return

    current_task = asyncio.current_task()
    to_cancel = []
    to_keep = []
    for t in tasks:
        if t is current_task:
            to_keep.append(t)
        else:
            to_cancel.append(t)

    if to_cancel:
        logger.debug(f"Cancelling {len(to_cancel)} scheduled tasks for user {user_id} (keeping {len(to_keep)} running)")
        for t in to_cancel:
            t.cancel()
    else:
        logger.debug(f"No cancellable tasks for user {user_id} (only current running task present)")

    # Update registry: keep running task if any, else remove key
    if to_keep:
        scheduled_tasks[user_id] = to_keep
    else:
        scheduled_tasks.pop(user_id, None)

# State-validation wrappers before executing tasks
async def _exec_auto_payment(user_id: int, planned_expired: date, days_before: int):
    if payment_settings.auto_renewal_mode != "yookassa":
        logger.debug("Skipping auto payment for user %s: auto-renewal disabled", user_id)
        return
    user = await Users.get_or_none(id=user_id)
    if not user or normalize_date(user.expired_at) != planned_expired or not user.renew_id:
        logger.debug(f"Skipping auto payment for user {user_id}")
        return
    await create_auto_payment(user, disable_on_fail=(days_before == 0))


async def _exec_notify_auto_payment_reminder(
    user_id: int, planned_expired: date, days_before: int
):
    await send_auto_payment_reminder_if_needed(user_id, planned_expired, days_before)


async def _exec_notify_expiring(user_id: int, planned_expired: date, days_before: int):
    user = await Users.get_or_none(id=user_id)
    if not user or normalize_date(user.expired_at) != planned_expired or user.renew_id:
        logger.debug(f"Skipping notify expiring for user {user_id}")
        return
    # Idempotency via notification marks
    key = f"{days_before}d:{planned_expired}"
    already = await NotificationMarks.filter(user_id=user.id, type="subscription_expiring", key=key).exists()
    if already:
        logger.debug(f"Skipping duplicate subscription expiring for user {user_id}, key={key}")
        return
    sent_ok = await notify_expiring_subscription(user)
    if sent_ok:
        await NotificationMarks.create(user_id=user.id, type="subscription_expiring", key=key)

async def _exec_cancel_if_unpaid(user_id: int, planned_expired: date):
    """Checks if a subscription was renewed, and if not, cancels it."""
    if payment_settings.auto_renewal_mode != "yookassa":
        logger.debug(
            "Skipping cancel_if_unpaid for user %s: auto-renewal disabled",
            user_id,
        )
        return
    user = await Users.get_or_none(id=user_id)
    # If user does not exist, or subscription was renewed (expired_at changed), or auto-renewal was cancelled, do nothing.
    if not user or normalize_date(user.expired_at) != planned_expired or not user.renew_id:
        logger.debug(f"Skipping cancel_if_unpaid for user {user_id}. Conditions not met.")
        return

    logger.warning(f"User {user_id} subscription renewal failed multiple times. Cancelling subscription.")
    # Cancel subscription
    user.is_subscribed = False
    user.renew_id = None
    await user.save()

    notification_key = str(planned_expired)
    if user.is_blocked:
        logger.info(
            f"Subscription cancelled for blocked user {user.id} without notification"
        )
        return

    if is_quiet_hours():
        await ensure_notification_mark(
            user_id=user.id,
            mark_type=PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
            key=notification_key,
        )
        logger.info(
            f"Deferred cancellation notification for user {user.id} until quiet hours end"
        )
        return

    # Notify user about cancellation
    sent_ok = await notify_subscription_cancelled_after_failures(user)
    if sent_ok:
        await ensure_notification_mark(
            user_id=user.id,
            mark_type=SUBSCRIPTION_CANCELLED_AFTER_FAILURES_MARK_TYPE,
            key=notification_key,
        )
        return

    refreshed_user = await Users.get_or_none(id=user.id)
    if refreshed_user and not refreshed_user.is_blocked:
        await ensure_notification_mark(
            user_id=user.id,
            mark_type=PENDING_SUBSCRIPTION_CANCELLED_MARK_TYPE,
            key=notification_key,
        )
        logger.warning(
            f"Cancellation notification delivery not confirmed for user {user.id}; queued for retry"
        )

async def _exec_notify_expired(user_id: int, planned_expired: date):
    user = await Users.get_or_none(id=user_id)
    if not user or normalize_date(user.expired_at) != planned_expired or user.renew_id:
        logger.debug(f"Skipping notify expired for user {user_id}")
        return
    key = str(planned_expired)
    already = await NotificationMarks.filter(
        user_id=user.id,
        type=SUBSCRIPTION_EXPIRED_MARK_TYPE,
        key=key,
    ).exists()
    if already:
        logger.debug(f"Skipping duplicate expired notify for user {user_id}, key={key}")
        return
    if user.is_blocked:
        logger.info(f"Skipping expired notification for blocked user {user.id}")
        return
    sent_ok = await on_disabled(user)
    if sent_ok:
        await ensure_notification_mark(
            user_id=user.id,
            mark_type=SUBSCRIPTION_EXPIRED_MARK_TYPE,
            key=key,
        )


async def _exec_extend_trial(user_id: int, planned_expired: date):
    """
    Продляет trial подписку пользователя и отправляет уведомление.
    ОПТИМИЗИРОВАНО для 10k+ пользователей с rate limiting 28.5 сообщений/сек.
    """
    global _last_trial_notification_time, _trial_extension_stats
    
    user = await Users.get_or_none(id=user_id)
    if not user or not user.is_trial or normalize_date(user.expired_at) != planned_expired or user.connected_at:
        logger.debug(f"[{user_id}] Skipping extend trial for user because conditions not met (user: {bool(user)}, is_trial: {user.is_trial if user else 'N/A'}, expired_at: {user.expired_at if user else 'N/A'} vs {planned_expired}, connected_at: {user.connected_at if user else 'N/A'})")
        return
    
    # Idempotency: skip if extension already applied for this planned_expired
    key = str(planned_expired)
    already_applied = await NotificationMarks.filter(user_id=user.id, type="trial_extension_applied", key=key).exists()
    if already_applied:
        logger.debug(f"[{user_id}] Skipping trial extension: already applied for key={key}")
        # Try to send missing notification if not sent yet (do not block here)
        already_notified = await NotificationMarks.filter(user_id=user.id, type="trial_extension_notified", key=key).exists()
        if not already_notified:
            # Fire-and-forget notify; dedicated periodic task will also retry
            if is_quiet_hours():
                logger.info(
                    f"[{user_id}] Delaying missing trial extension notification for key={key} until quiet hours end"
                )
            else:
                try:
                    extension_days = app_settings.trial_days // 2
                    sent_ok = await notify_trial_extended(user, extension_days)
                    if sent_ok:
                        await NotificationMarks.create(
                            user_id=user.id, type="trial_extension_notified", key=key
                        )
                except Exception as e:
                    logger.warning(f"[{user_id}] Failed to send missing trial extension notification for key={key}: {e}")
        return

    logger.debug(f"[{user_id}] Attempting to extend trial. Current expired_at: {user.expired_at}")
    
    # Инициализация статистики производительности
    if _trial_extension_stats["performance"]["start_time"] is None:
        _trial_extension_stats["performance"]["start_time"] = asyncio.get_event_loop().time()
    
    _trial_extension_stats["total_attempts"] += 1
    
    # Рассчитываем количество дней для продления (половина от trial_days)
    extension_days = app_settings.trial_days // 2
    
    # Продляем trial
    user.expired_at += timedelta(days=extension_days)
    await user.save()
    logger.debug(f"[{user_id}] Trial extended by {extension_days} days. New expired_at: {user.expired_at}")
    # Mark that extension has been applied for idempotency/catch-up detection
    await NotificationMarks.create(user_id=user.id, type="trial_extension_applied", key=key)
    
    # Rate limiting для уведомлений
    current_time = asyncio.get_event_loop().time()
    if _last_trial_notification_time is not None:
        time_since_last = current_time - _last_trial_notification_time
        if time_since_last < _trial_notification_delay:
            wait_time = _trial_notification_delay - time_since_last
            logger.debug(f"[{user_id}] Rate limiting: waiting {wait_time:.3f}s")
            await asyncio.sleep(wait_time)
    
    # Отправляем уведомление с таймаутом (было 120с, стало 600с = 10 минут)
    if is_quiet_hours():
        logger.info(
            f"[{user_id}] Trial extension applied for key={key}; notification deferred until quiet hours end"
        )
        return
    try:
        notification_result = await asyncio.wait_for(
            notify_trial_extended(user, extension_days),
            timeout=600.0  # ОПТИМИЗИРОВАНО: 10 минут для больших объёмов
        )
        
        if notification_result:
            _trial_extension_stats["successful_notifications"] += 1
            logger.debug(f"[{user_id}] Trial extension notification sent successfully")
            # Mark notification as sent to avoid duplicates and enable catch-up logic
            try:
                await NotificationMarks.create(user_id=user.id, type="trial_extension_notified", key=key)
            except Exception as e:
                logger.warning(f"[{user_id}] Failed to create trial_extension_notified mark: {e}")
        else:
            _trial_extension_stats["failed_notifications"] += 1
            logger.error(f"[{user_id}] Trial extension notification failed to send")
        
    except asyncio.TimeoutError:
        _trial_extension_stats["timeouts"] += 1
        logger.error(f"[{user_id}] Trial extension notification timed out after 600s")
        
    except Exception as e:
        # Проверяем ошибки Telegram API
        if "429" in str(e) or "Too Many Requests" in str(e):
            _trial_extension_stats["telegram_429_errors"] += 1
            logger.warning(f"[{user_id}] Telegram rate limit hit: {e}")
        else:
            _trial_extension_stats["failed_notifications"] += 1
            logger.error(f"[{user_id}] Trial extension notification failed: {e}")
    
    # Обновляем статистику производительности
    _trial_extension_stats["performance"]["processed_count"] += 1
    _last_trial_notification_time = asyncio.get_event_loop().time()
    
    # Рассчитываем производительность
    elapsed_time = _last_trial_notification_time - _trial_extension_stats["performance"]["start_time"]
    if elapsed_time > 0:
        _trial_extension_stats["performance"]["messages_per_second"] = (
            _trial_extension_stats["performance"]["processed_count"] / elapsed_time
        )
        
        # ETA для завершения (если есть информация о общем количестве)
        if _trial_extension_stats["total_attempts"] > 0:
            remaining = _trial_extension_stats["total_attempts"] - _trial_extension_stats["performance"]["processed_count"]
            if remaining > 0 and _trial_extension_stats["performance"]["messages_per_second"] > 0:
                eta_seconds = remaining / _trial_extension_stats["performance"]["messages_per_second"]
                _trial_extension_stats["performance"]["eta_completion"] = eta_seconds
    
    # Логируем прогресс каждые 100 уведомлений (DEBUG уровень)
    if _trial_extension_stats["performance"]["processed_count"] % 100 == 0:
        mps = _trial_extension_stats["performance"]["messages_per_second"]
        processed = _trial_extension_stats["performance"]["processed_count"]
        logger.debug(f"Trial extension progress: {processed} processed, {mps:.2f} msg/sec")

async def _exec_notify_trial_end(user_id: int, planned_expired: date):
    user = await Users.get_or_none(id=user_id)
    if not user or not user.is_trial or normalize_date(user.expired_at) != planned_expired:
        logger.debug(f"Skipping notify trial end for user {user_id}")
        return
    # Idempotency via notification marks
    key = str(planned_expired)
    already = await NotificationMarks.filter(user_id=user.id, type="trial_ended", key=key).exists()
    if already:
        logger.debug(f"Skipping duplicate trial end notify for user {user_id}, key={key}")
        return
    user.is_trial = False
    await user.save()
    if user.is_blocked:
        logger.info(f"Trial ended for blocked user {user.id} without notification")
        return
    if is_quiet_hours():
        await ensure_notification_mark(
            user_id=user.id,
            mark_type=PENDING_TRIAL_ENDED_MARK_TYPE,
            key=key,
        )
        logger.info(
            f"Deferred trial-ended notification for user {user.id} until quiet hours end"
        )
        return
    sent_ok = await notify_trial_ended(user)
    if sent_ok:
        await ensure_notification_mark(user_id=user.id, mark_type="trial_ended", key=key)
        return

    refreshed_user = await Users.get_or_none(id=user.id)
    if refreshed_user and not refreshed_user.is_blocked:
        await ensure_notification_mark(
            user_id=user.id,
            mark_type=PENDING_TRIAL_ENDED_MARK_TYPE,
            key=key,
        )
        logger.warning(
            f"Trial-ended notification delivery not confirmed for user {user.id}; queued for retry"
        )

## referral prompt per-user function removed; handled by periodic task

async def _exec_notify_expiring_trial(user_id: int, planned_expired: date):
    user = await Users.get_or_none(id=user_id)
    if not user or not user.is_trial or normalize_date(user.expired_at) != planned_expired:
        logger.debug(f"Skipping notify expiring trial for user {user_id}")
        return
    # Idempotency via notification marks
    key = str(planned_expired)
    already = await NotificationMarks.filter(user_id=user.id, type="trial_expiring", key=key).exists()
    if already:
        logger.debug(f"Skipping duplicate trial expiring for user {user_id}, key={key}")
        return
    if user.is_blocked:
        logger.info(f"Skipping trial expiring notification for blocked user {user.id}")
        return
    sent_ok = await notify_expiring_trial(user)
    if sent_ok:
        await NotificationMarks.create(user_id=user.id, type="trial_expiring", key=key)

async def _exec_notify_trial_1_day_left(user_id: int, planned_expired: date):
    user = await Users.get_or_none(id=user_id)
    if not user or not user.is_trial or normalize_date(user.expired_at) != planned_expired:
        logger.debug(f"Skipping 1-day trial marketing notify for user {user_id}")
        return
    # Idempotency via notification marks
    key = f"1d:{planned_expired}"
    already = await NotificationMarks.filter(user_id=user.id, type="trial_pre_expiring", key=key).exists()
    if already:
        logger.debug(f"Skipping duplicate 1-day trial marketing notify for user {user_id}, key={key}")
        return
    if user.is_blocked:
        logger.info(f"Skipping trial marketing notification for blocked user {user.id}")
        return
    sent_ok = await notify_trial_three_days_left(user)
    if sent_ok:
        await NotificationMarks.create(user_id=user.id, type="trial_pre_expiring", key=key)

async def schedule_user_tasks(user):
    """Schedule subscription, trial, and referral tasks for a user."""
    # Skip blocked users
    if user.is_blocked:
        logger.debug(f"Skipping task scheduling for blocked user {user.id}")
        return
        
    # Cancel existing tasks for this user before scheduling new ones
    cancel_user_tasks(user.id)
    scheduled_tasks[user.id] = []
    now = datetime.now(MOSCOW)

    # Subscription tasks: auto-payment and notifications
    if user.is_subscribed and user.expired_at:
        # Base time at midnight of expiration day
        base = datetime.combine(user.expired_at, time.min).replace(tzinfo=MOSCOW)

        if user.renew_id and payment_settings.auto_renewal_mode == "yookassa":
            # Logic for users WITH auto-renewal
            # 4, 3 and 2 days before: autopay attempt at midnight.
            # Check which payment attempts are needed to avoid multiple simultaneous payments
            current_time = datetime.now(MOSCOW)  # Get fresh current time for accurate comparison
            for days_before in (4, 3, 2):
                reminder_eta = build_auto_payment_reminder_eta(
                    planned_expired=user.expired_at,
                    days_before=days_before,
                )
                reminder_task = schedule_coro(
                    reminder_eta,
                    _exec_notify_auto_payment_reminder,
                    user.id,
                    user.expired_at,
                    days_before,
                    skip_if_past=True,
                )
                scheduled_tasks[user.id].append(reminder_task)

            payment_attempts_needed = []
            for days_before in (4, 3, 2):
                eta = base - timedelta(days=days_before)
                if eta <= current_time:
                    # If this payment time has passed, we need to execute it
                    payment_attempts_needed.append((eta, days_before))
                else:
                    # Future payment - schedule normally
                    task = schedule_coro(eta, _exec_auto_payment, user.id, user.expired_at, days_before)
                    scheduled_tasks[user.id].append(task)
            
            # If multiple payment attempts are needed (due to restart), execute only the most recent one
            if payment_attempts_needed:
                # Sort by eta (most recent time first) and execute only the first one
                payment_attempts_needed.sort(key=lambda x: x[0], reverse=True)
                most_recent_eta, most_recent_days = payment_attempts_needed[0]
                
                logger.info(f"Multiple autopay attempts needed for user {user.id}. Executing only most recent: {most_recent_days} days before")
                task = schedule_coro(most_recent_eta, _exec_auto_payment, user.id, user.expired_at, most_recent_days, skip_if_past=True)
                scheduled_tasks[user.id].append(task)

            # 1 day before: final check. If not paid, cancel subscription.
            eta_cancel = base - timedelta(days=1)
            task = schedule_coro(eta_cancel, _exec_cancel_if_unpaid, user.id, user.expired_at, skip_if_past=True)
            scheduled_tasks[user.id].append(task)
        else:
            # Logic for users WITHOUT auto-renewal: send expiration reminders
            # 3 and 2 days before: expiration reminder after quiet hours if needed
            for days_before in (3, 2):
                eta = normalize_user_notification_eta(base - timedelta(days=days_before))
                # Skip expiration reminders if time has already passed
                task = schedule_coro(eta, _exec_notify_expiring, user.id, user.expired_at, days_before, skip_if_past=True)
                scheduled_tasks[user.id].append(task)
            # 1 day before: expiration reminder at noon
            eta_remind = base - timedelta(days=1)
            reminder_time = eta_remind + timedelta(hours=12)
            # Skip expiration reminders if time has already passed
            task = schedule_coro(reminder_time, _exec_notify_expiring, user.id, user.expired_at, 1, skip_if_past=True)
            scheduled_tasks[user.id].append(task)

            # On expiration day: send notification that subscription has expired after quiet hours.
            # This will be skipped for auto-renewal users by the check inside _exec_notify_expired.
            # Skip expired notifications if time has already passed
            expired_eta = normalize_user_notification_eta(base)
            task = schedule_coro(expired_eta, _exec_notify_expired, user.id, user.expired_at, skip_if_past=True)
            scheduled_tasks[user.id].append(task)

    # Trial tasks
    # Only for users with trial assigned and not subscribed
    if user.is_trial and not user.is_subscribed and user.expired_at:
        exp_dt = datetime.combine(user.expired_at, time.min).replace(tzinfo=MOSCOW)
        # Marketing notification exactly 1 day before trial end, but not during quiet hours
        one_day_before_eta = normalize_user_notification_eta(exp_dt - timedelta(days=1))
        task = schedule_coro(one_day_before_eta, _exec_notify_trial_1_day_left, user.id, user.expired_at, skip_if_past=True)
        scheduled_tasks[user.id].append(task)
        # 2h/24h moved to periodic batch (retry_trial_notifications)
        # Notification about expiring trial - at 12:00 the day before
        day_before = exp_dt - timedelta(days=1)
        exp_notification_eta = datetime.combine(day_before.date(), time(hour=12, minute=0)).replace(tzinfo=MOSCOW)
        # Skip expiring trial notifications if time has already passed
        task = schedule_coro(exp_notification_eta, _exec_notify_expiring_trial, user.id, user.expired_at, skip_if_past=True)
        scheduled_tasks[user.id].append(task)
        
        # Trial extension check - skip if time has passed
        ext_eta = exp_dt - timedelta(days=2)
        task = schedule_coro(ext_eta, _exec_extend_trial, user.id, user.expired_at, skip_if_past=True)
        scheduled_tasks[user.id].append(task)
        # Trial end notification - skip if time has passed
        task = schedule_coro(exp_dt, _exec_notify_trial_end, user.id, user.expired_at, skip_if_past=True)
        scheduled_tasks[user.id].append(task)

    # Referral tasks moved to periodic batch (run_referral_prompts_scheduler)

    # Log summary of scheduled tasks for this user
    logger.debug(f"User {user.id}: total {len(scheduled_tasks[user.id])} tasks scheduled")

async def blocked_users_cleanup_scheduler():
    """Deprecated in favor of tasks.blocked_users_cleanup.run_blocked_users_cleanup_scheduler.

    Left for backward compatibility if imported elsewhere.
    """
    from bloobcat.tasks.blocked_users_cleanup import run_blocked_users_cleanup_scheduler  # noqa: WPS433
    await run_blocked_users_cleanup_scheduler()

## deprecated retry_send_missed_trial_notifications removed; periodic task in bloobcat.tasks

## deprecated retry_missed_trial_extensions removed; periodic task in bloobcat.tasks

## deprecated retry_missed_trial_endings removed; periodic task in bloobcat.tasks

## deprecated cleanup_missed_cancellations removed; periodic task in bloobcat.tasks

async def cleanup_blocked_users():
    """
    Очищает пользователей, заблокировавших бота более X дней назад.
    УСЛОВИЯ УДАЛЕНИЯ:
    1. Триальные: заблокированы > 7 дней назад и нет активного тарифа
    2. Платные: заблокированы > 7 дней И подписка истекла > 7 дней назад
    Использует существующий метод user.delete() для полной очистки.
    """
    if not app_settings.cleanup_blocked_users_enabled:
        logger.debug("Cleanup of blocked users is disabled")
        return
        
    logger.info("Starting cleanup of blocked users...")
    
    # Находим пользователей заблокированных более X дней назад
    cutoff_date = datetime.now(MOSCOW) - timedelta(days=app_settings.blocked_user_cleanup_days)
    today = date.today()
    subscription_cutoff_date = today - timedelta(days=app_settings.blocked_user_cleanup_days)
    
    # СЛУЧАЙ 1: Триальные пользователи.
    # Бизнес-правило: триал можно удалять по блокировке, но только если у него нет активного тарифа.
    # Это защита от сценария, когда флаг is_trial ошибочно остался True у платного пользователя.
    blocked_trial_users = await Users.filter(
        is_blocked=True,
        blocked_at__lte=cutoff_date,
        is_trial=True,
        active_tariff_id__isnull=True,
    )
    
    # СЛУЧАЙ 2: Платные пользователи (заблокированы > 7 дней И подписка истекла > 7 дней назад)
    blocked_expired_paid_users = await Users.filter(
        is_blocked=True,
        blocked_at__lte=cutoff_date,
        is_trial=False,  # Платные пользователи
        expired_at__lte=subscription_cutoff_date  # Подписка истекла > 7 дней назад
    )
    
    # Объединяем списки для удаления
    blocked_users = list(blocked_trial_users) + list(blocked_expired_paid_users)
    
    cleanup_count = 0
    error_count = 0
    trial_deleted = 0
    paid_deleted = 0
    
    # Дополнительно считаем заблокированных платных с активной подпиской (которых НЕ удаляем)
    blocked_paid_active = await Users.filter(
        is_blocked=True,
        blocked_at__lte=cutoff_date,
        is_trial=False,  # Платные пользователи
        expired_at__gt=subscription_cutoff_date  # С активной подпиской (истекла < 7 дней назад)
    ).count()
    
    # Диагностика: "триальные" с активным тарифом (сигнал возможной рассинхронизации флагов)
    blocked_trial_with_active_tariff = await Users.filter(
        is_blocked=True,
        blocked_at__lte=cutoff_date,
        is_trial=True,
        active_tariff_id__not_isnull=True,
    ).count()

    logger.info(f"Found {len(blocked_trial_users)} blocked trial users and {len(blocked_expired_paid_users)} blocked paid users with expired subscriptions for cleanup")
    if blocked_paid_active > 0:
        logger.info(f"Preserving {blocked_paid_active} blocked paid users with active subscriptions")
    if blocked_trial_with_active_tariff > 0:
        logger.warning(
            f"Preserving {blocked_trial_with_active_tariff} blocked 'trial' users with active_tariff_id set (possible is_trial mismatch)"
        )

    for user in blocked_users:
        try:
            user_type = "trial" if user.is_trial else f"paid (expired {user.expired_at})"
            logger.debug(f"Cleaning up blocked {user_type} user {user.id} (blocked at: {user.blocked_at})")
            await user.delete()  # Полная очистка: scheduler + RemnaWave + БД
            cleanup_count += 1
            if user.is_trial:
                trial_deleted += 1
            else:
                paid_deleted += 1
        except Exception as e:
            error_count += 1
            logger.error(f"Failed to cleanup blocked user {user.id}: {e}")
    
    if cleanup_count > 0 or error_count > 0 or blocked_paid_active > 0:
        logger.info(f"Blocked users cleanup completed: {trial_deleted} trial + {paid_deleted} expired paid users deleted, {error_count} errors, {blocked_paid_active} active paid users preserved")
        try:
            from bloobcat.bot.notifications.admin import send_admin_message  # noqa: WPS433

            summary_lines = [
                "🧹 <b>Очистка заблокированных пользователей</b>",
                f"Удалено: {trial_deleted} trial + {paid_deleted} paid (всего {cleanup_count})",
            ]
            if blocked_paid_active > 0:
                summary_lines.append(
                    f"Сохранено платных с активной подпиской: {blocked_paid_active}"
                )
            if blocked_trial_with_active_tariff > 0:
                summary_lines.append(
                    f"⚠️ trial с активным тарифом (пропущены): {blocked_trial_with_active_tariff}"
                )
            if error_count > 0:
                summary_lines.append(f"❌ Ошибок: {error_count}")
            summary_lines.append(
                f"Порог: {app_settings.blocked_user_cleanup_days} дн."
            )
            summary_lines.append("#cleanup #blocked_users")
            await send_admin_message("\n".join(summary_lines))
        except Exception as notify_error:
            logger.error(f"Failed to send blocked users cleanup summary to admin log: {notify_error}")
    else:
        logger.debug("No blocked users found for cleanup")



async def schedule_all_tasks():
    """Reschedule all tasks for all users. Typically run on startup."""
    logger.info("Scheduling all tasks for all users...")
    # Переведено в периодический таск: пропущенные trial-уведомления и продления
    # Переведено в периодический таск: пропущенные окончания trial
    # Переведено в периодический таск: пропущенные отмены автопродления
    await cleanup_blocked_users()  # Очистка заблокированных пользователей при старте
    users = await Users.all()
    for user in users:
        await schedule_user_tasks(user)
    logger.info(f"Tasks scheduled for {len(users)} users")
    # Start periodic RemnaWave updater
    asyncio.create_task(run_remnawave_scheduler())
    # Start LTE usage limiter
    asyncio.create_task(run_lte_usage_limiter_scheduler())
    asyncio.create_task(run_lte_usage_limiter_quick_scheduler())
    # Start periodic blocked users cleanup
    from bloobcat.tasks.blocked_users_cleanup import run_blocked_users_cleanup_scheduler  # noqa: WPS433
    asyncio.create_task(run_blocked_users_cleanup_scheduler())
    # Start periodic retry of missed trial notifications
    asyncio.create_task(run_retry_trial_notifications_scheduler())
    # Start periodic retry of missed trial extension notifications
    asyncio.create_task(run_retry_trial_extension_notifications_scheduler())
    # Start periodic retry of missed trial extensions
    asyncio.create_task(run_retry_trial_extensions_scheduler())
    # Start periodic retry of missed trial endings
    asyncio.create_task(run_retry_trial_endings_scheduler())
    # Start periodic cleanup of missed cancellations
    asyncio.create_task(run_cleanup_missed_cancellations_scheduler())
    # Start evening catch-up for missed auto-payment reminders
    asyncio.create_task(run_auto_payment_reminders_scheduler())
    # Start pending quiet-hours delivery and morning catch-ups for user notifications
    asyncio.create_task(run_quiet_hours_notifications_scheduler())
    # Start trial expiring catch-up (12:00 day-before)
    asyncio.create_task(run_trial_expiring_catchup_scheduler())
    # Start subscription expiring catch-up (3/2 days at 08:00, 1 day at 12:00)
    asyncio.create_task(run_subscription_expiring_catchup_scheduler())
    # Start periodic referral prompts (7d, 14d, then every 30d)
    asyncio.create_task(run_referral_prompts_scheduler())
    # Nudge users to add Vectra to home screen (24h → 7d → 30d decay; 3 attempts max).
    asyncio.create_task(run_home_screen_install_promo_scheduler())
    # Start winback discounts scheduler
    asyncio.create_task(run_winback_discounts_scheduler())
    # Start trial/active_tariff fixer
    asyncio.create_task(run_trial_active_tariff_fix_scheduler())
    # Reconcile manual payments in case YooKassa webhook delivery fails.
    asyncio.create_task(run_payment_reconcile_scheduler())
    # Retry RemnaWave user deletes that failed with transient errors.
    asyncio.create_task(run_remnawave_delete_retry_scheduler())
    # Precompute paid/trial traffic and revenue analytics for Directus.
    asyncio.create_task(run_service_growth_analytics_scheduler())
    # Safety net: resume frozen base subscriptions after family ends.
    asyncio.create_task(run_subscription_resume_scheduler())
    # Cleanup expired device setup links and unbound temporary device-users.
    asyncio.create_task(run_temp_setup_cleanup_scheduler())
    # Start automatic statistics scheduler
    from bloobcat.statistics.scheduler import statistics_scheduler
    asyncio.create_task(statistics_scheduler()) 
