"""Daily digest scheduling: next 19:00 (or configured HH:MM) in the
configured timezone, then a sleep → run → recompute loop.

Follows the in-process asyncio loop style of ``gmail_calendar/bot.py`` —
no cron, no APScheduler. Asia/Singapore has no DST so the naive
"replace hour/minute, add a day" arithmetic is safe; the helper is still
unit-tested.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

import pytz

from .config import SENTINEL_SUMMARY_TIME, SENTINEL_TIMEZONE

logger = logging.getLogger(__name__)


def next_run_at(
    now: datetime,
    tz: pytz.BaseTzInfo | None = None,
    hhmm: str | None = None,
) -> datetime:
    """Next fire time (tz-aware, in ``tz``): today's HH:MM or tomorrow's."""
    tz = tz or pytz.timezone(SENTINEL_TIMEZONE)
    hhmm = hhmm or SENTINEL_SUMMARY_TIME
    hh, mm = (int(part) for part in hhmm.split(":"))
    now_local = now.astimezone(tz)
    candidate = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate


async def scheduler_loop(
    run_summary: Callable[[], Awaitable[None]],
    *,
    tz_name: str | None = None,
    hhmm: str | None = None,
) -> None:
    """Sleep until the next fire time, run, recompute — forever.

    ``run_summary`` is an async callable (the orchestrator's
    ``run_daily_summary`` bound to its dependencies). Exceptions from a run
    are logged and swallowed: messages stay pending and the loop continues.
    """
    tz = pytz.timezone(tz_name or SENTINEL_TIMEZONE)
    while True:
        now_utc = datetime.now(tz=pytz.UTC)
        target = next_run_at(now_utc, tz=tz, hhmm=hhmm)
        wait_seconds = max(0.0, (target - now_utc).total_seconds())
        logger.info(
            "Next digest run at %s (in %.0f min)",
            target.isoformat(), wait_seconds / 60,
        )
        await asyncio.sleep(wait_seconds)
        try:
            await run_summary()
        except Exception:  # noqa: BLE001 — a failed run must not kill the loop
            logger.exception("Daily summary failed — messages stay pending")
