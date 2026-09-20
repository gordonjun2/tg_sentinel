"""Manual digest trigger — run the summarisation right now.

The scheduled digest fires once a day at SENTINEL_SUMMARY_TIME (19:00 SGT
by default). Use this to trigger a run immediately, e.g. to test the
pipeline live without waiting::

    python -m partner_message_summarisation.run_now

Safe to run while the service is up: it shares nothing with the listener
(no ``sentinel_listener.session`` use) and connects its own throwaway bot
session (``data/sentinel_run_now.session``, authorized from BOT_TOKEN —
no interactive login, no clash with the service's ``sentinel_bot``). The
DB advisory lock keeps it from overlapping a scheduled run (one of them
just skips). Messages are marked completed only after successful
delivery; a failed run leaves them pending for the next attempt.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from .bot import run_daily_summary
from .config import SENTINEL_BOT_SESSION_NAME
from .db import PartnerMessageSummarisationDB
from .notifier import make_bot_client

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("partner_message_summarisation.run_now")

# Dedicated session file (same data/ dir as the service sessions) so the
# CLI never fights the running service for a Pyrogram sqlite session.
RUN_NOW_SESSION = os.path.join(
    os.path.dirname(SENTINEL_BOT_SESSION_NAME), "sentinel_run_now"
)


async def run_now() -> None:
    db = PartnerMessageSummarisationDB()
    await db.connect()
    bot_client = make_bot_client(RUN_NOW_SESSION)
    await bot_client.start()
    try:
        await run_daily_summary(db, bot_client)
        runs = await db.last_runs(1)
        if runs:
            run = runs[0]
            logger.info(
                "Run #%d %s: msgs=%s chats=%s parts=%s%s",
                run["id"], run["status"], run["message_count"],
                run["chat_count"], run["report_parts"],
                "" if not run["error"] else f" error={run['error'][:300]}",
            )
    finally:
        try:
            await bot_client.stop()
        except Exception:  # noqa: BLE001 — best-effort shutdown
            pass
        await db.close()


def main() -> None:
    try:
        asyncio.run(run_now())
    except KeyboardInterrupt:
        logger.info("Interrupted — messages stay pending, safe to re-run.")
        sys.exit(0)


if __name__ == "__main__":
    main()
