"""Operator CLI: pending backlog, recent runs, monitored chats.

    python -m partner_message_summarisation.status
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from .db import PartnerMessageSummarisationDB


def _age(dt: datetime | None) -> str:
    if dt is None:
        return "n/a"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - dt).total_seconds()
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


async def _print_status() -> None:
    db = PartnerMessageSummarisationDB()
    await db.connect()
    try:
        stats = await db.pending_stats()
        print("=== partner_message_summarisation status ===")
        print(
            f"Pending backlog: {stats['total']} message(s)"
            f" (oldest: {_age(stats['oldest'])})"
        )
        for row in stats["per_chat"]:
            print(
                f"  - {row['chat_title'] or row['chat_id']}: "
                f"{row['n']} (oldest {_age(row['oldest'])})"
            )

        print("\nLast runs:")
        runs = await db.last_runs(7)
        if not runs:
            print("  (none)")
        for run in runs:
            completed = run["completed_at"] or run["started_at"]
            print(
                f"  #{run['id']} {run['status']:<8} window "
                f"{run['window_start']:%d %b %H:%M} → {run['window_end']:%d %b %H:%M}"
                f" · msgs={run['message_count']} chats={run['chat_count']}"
                f" · {run['llm_provider'] or '-'} {run['llm_latency_ms'] or '-'}ms"
                f" · parts={run['report_parts'] or '-'}"
                f" · {_age(completed)}"
            )
            if run["error"]:
                print(f"         error: {run['error'][:200]}")

        print("\nMonitored chats:")
        chats = await db.active_chats()
        if not chats:
            print("  (none discovered yet)")
        for chat in chats:
            print(f"  - {chat['title']} ({chat['chat_type']}, {chat['chat_id']})")
    finally:
        await db.close()


def main() -> None:
    try:
        asyncio.run(_print_status())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
