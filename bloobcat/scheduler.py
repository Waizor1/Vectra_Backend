import asyncio
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users
from bloobcat.routes.payment import create_auto_payment
from bloobcat.bot.notifications.subscription.expiration import notify_expiring_subscription
from bloobcat.bot.notifications.subscription.key import on_disabled
from bloobcat.bot.notifications.trial.no_trial import notify_no_trial_taken
from bloobcat.bot.notifications.trial.extended import notify_trial_extended
from bloobcat.bot.notifications.trial.end import notify_trial_ended
from bloobcat.bot.notifications.general.referral import on_referral_prompt
from bloobcat.routes.remnawave.catcher import remnawave_updater
from bloobcat.logger import get_logger

logger = get_logger("scheduler")

MOSCOW = ZoneInfo("Europe/Moscow")
# Global mapping of user_id to scheduled asyncio tasks for cancellation
scheduled_tasks = {}

def schedule_coro(at_time: datetime, coro, *args):
    """Schedule coroutine execution at a specific datetime."""
    now = datetime.now(MOSCOW)
    delay = (at_time - now).total_seconds()
    
    if delay <= 0:
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
    user = await Users.get_or_none(id=user_id)
    if not user or not user.is_trial or user.expired_at != planned_expired or user.connected_at:
        logger.debug(f"Skipping extend trial for user {user_id}")
        return
    await user.extend_subscription(5)
    await notify_trial_extended(user, 5)
    await schedule_user_tasks(user)

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
        # 3 and 2 days before: autopay and expiration reminder at midnight
        for days_before in (3, 2):
            eta = base - timedelta(days=days_before)
            if eta > now:
                # Auto-payment attempt
                task = schedule_coro(eta, _exec_auto_payment, user.id, user.expired_at, days_before)
                scheduled_tasks[user.id].append(task)
                # Expiration reminder
                task = schedule_coro(eta, _exec_notify_expiring, user.id, user.expired_at, days_before)
                scheduled_tasks[user.id].append(task)
        # 1 day before: autopay at midnight, expiration reminder at noon
        eta = base - timedelta(days=1)
        if eta > now:
            # Auto-payment attempt
            task = schedule_coro(eta, _exec_auto_payment, user.id, user.expired_at, 1)
            scheduled_tasks[user.id].append(task)
            # Reminder that subscription will end tonight at midnight
            reminder_time = eta + timedelta(hours=12)
            task = schedule_coro(reminder_time, _exec_notify_expiring, user.id, user.expired_at, 1)
            scheduled_tasks[user.id].append(task)
        # On expiration day: send notification that subscription has expired
        if base > now:
            task = schedule_coro(base, _exec_notify_expired, user.id, user.expired_at)
            scheduled_tasks[user.id].append(task)

    # Trial tasks
    # Only for users with trial assigned and not subscribed
    if user.is_trial and not user.is_subscribed and user.expired_at:
        exp_dt = datetime.combine(user.expired_at, time.min).replace(tzinfo=MOSCOW)
        # Calculate time for notifications from registration_date
        reg_dt = user.registration_date.replace(tzinfo=MOSCOW)
        for hours in (2, 24):
            # Calculate target time as registration time + hours
            target_time = reg_dt + timedelta(hours=hours)
            # Check if notification was already sent
            notification_sent = (hours == 2 and user.notification_2h_sent) or (hours == 24 and user.notification_24h_sent)
            
            if not notification_sent:
                # Use target_time as basis for scheduling, not "now + hours"
                eta = target_time
                if eta > now:
                    task = schedule_coro(eta, _exec_notify_no_trial, user.id, hours)
                    scheduled_tasks[user.id].append(task)
                else:
                    # If the target time is already in the past, execute immediately
                    logger.debug(f"Sending immediate 'no trial taken' ({hours}h) to user {user.id}, registration was at {reg_dt}")
                    task = schedule_coro(now, _exec_notify_no_trial, user.id, hours)
                    scheduled_tasks[user.id].append(task)
        # Trial extension check
        ext_eta = exp_dt - timedelta(hours=12)
        if ext_eta > now:
            task = schedule_coro(ext_eta, _exec_extend_trial, user.id, user.expired_at)
            scheduled_tasks[user.id].append(task)
        # Trial end notification
        if exp_dt > now:
            task = schedule_coro(exp_dt, _exec_notify_trial_end, user.id, user.expired_at)
            scheduled_tasks[user.id].append(task)

    # Referral tasks at 18:00 local time
    start_dt = datetime.combine(user.registration_date.date(), time(hour=18)).replace(tzinfo=MOSCOW)
    for days in (7, 14, 30):
        eta = start_dt + timedelta(days=days)
        if eta > now:
            task = schedule_coro(eta, _exec_referral_prompt, user.id, days)
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

        hours_since_reg = (now - user.registration_date.replace(tzinfo=MOSCOW)).total_seconds() / 3600
        
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
        ext_eta = exp_dt - timedelta(hours=12)
        # Если момент продления уже прошёл (включая истекший trial)
        if ext_eta <= now_dt:
            logger.debug(f"Extending (missed) trial for user {user.id}")
            await _exec_extend_trial(user.id, user.expired_at)
            extended_count += 1
    logger.debug(f"Finished missed trial extensions: processed {processed}, extended {extended_count}")

async def retry_missed_trial_endings():
    """Retry trial end notifications and flag cleanup if trial expired during downtime."""
    logger.debug("Starting missed trial end check")
    # Только для тех, кто действительно подключался (connected_at != None)
    users = await Users.filter(is_trial=True, is_subscribed=False).exclude(connected_at=None)
    processed = 0
    finalized = 0
    now_dt = datetime.now(MOSCOW)
    for user in users:
        processed += 1
        exp_dt = datetime.combine(user.expired_at, time.min).replace(tzinfo=MOSCOW)
        # Если до окончания триала время уже прошло
        if exp_dt <= now_dt:
            logger.debug(f"Finalizing missed trial end for user {user.id}")
            await _exec_notify_trial_end(user.id, user.expired_at)
            finalized += 1
    logger.debug(f"Finished missed trial end check: processed {processed}, finalized {finalized}")

async def schedule_all_tasks():
    """Schedule tasks for all users on application startup."""
    # First try to send any missed notifications
    await retry_send_missed_trial_notifications()
    # Then retry missed trial extensions
    await retry_missed_trial_extensions()
    # Then finalize missed trial ends
    await retry_missed_trial_endings()
    
    users = await Users.all()
    for user in users:
        await schedule_user_tasks(user)
    # Start periodic RemnaWave updater
    asyncio.create_task(remnawave_scheduler()) 