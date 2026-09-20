"""Tests for the one-shot backfill utility (fakes only — no Telegram)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from partner_message_summarisation.backfill import backfill_chat, service_running
from partner_message_summarisation.tests.test_listener import _chat, _message


class _BackfillDB:
    def __init__(self, existing_ids: set[int] | None = None) -> None:
        self.existing = existing_ids or set()
        self.inserted: list[dict] = []
        self.bumped: list[int] = []

    async def insert_message(self, row: dict) -> bool:
        if row["message_id"] in self.existing:
            return False
        self.inserted.append(row)
        return True

    async def bump_chat_last_seen(self, chat_id: int) -> None:
        self.bumped.append(chat_id)


class _HistoryClient:
    def __init__(self, messages: list) -> None:
        self._messages = messages

    async def get_chat_history(self, chat_id: int, limit: int):
        for msg in self._messages[:limit]:
            yield msg


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_backfill_counts_and_dedup() -> None:
    db = _BackfillDB(existing_ids={2})
    client = _HistoryClient([_message(id=3), _message(id=2), _message(id=1)])
    new, dup = _run(backfill_chat(client, db, _chat(), limit=10))
    assert (new, dup) == (2, 1)
    assert [r["message_id"] for r in db.inserted] == [3, 1]
    assert db.bumped == [-100123]


def test_backfill_skips_service_messages() -> None:
    db = _BackfillDB()
    service_msg = SimpleNamespace(service=True, id=99, chat=_chat())
    client = _HistoryClient([service_msg, _message(id=1)])
    new, dup = _run(backfill_chat(client, db, _chat(), limit=10))
    assert (new, dup) == (1, 0)
    assert [r["message_id"] for r in db.inserted] == [1]


def test_backfill_respects_limit() -> None:
    db = _BackfillDB()
    client = _HistoryClient([_message(id=i) for i in range(20, 0, -1)])
    new, _ = _run(backfill_chat(client, db, _chat(), limit=5))
    assert new == 5


def test_service_running_check(monkeypatch) -> None:
    import subprocess

    class _R:
        def __init__(self, code: int) -> None:
            self.returncode = code

    def _found(args, **kw):
        return _R(0)

    def _missing(args, **kw):
        return _R(1)

    monkeypatch.setattr(subprocess, "run", _found)
    assert service_running() is True
    monkeypatch.setattr(subprocess, "run", _missing)
    assert service_running() is False
