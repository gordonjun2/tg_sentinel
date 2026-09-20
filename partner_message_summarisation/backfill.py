"""One-shot history backfill for partner groups (initial scrape).

The service intentionally starts from "now" (spec: first-ever run does not
import history). Use this utility when you WANT an initial scrape::

    python -m partner_message_summarisation.backfill                  # 200/group
    python -m partner_message_summarisation.backfill --limit 500
    python -m partner_message_summarisation.backfill --chat "AI Builders"

IMPORTANT — stop the service first (``kill <pid>``)::

    two processes cannot share one Pyrogram session file. The utility
    refuses to run while the service is up unless ``--force`` is passed.

Inserted messages are deduplicated (re-running is safe) and land as
``completed`` — history is presumed already seen, so it never feeds the
next summary run. New live messages are still archived as ``pending`` by the
service as usual.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
from datetime import datetime, timezone

from .config import SENTINEL_MAX_CATCHUP_PER_CHAT
from .db import PartnerMessageSummarisationDB
from .groups import discover_target_chats
from .listener import make_user_client, serialize_message

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("partner_message_summarisation.backfill")

_SERVICE_PATTERN = "partner_message_summarisation.bot"


def service_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", _SERVICE_PATTERN], capture_output=True
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001 — check is best-effort
        return False


async def backfill_chat(
    client, db: PartnerMessageSummarisationDB, chat, limit: int
) -> tuple[int, int]:
    """Fetch the newest ``limit`` messages of one chat. Returns (new, dup)."""
    new = dup = 0
    async for msg in client.get_chat_history(chat.id, limit=limit):
        if getattr(msg, "service", None):
            continue  # join/leave/pin notices — same filter as the live handler
        row = serialize_message(msg, chat_title=chat.title)
        row["status"] = "completed"  # already-seen history: never re-summarized
        row["processed_at"] = datetime.now(timezone.utc)
        try:
            if await db.insert_message(row):
                new += 1
            else:
                dup += 1
        except Exception:  # noqa: BLE001 — one bad message must not stop the scrape
            logger.warning(
                "Backfill insert failed for %s/%s", chat.id, msg.id, exc_info=True
            )
    await db.bump_chat_last_seen(chat.id)
    return new, dup


async def run_backfill(limit: int, chat_filter: str | None) -> None:
    db = PartnerMessageSummarisationDB()
    await db.connect()
    client = make_user_client()
    await client.start()
    try:
        chats = await discover_target_chats(client, db)
        if chat_filter:
            chats = [
                c for c in chats if chat_filter.casefold() in (c.title or "").casefold()
            ]
        if not chats:
            logger.warning("No matching partner groups — nothing to scrape")
            return
        logger.info(
            "Backfilling up to %d message(s) per chat across %d chat(s)",
            limit, len(chats),
        )
        total_new = total_dup = 0
        for chat in chats:
            new, dup = await backfill_chat(client, db, chat, limit)
            total_new += new
            total_dup += dup
            logger.info(
                "  %s (%s): %d new, %d duplicate(s)",
                chat.title, chat.id, new, dup,
            )
        logger.info(
            "Backfill complete: %d new message(s) archived as completed "
            "(%d duplicates skipped)", total_new, total_dup,
        )
    finally:
        try:
            await client.stop()
        except Exception:  # noqa: BLE001
            pass
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-shot initial scrape of partner group history."
    )
    parser.add_argument(
        "--limit", type=int, default=SENTINEL_MAX_CATCHUP_PER_CHAT,
        help="Max messages to fetch per chat (default: %(default)s)",
    )
    parser.add_argument(
        "--chat", default=None,
        help="Only scrape chats whose title contains this substring",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Run even if the service process appears to be up (session conflict risk)",
    )
    args = parser.parse_args()

    if service_running() and not args.force:
        logger.error(
            "The service is currently running and holds the Pyrogram session. "
            "Stop it first: kill <pid>  (then bash start_all.sh again afterwards). "
            "Override with --force at your own risk."
        )
        sys.exit(1)

    try:
        asyncio.run(run_backfill(args.limit, args.chat))
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
