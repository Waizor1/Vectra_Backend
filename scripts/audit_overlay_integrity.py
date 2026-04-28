import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tortoise import Tortoise

# Ensure project root is importable when script is run directly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.db.active_tariff import ActiveTariffs  # noqa: E402
from bloobcat.db.subscription_freezes import SubscriptionFreezes  # noqa: E402
from bloobcat.db.users import Users, normalize_date  # noqa: E402
from bloobcat.logger import get_logger  # noqa: E402
from bloobcat.settings import app_settings  # noqa: E402

logger = get_logger("scripts.audit_overlay_integrity")


@dataclass
class AuditRow:
    risk_type: str
    user_id: int
    username: str
    freeze_id: int
    user_expired_at: str | None
    family_expires_at: str | None
    user_hwid_limit: int | None
    active_hwid_limit: int | None
    active_tariff_id: str | None
    base_hwid_limit_snapshot: int | None
    note: str


def _family_devices_limit() -> int:
    return max(1, int(getattr(app_settings, "family_devices_limit", 10) or 10))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Overlay integrity audit (dry-run only).\n"
            "Detects stale family freeze expiry and potential family entitlement demotion."
        )
    )
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "output" / "overlay_integrity_audit.csv"),
        help="Path to CSV report file.",
    )
    parser.add_argument(
        "--preview-limit",
        type=int,
        default=20,
        help="How many rows to print in logs as preview.",
    )
    return parser.parse_args()


def _to_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


async def _run_audit() -> list[AuditRow]:
    today = date.today()
    family_limit = _family_devices_limit()
    rows: list[AuditRow] = []

    freezes = await SubscriptionFreezes.filter(
        is_active=True,
        resume_applied=False,
        freeze_reason="family_overlay",
    ).order_by("id")

    if not freezes:
        return rows

    user_ids = {int(freeze.user_id) for freeze in freezes}
    users = await Users.filter(id__in=list(user_ids)).all()
    users_by_id = {int(user.id): user for user in users}

    active_tariff_ids = {
        str(user.active_tariff_id)
        for user in users
        if getattr(user, "active_tariff_id", None)
    }
    active_tariffs = await ActiveTariffs.filter(id__in=list(active_tariff_ids)).all() if active_tariff_ids else []
    active_by_id = {str(row.id): row for row in active_tariffs}

    for freeze in freezes:
        user_id = int(freeze.user_id)
        user = users_by_id.get(user_id)
        if not user:
            rows.append(
                AuditRow(
                    risk_type="orphan_freeze_user_missing",
                    user_id=user_id,
                    username="",
                    freeze_id=int(freeze.id),
                    user_expired_at=None,
                    family_expires_at=str(normalize_date(freeze.family_expires_at)) if freeze.family_expires_at else None,
                    user_hwid_limit=None,
                    active_hwid_limit=None,
                    active_tariff_id=None,
                    base_hwid_limit_snapshot=_to_int_or_none(getattr(freeze, "base_hwid_limit", None)),
                    note="Freeze active but user record is missing.",
                )
            )
            continue

        username = str(getattr(user, "username", "") or "")
        user_expired_at = normalize_date(getattr(user, "expired_at", None))
        family_expires_at = normalize_date(getattr(freeze, "family_expires_at", None))
        user_hwid_limit = _to_int_or_none(getattr(user, "hwid_limit", None))

        active_tariff = None
        active_tariff_id = str(getattr(user, "active_tariff_id", "") or "").strip()
        if active_tariff_id:
            active_tariff = active_by_id.get(active_tariff_id)
        active_hwid_limit = _to_int_or_none(getattr(active_tariff, "hwid_limit", None)) if active_tariff else None

        # Risk A: stale family expiry in freeze (scheduler could rollback without guard).
        if user_expired_at and (family_expires_at is None or user_expired_at > family_expires_at):
            rows.append(
                AuditRow(
                    risk_type="stale_family_expiry_drift",
                    user_id=user_id,
                    username=username,
                    freeze_id=int(freeze.id),
                    user_expired_at=str(user_expired_at),
                    family_expires_at=str(family_expires_at) if family_expires_at else None,
                    user_hwid_limit=user_hwid_limit,
                    active_hwid_limit=active_hwid_limit,
                    active_tariff_id=active_tariff_id or None,
                    base_hwid_limit_snapshot=_to_int_or_none(getattr(freeze, "base_hwid_limit", None)),
                    note="user.expired_at is ahead of freeze.family_expires_at",
                )
            )

        # Risk B: active overlay owner has lowered effective device limit (possible demotion).
        overlay_active = bool(family_expires_at and family_expires_at >= today)
        effective_hwid_limit = active_hwid_limit if active_hwid_limit is not None else user_hwid_limit
        if overlay_active and effective_hwid_limit is not None and effective_hwid_limit < family_limit:
            rows.append(
                AuditRow(
                    risk_type="overlay_entitlement_demotion_risk",
                    user_id=user_id,
                    username=username,
                    freeze_id=int(freeze.id),
                    user_expired_at=str(user_expired_at) if user_expired_at else None,
                    family_expires_at=str(family_expires_at),
                    user_hwid_limit=user_hwid_limit,
                    active_hwid_limit=active_hwid_limit,
                    active_tariff_id=active_tariff_id or None,
                    base_hwid_limit_snapshot=_to_int_or_none(getattr(freeze, "base_hwid_limit", None)),
                    note=f"effective_hwid_limit={effective_hwid_limit} < family_limit={family_limit}",
                )
            )

    return rows


def _write_csv(path: Path, rows: list[AuditRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "risk_type",
        "user_id",
        "username",
        "freeze_id",
        "user_expired_at",
        "family_expires_at",
        "user_hwid_limit",
        "active_hwid_limit",
        "active_tariff_id",
        "base_hwid_limit_snapshot",
        "note",
    ]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "risk_type": row.risk_type,
                    "user_id": row.user_id,
                    "username": row.username,
                    "freeze_id": row.freeze_id,
                    "user_expired_at": row.user_expired_at,
                    "family_expires_at": row.family_expires_at,
                    "user_hwid_limit": row.user_hwid_limit,
                    "active_hwid_limit": row.active_hwid_limit,
                    "active_tariff_id": row.active_tariff_id,
                    "base_hwid_limit_snapshot": row.base_hwid_limit_snapshot,
                    "note": row.note,
                }
            )


async def main() -> None:
    args = _parse_args()
    output_csv = Path(args.output_csv).resolve()
    preview_limit = max(1, int(args.preview_limit or 20))

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        rows = await _run_audit()
        _write_csv(output_csv, rows)

        stale_count = sum(1 for row in rows if row.risk_type == "stale_family_expiry_drift")
        demotion_count = sum(1 for row in rows if row.risk_type == "overlay_entitlement_demotion_risk")
        orphan_count = sum(1 for row in rows if row.risk_type == "orphan_freeze_user_missing")

        logger.info(
            "Overlay integrity audit finished: total=%s stale=%s demotion=%s orphan=%s csv=%s",
            len(rows),
            stale_count,
            demotion_count,
            orphan_count,
            output_csv,
        )

        for row in rows[:preview_limit]:
            logger.info(
                "AUDIT row risk=%s user=%s freeze=%s note=%s",
                row.risk_type,
                row.user_id,
                row.freeze_id,
                row.note,
            )
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())

