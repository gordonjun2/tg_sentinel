"""Tests for run_daily_summary orchestration logic with fakes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from partner_message_summarisation import bot as bot_mod
from partner_message_summarisation.db import PartnerMessageSummarisationDB  # noqa: F401  (real class used for lock key)


class FakeDB:
    def __init__(self, lock_acquired: bool = True, pending: list | None = None) -> None:
        self.lock_acquired = lock_acquired
        self.pending = pending or []
        self.started: list = []
        self.succeeded: list = []
        self.failed: list = []
        self.completed_ids: list | None = None
        self.unlocked = False

    async def try_advisory_lock(self) -> bool:
        return self.lock_acquired

    async def unlock_advisory(self) -> None:
        self.unlocked = True

    async def get_last_success_window_end(self):
        return None

    async def start_run(self, window_start, window_end) -> int:
        self.started.append((window_start, window_end))
        return 1

    async def succeed_run(self, run_id: int, **counts) -> None:
        self.succeeded.append((run_id, counts))

    async def fail_run(self, run_id: int, error: str) -> None:
        self.failed.append((run_id, error))

    async def fetch_pending(self) -> list:
        return self.pending

    async def mark_completed(self, ids: list[int], run_id: int) -> None:
        self.completed_ids = list(ids)


def _pending_rows() -> list[dict]:
    return [
        {
            "id": 11,
            "chat_id": -100123,
            "chat_title": "SISC <> A",
            "message_id": 1,
            "message_date": datetime(2026, 9, 18, 1, 0, tzinfo=timezone.utc),
            "message_text": "hello",
        },
        {
            "id": 12,
            "chat_id": -100123,
            "chat_title": "SISC <> A",
            "message_id": 2,
            "message_date": datetime(2026, 9, 18, 1, 30, tzinfo=timezone.utc),
            "message_text": "world",
        },
    ]


def _run(coro):
    return asyncio.run(coro)


def _fake_unanswered_summarizer(points=None):
    async def fake(items):
        return points or []

    return fake


def _fake_summary_result(groups):
    """Build a (PartnerMessageSummary, provider, latency) fake LLM result.

    ``groups``: iterable of (group_name, summary, has_signal).
    """
    from partner_message_summarisation.summarizer import (
        GroupSummary,
        PartnerMessageSummary,
    )

    return (
        PartnerMessageSummary(
            groups=[
                GroupSummary(group_name=name, summary=[text], has_signal=signal)
                for name, text, signal in groups
            ]
        ),
        "fake-provider",
        5,
    )


def _two_chat_pending() -> list[dict]:
    rows = _pending_rows()
    rows.append(
        {
            "id": 13,
            "chat_id": -100456,
            "chat_title": "SISC <> B",
            "message_id": 1,
            "message_date": datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc),
            "message_text": "Thank you!",
        }
    )
    return rows


def test_empty_day_silent_success_without_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered = []

    async def fake_deliver(client, parts):
        delivered.append(parts)
        return True

    monkeypatch.setattr(bot_mod, "deliver_report", fake_deliver)
    db = FakeDB(pending=[])
    _run(bot_mod.run_daily_summary(db, bot_client=None))
    assert delivered == []
    assert len(db.succeeded) == 1
    run_id, counts = db.succeeded[0]
    assert counts["message_count"] == 0 and counts["chat_count"] == 0
    assert db.completed_ids is None


def test_successful_run_commits_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered = []

    async def fake_deliver(client, parts):
        delivered.append(parts)
        return True

    async def fake_summary(chunks):
        return _fake_summary_result([("SISC <> A", "Alice asked about timelines.", True)])

    monkeypatch.setattr(bot_mod, "deliver_report", fake_deliver)
    monkeypatch.setattr(bot_mod, "generate_summary", fake_summary)
    monkeypatch.setattr(
        bot_mod, "summarize_unanswered", _fake_unanswered_summarizer()
    )
    db = FakeDB(pending=_pending_rows())
    _run(bot_mod.run_daily_summary(db, bot_client=None))
    assert db.completed_ids == [11, 12]
    run_id, counts = db.succeeded[0]
    assert counts["message_count"] == 2
    assert counts["chat_count"] == 1
    assert counts["llm_provider"] == "fake-provider"
    assert counts["report_parts"] == len(delivered[0])
    assert db.unlocked is True


def test_delivery_failure_leaves_batch_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_deliver(client, parts):
        return False

    async def fake_summary(chunks):
        return _fake_summary_result([("SISC <> A", "Alice asked about timelines.", True)])

    monkeypatch.setattr(bot_mod, "deliver_report", fake_deliver)
    monkeypatch.setattr(bot_mod, "generate_summary", fake_summary)
    monkeypatch.setattr(
        bot_mod, "summarize_unanswered", _fake_unanswered_summarizer()
    )
    db = FakeDB(pending=_pending_rows())
    with pytest.raises(RuntimeError, match="delivery failed"):
        _run(bot_mod.run_daily_summary(db, bot_client=None))
    assert db.completed_ids is None  # nothing committed
    assert len(db.failed) == 1 and "delivery" in db.failed[0][1]
    assert db.succeeded == []
    assert db.unlocked is True  # lock always released


def test_llm_failure_fails_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(chunks):
        raise RuntimeError("both LLM providers failed")

    monkeypatch.setattr(bot_mod, "generate_summary", boom)
    db = FakeDB(pending=_pending_rows())
    with pytest.raises(RuntimeError):
        _run(bot_mod.run_daily_summary(db, bot_client=None))
    assert db.completed_ids is None
    assert "both LLM providers failed" in db.failed[0][1]


def test_lock_not_acquired_skips_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDB(lock_acquired=False, pending=_pending_rows())
    _run(bot_mod.run_daily_summary(db, bot_client=None))
    assert db.started == [] and db.succeeded == [] and db.failed == []


def test_all_low_signal_groups_skip_delivery_but_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered = []

    async def fake_deliver(client, parts):
        delivered.append(parts)
        return True

    async def fake_summary(chunks):
        return _fake_summary_result(
            [("SISC <> A", "Alice thanked the group.", False)]
        )

    monkeypatch.setattr(bot_mod, "deliver_report", fake_deliver)
    monkeypatch.setattr(bot_mod, "generate_summary", fake_summary)
    monkeypatch.setattr(
        bot_mod, "summarize_unanswered", _fake_unanswered_summarizer()
    )
    db = FakeDB(pending=_pending_rows())
    _run(bot_mod.run_daily_summary(db, bot_client=None))

    assert delivered == []  # nothing sent to the admin group
    assert db.completed_ids == [11, 12]  # still processed as completed
    assert len(db.succeeded) == 1
    assert db.succeeded[0][1]["report_parts"] == 0


def test_mixed_signal_run_reports_only_signal_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered = []

    async def fake_deliver(client, parts):
        delivered.append(parts)
        return True

    async def fake_summary(chunks):
        return _fake_summary_result(
            [
                ("SISC <> A", "Alice proposed an event date.", True),
                ("SISC <> B", "Bob said thank you.", False),
            ]
        )

    from partner_message_summarisation.summarizer import UnansweredPoint

    monkeypatch.setattr(bot_mod, "deliver_report", fake_deliver)
    monkeypatch.setattr(bot_mod, "generate_summary", fake_summary)
    monkeypatch.setattr(
        bot_mod,
        "summarize_unanswered",
        _fake_unanswered_summarizer(
            [
                UnansweredPoint(group_name="SISC <> A", point="Alice wants a date"),
                UnansweredPoint(group_name="SISC <> B", point="Bob said thanks"),
            ]
        ),
    )
    db = FakeDB(pending=_two_chat_pending())
    _run(bot_mod.run_daily_summary(db, bot_client=None))

    assert len(delivered) == 1
    report = "".join(delivered[0])
    assert "Alice proposed an event date" in report  # signal summary present
    assert "Alice wants a date" in report  # signal-group unanswered present
    assert "Bob said thank you" not in report  # low-signal summary omitted
    assert "Bob said thanks" not in report  # its unanswered point dropped too
    assert sorted(db.completed_ids) == [11, 12, 13]  # noise still completed


def test_unanswered_in_signal_group_still_delivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A group with substance but no LLM summary text still reports via unanswered."""
    delivered = []

    async def fake_deliver(client, parts):
        delivered.append(parts)
        return True

    async def fake_summary(chunks):
        return _fake_summary_result([])  # LLM returned no groups at all

    from partner_message_summarisation.summarizer import UnansweredPoint

    monkeypatch.setattr(bot_mod, "deliver_report", fake_deliver)
    monkeypatch.setattr(bot_mod, "generate_summary", fake_summary)
    monkeypatch.setattr(
        bot_mod,
        "summarize_unanswered",
        _fake_unanswered_summarizer(
            [UnansweredPoint(group_name="SISC <> A", point="Alice awaits a reply")]
        ),
    )
    db = FakeDB(pending=_pending_rows())
    _run(bot_mod.run_daily_summary(db, bot_client=None))

    # groups empty → signal_names empty → unanswered filtered out → silent skip
    assert delivered == []
    assert db.completed_ids == [11, 12]
    assert db.succeeded[0][1]["report_parts"] == 0
