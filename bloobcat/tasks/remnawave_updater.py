import asyncio

from bloobcat.logger import get_logger

logger = get_logger("tasks.remnawave_updater")


async def run_remnawave_scheduler(interval_seconds: int = 600):
    """Periodically run RemnaWave updater.

    Keeps a small wrapper to decouple the loop from the scheduler module.
    """
    logger.info(f"Starting RemnaWave updater scheduler (interval: {interval_seconds}s)")
    while True:
        try:
            # Late import to avoid circular import
            from bloobcat.routes.remnawave.catcher import remnawave_updater  # noqa: WPS433
            await remnawave_updater()
        except Exception:
            pass
        await asyncio.sleep(interval_seconds)


