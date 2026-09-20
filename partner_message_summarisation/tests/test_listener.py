"""Tests for serialize_message, the ingest retry queue, and catch-up bounds."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from pyrogram.enums import ChatType, MessageMediaType

from partner_message_summarisation.listener import (
    IngestQueue,
    catchup_chat,
    serialize_message,
)


def _run(coro):
    return asyncio.run(coro)


def _user(id=42, username="alice", first_name="Alice", last_name="Ng"):
    return SimpleNamespace(
        id=id, username=username, first_name=first_name, last_name=last_name
    )


def _chat(id=-100123, title="SISC <> AI Builders", type=ChatType.SUPERGROUP):
    return SimpleNamespace(id=id, title=title, type=type)


def _message(**over) -> SimpleNamespace:
    msg = SimpleNamespace(
        id=101,
        chat=_chat(),
        from_user=_user(),
        sender_chat=None,
        author_signature=None,
        text="hello world",
        caption=None,
        media=None,
        date=datetime(2026, 9, 18, 9, 14, tzinfo=timezone.utc),
        reply_to_message_id=None,
        forward_date=None,
        forward_from=None,
        forward_from_chat=None,
    )
    for key, value in over.items():
        setattr(msg, key, value)
    return msg


# -- serialize_message --------------------------------------------------------


def test_serialize_text_message_full_metadata() -> None:
    row = serialize_message(_message())
    assert row["chat_id"] == -100123
    assert row["chat_title"] == "SISC <> AI Builders"
    assert row["message_id"] == 101
    assert row["sender_user_id"] == 42
    assert row["sender_username"] == "alice"
    assert row["sender_display_name"] == "Alice Ng"
    assert row["message_text"] == "hello world"
    assert row["message_date"] == datetime(2026, 9, 18, 9, 14, tzinfo=timezone.utc)
    assert row["is_forward"] is False
    assert row["media_type"] is None


def test_serialize_caption_falls_back_to_text_column() -> None:
    photo = SimpleNamespace(file_id="photo-1", file_name=None)
    msg = _message(
        text=None, caption="look at this", media=MessageMediaType.PHOTO, photo=photo
    )
    row = serialize_message(msg)
    assert row["message_text"] == "look at this"
    assert row["caption"] == "look at this"
    assert row["media_type"] == "PHOTO"
    assert row["media_file_id"] == "photo-1"


def test_serialize_document_media_fields() -> None:
    doc = SimpleNamespace(file_id="doc-9", file_name="report.pdf")
    msg = _message(text="see attached", media=MessageMediaType.DOCUMENT, document=doc)
    row = serialize_message(msg)
    assert row["media_type"] == "DOCUMENT"
    assert row["media_file_id"] == "doc-9"
    assert row["media_file_name"] == "report.pdf"


def test_serialize_anonymous_admin_falls_back_to_sender_chat() -> None:
    msg = _message(from_user=None, sender_chat=_chat(id=-100999, title="SISC <> Ops"))
    row = serialize_message(msg)
    assert row["sender_user_id"] is None
    assert row["sender_display_name"] == "SISC <> Ops"


def test_serialize_forward_fields() -> None:
    fwd_date = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
    msg = _message(
        forward_date=fwd_date,
        forward_from_chat=SimpleNamespace(id=-100777, title="News Source"),
        forward_from=None,
    )
    row = serialize_message(msg)
    assert row["is_forward"] is True
    assert row["forward_date"] == fwd_date
    assert row["forward_from_chat_id"] == -100777
    assert row["forward_from_chat_title"] == "News Source"


def test_serialize_forward_hidden_author_name() -> None:
    fwd_date = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)
    msg = _message(
        forward_date=fwd_date,
        forward_from=None,
        forward_sender_name="Hidden Author",
    )
    row = serialize_message(msg)
    assert row["is_forward"] is True
    assert row["forward_from_name"] == "Hidden Author"


def test_serialize_reply_reference() -> None:
    row = serialize_message(_message(reply_to_message_id=99))
    assert row["reply_to_message_id"] == 99


# -- IngestQueue ---------------------------------------------------------------


class _FlakyDB:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.inserted: list[dict] = []

    async def insert_message(self, row: dict) -> bool:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("db down")
        self.inserted.append(row)
        return True


def test_retry_queue_retries_failed_inserts() -> None:
    async def scenario() -> None:
        queue = IngestQueue()
        db = _FlakyDB(fail_times=1)
        row = serialize_message(_message())
        queue.push(row)
        # first drain still fails (db down), row stays queued
        assert await queue.drain(db) == 0
        assert len(queue) == 1
        # second drain succeeds once the db recovers
        assert await queue.drain(db) == 1
        assert len(queue) == 0
        assert db.inserted == [row]

    asyncio.run(scenario())


# -- catch-up ------------------------------------------------------------------


class _CatchupDB:
    def __init__(self, last: int | None) -> None:
        self.last = last
        self.inserts: list[dict] = []

    async def get_last_collected_message_id(self, chat_id: int) -> int | None:
        return self.last

    async def insert_message(self, row: dict) -> bool:
        self.inserts.append(row)
        return True


class _HistoryClient:
    def __init__(self, ids: list[int]) -> None:
        self._ids = ids  # newest first, like get_chat_history

    async def get_chat_history(self, chat_id: int, limit: int):
        for i in self._ids[:limit]:
            yield _message(id=i)


def test_catchup_skips_first_run_chat() -> None:
    db = _CatchupDB(last=None)
    count = _run(catchup_chat(_HistoryClient([10, 9]), db, _chat()))
    assert count == 0 and db.inserts == []


def test_catchup_fetches_only_newer_and_respects_bound() -> None:
    db = _CatchupDB(last=8)
    client = _HistoryClient([12, 11, 10, 9, 8, 7])
    count = _run(catchup_chat(client, db, _chat(), limit=10))
    assert count == 4  # 12, 11, 10, 9 (stops at 8)
    assert [r["message_id"] for r in db.inserts] == [12, 11, 10, 9]


def test_catchup_warns_when_bound_exceeded(caplog) -> None:
    db = _CatchupDB(last=5)
    client = _HistoryClient([20, 19, 18, 17])
    count = _run(catchup_chat(client, db, _chat(), limit=4))
    assert count == 4
    assert any("permanently missed" in r.message for r in caplog.records)
