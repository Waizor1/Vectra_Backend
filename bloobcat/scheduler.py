import asyncio
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.routes.payment import create_auto_payment
from bloobcat.bot.notifications.subscription.expiration import notify_expiring_subscription
from bloobcat.bot.notifications.subscription.renewal import notify_subscription_cancelled_after_failures
from bloobcat.bot.notifications.subscription.key import on_disabled
from bloobcat.bot.notifications.trial.no_trial import notify_no_trial_taken
from bloobcat.bot.notifications.trial.extended import notify_trial_extended
from bloobcat.bot.notifications.trial.end import notify_trial_ended
from bloobcat.bot.notifications.trial.expiring import notify_expiring_trial
from bloobcat.bot.notifications.general.referral import on_referral_prompt
from bloobcat.routes.remnawave.catcher import remnawave_updater
from bloobcat.logger import get_logger

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
    tasks = scheduled_tasks.pop(user_id, [])
    if tasks:
        logger.debug(f"Cancelling {len(tasks)} scheduled tasks for user {user_id}")
    else:
        logger.debug(f"No scheduled tasks to cancel for user {user_id}")
    for t in tasks:
        t.cancel()

# State-validation wrappers before executing tasks
async def _exec_auto_payment(user_id: int, planned_expired: date, days_before: int):
    user = await Users.get_or_none(id=user_id)
    if not user or user.expired_at != planned_expired or not user.renew_id:
        logger.debug(f"Skipping auto payment for user {user_id}")
        return
    await create_auto_payment(user, disable_on_fail=(days_before == 0))

async def _exec_notify_expiring(user_id: int, planned_expired: date, days_before: int):
    user = await Users.get_or_none(id=user_id)
    if not user or user.expired_at != planned_expired or user.renew_id:
        logger.debug(f"Skipping notify expiring for user {user_id}")
        return
    await notify_expiring_subscription(user)

async def _exec_cancel_if_unpaid(user_id: int, planned_expired: date):
    """Checks if a subscription was renewed, and if not, cancels it."""
    user = await Users.get_or_none(id=user_id)
    # If user does not exist, or subscription was renewed (expired_at changed), or auto-renewal was cancelled, do nothing.
    if not user or user.expired_at != planned_expired or not user.renew_id:
        logger.debug(f"Skipping cancel_if_unpaid for user {user_id}. Conditions not met.")
        return

    logger.warning(f"User {user_id} subscription renewal failed multiple times. Cancelling subscription.")
    # Cancel subscription
    user.is_subscribed = False
    user.renew_id = None
    await user.save()

    # Notify user about cancellation
    await notify_subscription_cancelled_after_failures(user)

async def _exec_notify_expired(user_id: int, planned_expired: date):
    user = await Users.get_or_none(id=user_id)
    if not user or user.expired_at != planned_expired or user.renew_id:
        logger.debug(f"Skipping notify expired for user {user_id}")
        return
    await on_disabled(user)

async def _exec_notify_no_trial(user_id: int, hours_passed: int):
    user = await Users.get_or_none(id=user_id)
    # Only for users who were assigned a trial and haven't subscribed yet
    if not user or not user.is_trial or user.connected_at:
        logger.debug(f"Skipping notify no trial for user {user_id} (no trial assigned)")
        return
    if user.is_subscribed:
        logger.debug(f"Skipping notify no trial for user {user_id} (already subscribed)")
        return
    # Send notification
    await notify_no_trial_taken(user, hours_passed)
    # Mark as sent
    if hours_passed == 2:
        user.notification_2h_sent = True
    elif hours_passed == 24:
        user.notification_24h_sent = True
    await user.save()

async def _exec_extend_trial(user_id: int, planned_expired: date):
    """
    Продляет trial подписку пользователя и отправляет уведомление.
    ОПТИМИЗИРОВАНО для 10k+ пользователей с rate limiting 28.5 сообщений/сек.
    """
    global _last_trial_notification_time, _trial_extension_stats
    
    user = await Users.get_or_none(id=user_id)
    if not user or not user.is_trial or user.expired_at != planned_expired or user.connected_at:
        logger.debug(f"[{user_id}] Skipping extend trial for user because conditions not met (user: {bool(user)}, is_trial: {user.is_trial if user else 'N/A'}, expired_at: {user.expired_at if user else 'N/A'} vs {planned_expired}, connected_at: {user.connected_at if user else 'N/A'})")
        return
    
    logger.debug(f"[{user_id}] Attempting to extend trial. Current expired_at: {user.expired_at}")
    
    # Инициализация статистики производительности
    if _trial_extension_stats["performance"]["start_time"] is None:
        _trial_extension_stats["performance"]["start_time"] = asyncio.get_event_loop().time()
    
    _trial_extension_stats["total_attempts"] += 1
    
    # Продляем trial
    user.expired_at += timedelta(days=1)
    await user.save()
    logger.debug(f"[{user_id}] Trial extended. New expired_at: {user.expired_at}")
    
    # Rate limiting для уведомлений
    current_time = asyncio.get_event_loop().time()
    if _last_trial_notification_time is not None:
        time_since_last = current_time - _last_trial_notification_time
        if time_since_last < _trial_notification_delay:
            wait_time = _trial_notification_delay - time_since_last
            logger.debug(f"[{user_id}] Rate limiting: waiting {wait_time:.3f}s")
            await asyncio.sleep(wait_time)
    
    # Отправляем уведомление с таймаутом (было 120с, стало 600с = 10 минут)
    try:
        notification_result = await asyncio.wait_for(
            notify_trial_extended(user, 1),
            timeout=600.0  # ОПТИМИЗИРОВАНО: 10 минут для больших объёмов
        )
        
        if notification_result:
            _trial_extension_stats["successful_notifications"] += 1
            logger.debug(f"[{user_id}] Trial extension notification sent successfully")
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
    if not user or not user.is_trial or user.expired_at != planned_expired:
        logger.debug(f"Skipping notify trial end for user {user_id}")
        return
    await notify_trial_ended(user)
    user.is_trial = False
    await user.save()

async def _exec_referral_prompt(user_id: int, scheduled_days: int):
    user = await Users.get_or_none(id=user_id)
    if not user or not user.is_registered or not user.is_subscribed or user.referrals > 0:
        logger.debug(f"Skipping referral prompt for user {user_id}")
        return
    await on_referral_prompt(user, scheduled_days)

async def _exec_notify_expiring_trial(user_id: int, planned_expired: date):
    user = await Users.get_or_none(id=user_id)
    if not user or not user.is_trial or user.expired_at != planned_expired:
        logger.debug(f"Skipping notify expiring trial for user {user_id}")
        return
    await notify_expiring_trial(user)

async def schedule_user_tasks(user):
    """Schedule subscription, trial, and referral tasks for a user."""
    # Cancel existing tasks for this user before scheduling new ones
    cancel_user_tasks(user.id)
    scheduled_tasks[user.id] = []
    now = datetime.now(MOSCOW)

    # Subscription tasks: auto-payment and notifications
    if user.is_subscribed and user.expired_at:
        # Base time at midnight of expiration day
        base = datetime.combine(user.expired_at, time.min).replace(tzinfo=MOSCOW)

        if user.renew_id:
            # Logic for users WITH auto-renewal
            # 4, 3 and 2 days before: autopay attempt at midnight.
            # Check which payment attempts are needed to avoid multiple simultaneous payments
            current_time = datetime.now(MOSCOW)  # Get fresh current time for accurate comparison
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
                task = schedule_coro(most_recent_eta, _exec_auto_payment, user.id, user.expired_at, most_recent_days)
                scheduled_tasks[user.id].append(task)

            # 1 day before: final check. If not paid, cancel subscription.
            eta_cancel = base - timedelta(days=1)
            task = schedule_coro(eta_cancel, _exec_cancel_if_unpaid, user.id, user.expired_at)
            scheduled_tasks[user.id].append(task)
        else:
            # Logic for users WITHOUT auto-renewal: send expiration reminders
            # 3 and 2 days before: expiration reminder at midnight
            for days_before in (3, 2):
                eta = base - timedelta(days=days_before)
                # Skip expiration reminders if time has already passed
                task = schedule_coro(eta, _exec_notify_expiring, user.id, user.expired_at, days_before, skip_if_past=True)
                scheduled_tasks[user.id].append(task)
            # 1 day before: expiration reminder at noon
            eta_remind = base - timedelta(days=1)
            reminder_time = eta_remind + timedelta(hours=12)
            # Skip expiration reminders if time has already passed
            task = schedule_coro(reminder_time, _exec_notify_expiring, user.id, user.expired_at, 1, skip_if_past=True)
            scheduled_tasks[user.id].append(task)

            # On expiration day: send notification that subscription has expired.
            # This will be skipped for auto-renewal users by the check inside _exec_notify_expired.
            # Skip expired notifications if time has already passed
            task = schedule_coro(base, _exec_notify_expired, user.id, user.expired_at, skip_if_past=True)
            scheduled_tasks[user.id].append(task)

    # Trial tasks
    # Only for users with trial assigned and not subscribed
    if user.is_trial and not user.is_subscribed and user.expired_at:
        exp_dt = datetime.combine(user.expired_at, time.min).replace(tzinfo=MOSCOW)
        # Calculate time for notifications from registration_date
        reg_dt = user.registration_date.replace(tzinfo=ZoneInfo("UTC")).astimezone(MOSCOW)
        for hours in (2, 24):
            # Calculate target time as registration time + hours
            target_time = reg_dt + timedelta(hours=hours)
            # Check if notification was already sent
            notification_sent = (hours == 2 and user.notification_2h_sent) or (hours == 24 and user.notification_24h_sent)
            
            if not notification_sent:
                # Skip trial notifications if time has already passed
                task = schedule_coro(target_time, _exec_notify_no_trial, user.id, hours, skip_if_past=True)
                scheduled_tasks[user.id].append(task)
        
        # Notification about expiring trial - at 12:00 the day before
        day_before = exp_dt - timedelta(days=1)
        exp_notification_eta = datetime.combine(day_before.date(), time(hour=12, minute=0)).replace(tzinfo=MOSCOW)
        # Skip expiring trial notifications if time has already passed
        task = schedule_coro(exp_notification_eta, _exec_notify_expiring_trial, user.id, user.expired_at, skip_if_past=True)
        scheduled_tasks[user.id].append(task)
        
        # Trial extension check
        ext_eta = exp_dt - timedelta(days=2)
        task = schedule_coro(ext_eta, _exec_extend_trial, user.id, user.expired_at)
        scheduled_tasks[user.id].append(task)
        # Trial end notification
        task = schedule_coro(exp_dt, _exec_notify_trial_end, user.id, user.expired_at)
        scheduled_tasks[user.id].append(task)

    # Referral tasks at 18:00 local time
    start_dt = datetime.combine(user.registration_date.date(), time(hour=18)).replace(tzinfo=MOSCOW)
    for days in (7, 14, 30):
        eta = start_dt + timedelta(days=days)
        # Skip referral notifications if time has already passed
        task = schedule_coro(eta, _exec_referral_prompt, user.id, days, skip_if_past=True)
        scheduled_tasks[user.id].append(task)

    # Log summary of scheduled tasks for this user
    logger.debug(f"User {user.id}: total {len(scheduled_tasks[user.id])} tasks scheduled")

async def remnawave_scheduler(interval_seconds: int = 600):
    """Periodically run RemnaWave updater."""
    while True:
        try:
            await remnawave_updater()
        except Exception:
            pass
        await asyncio.sleep(interval_seconds)

async def retry_send_missed_trial_notifications():
    """Retry sending trial notifications to users who haven't received them yet"""
    logger.debug(f"Starting missed trial notifications check")
    # Только для пользователей с активным триалом, без коннектов и без платной подписки
    users = await Users.filter(is_trial=True, connected_at=None, is_subscribed=False)
    
    processed_count = 0
    sent_count = 0
    
    now = datetime.now(MOSCOW)
    for user in users:
        processed_count += 1
        # Skip users who have subscribed since assignment
        if user.is_subscribed:
            continue
        
        # Skip users who made a payment
        from bloobcat.db.payments import ProcessedPayments
        if await ProcessedPayments.filter(user_id=user.id, status="succeeded").exists():
            continue

        reg_dt = user.registration_date.replace(tzinfo=ZoneInfo("UTC")).astimezone(MOSCOW)
        hours_since_reg = (now - reg_dt).total_seconds() / 3600
        
        if hours_since_reg >= 2 and not user.notification_2h_sent:
            logger.debug(f"Sending missed 2h notification to user {user.id}, registration {hours_since_reg:.1f}h ago")
            await notify_no_trial_taken(user, 2)
            user.notification_2h_sent = True
            await user.save()
            sent_count += 1
        
        # If user registered more than 24 hours ago, send 24h notification
        if hours_since_reg >= 24 and not user.notification_24h_sent:
            logger.debug(f"Sending missed 24h notification to user {user.id}, registration {hours_since_reg:.1f}h ago")
            await notify_no_trial_taken(user, 24)
            user.notification_24h_sent = True
            await user.save()
            sent_count += 1
    
    logger.debug(f"Finished missed trial notifications: processed {processed_count}, sent {sent_count}")

async def retry_missed_trial_extensions():
    """Retry trial extensions for users if the extension time has already passed."""
    logger.debug("Starting missed trial extensions check")
    users = await Users.filter(is_trial=True, connected_at=None, is_subscribed=False)
    processed = 0
    extended_count = 0
    now_dt = datetime.now(MOSCOW)
    for user in users:
        processed += 1
        exp_dt = datetime.combine(user.expired_at, time.min).replace(tzinfo=MOSCOW)
        ext_eta = exp_dt - timedelta(days=2)
        # Если момент продления уже прошёл (включая истекший trial)
        if ext_eta <= now_dt:
            logger.debug(f"Extending (missed) trial for user {user.id}")
            await _exec_extend_trial(user.id, user.expired_at)
            extended_count += 1
    logger.debug(f"Finished missed trial extensions: processed {processed}, extended {extended_count}")

async def retry_missed_trial_endings():
    """Retry ending trials for users whose trial expiration was missed"""
    logger.debug(f"Starting missed trial endings check")
    users = await Users.filter(is_trial=True, expired_at__lte=date.today())
    
    ended_count = 0
    for user in users:
        logger.info(f"Retrying trial end for user {user.id}")
        await notify_trial_ended(user)
        user.is_trial = False
        await user.save()
        ended_count += 1
        
    logger.debug(f"Finished missed trial endings check. Ended: {ended_count}")

async def cleanup_missed_cancellations():
    """
    Finds and cancels subscriptions for users where auto-renewal failed
    and the cancellation task was missed (e.g., due to bot downtime).
    """
    logger.info("Running cleanup for missed subscription cancellations...")
    
    # We are looking for users who are in an inconsistent state:
    # 1. They are still marked as subscribed.
    # 2. They have auto-renewal enabled.
    # 3. Their expiration date has already passed.
    # This is a clear sign that renewal and cancellation tasks failed.
    
    users_to_cancel = await Users.filter(
        is_subscribed=True,
        renew_id__not_isnull=True,
        expired_at__lte=date.today()
    )
    
    cancelled_count = 0
    for user in users_to_cancel:
        logger.warning(f"Found missed cancellation for user {user.id} (expired at {user.expired_at}). Cancelling now.")
        
        # This is the same logic as in _exec_cancel_if_unpaid
        user.is_subscribed = False
        user.renew_id = None
        await user.save()

        # Notify user about cancellation
        await notify_subscription_cancelled_after_failures(user)
        cancelled_count += 1
        
    logger.info(f"Finished cleanup for missed cancellations. Cancelled: {cancelled_count} users.")

async def schedule_all_tasks():
    """Reschedule all tasks for all users. Typically run on startup."""
    logger.info("Scheduling all tasks for all users...")
    await retry_send_missed_trial_notifications()
    await retry_missed_trial_extensions()
    await retry_missed_trial_endings()
    await cleanup_missed_cancellations()
    users = await Users.all()
    for user in users:
        await schedule_user_tasks(user)
    logger.info(f"Tasks scheduled for {len(users)} users")
    # Start periodic RemnaWave updater
    asyncio.create_task(remnawave_scheduler()) 