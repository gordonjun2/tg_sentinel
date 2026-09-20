"""Tests for next_run_at: before/after/exactly target, tz conversion, override."""

from __future__ import annotations

from datetime import datetime

import pytz

from partner_message_summarisation.scheduler import next_run_at

SGT = pytz.timezone("Asia/Singapore")
UTC = pytz.UTC


def test_before_target_schedules_today() -> None:
    now = SGT.localize(datetime(2026, 9, 18, 12, 0))
    target = next_run_at(now)
    assert target == SGT.localize(datetime(2026, 9, 18, 19, 0))


def test_after_target_rolls_to_tomorrow() -> None:
    now = SGT.localize(datetime(2026, 9, 18, 20, 0))
    target = next_run_at(now)
    assert target == SGT.localize(datetime(2026, 9, 19, 19, 0))


def test_exactly_at_target_rolls_to_tomorrow() -> None:
    now = SGT.localize(datetime(2026, 9, 18, 19, 0))
    target = next_run_at(now)
    assert target == SGT.localize(datetime(2026, 9, 19, 19, 0))


def test_one_second_before_target_stays_today() -> None:
    now = SGT.localize(datetime(2026, 9, 18, 18, 59, 59))
    target = next_run_at(now)
    assert target == SGT.localize(datetime(2026, 9, 18, 19, 0))


def test_utc_input_converts_to_sgt_target() -> None:
    # 11:00 UTC == 19:00 SGT on the same calendar day
    now_utc = UTC.localize(datetime(2026, 9, 18, 10, 0))
    target = next_run_at(now_utc)
    assert target.utcoffset().total_seconds() == 8 * 3600
    assert target == SGT.localize(datetime(2026, 9, 18, 19, 0))
    # 12:00 UTC == 20:00 SGT → already past → tomorrow
    late_utc = UTC.localize(datetime(2026, 9, 18, 12, 0))
    assert next_run_at(late_utc) == SGT.localize(datetime(2026, 9, 19, 19, 0))


def test_hhmm_override() -> None:
    now = SGT.localize(datetime(2026, 9, 18, 18, 0))
    assert next_run_at(now, hhmm="18:30") == SGT.localize(
        datetime(2026, 9, 18, 18, 30)
    )
    assert next_run_at(now, hhmm="07:15") == SGT.localize(
        datetime(2026, 9, 19, 7, 15)
    )


def test_custom_timezone() -> None:
    utc_now = UTC.localize(datetime(2026, 9, 18, 12, 0))
    target = next_run_at(utc_now, tz=UTC, hhmm="13:00")
    assert target == UTC.localize(datetime(2026, 9, 18, 13, 0))
