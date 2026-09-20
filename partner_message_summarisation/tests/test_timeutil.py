"""Tests for the shared timezone helpers (everything user-facing is SGT)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytz

from partner_message_summarisation.timeutil import (
    ensure_utc,
    fmt_sentinel,
    sentinel_tz,
    to_sentinel_tz,
)

SGT = pytz.timezone("Asia/Singapore")


def test_sentinel_tz_is_configured_zone() -> None:
    assert str(sentinel_tz()) == "Asia/Singapore"


def test_ensure_utc_makes_naive_utc() -> None:
    naive = datetime(2026, 9, 18, 1, 14)
    aware = ensure_utc(naive)
    assert aware.tzinfo == timezone.utc
    assert aware.replace(tzinfo=None) == naive


def test_ensure_utc_keeps_aware_unchanged() -> None:
    aware = datetime(2026, 9, 18, 9, 14, tzinfo=SGT)
    assert ensure_utc(aware) is aware


def test_to_sentinel_tz_converts_utc() -> None:
    utc_dt = datetime(2026, 9, 18, 1, 14, tzinfo=timezone.utc)
    local = to_sentinel_tz(utc_dt)
    assert local.tzinfo is not None
    assert (local.hour, local.minute) == (9, 14)


def test_to_sentinel_tz_treats_naive_as_utc() -> None:
    naive = datetime(2026, 9, 18, 1, 14)  # Pyrogram-style naive UTC
    local = to_sentinel_tz(naive)
    assert local.utcoffset() == timedelta(hours=8)
    assert (local.hour, local.minute) == (9, 14)


def test_fmt_sentinel_default_and_custom_format() -> None:
    utc_dt = datetime(2026, 9, 18, 1, 14, tzinfo=timezone.utc)
    assert fmt_sentinel(utc_dt) == "09:14"
    assert fmt_sentinel(utc_dt, "%d %b %H:%M") == "18 Sep 09:14"
