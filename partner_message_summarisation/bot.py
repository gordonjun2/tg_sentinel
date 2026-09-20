"""Long-running entry point: ingest listener + daily summary scheduler.

Wires the user-account listener (live handlers + bounded catch-up +
insert-retry loop) and the daily scheduler (19:00 SGT by default) that
summarizes pending messages and delivers the report via the bot client.
Graceful shutdown is SIGTERM/SIGINT-safe: a mid-delivery kill leaves all
batch rows ``pending`` (never half-completed).

Run with: ``python -m partner_message_summarisation.bot``
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

from .config import SENTINEL_DRY_RUN
from .db import PartnerMessageSummarisationDB
from .groups import discover_target_chats
from .listener import (
    IngestQueue,
    catchup_all,
    make_user_client,
    register_handlers,
    retry_loop,
)
from .notifier import deliver_report, make_bot_client
from .report import render_report, split_report
from .scheduler import scheduler_loop
from .summarizer import (
    chunk_transcripts,
    generate_summary,
    sanitize_summary,
    summarize_unanswered,
)
from .unanswered import find_unanswered_by_chat

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("partner_message_summarisation.bot")

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


async def run_daily_summary(db: PartnerMessageSummarisationDB, bot_client) -> None:
    """One summary run — the idempotency core (§11).

    Advisory lock → run row → fetch pending → transcripts → LLM →
    render/split/deliver → only then mark the batch completed. Any
    failure marks the run failed and leaves every message pending.
    """
    if not await db.try_advisory_lock():
        logger.warning("Another summary run holds the advisory lock — skipping")
        return
    try:
        window_start = await db.get_last_success_window_end() or _EPOCH
        window_end = datetime.now(timezone.utc)
        run_id = await db.start_run(window_start, window_end)
        logger.info(
            "Summary run %d started (window %s → %s)",
            run_id,
            window_start.isoformat(),
            window_end.isoformat(),
        )
        try:
            await _execute_run(db, bot_client, run_id, window_start, window_end)
        except Exception as exc:  # noqa: BLE001 — run must record failure
            logger.exception("Summary run %d failed", run_id)
            try:
                await db.fail_run(run_id, str(exc))
            except Exception:  # noqa: BLE001
                logger.exception("Failed to record run failure")
            raise
    finally:
        await db.unlock_advisory()


async def _execute_run(
    db: PartnerMessageSummarisationDB,
    bot_client,
    run_id: int,
    window_start: datetime,
    window_end: datetime,
) -> None:
    pending = await db.fetch_pending()
    if not pending:
        logger.info("No pending messages — silent skip (run %d)", run_id)
        await db.succeed_run(
            run_id, message_count=0, chat_count=0,
            llm_provider=None, llm_latency_ms=None, report_parts=None,
        )
        return

    # group by chat, transcript order: message count desc (§9.2)
    chats: dict[int, list[dict]] = {}
    for row in pending:
        chats.setdefault(row["chat_id"], []).append(row)
    ordered = sorted(
        (
            (rows[0]["chat_title"] or f"chat {chat_id}", rows)
            for chat_id, rows in chats.items()
        ),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    chat_counts = {title: len(rows) for title, rows in ordered}

    chunks = chunk_transcripts(ordered)
    logger.info(
        "Run %d: %d message(s) across %d chat(s), %d transcript chunk(s)",
        run_id, len(pending), len(chats), len(chunks),
    )
    summary, provider, latency_ms = await generate_summary(chunks)
    summary = sanitize_summary(summary, set(chat_counts))

    # Only actionable / informational content reaches the admin group: the
    # LLM flags low-signal groups (pure thanks/greetings/chatter) — their
    # messages are still processed as completed, just not reported.
    signal_names = {
        g.group_name.casefold() for g in summary.groups if g.has_signal
    }
    noise = [g.group_name for g in summary.groups if not g.has_signal]
    if noise:
        logger.info(
            "Run %d: %d low-signal group(s) omitted from the report: %s",
            run_id, len(noise), ", ".join(sorted(noise)),
        )
    summary.groups = [g for g in summary.groups if g.has_signal]
    unanswered_all = await summarize_unanswered(find_unanswered_by_chat(ordered))
    unanswered = [
        p
        for p in unanswered_all
        if p.group_name.casefold() in signal_names
    ]

    if not summary.groups and not unanswered:
        logger.info(
            "Run %d: nothing actionable or informational — silent skip "
            "(%d message(s) marked completed)", run_id, len(pending),
        )
        await db.mark_completed([row["id"] for row in pending], run_id)
        await db.succeed_run(
            run_id,
            message_count=len(pending),
            chat_count=len(chats),
            llm_provider=provider,
            llm_latency_ms=latency_ms,
            report_parts=0,
        )
        return

    report = render_report(
        summary, window_start, window_end, chat_counts, unanswered
    )
    parts = split_report(report)
    logger.info(
        "Run %d: summary ready (%s, %d ms) — %d part(s)",
        run_id, provider, latency_ms, len(parts),
    )
    if SENTINEL_DRY_RUN:
        logger.info("Run %d: SENTINEL_DRY_RUN is set — report will be logged only", run_id)
    delivered = await deliver_report(bot_client, parts)
    if not delivered:
        raise RuntimeError("delivery failed — batch stays pending")

    await db.mark_completed([row["id"] for row in pending], run_id)
    await db.succeed_run(
        run_id,
        message_count=len(pending),
        chat_count=len(chats),
        llm_provider=provider,
        llm_latency_ms=latency_ms,
        report_parts=len(parts),
    )
    logger.info(
        "Run %d success: %d message(s) completed (pending backlog: 0)",
        run_id, len(pending),
    )


async def main() -> None:
    logger.info("partner_message_summarisation service starting")

    db = PartnerMessageSummarisationDB()
    await db.connect()
    user_client = make_user_client()
    bot_client = make_bot_client()
    await user_client.start()
    await bot_client.start()
    logger.info("Pyrogram clients started (listener + deliverer)")

    try:
        targets = await discover_target_chats(user_client, db)
        queue = IngestQueue()
        register_handlers(user_client, db, [c.id for c in targets], queue)
        recovered = await catchup_all(user_client, db, targets)
        if recovered:
            logger.info("Catch-up recovered %d message(s)", recovered)

        # SIGTERM/SIGINT cancel the gather: in-flight inserts are lost to the
        # retry deque (recovered by next startup catch-up) and a mid-delivery
        # kill leaves rows pending — never half-completed (§11).
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)

        await asyncio.gather(
            retry_loop(db, queue),
            scheduler_loop(lambda: run_daily_summary(db, bot_client)),
        )
    finally:
        for client in (user_client, bot_client):
            try:
                await client.stop()
            except Exception:  # noqa: BLE001 — best-effort shutdown
                pass
        await db.close()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Interrupted — exiting.")
        sys.exit(0)
