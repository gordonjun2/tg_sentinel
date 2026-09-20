"""Timezone helpers: every user-facing timestamp is rendered in SGT.

Telegram (Pyrogram) datetimes are naive UTC; the DB column is
``TIMESTAMPTZ``. Naive values are treated as UTC everywhere — both when
persisting and when rendering.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytz

from .config import SENTINEL_TIMEZONE


def sentinel_tz() -> pytz.BaseTzInfo:
    return pytz.timezone(SENTINEL_TIMEZONE)


def ensure_utc(dt: datetime) -> datetime:
    """Naive datetimes are UTC (Pyrogram convention) — make that explicit."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def to_sentinel_tz(dt: datetime) -> datetime:
    return ensure_utc(dt).astimezone(sentinel_tz())


def fmt_sentinel(dt: datetime, fmt: str = "%H:%M") -> str:
    return to_sentinel_tz(dt).strftime(fmt)
