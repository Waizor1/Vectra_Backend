import argparse
import asyncio
import json
from typing import Any

from bloobcat.bot.notifications.admin import send_admin_message
from bloobcat.logger import get_logger
from scripts.db_growth_report import collect_db_growth_report


logger = get_logger("scripts.db_growth_notify")


def _build_message(payload: dict[str, Any], *, top_lines: int) -> str:
    summary = payload.get("summary") or {}
    warning = payload.get("warning")
    tables = payload.get("top_tables") or []

    header = "🗄️ <b>DB Growth Check</b>\n\n"
    if warning:
        header += f"⚠️ <b>Warning:</b> {warning}\n\n"
    else:
        header += "✅ Size is within configured threshold.\n\n"

    lines: list[str] = []
    lines.append(f"• DB size: <b>{summary.get('database_size_mb', 'n/a')} MB</b>")
    lines.append(f"• Tables scanned: <b>{summary.get('table_count', 'n/a')}</b>")
    lines.append(f"• No scans: <b>{summary.get('tables_without_scans', 'n/a')}</b>")
    lines.append(f"• High dead ratio: <b>{summary.get('tables_with_high_dead_ratio', 'n/a')}</b>")

    top_rows = []
    for row in tables[: max(1, top_lines)]:
        top_rows.append(
            f"- {row.get('schema')}.{row.get('table')}: {row.get('size_mb')} MB"
            f" | dead={row.get('dead_ratio')}"
            f" | scans={row.get('total_scans')}"
        )

    top_block = "\n".join(top_rows) if top_rows else "- no tables"
    body = "\n".join(lines)
    return f"{header}{body}\n\n<b>Top tables:</b>\n{top_block}\n\n#db_growth"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run DB growth check and notify admin chat.")
    parser.add_argument("--warn-mb", type=float, default=4096.0, help="Warning threshold for DB size in MB.")
    parser.add_argument("--top-n", type=int, default=30, help="How many largest tables to analyze.")
    parser.add_argument("--top-lines", type=int, default=8, help="How many top tables to include in Telegram message.")
    parser.add_argument(
        "--send-always",
        action="store_true",
        help="Send Telegram message even when warning threshold is not exceeded.",
    )
    args = parser.parse_args()

    payload = await collect_db_growth_report(top_n=max(1, args.top_n), warn_size_mb=max(1.0, args.warn_mb))
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    should_send = bool(payload.get("warning")) or args.send_always
    if not should_send:
        logger.info("DB growth check passed without warning; notification skipped.")
        return

    message = _build_message(payload, top_lines=max(1, args.top_lines))
    await send_admin_message(message)
    logger.info("DB growth notification sent to admin chat.")


if __name__ == "__main__":
    asyncio.run(main())

