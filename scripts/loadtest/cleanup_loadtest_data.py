from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import zlib
from pathlib import Path

from tortoise import Tortoise
from tortoise.expressions import Q

# Ensure project root is importable when running as a script.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM
from bloobcat.db.users import Users

POOL_STRIDE = 1_000_000
LOADTEST_BASE_ID_START = 8_000_000_000
LOADTEST_BASE_ID_MODULUS = 900_000
HARNESS_TAG = "loadtest_harness_v1"
RUN_ID_MAX_LEN = 40
RUN_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")
RUN_ID_EXTRACT_RE = re.compile(r"run_id=([a-zA-Z0-9_-]{1,40})")


def _normalize_run_id(raw: str) -> str:
    normalized = RUN_ID_SANITIZE_RE.sub("-", str(raw or "").strip())[:RUN_ID_MAX_LEN]
    normalized = normalized.strip("-")
    if not normalized:
        raise ValueError("run_id is empty after normalization")
    return normalized


def _crc32(value: str) -> int:
    return zlib.crc32(value.encode("utf-8")) & 0xFFFFFFFF


def _base_id_for_run(run_id: str) -> int:
    return LOADTEST_BASE_ID_START + (_crc32(run_id) % LOADTEST_BASE_ID_MODULUS) * POOL_STRIDE


def _range_for_run(run_id: str) -> tuple[int, int]:
    base_id = _base_id_for_run(run_id)
    return base_id, base_id + 4 * POOL_STRIDE


def _mutation_allowed() -> bool:
    return os.getenv("ALLOW_LOADTEST_MUTATIONS", "").strip().lower() == "true"


def _extract_run_id(*fields: str | None) -> str | None:
    for field in fields:
        if not field:
            continue
        match = RUN_ID_EXTRACT_RE.search(field)
        if match:
            return match.group(1)
    return None


def _is_harness_owned(*fields: str | None) -> bool:
    lowered = " ".join(str(field or "").lower() for field in fields)
    return HARNESS_TAG in lowered or ("loadtest" in lowered and "run_id=" in lowered)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cleanup loadtest harness users (dry-run by default)",
    )
    parser.add_argument("--run-id", default=None, help="Optional run identifier (normalized)")
    parser.add_argument("--apply", action="store_true", help="Apply deletion (requires mutation gate)")
    return parser


async def _collect_run_scoped_candidates(run_id: str) -> list[Users]:
    range_min, range_max = _range_for_run(run_id)
    users = await Users.filter(id__gte=range_min, id__lt=range_max).all()
    candidates: list[Users] = []
    for user in users:
        if not _is_harness_owned(user.username, user.utm, user.full_name):
            continue
        owner_run = _extract_run_id(user.username, user.utm, user.full_name)
        if owner_run == run_id:
            candidates.append(user)
    return candidates


async def _collect_all_runs_candidates() -> tuple[list[Users], list[str]]:
    users = await Users.filter(
        Q(username__icontains="run_id=")
        | Q(utm__icontains="run_id=")
        | Q(full_name__icontains="run_id=")
        | Q(username__icontains=HARNESS_TAG)
        | Q(utm__icontains=HARNESS_TAG)
        | Q(full_name__icontains=HARNESS_TAG)
    ).all()

    candidates: list[Users] = []
    skipped: list[str] = []
    for user in users:
        if not _is_harness_owned(user.username, user.utm, user.full_name):
            continue

        owner_run = _extract_run_id(user.username, user.utm, user.full_name)
        if not owner_run:
            skipped.append(f"id={user.id}:missing_run_id_marker")
            continue

        range_min, range_max = _range_for_run(owner_run)
        if user.id < range_min or user.id >= range_max:
            skipped.append(f"id={user.id}:out_of_run_range(run_id={owner_run})")
            continue

        candidates.append(user)

    return candidates, skipped


async def _cleanup(args: argparse.Namespace) -> None:
    normalized_run_id = _normalize_run_id(args.run_id) if args.run_id else None

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        if normalized_run_id:
            candidates = await _collect_run_scoped_candidates(normalized_run_id)
            skipped: list[str] = []
        else:
            candidates, skipped = await _collect_all_runs_candidates()

        candidate_ids = sorted(int(user.id) for user in candidates)
        dry_run = not args.apply
        deleted = 0

        if args.apply:
            if not _mutation_allowed():
                raise RuntimeError("Refusing mutations: set ALLOW_LOADTEST_MUTATIONS=true")
            if candidate_ids:
                deleted = int(await Users.filter(id__in=candidate_ids).delete())

        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": "dry-run" if dry_run else "apply",
                    "run_id": normalized_run_id,
                    "candidate_count": len(candidate_ids),
                    "deleted_count": deleted,
                    "candidate_id_sample": candidate_ids[:20],
                    "skipped_count": len(skipped),
                    "skipped_sample": skipped[:20],
                },
                ensure_ascii=False,
            )
        )
    finally:
        await Tortoise.close_connections()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_cleanup(args))


if __name__ == "__main__":
    main()
