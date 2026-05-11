#!/usr/bin/env python3
"""Fail CI if a new migration creates an index without CONCURRENTLY.

`CREATE INDEX` (without CONCURRENTLY) takes a ShareLock on the table and
blocks writes for the duration of the build. On hot tables like
`users`, `active_tariffs`, `payments`, `error_reports`, that means
several minutes of write blocking during a release rollout.

Already-applied migrations that did this are grandfathered — re-running
them under CONCURRENTLY makes no sense once the index exists. The cap
is the largest prefix that was on production at the time this linter
was introduced.

Run from the Vectra_Backend repo root::

    python scripts/check_migration_safety.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "migrations" / "models"

# Anything strictly greater than this prefix must use CREATE INDEX CONCURRENTLY.
# Bump GRANDFATHERED_MAX_PREFIX in lockstep with the latest prefix that
# legitimately landed without CONCURRENTLY (see review history).
GRANDFATHERED_MAX_PREFIX = 107

CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?!CONCURRENTLY)", re.IGNORECASE
)


def _prefix(path: Path) -> int:
    head = path.name.split("_", 1)[0]
    return int(head) if head.isdigit() else -1


def main() -> int:
    if not ROOT.is_dir():
        print(f"ERROR: migrations directory missing: {ROOT}", file=sys.stderr)
        return 2

    offenders: list[tuple[str, list[int]]] = []
    for path in sorted(ROOT.glob("*.py")):
        prefix = _prefix(path)
        if prefix <= GRANDFATHERED_MAX_PREFIX:
            continue
        text = path.read_text(encoding="utf-8")
        bad_lines: list[int] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if CREATE_INDEX_RE.search(line):
                bad_lines.append(lineno)
        if bad_lines:
            offenders.append((path.name, bad_lines))

    if offenders:
        print(
            "ERROR: migrations create indexes without CONCURRENTLY "
            f"(prefix > {GRANDFATHERED_MAX_PREFIX}):",
            file=sys.stderr,
        )
        for name, lines in offenders:
            print(f"  - {name}: lines {lines}", file=sys.stderr)
        print(
            "Hot tables (users, active_tariffs, payments, error_reports) "
            "must use:\n"
            "    SET lock_timeout = '5s';\n"
            "    CREATE INDEX CONCURRENTLY IF NOT EXISTS ...\n"
            "executed outside the migration transaction.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: no CREATE INDEX without CONCURRENTLY in migrations "
        f"newer than prefix {GRANDFATHERED_MAX_PREFIX}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
