import asyncio
from datetime import date

from bloobcat.db.subscription_freezes import SubscriptionFreezes
from bloobcat.db.users import Users, normalize_date
from bloobcat.logger import get_logger
from bloobcat.services.subscription_overlay import resume_frozen_base_if_due


logger = get_logger("subscription_resume")


async def run_subscription_resume_once(batch_limit: int = 100) -> None:
    today = date.today()
    freezes = (
        await SubscriptionFreezes.filter(
            is_active=True,
            resume_applied=False,
            family_expires_at__lt=today,
        )
        .order_by("family_expires_at")
        .limit(batch_limit)
    )
    for freeze in freezes:
        user = await Users.get_or_none(id=int(freeze.user_id))
        if not user:
            continue
        try:
            await resume_frozen_base_if_due(user)
        except Exception as exc:
            logger.error("subscription resume task failed for user=%s: %s", user.id, exc, exc_info=True)


async def run_subscription_resume_scheduler(interval_seconds: int = 300) -> None:
    logger.info("Starting subscription resume scheduler (interval: %ss)", interval_seconds)
    while True:
        try:
            await run_subscription_resume_once()
        except Exception as exc:
            logger.error("subscription resume scheduler loop error: %s", exc, exc_info=True)
        await asyncio.sleep(interval_seconds)
