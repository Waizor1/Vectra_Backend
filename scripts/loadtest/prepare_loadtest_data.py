from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tortoise import Tortoise

# Ensure project root is importable when running as a script.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM
from bloobcat.db.users import Users
from bloobcat.funcs.auth_tokens import create_access_token

POOL_STRIDE = 1_000_000
LOADTEST_BASE_ID_START = 8_000_000_000
LOADTEST_BASE_ID_MODULUS = 900_000
HARNESS_TAG = "loadtest_harness_v1"
RUN_ID_MAX_LEN = 40
RUN_ID_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")
RUN_ID_EXTRACT_RE = re.compile(r"run_id=([a-zA-Z0-9_-]{1,40})")


@dataclass(frozen=True)
class PoolSpec:
    kind: str
    offset: int
    count: int


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


def _ids_for_pool(base_id: int, offset: int, count: int) -> list[int]:
    start = base_id + offset * POOL_STRIDE
    return [start + idx for idx in range(count)]


def _build_user_markers(run_id: str, kind: str, index: int) -> tuple[str, str, str]:
    username = f"lt-{run_id}-{kind}-{index}"
    utm = f"{HARNESS_TAG}:run_id={run_id}:kind={kind}"
    full_name = f"Loadtest Harness run_id={run_id} kind={kind} idx={index}"
    return username[:100], utm[:100], full_name[:1000]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare deterministic harness-tagged loadtest users and artifact",
    )
    parser.add_argument("--run-id", default="wave-001", help="Logical run identifier (normalized)")
    parser.add_argument("--regular-count", type=int, default=500, help="Regular user token pool size")
    parser.add_argument("--partner-count", type=int, default=120, help="Partner user token pool size")
    parser.add_argument("--delete-api-count", type=int, default=120, help="Delete API canary id pool size")
    parser.add_argument(
        "--delete-directus-count",
        type=int,
        default=120,
        help="Delete Directus canary id pool size",
    )
    parser.add_argument(
        "--artifact-path",
        default=str(Path(__file__).resolve().parent / "artifacts" / "loadtest_data.json"),
        help="Artifact JSON output path",
    )
    return parser


def _validate_count(name: str, value: int) -> None:
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    if value >= POOL_STRIDE:
        raise ValueError(f"{name} must be < {POOL_STRIDE}")


async def _prepare(args: argparse.Namespace) -> None:
    if not _mutation_allowed():
        raise RuntimeError("Refusing mutations: set ALLOW_LOADTEST_MUTATIONS=true")

    run_id = _normalize_run_id(args.run_id)
    base_id = _base_id_for_run(run_id)
    range_min = base_id
    range_max = base_id + 4 * POOL_STRIDE

    _validate_count("regular-count", args.regular_count)
    _validate_count("partner-count", args.partner_count)
    _validate_count("delete-api-count", args.delete_api_count)
    _validate_count("delete-directus-count", args.delete_directus_count)

    pool_specs = [
        PoolSpec(kind="regular", offset=0, count=args.regular_count),
        PoolSpec(kind="partner", offset=1, count=args.partner_count),
        PoolSpec(kind="delete_api", offset=2, count=args.delete_api_count),
        PoolSpec(kind="delete_directus", offset=3, count=args.delete_directus_count),
    ]

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        in_range = await Users.filter(id__gte=range_min, id__lt=range_max).all()
        collisions: list[str] = []
        for user in in_range:
            if not _is_harness_owned(user.username, user.utm, user.full_name):
                continue
            owner_run = _extract_run_id(user.username, user.utm, user.full_name)
            if owner_run and owner_run != run_id:
                collisions.append(f"id={user.id},run_id={owner_run}")

        if collisions:
            preview = "; ".join(collisions[:10])
            raise RuntimeError(
                "Collision guard triggered: range already contains harness data from a different run_id. "
                f"target_run_id={run_id}, sample={preview}"
            )

        ids_by_kind = {
            spec.kind: _ids_for_pool(base_id=base_id, offset=spec.offset, count=spec.count)
            for spec in pool_specs
        }
        all_ids: list[int] = [user_id for ids in ids_by_kind.values() for user_id in ids]

        existing_users = await Users.filter(id__in=all_ids).all() if all_ids else []
        existing_by_id = {int(user.id): user for user in existing_users}

        to_create: list[Users] = []
        to_update: list[Users] = []

        for spec in pool_specs:
            ids = ids_by_kind[spec.kind]
            for idx, user_id in enumerate(ids):
                username, utm, full_name = _build_user_markers(run_id=run_id, kind=spec.kind, index=idx)
                is_partner = spec.kind == "partner"
                existing = existing_by_id.get(user_id)
                if existing is None:
                    to_create.append(
                        Users(
                            id=user_id,
                            username=username,
                            full_name=full_name,
                            utm=utm,
                            is_partner=is_partner,
                            is_registered=True,
                        )
                    )
                else:
                    existing.username = username
                    existing.utm = utm
                    existing.full_name = full_name
                    existing.is_partner = is_partner
                    existing.is_registered = True
                    to_update.append(existing)

        if to_create:
            await Users.bulk_create(to_create, batch_size=1000)
        if to_update:
            await Users.bulk_update(
                to_update,
                fields=["username", "utm", "full_name", "is_partner", "is_registered"],
                batch_size=1000,
            )

        regular_tokens = [create_access_token(user_id)[0] for user_id in ids_by_kind["regular"]]
        partner_tokens = [create_access_token(user_id)[0] for user_id in ids_by_kind["partner"]]
        delete_api_user_ids = ids_by_kind["delete_api"]
        delete_directus_user_ids = ids_by_kind["delete_directus"]

        artifact_path = Path(args.artifact_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_payload = {
            "run_id": run_id,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "base_id": base_id,
            "regular_tokens": regular_tokens,
            "partner_tokens": partner_tokens,
            "delete_api_user_ids": delete_api_user_ids,
            "delete_directus_user_ids": delete_directus_user_ids,
        }
        artifact_path.write_text(json.dumps(artifact_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "run_id": run_id,
                    "base_id": base_id,
                    "created": len(to_create),
                    "updated": len(to_update),
                    "artifact_path": str(artifact_path),
                    "regular_tokens": len(regular_tokens),
                    "partner_tokens": len(partner_tokens),
                    "delete_api_user_ids": len(delete_api_user_ids),
                    "delete_directus_user_ids": len(delete_directus_user_ids),
                },
                ensure_ascii=False,
            )
        )
    finally:
        await Tortoise.close_connections()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    asyncio.run(_prepare(args))


if __name__ == "__main__":
    main()
