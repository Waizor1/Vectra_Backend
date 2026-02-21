import asyncio

from bloobcat.logger import get_logger
from bloobcat.services.admin_integration import process_remnawave_delete_retry_jobs


logger = get_logger("tasks.remnawave_delete_retry")


async def run_remnawave_delete_retry_scheduler(interval_seconds: int = 60) -> None:
    logger.info("Starting RemnaWave delete retry scheduler (interval: %ss)", interval_seconds)
    while True:
        try:
            stats = await process_remnawave_delete_retry_jobs()
            if stats.get("processed", 0):
                logger.info("RemnaWave delete retry iteration stats: %s", stats)
        except Exception as exc:
            logger.error("Error in RemnaWave delete retry scheduler: %s", exc, exc_info=True)
        await asyncio.sleep(interval_seconds)
