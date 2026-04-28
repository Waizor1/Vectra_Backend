"""
One-off migrations runner for Vectra Connect backend (Vectra_Backend, Aerich + Tortoise).

Why:
- The app applies migrations on startup (see bloobcat/__main__.py lifespan),
  but during deploy it's useful to run migrations explicitly and fail fast.
"""

import asyncio
import argparse
import sys
from pathlib import Path
from typing import Any
from typing import cast

# Ensure the project root is importable when running as a script (so `import bloobcat` works).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM
from bloobcat.logger import get_logger
from scripts.verify_runtime_state import verify_runtime_state

logger = get_logger("scripts.apply_migrations")

# Keep a module-level symbol for tests that monkeypatch `Command`.
Command: type[Any] | None = None


def _resolve_command_class() -> type[Any]:
    global Command
    if Command is not None:
        return Command

    try:
        from aerich import Command as aerich_command  # type: ignore
    except ModuleNotFoundError as exc:
        if exc.name == "aerich":
            raise RuntimeError(
                "Aerich is required to apply migrations. Install it first "
                "(for example: `pip install aerich` or `poetry install`)."
            ) from exc
        raise

    Command = aerich_command
    return cast(type[Any], Command)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply DB migrations and verify runtime state")
    parser.add_argument(
        "--skip-runtime-verify",
        action="store_true",
        help="Skip post-migration runtime-state verification (emergency opt-out)",
    )
    return parser.parse_args(argv)


async def run(*, skip_runtime_verify: bool = False) -> None:
    command_class = _resolve_command_class()
    command = command_class(tortoise_config=TORTOISE_ORM, location="migrations")
    try:
        logger.info("Aerich init...")
        await command.init()
        logger.info("Aerich upgrade (transaction=true)...")
        await command.upgrade(run_in_transaction=True)
        logger.info("Migrations applied successfully.")
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
        else:
            raise

    if skip_runtime_verify:
        logger.warning("Skipping runtime-state verification by --skip-runtime-verify")
        return

    logger.info("Running post-migration runtime-state verification...")
    await verify_runtime_state()
    logger.info("Post-migration runtime-state verification passed.")


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(skip_runtime_verify=args.skip_runtime_verify))

