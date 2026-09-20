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

    async def fake_digest(chunks):
        from partner_message_summarisation.summarizer import DailyDigest

        return DailyDigest(), "fake-provider", 5

    monkeypatch.setattr(bot_mod, "deliver_report", fake_deliver)
    monkeypatch.setattr(bot_mod, "generate_digest", fake_digest)
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

    async def fake_digest(chunks):
        from partner_message_summarisation.summarizer import DailyDigest

        return DailyDigest(), "fake-provider", 5

    monkeypatch.setattr(bot_mod, "deliver_report", fake_deliver)
    monkeypatch.setattr(bot_mod, "generate_digest", fake_digest)
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

    monkeypatch.setattr(bot_mod, "generate_digest", boom)
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
