"""Guard: every Users column added via a recent migration must also live in
``_apply_generate_schema_compat_patches`` so that production can boot under
``SCHEMA_INIT_GENERATE_ONLY=true``.

Why this exists
---------------
On 2026-05-08 we shipped backend 1.23.0 with a new ``users.partner_link_mode``
column. The migration was correct, but production runs with
``SCHEMA_INIT_GENERATE_ONLY=true`` (see ``bloobcat/__main__.py``) which skips
runtime aerich migrations. The bootstrap helper
``_apply_generate_schema_compat_patches`` is the explicit list of ``ALTER TABLE
… ADD COLUMN IF NOT EXISTS`` statements that must already cover any column
read in ``lifespan``/``scheduler.schedule_all_tasks`` (e.g. ``Users.filter``
inside ``cleanup_blocked_users`` selects every model field). The new column
was missing → ``UndefinedColumnError`` → ~21 min API outage.

Cutoff
------
Migrations with a numeric prefix ≥ ``MIGRATION_NUMBER_CUTOFF`` are treated as
post-``SCHEMA_INIT_GENERATE_ONLY`` and therefore mandatory in compat-patches.
Older migrations are assumed already-applied on production and don't require
a compat entry. Bumping the cutoff is a deliberate choice; the default below
matches the known prod state.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "bloobcat" / "__main__.py"
MIGRATIONS_DIR = ROOT / "migrations" / "models"

# Earliest migration number that requires a compat-patch entry. Anything below
# this is assumed to already exist in production schema.
MIGRATION_NUMBER_CUTOFF = 100

_USERS_ADD_COLUMN_RE = re.compile(
    r'ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?"users"\s+'
    r'ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?"([a-zA-Z_][a-zA-Z0-9_]*)"',
    re.IGNORECASE | re.DOTALL,
)


def _migration_number(migration_path: Path) -> int:
    prefix = migration_path.name.split("_", 1)[0]
    return int(prefix) if prefix.isdigit() else 0


def _users_columns_in_migration(migration_path: Path) -> set[str]:
    text = migration_path.read_text(encoding="utf-8")
    return {match.group(1).lower() for match in _USERS_ADD_COLUMN_RE.finditer(text)}


def _migration_users_columns(cutoff: int = MIGRATION_NUMBER_CUTOFF) -> dict[str, set[str]]:
    """Return ``{migration_filename: {users-columns it adds}}`` for migrations >= cutoff."""
    out: dict[str, set[str]] = {}
    for migration_path in sorted(MIGRATIONS_DIR.glob("*.py")):
        if _migration_number(migration_path) < cutoff:
            continue
        columns = _users_columns_in_migration(migration_path)
        if columns:
            out[migration_path.name] = columns
    return out


def _compat_patches_users_columns() -> set[str]:
    """Extract every ``ALTER TABLE … "users" ADD COLUMN`` mention from
    ``_apply_generate_schema_compat_patches``.
    """
    text = MAIN_PY.read_text(encoding="utf-8")
    fn_signature = "async def _apply_generate_schema_compat_patches"
    start = text.find(fn_signature)
    if start < 0:
        raise AssertionError(
            f"{MAIN_PY} no longer contains _apply_generate_schema_compat_patches; "
            "rename or move requires updating this guard."
        )
    # Body ends at the next top-level def/async def
    next_def = re.search(r"\n(?:async\s+)?def\s+\w", text[start + len(fn_signature):])
    end = (start + len(fn_signature) + next_def.start()) if next_def else len(text)
    body = text[start:end]
    return {match.group(1).lower() for match in _USERS_ADD_COLUMN_RE.finditer(body)}


def test_recent_users_columns_are_present_in_compat_patches() -> None:
    """Every Users column added by a migration ≥ cutoff must be in compat-patches.

    On failure: add an ``ALTER TABLE IF EXISTS "users" ADD COLUMN IF NOT EXISTS
    "<col>" <type>...`` line to ``_apply_generate_schema_compat_patches`` in
    ``bloobcat/__main__.py``. The statement is idempotent — safe to add even if
    the column already exists on some environment.
    """
    compat = _compat_patches_users_columns()
    by_migration = _migration_users_columns()
    missing: dict[str, set[str]] = {}
    for migration_name, columns in by_migration.items():
        gap = columns - compat
        if gap:
            missing[migration_name] = gap

    if missing:
        lines = [
            "Users columns from recent migrations are missing from compat-patches",
            "(this is what caused the 2026-05-08 outage):",
            "",
        ]
        for migration_name, gap in sorted(missing.items()):
            for column in sorted(gap):
                lines.append(f"  • {column!r} (added in {migration_name})")
        lines.extend(
            [
                "",
                "Fix: in bloobcat/__main__.py → _apply_generate_schema_compat_patches,",
                "add an idempotent ALTER TABLE entry, e.g.:",
                "",
                '    ALTER TABLE IF EXISTS "users"',
                '        ADD COLUMN IF NOT EXISTS "<col>" <TYPE> [NOT NULL DEFAULT <value>];',
                "",
                "If a column intentionally does not need a compat entry (e.g. a column",
                "that is never queried during lifespan/scheduler startup and won't be",
                "deployed with SCHEMA_INIT_GENERATE_ONLY=true), bump",
                f"MIGRATION_NUMBER_CUTOFF in {Path(__file__).name}",
                "with a comment explaining why.",
            ]
        )
        raise AssertionError("\n".join(lines))


def test_compat_patches_entries_match_known_columns() -> None:
    """Sanity-check: the columns we already maintain in compat-patches actually
    exist in the Users model. Catches drift when a column is removed from the
    model but its compat-patch entry is left behind.
    """
    from bloobcat.db.users import Users  # imported lazily to avoid Tortoise side-effects at collection

    compat = _compat_patches_users_columns()
    model_columns: set[str] = set()
    for field_name, field in Users._meta.fields_map.items():
        relation_kinds = (
            "BackwardFKRelation",
            "BackwardOneToOneRelation",
            "BackwardOneToManyRelation",
            "ManyToManyRelation",
            "ForeignKeyRelation",
        )
        if type(field).__name__ in relation_kinds:
            continue
        source = getattr(field, "source_field", None)
        column = source or field_name
        if hasattr(field, "reference") and getattr(field, "reference", None) and not source:
            column = f"{field_name}_id"
        model_columns.add(column.lower())

    stale = compat - model_columns
    assert not stale, (
        f"compat-patches mention columns that no longer exist in the Users model: "
        f"{sorted(stale)}. Either restore the field on the model or remove the "
        f"corresponding ALTER TABLE entry from _apply_generate_schema_compat_patches."
    )
