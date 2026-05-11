#!/usr/bin/env python3
"""Fail CI if a new migration shares a numeric prefix with an existing one.

Historic duplicates that are already applied on production are
grandfathered — re-numbering them would require coordinated aerich state
edits and offers no runtime benefit. New migrations must pick a unique,
strictly-increasing prefix.

Run from the Vectra_Backend repo root::

    python scripts/check_migration_prefixes.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "migrations" / "models"

# Prefixes that already have duplicates on production. Do not relax these
# without first reconciling the aerich.aerich table on the prod database.
GRANDFATHERED: dict[str, int] = {
    "31": 2,
    "58": 2,
    "72": 2,
    "100": 5,
    "106": 2,
}


def main() -> int:
    if not ROOT.is_dir():
        print(f"ERROR: migrations directory missing: {ROOT}", file=sys.stderr)
        return 2

    prefixes: Counter[str] = Counter()
    for path in sorted(ROOT.glob("*.py")):
        prefix = path.name.split("_", 1)[0]
        if prefix.isdigit():
            prefixes[prefix] += 1

    bad: dict[str, int] = {}
    for prefix, count in prefixes.items():
        allowed = GRANDFATHERED.get(prefix, 1)
        if count > allowed:
            bad[prefix] = count

    if bad:
        print(
            "ERROR: migration prefix collisions beyond grandfathered "
            f"limits: {bad}. Grandfathered: {GRANDFATHERED}.",
            file=sys.stderr,
        )
        print(
            "Pick a unique, strictly-increasing prefix (next free numbers in "
            "the >=108 range).",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {sum(prefixes.values())} migrations, prefixes unique modulo "
        f"grandfathered: {sorted(GRANDFATHERED)}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
