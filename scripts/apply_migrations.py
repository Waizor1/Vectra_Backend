"""
One-off migrations runner for TVPN_BACK_END (Aerich + Tortoise).

Why:
- The app applies migrations on startup (see bloobcat/__main__.py lifespan),
  but during deploy it's useful to run migrations explicitly and fail fast.
"""

import asyncio
import sys
from pathlib import Path

from aerich import Command  # type: ignore

# Ensure the project root is importable when running as a script (so `import bloobcat` works).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM
from bloobcat.logger import get_logger

logger = get_logger("scripts.apply_migrations")


async def run() -> None:
    command = Command(tortoise_config=TORTOISE_ORM, location="migrations")
    try:
        logger.info("Aerich init...")
        await command.init()
        logger.info("Aerich upgrade (transaction=true)...")
        await command.upgrade(run_in_transaction=True)
        logger.info("Migrations applied successfully.")
        return
    except Exception as e:
        error_text = str(e).lower()
        if ("aerich" in error_text and "does not exist" in error_text) or "relation \"aerich\"" in error_text:
            logger.warning("Aerich table missing; running init_db(safe=True) and retrying upgrade...")
            init_db = getattr(command, "init_db", None)
            if init_db is None:
                raise RuntimeError("Aerich Command has no init_db() method") from e
            await init_db(safe=True)
            await command.upgrade(run_in_transaction=True)
            logger.info("Migrations applied successfully (after init_db).")
            return
        raise


if __name__ == "__main__":
    asyncio.run(run())

