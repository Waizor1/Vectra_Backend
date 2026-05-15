"""
Post-migration runtime-state verification for critical DB invariants.

Fail-closed by design: any invariant violation raises RuntimeError.
"""

import asyncio
import re
import sys
from pathlib import Path

import asyncpg

# Ensure the project root is importable when running as a script.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.logger import get_logger

logger = get_logger("scripts.verify_runtime_state")


FK_RULE_QUERY = """
SELECT
    tc.constraint_schema,
    tc.constraint_name,
    rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
 AND tc.constraint_schema = kcu.constraint_schema
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name
 AND ccu.constraint_schema = tc.constraint_schema
JOIN information_schema.referential_constraints rc
  ON rc.constraint_name = tc.constraint_name
 AND rc.constraint_schema = tc.constraint_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND kcu.table_name = $1
  AND kcu.column_name = $2
  AND ccu.table_name = $3
  AND ccu.column_name = $4
  AND kcu.table_schema = $5
  AND ccu.table_schema = $5
  AND tc.constraint_schema = $5
ORDER BY tc.constraint_schema, tc.constraint_name
"""

RUNTIME_SCHEMA_QUERY = """
SELECT n.nspname AS schema_name
FROM pg_namespace n
JOIN pg_class users_tbl
  ON users_tbl.relnamespace = n.oid
 AND users_tbl.relname = 'users'
 AND users_tbl.relkind IN ('r', 'p')
JOIN pg_class active_tariffs_tbl
  ON active_tariffs_tbl.relnamespace = n.oid
 AND active_tariffs_tbl.relname = 'active_tariffs'
 AND active_tariffs_tbl.relkind IN ('r', 'p')
JOIN pg_class notification_marks_tbl
  ON notification_marks_tbl.relnamespace = n.oid
 AND notification_marks_tbl.relname = 'notification_marks'
 AND notification_marks_tbl.relkind IN ('r', 'p')
JOIN pg_class promo_usages_tbl
  ON promo_usages_tbl.relnamespace = n.oid
 AND promo_usages_tbl.relname = 'promo_usages'
 AND promo_usages_tbl.relkind IN ('r', 'p')
JOIN pg_class retry_jobs_tbl
  ON retry_jobs_tbl.relnamespace = n.oid
 AND retry_jobs_tbl.relname = 'remnawave_retry_jobs'
 AND retry_jobs_tbl.relkind IN ('r', 'p')
WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY n.nspname
"""

INDEX_QUERY = """
SELECT schemaname, indexname, indexdef
FROM pg_indexes
WHERE indexname = $1
ORDER BY schemaname
"""

RETRY_ACTIVE_INDEX_NAME = "ux_remnawave_retry_jobs_active_user"
RETRY_ACTIVE_INDEX_EXPECTATION = (
    "UNIQUE index on remnawave_retry_jobs (job_type, user_id) "
    "with predicate status IN ('pending', 'processing')"
)
RETRY_ACTIVE_STATUSES = {"pending", "processing"}
RETRY_ACTIVE_KEY_COLUMNS = ["job_type", "user_id"]
STATUS_IDENTIFIER_PATTERN = r"(?:\(\s*)*status(?:\s*\))*"


# --- Recent-migration table existence guard ---------------------------------
#
# 2026-05-15 incident: PR #88 deployed and `apply_migrations.py` logged
# "Applied migrations: 119_..." plus "Post-migration runtime-state verification
# passed", but `golden_period_configs` (and the rest of the new tables) were
# NOT actually present in production. The trigger path is the legacy-tolerant
# upgrade in `_legacy_tolerant_upgrade` — it silently no-ops the SQL under
# certain conditions while still writing the "applied" log line.
#
# The narrow runtime-schema query above only checks five long-standing tables;
# anything created by recent migrations is invisible to the verification gate.
# This guard fixes that by parsing every migration file that landed in the
# last `MIGRATION_LOOKBACK_COUNT` revisions, extracting the table names from
# their `CREATE TABLE [IF NOT EXISTS] "name"` statements, and confirming each
# one actually exists in the runtime schema. Fails closed on missing tables
# — no more silent no-op deploys.

MIGRATIONS_DIR = ROOT / "migrations" / "models"
MIGRATION_LOOKBACK_COUNT = 15
_CREATE_TABLE_RE = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"([a-z_][a-z0-9_]*)"',
    re.IGNORECASE,
)


def _list_recent_migration_files(
    migrations_dir: Path = MIGRATIONS_DIR,
    lookback: int = MIGRATION_LOOKBACK_COUNT,
) -> list[Path]:
    """Return the most recent migration files sorted by their numeric prefix.

    Migration files follow the pattern ``<NN>_<YYYYMMDDHHMMSS>_<slug>.py`` —
    we sort by the integer prefix so the lookback window is stable even when
    timestamps drift between machines or merge order.
    """
    if not migrations_dir.is_dir():
        return []
    candidates: list[tuple[int, Path]] = []
    for path in migrations_dir.glob("*.py"):
        if path.name == "__init__.py":
            continue
        prefix = path.name.split("_", 1)[0]
        try:
            order = int(prefix)
        except ValueError:
            continue
        candidates.append((order, path))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in candidates[:lookback]]


def _extract_table_names_from_migration(path: Path) -> list[str]:
    """Pull all CREATE TABLE table names out of a migration file.

    The regex matches both ``CREATE TABLE "x"`` and ``CREATE TABLE IF NOT
    EXISTS "x"`` styles which are the two forms used across the migration
    history. Names are deduplicated while preserving order so the verification
    log is stable.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _CREATE_TABLE_RE.finditer(source):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


async def _verify_recent_migration_tables(
    conn: asyncpg.Connection,
    *,
    runtime_schema: str | None,
    migrations_dir: Path | None = None,
    lookback: int | None = None,
) -> str | None:
    """Confirm every table from recent migrations actually exists.

    Returns an issue string when any expected table is missing, or ``None``
    when the schema matches the migration intent. Skipped (returns ``None``)
    when the runtime schema couldn't be resolved — the runtime-schema check
    is the prerequisite signal that the DB is reachable at all.

    `migrations_dir` and `lookback` fall back to the module-level constants
    when not supplied so tests can monkeypatch the module values without
    re-threading them through ``_collect_issues``.
    """
    if runtime_schema is None:
        return None

    effective_dir = migrations_dir if migrations_dir is not None else MIGRATIONS_DIR
    effective_lookback = lookback if lookback is not None else MIGRATION_LOOKBACK_COUNT

    recent_files = _list_recent_migration_files(effective_dir, effective_lookback)
    if not recent_files:
        # No migration files found locally — this is a packaging issue, not a
        # silent-no-op deploy. Surface as a separate issue instead of false
        # success.
        return (
            f"No migration files found under {effective_dir} "
            "(packaging or path resolution issue)."
        )

    expected: dict[str, str] = {}  # table_name -> origin migration file
    for path in recent_files:
        for table in _extract_table_names_from_migration(path):
            # First-seen wins — older create wins over later ALTER references.
            expected.setdefault(table, path.name)

    if not expected:
        return None

    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = $1
          AND table_name = ANY($2::text[])
        """,
        runtime_schema,
        list(expected.keys()),
    )
    present = {str(row["table_name"]) for row in rows}

    missing = [(name, origin) for name, origin in expected.items() if name not in present]
    if not missing:
        return None

    formatted = ", ".join(f"{name} (from {origin})" for name, origin in missing)
    return (
        f"Recent migrations claim to create tables that are absent from runtime "
        f"schema {runtime_schema!r}: {formatted}. "
        f"This usually means apply_migrations.py logged success but the SQL did "
        f"not actually execute — see scripts/apply_migrations.py "
        f"_legacy_tolerant_upgrade comment."
    )


def _format_fk_rows(rows: list[dict]) -> str:
    if not rows:
        return "none"
    return ", ".join(
        f"{row['constraint_schema']}.{row['constraint_name']}={row['delete_rule']}" for row in rows
    )


def _build_fk_issue(
    *,
    source_table: str,
    source_column: str,
    target_table: str,
    target_column: str,
    expected_rule: str,
    rows: list[dict],
) -> str | None:
    if not rows:
        return (
            f"Missing FK for {source_table}.{source_column} -> {target_table}.{target_column}; "
            f"expected ON DELETE {expected_rule}."
        )

    actual_rules = {str(row["delete_rule"]).upper() for row in rows}
    if actual_rules != {expected_rule}:
        return (
            f"Unexpected FK rule for {source_table}.{source_column} -> {target_table}.{target_column}; "
            f"expected ON DELETE {expected_rule}, found {_format_fk_rows(rows)}."
        )

    return None


def _build_runtime_schema_issue(schema_rows: list[dict]) -> str | None:
    candidates = sorted(
        {
            str(row.get("schema_name", "")).strip()
            for row in schema_rows
            if str(row.get("schema_name", "")).strip()
        }
    )

    if len(candidates) == 1:
        return None

    if not candidates:
        return (
            "Unable to resolve runtime schema for FK verification: expected exactly one schema "
            "containing users, active_tariffs, notification_marks, promo_usages, and remnawave_retry_jobs; "
            "found none."
        )

    return (
        "Ambiguous runtime schema for FK verification: expected exactly one schema "
        "containing users, active_tariffs, notification_marks, promo_usages, and remnawave_retry_jobs; "
        f"found {len(candidates)} candidates: {', '.join(candidates)}. "
        "Drop shadow/duplicate schemas or make runtime target schema unambiguous."
    )


def _normalize_indexdef(indexdef: str) -> str:
    normalized = indexdef.strip().lower().replace('"', "")
    return re.sub(r"\s+", " ", normalized)


def _extract_where_clause(indexdef: str) -> str | None:
    parts = indexdef.split(" where ", 1)
    if len(parts) != 2:
        return None
    return parts[1]


def _strip_type_casts(sql: str) -> str:
    return re.sub(
        r"::\s*(?:\"[a-z_][a-z0-9_]*\"|[a-z_][a-z0-9_]*)(?:\s+(?:\"[a-z_][a-z0-9_]*\"|[a-z_][a-z0-9_]*))*\s*(?:\[\s*\])?",
        "",
        sql,
    )


def _strip_outer_parentheses(sql: str) -> str:
    candidate = sql.strip()
    while candidate.startswith("(") and candidate.endswith(")"):
        depth = 0
        encloses_all = True
        for idx, char in enumerate(candidate):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return candidate
                if depth == 0 and idx < len(candidate) - 1:
                    encloses_all = False
                    break
        if not encloses_all or depth != 0:
            break
        candidate = candidate[1:-1].strip()
    return candidate


def _extract_index_key_columns(indexdef: str) -> list[str] | None:
    match = re.search(
        r"\bon\s+(?:[a-z_][a-z0-9_]*\.)?remnawave_retry_jobs\b"
        r"(?:\s+using\s+[a-z_][a-z0-9_]*)?\s*\((?P<columns>[^)]*)\)",
        indexdef,
    )
    if match is None:
        return None

    raw_columns = [part.strip() for part in match.group("columns").split(",") if part.strip()]
    if not raw_columns:
        return None

    normalized_columns: list[str] = []
    for raw_column in raw_columns:
        identifier_match = re.match(r"([a-z_][a-z0-9_]*)\b", raw_column)
        if identifier_match is None:
            return None
        normalized_columns.append(identifier_match.group(1))

    return normalized_columns


def _parse_predicate_statuses(values_sql: str) -> list[str] | None:
    items = [item.strip() for item in values_sql.split(",") if item.strip()]
    if not items:
        return None

    statuses: list[str] = []
    for item in items:
        literal_match = re.fullmatch(r"'([^']+)'", item)
        if literal_match is None:
            return None
        statuses.append(literal_match.group(1))

    return statuses


def _extract_array_values(array_sql: str) -> str | None:
    array_expr = _strip_outer_parentheses(array_sql)
    array_match = re.fullmatch(r"array\s*\[(?P<values>.+)\]", array_expr)
    if array_match is None:
        return None
    return array_match.group("values")


def _is_exact_active_status_predicate(predicate_sql: str) -> bool:
    predicate = _strip_outer_parentheses(_strip_type_casts(predicate_sql).strip())
    if not predicate:
        return False

    if re.search(r"\b(and|or)\b", predicate):
        return False

    in_match = re.fullmatch(rf"{STATUS_IDENTIFIER_PATTERN}\s+in\s*\((?P<values>.+)\)", predicate)
    if in_match is not None:
        statuses = _parse_predicate_statuses(in_match.group("values"))
        return statuses is not None and set(statuses) == RETRY_ACTIVE_STATUSES and len(statuses) == 2

    any_match = re.fullmatch(rf"{STATUS_IDENTIFIER_PATTERN}\s*=\s*any\s*\(\s*(?P<array>.+)\s*\)", predicate)
    if any_match is not None:
        values_sql = _extract_array_values(any_match.group("array"))
        if values_sql is None:
            return False
        statuses = _parse_predicate_statuses(values_sql)
        return statuses is not None and set(statuses) == RETRY_ACTIVE_STATUSES and len(statuses) == 2

    return False


def _validate_retry_active_index(indexdef: str) -> list[str]:
    issues: list[str] = []
    normalized = _normalize_indexdef(indexdef)

    if "create unique index" not in normalized:
        issues.append("index is not UNIQUE")

    if not re.search(r"\bon\s+(?:[a-z_][a-z0-9_]*\.)?remnawave_retry_jobs\b", normalized):
        issues.append("index target table is not remnawave_retry_jobs")

    key_columns = _extract_index_key_columns(normalized)
    if key_columns != RETRY_ACTIVE_KEY_COLUMNS:
        issues.append("index key columns are not (job_type, user_id)")

    where_clause = _extract_where_clause(normalized)
    if where_clause is None:
        issues.append("index is not partial (missing WHERE predicate)")
        return issues

    if not _is_exact_active_status_predicate(where_clause):
        issues.append("index predicate must enforce exactly pending + processing statuses")

    return issues


def _build_retry_index_issue(index_rows: list[dict]) -> str | None:
    if not index_rows:
        return (
            f"Missing required unique index {RETRY_ACTIVE_INDEX_NAME} "
            "(active remnawave retry jobs uniqueness invariant)."
        )

    if len(index_rows) != 1:
        found_definitions = "; ".join(
            f"{row.get('schemaname', '<unknown-schema>')}: {row.get('indexdef', '<missing-indexdef>')}"
            for row in index_rows
        )
        return (
            f"Ambiguous index definition for {RETRY_ACTIVE_INDEX_NAME}: expected exactly one matching "
            f"definition across schemas, found {len(index_rows)} rows. "
            f"Found definitions: {found_definitions}. "
            f"Drop duplicate/conflicting indexes and keep a single expected definition."
        )

    row = index_rows[0]
    validation_issues = _validate_retry_active_index(str(row.get("indexdef", "")))
    if not validation_issues:
        return None

    found_definition = (
        f"{row.get('schemaname', '<unknown-schema>')}: {row.get('indexdef', '<missing-indexdef>')}"
    )
    issues_summary = ", ".join(sorted(set(validation_issues)))
    return (
        f"Invalid index definition for {RETRY_ACTIVE_INDEX_NAME}. "
        f"Expected {RETRY_ACTIVE_INDEX_EXPECTATION}. "
        f"Detected issues: {issues_summary}. "
        f"Found definition: {found_definition}. "
        f"Recreate {RETRY_ACTIVE_INDEX_NAME} with the expected definition."
    )


async def _collect_issues(conn: asyncpg.Connection) -> list[str]:
    issues: list[str] = []

    schema_rows = [dict(row) for row in await conn.fetch(RUNTIME_SCHEMA_QUERY)]
    schema_issue = _build_runtime_schema_issue(schema_rows)
    runtime_schema: str | None = None
    if schema_issue:
        issues.append(schema_issue)
    else:
        runtime_schema = str(schema_rows[0]["schema_name"]).strip()

    fk_invariants = [
        ("active_tariffs", "user_id", "users", "id", "CASCADE"),
        ("notification_marks", "user_id", "users", "id", "CASCADE"),
        ("promo_usages", "user_id", "users", "id", "CASCADE"),
        ("users", "referred_by", "users", "id", "SET NULL"),
    ]

    if runtime_schema:
        for source_table, source_column, target_table, target_column, expected_rule in fk_invariants:
            rows = await conn.fetch(
                FK_RULE_QUERY,
                source_table,
                source_column,
                target_table,
                target_column,
                runtime_schema,
            )
            issue = _build_fk_issue(
                source_table=source_table,
                source_column=source_column,
                target_table=target_table,
                target_column=target_column,
                expected_rule=expected_rule,
                rows=[dict(row) for row in rows],
            )
            if issue:
                issues.append(issue)

    index_rows = await conn.fetch(INDEX_QUERY, RETRY_ACTIVE_INDEX_NAME)
    index_issue = _build_retry_index_issue([dict(row) for row in index_rows])
    if index_issue:
        issues.append(index_issue)

    migration_tables_issue = await _verify_recent_migration_tables(
        conn, runtime_schema=runtime_schema
    )
    if migration_tables_issue:
        issues.append(migration_tables_issue)

    return issues


async def verify_runtime_state(conn: asyncpg.Connection | None = None) -> None:
    """Verify critical runtime DB invariants and raise on drift."""
    own_connection = conn is None
    if own_connection:
        from bloobcat.settings import script_settings

        conn = await asyncpg.connect(script_settings.db.get_secret_value())

    assert conn is not None
    try:
        issues = await _collect_issues(conn)
    finally:
        if own_connection:
            await conn.close()

    if issues:
        for issue in issues:
            logger.error("Runtime-state verification failed: {}", issue)
        raise RuntimeError(
            "Runtime-state verification failed. "
            "Fix DB drift before continuing. "
            + " | ".join(issues)
        )

    logger.info("Runtime-state verification passed.")


async def _cli() -> None:
    await verify_runtime_state()


def main() -> None:
    asyncio.run(_cli())


if __name__ == "__main__":
    main()
