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
_OLD_FORMAT_MIGRATION_FRAGMENT = "old format of migration file detected"
_LEGACY_ALREADY_APPLIED_FRAGMENTS = (
    "already exists",
    "duplicatecolumnerror",
    "duplicate column",
    "duplicate table",
    "duplicate constraint",
    "duplicate object",
)

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


def _is_old_format_migration_error(exc: Exception) -> bool:
    return _OLD_FORMAT_MIGRATION_FRAGMENT in str(exc).lower()


def _legacy_migration_number(version_file: str | None) -> int | None:
    if not version_file:
        return None
    prefix = version_file.split("_", 1)[0]
    try:
        return int(prefix)
    except ValueError:
        return None


def _is_legacy_schema_already_applied_error(
    exc: Exception,
    *,
    version_file: str | None = None,
) -> bool:
    error_text = str(exc).lower()
    if any(fragment in error_text for fragment in _LEGACY_ALREADY_APPLIED_FRAGMENTS):
        return True

    # Some old migrations rename/drop columns that are already absent in the
    # current production schema (for example tariffs.price -> base_price).
    # Treat that as an already-applied marker only while reconciling the legacy
    # pre-current migration range. New/current migrations should still fail
    # closed on missing dependencies.
    migration_number = _legacy_migration_number(version_file)
    return bool(
        migration_number is not None
        and migration_number < 100
        and "does not exist" in error_text
    )


async def _prepare_legacy_aerich_upgrade(command: Any) -> None:
    """Prepare Aerich 0.9.x to apply legacy migration files.

    Production has a long-lived migration history that predates Aerich's
    `MODELS_STATE` file format. Aerich 0.9.x refuses `Command.init()` when the
    newest file is still in that old format, but `Command.upgrade()` can still
    safely apply SQL migrations and records current model state for files that
    do not provide `MODELS_STATE`.

    This keeps deploy fail-closed for real upgrade/runtime verification errors
    while allowing the legacy migration catalog to keep working.
    """
    from aerich.migrate import Migrate  # type: ignore
    from tortoise import Tortoise

    if not Tortoise._inited:
        await Tortoise.init(config=TORTOISE_ORM)

    Migrate.app = command.app
    Migrate.migrate_location = Path(command.location, command.app)


async def _legacy_tolerant_upgrade(command: Any) -> None:
    """Apply missing migrations while tolerating pre-existing legacy schema.

    Some production databases were migrated before Aerich records were kept
    consistently. In that case Aerich may think an old migration is pending
    while the target column/table/constraint already exists. For this legacy
    recovery path only, mark that migration as applied and continue so new
    migrations can still be applied normally and runtime verification remains
    the final safety gate.
    """
    from aerich.migrate import Migrate  # type: ignore
    from aerich.models import Aerich  # type: ignore
    from aerich.utils import get_app_connection_name  # type: ignore
    from tortoise.exceptions import OperationalError
    from tortoise.transactions import in_transaction

    app_conn_name = get_app_connection_name(command.tortoise_config, command.app)
    migrated: list[str] = []
    marked_already_applied: list[str] = []

    for version_module in Migrate.get_all_version_modules():
        version_file = version_module.name + ".py"
        try:
            exists = await Aerich.exists(version=version_file, app=command.app)
        except OperationalError:
            exists = False
        if exists:
            continue

        try:
            async with in_transaction(app_conn_name) as conn:
                await command._upgrade(conn, version_file, False, version_module)
            migrated.append(version_file)
        except OperationalError as exc:
            if not _is_legacy_schema_already_applied_error(exc, version_file=version_file):
                raise

            logger.warning(
                "Migration {} appears already applied in legacy schema ({}); "
                "recording Aerich marker and continuing.",
                version_file,
                exc,
            )
            async with in_transaction(app_conn_name) as conn:
                await command._upgrade(conn, version_file, True, version_module)
            marked_already_applied.append(version_file)

    if migrated:
        logger.info("Applied migrations: {}", ", ".join(migrated))
    if marked_already_applied:
        logger.warning(
            "Marked legacy already-applied migrations: {}",
            ", ".join(marked_already_applied),
        )


async def run(*, skip_runtime_verify: bool = False) -> None:
    command_class = _resolve_command_class()
    command = command_class(tortoise_config=TORTOISE_ORM, location="migrations")
    use_legacy_tolerant_upgrade = False
    try:
        logger.info("Aerich init...")
        try:
            await command.init()
        except Exception as init_error:
            if not _is_old_format_migration_error(init_error):
                raise
            logger.warning(
                "Aerich migration files use legacy format; preparing relaxed "
                "upgrade path for existing production history."
            )
            await _prepare_legacy_aerich_upgrade(command)
            use_legacy_tolerant_upgrade = True
        logger.info("Aerich upgrade (transaction=true)...")
        if use_legacy_tolerant_upgrade:
            await _legacy_tolerant_upgrade(command)
        else:
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
