import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from tortoise import Tortoise

# Ensure project root is importable when script is run directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.db.users import Users  # noqa: E402
from bloobcat.logger import get_logger  # noqa: E402
from bloobcat.routes.remnawave.client import RemnaWaveClient  # noqa: E402
from bloobcat.services.admin_integration import is_remnawave_not_found_error  # noqa: E402
from bloobcat.settings import remnawave_settings  # noqa: E402

logger = get_logger("scripts.cleanup_remnawave_orphans")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cleanup helper for RemnaWave/local users.\n"
            "Default mode is dry-run; use --apply to execute changes."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (delete orphan RemnaWave users and normalize local hwid_limit).",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="RemnaWave users page size (default: 100).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Max pages to fetch from RemnaWave (default: 200).",
    )
    return parser.parse_args()


def _extract_users_page(payload: Any) -> tuple[list[dict], int | None]:
    if not isinstance(payload, dict):
        return [], None
    response = payload.get("response")
    if not isinstance(response, dict):
        return [], None
    users = response.get("users")
    total = response.get("total")
    if not isinstance(users, list):
        return [], total if isinstance(total, int) else None
    normalized_users = [item for item in users if isinstance(item, dict)]
    return normalized_users, (total if isinstance(total, int) else None)


async def run() -> None:
    args = parse_args()
    apply_changes = bool(args.apply)
    page_size = max(1, int(args.page_size or 100))
    max_pages = max(1, int(args.max_pages or 200))

    await Tortoise.init(config=TORTOISE_ORM)
    client = RemnaWaveClient(remnawave_settings.url, remnawave_settings.token.get_secret_value())
    try:
        local_user_ids = {str(user_id) for user_id in await Users.all().values_list("id", flat=True)}
        logger.info("Loaded local users: %s", len(local_user_ids))

        orphan_candidates: list[dict] = []
        total_fetched = 0
        for page_index in range(max_pages):
            start = page_index * page_size
            response = await client.users.get_users(size=page_size, start=start)
            users_page, total_remote = _extract_users_page(response)
            if not users_page:
                break

            total_fetched += len(users_page)
            for remote_user in users_page:
                telegram_id = remote_user.get("telegramId")
                remote_uuid = str(remote_user.get("uuid") or "").strip()
                if not remote_uuid:
                    continue
                telegram_id_str = str(telegram_id).strip() if telegram_id is not None else ""
                if not telegram_id_str or telegram_id_str not in local_user_ids:
                    orphan_candidates.append(
                        {
                            "uuid": remote_uuid,
                            "telegram_id": telegram_id_str or None,
                            "username": remote_user.get("username"),
                        }
                    )

            if len(users_page) < page_size:
                break
            if total_remote is not None and total_fetched >= total_remote:
                break

        logger.info(
            "RemnaWave scan complete: fetched=%s orphan_candidates=%s",
            total_fetched,
            len(orphan_candidates),
        )

        if orphan_candidates:
            preview = orphan_candidates[:20]
            logger.info("Orphan preview (up to 20): %s", preview)

        deleted_orphans = 0
        orphan_delete_errors = 0
        if apply_changes:
            for orphan in orphan_candidates:
                orphan_uuid = orphan["uuid"]
                try:
                    await client.users.delete_user(orphan_uuid)
                    deleted_orphans += 1
                except Exception as exc:
                    if is_remnawave_not_found_error(str(exc)):
                        deleted_orphans += 1
                        continue
                    orphan_delete_errors += 1
                    logger.warning("Failed to delete orphan uuid=%s: %s", orphan_uuid, exc)
            logger.info(
                "Orphan delete result: deleted=%s errors=%s",
                deleted_orphans,
                orphan_delete_errors,
            )
        else:
            logger.info("Dry-run mode: orphan users were not deleted")

        invalid_local = await Users.filter(hwid_limit__not_isnull=True, hwid_limit__lte=0).values(
            "id",
            "hwid_limit",
        )
        logger.info("Local users with invalid hwid_limit<=0: %s", len(invalid_local))
        if invalid_local:
            logger.info("Invalid local preview (up to 20): %s", invalid_local[:20])

        normalized_local = 0
        if apply_changes and invalid_local:
            normalized_local = await Users.filter(hwid_limit__not_isnull=True, hwid_limit__lte=0).update(hwid_limit=1)
            logger.info("Normalized local users hwid_limit to 1: %s", normalized_local)
        elif not apply_changes:
            logger.info("Dry-run mode: local users were not updated")

        logger.info(
            "Cleanup finished: apply=%s orphan_candidates=%s orphan_deleted=%s local_invalid=%s local_normalized=%s",
            apply_changes,
            len(orphan_candidates),
            deleted_orphans,
            len(invalid_local),
            normalized_local,
        )
    finally:
        await client.close()
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(run())
