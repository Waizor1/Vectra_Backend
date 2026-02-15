import asyncio
import json
import os
from dataclasses import asdict, dataclass
from typing import Any

import asyncpg

from bloobcat.settings import script_settings


@dataclass
class DbGrowthSummary:
    database_size_mb: float
    table_count: int
    tables_without_scans: int
    tables_with_high_dead_ratio: int


async def _fetch_database_size(conn: asyncpg.Connection) -> float:
    value = await conn.fetchval("SELECT pg_database_size(current_database())")
    return round((float(value or 0) / 1024 / 1024), 2)


async def _fetch_table_sizes(conn: asyncpg.Connection, limit: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
            schemaname,
            relname AS table_name,
            n_live_tup,
            n_dead_tup,
            seq_scan + idx_scan AS total_scans,
            pg_total_relation_size(relid) AS total_bytes
        FROM pg_stat_user_tables
        ORDER BY pg_total_relation_size(relid) DESC
        LIMIT $1
        """,
        limit,
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        total_bytes = int(row["total_bytes"] or 0)
        live = int(row["n_live_tup"] or 0)
        dead = int(row["n_dead_tup"] or 0)
        result.append(
            {
                "schema": row["schemaname"],
                "table": row["table_name"],
                "size_mb": round(total_bytes / 1024 / 1024, 2),
                "live_rows": live,
                "dead_rows": dead,
                "dead_ratio": round((dead / max(1, live + dead)), 4),
                "total_scans": int(row["total_scans"] or 0),
            }
        )
    return result


def _build_summary(table_sizes: list[dict[str, Any]], db_size_mb: float) -> DbGrowthSummary:
    no_scan_count = sum(1 for row in table_sizes if int(row["total_scans"]) == 0)
    high_dead_ratio = sum(1 for row in table_sizes if float(row["dead_ratio"]) >= 0.2)
    return DbGrowthSummary(
        database_size_mb=db_size_mb,
        table_count=len(table_sizes),
        tables_without_scans=no_scan_count,
        tables_with_high_dead_ratio=high_dead_ratio,
    )


def format_warning(database_size_mb: float, warn_size_mb: float) -> str | None:
    if database_size_mb < warn_size_mb:
        return None
    return (
        f"Database size is {database_size_mb} MB, which is above warning threshold {warn_size_mb} MB. "
        "Review top tables and consider retention/VACUUM strategy."
    )


async def collect_db_growth_report(*, top_n: int, warn_size_mb: float) -> dict[str, Any]:
    conn = await asyncpg.connect(script_settings.db.get_secret_value())
    try:
        db_size_mb = await _fetch_database_size(conn)
        table_sizes = await _fetch_table_sizes(conn, max(1, top_n))
        summary = _build_summary(table_sizes, db_size_mb)
    finally:
        await conn.close()

    return {
        "summary": asdict(summary),
        "top_tables": table_sizes,
        "warning": format_warning(db_size_mb, warn_size_mb),
    }


async def main() -> None:
    top_n = int(os.getenv("DB_GROWTH_TOP_N", "30"))
    warn_size_mb = float(os.getenv("DB_GROWTH_WARN_MB", "4096"))
    payload = await collect_db_growth_report(top_n=top_n, warn_size_mb=warn_size_mb)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

