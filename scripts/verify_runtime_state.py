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
