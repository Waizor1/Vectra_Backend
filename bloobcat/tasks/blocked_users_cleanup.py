import asyncio

from bloobcat.logger import get_logger
from bloobcat.settings import app_settings

logger = get_logger("tasks.blocked_users_cleanup")


async def run_blocked_users_cleanup_scheduler():
    """Periodically runs cleanup of blocked users according to app settings.

    Uses `cleanup_blocked_users_interval_hours` and `cleanup_blocked_users_enabled`.
    """
    if not app_settings.cleanup_blocked_users_enabled:
        logger.info("Blocked users cleanup scheduler disabled")
        return

    interval_seconds = app_settings.cleanup_blocked_users_interval_hours * 3600
    logger.info(
        f"Starting blocked users cleanup scheduler (interval: {app_settings.cleanup_blocked_users_interval_hours}h)"
    )

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            # Late import to avoid circular import
            from bloobcat.scheduler import cleanup_blocked_users  # noqa: WPS433
            await cleanup_blocked_users()
        except Exception as e:
            logger.error(f"Error in blocked users cleanup scheduler: {e}")


