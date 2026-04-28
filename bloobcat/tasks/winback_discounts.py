import asyncio
from datetime import date, timedelta, time, datetime
from zoneinfo import ZoneInfo

from bloobcat.db.users import Users, normalize_date
from bloobcat.db.discounts import PersonalDiscount
from bloobcat.db.notifications import NotificationMarks
from bloobcat.logger import get_logger
from bloobcat.bot.notifications.winback.discount_offer import notify_winback_discount_offer
from bloobcat.tasks.quiet_hours import (
    PENDING_WINBACK_DISCOUNT_MARK_TYPE,
    build_pending_meta,
    build_winback_notification_key,
    ensure_notification_mark,
    is_quiet_hours,
)

logger = get_logger("winback_discounts")
MOSCOW = ZoneInfo("Europe/Moscow")

CHURN_DAYS = 7  # Days after subscription expiration to consider user churned
DISCOUNT_DURATION_DAYS = 7
NOTIFICATION_COOLDOWN_DAYS = 30 # Don't send a new offer if one was sent in the last 30 days


def _compute_winback_discount_percent(days_since_expired: int) -> int:
    """
    Возвращает маркетингово обоснованную многоярусную скидку на основе сроков ухода.
    Минимум 25% на 7-й день, плавный рост, максимум 99%.
    """
    if days_since_expired < CHURN_DAYS:
        return 0
    # Ступени роста скидок (включительно по нижней границе)
    if days_since_expired < 14:
        return 25
    if days_since_expired < 21:
        return 30
    if days_since_expired < 30:
        return 35
    if days_since_expired < 45:
        return 40
    if days_since_expired < 60:
        return 50
    if days_since_expired < 90:
        return 60
    if days_since_expired < 120:
        return 70
    if days_since_expired < 180:
        return 80
    if days_since_expired < 270:
        return 90
    if days_since_expired < 365:
        return 95
    return 99


async def _upsert_winback_discount(user: Users, discount_percent: int, new_expires_at: date) -> bool:
    """
    Создаёт или обновляет единственную winback-скидку пользователя.
    - Если скидки нет — создаёт.
    - Если есть — повышает percent при необходимости, продлевает expires_at, сбрасывает remaining_uses=1.
    - Удаляет возможные дубликаты (оставляя одну запись).
    Возвращает True, если была создана новая скидка или изменены поля текущей (percent/expiry/remaining_uses).
    """
    changed = False

    existing = await PersonalDiscount.filter(user_id=user.id, source="winback").order_by("-percent", "-id").all()
    keep_obj = existing[0] if existing else None

    # Удаляем дубликаты, если есть
    if existing and len(existing) > 1:
        for obj in existing[1:]:
            try:
                await obj.delete()
                changed = True
            except Exception as e:
                logger.warning(f"Failed to delete duplicate winback discount id={obj.id} for user {user.id}: {e}")

    if keep_obj is None:
        await PersonalDiscount.create(
            user_id=user.id,
            percent=discount_percent,
            expires_at=new_expires_at,
            remaining_uses=1,
            source="winback"
        )
        return True

    # Повышаем процент, если новая ступень выше текущей
    if int(discount_percent) > int(keep_obj.percent):
        keep_obj.percent = int(discount_percent)
        changed = True

    # Продлеваем срок действия, если новый больше
    # Или если срок уже истёк — обязательно обновляем
    if not keep_obj.expires_at or keep_obj.expires_at < new_expires_at:
        keep_obj.expires_at = new_expires_at
        changed = True

    # Сбрасываем remaining_uses до 1, чтобы скидка гарантированно была применима
    if int(keep_obj.remaining_uses or 0) != 1:
        keep_obj.remaining_uses = 1
        changed = True

    if changed:
        await keep_obj.save()
    return changed


async def create_winback_discounts():
    """
    Finds churned users and creates or updates personal winback discounts for them.
    """
    logger.info("Starting winback discount creation task...")
    now_msk = datetime.now(MOSCOW)

    churn_date = date.today() - timedelta(days=CHURN_DAYS)

    churned_users = await Users.filter(
        is_subscribed=False,
        expired_at__lte=churn_date
    ).all()

    logger.info(f"Found {len(churned_users)} potentially churned users.")

    for user in churned_users:
        # Check if a winback notification was sent recently
        recent_notification = await NotificationMarks.filter(
            user_id=user.id,
            type="winback_discount",
            sent_at__gte=now_msk - timedelta(days=NOTIFICATION_COOLDOWN_DAYS)
        ).exists()

        expired_at = normalize_date(user.expired_at)
        days_since_expired = (date.today() - expired_at).days if expired_at else 0
        discount_percent = _compute_winback_discount_percent(days_since_expired)
        if discount_percent <= 0:
            logger.debug(f"User {user.id} not eligible for winback discount yet (days_since_expired={days_since_expired}).")
            continue

        expires_at = date.today() + timedelta(days=DISCOUNT_DURATION_DAYS)

        changed = await _upsert_winback_discount(user, discount_percent, expires_at)

        # Уведомляем, только если произошли изменения и не было свежих рассылок
        if changed and not recent_notification:
            logger.info(f"Winback discount upserted for user {user.id}: {discount_percent}% (days_since_expired={days_since_expired})")
            key = build_winback_notification_key(expires_at)
            await NotificationMarks.filter(
                user_id=user.id,
                type=PENDING_WINBACK_DISCOUNT_MARK_TYPE,
            ).delete()
            if user.is_blocked:
                logger.info(
                    f"Skipping winback notification for blocked user {user.id}"
                )
            elif is_quiet_hours(now_msk):
                await ensure_notification_mark(
                    user_id=user.id,
                    mark_type=PENDING_WINBACK_DISCOUNT_MARK_TYPE,
                    key=key,
                    meta=build_pending_meta(
                        discount_percent=discount_percent,
                        expires_at=expires_at.isoformat(),
                    ),
                )
            else:
                delivered = await notify_winback_discount_offer(
                    user, discount_percent, expires_at
                )
                if delivered:
                    await ensure_notification_mark(
                        user_id=user.id,
                        mark_type="winback_discount",
                        key=key,
                    )
                else:
                    refreshed_user = await Users.get_or_none(id=user.id)
                    if refreshed_user and not refreshed_user.is_blocked:
                        await ensure_notification_mark(
                            user_id=user.id,
                            mark_type=PENDING_WINBACK_DISCOUNT_MARK_TYPE,
                            key=key,
                            meta=build_pending_meta(
                                discount_percent=discount_percent,
                                expires_at=expires_at.isoformat(),
                            ),
                        )
                        logger.warning(
                            f"Winback notification delivery not confirmed for user {user.id}; queued for retry"
                        )

    logger.info("Winback discount creation task finished.")


def get_next_daily_run_time() -> datetime:
    """Calculates the next run time for 2 AM Moscow time."""
    now = datetime.now(MOSCOW)
    tomorrow = now.date() + timedelta(days=1)
    next_run_time = datetime.combine(tomorrow, time(2, 0)).replace(tzinfo=MOSCOW)
    return next_run_time


async def run_winback_discounts_scheduler():
    """
    Runs the winback discount creation task periodically.
    """
    logger.info("Starting winback discounts scheduler.")
    while True:
        next_run_time = get_next_daily_run_time()
        delay = (next_run_time - datetime.now(MOSCOW)).total_seconds()
        if delay > 0:
            logger.info(f"Sleeping for {delay} seconds until the next winback discount run.")
            await asyncio.sleep(delay)

        try:
            await create_winback_discounts()
        except Exception as e:
            logger.error(f"Error in winback discount scheduler: {e}", exc_info=True)
