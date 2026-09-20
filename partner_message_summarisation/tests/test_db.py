"""Integration tests for PartnerMessageSummarisationDB — real PostgreSQL, env-gated.

Skipped unless ``PARTNER_MESSAGE_SUMMARISATION_TEST_DATABASE_URL`` is set, e.g.::

    PARTNER_MESSAGE_SUMMARISATION_TEST_DATABASE_URL=postgres://user:pass@localhost:5432/partner_message_summarisation_test \
        venv/bin/python -m pytest partner_message_summarisation/tests/test_db.py -v

Covers: schema idempotence, dedup on (chat_id, message_id), pending→completed
state transitions, summary_runs window query, advisory lock exclusivity.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("PARTNER_MESSAGE_SUMMARISATION_TEST_DATABASE_URL"),
    reason="PARTNER_MESSAGE_SUMMARISATION_TEST_DATABASE_URL not set — needs a real PostgreSQL",
)

from partner_message_summarisation.db import PartnerMessageSummarisationDB  # noqa: E402

DSN = os.getenv("PARTNER_MESSAGE_SUMMARISATION_TEST_DATABASE_URL", "")


def _run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def db() -> PartnerMessageSummarisationDB:
    database = PartnerMessageSummarisationDB(dsn=DSN)
    _run(database.connect())
    # start from a clean slate each run
    _run(
        database._execute(
            "TRUNCATE tg_messages, summary_runs, tg_chats RESTART IDENTITY CASCADE"
        )
    )
    yield database
    _run(database.close())


def _msg_row(chat_id: int = -100123, message_id: int = 1, **over) -> dict:
    row = {
        "chat_id": chat_id,
        "chat_title": "SISC <> AI Builders",
        "message_id": message_id,
        "sender_user_id": 42,
        "sender_username": "alice",
        "sender_display_name": "Alice",
        "message_text": "hello world",
        "caption": None,
        "message_date": datetime(2026, 9, 18, 9, 14, tzinfo=timezone.utc),
        "reply_to_message_id": None,
        "is_forward": False,
        "forward_from_name": None,
        "forward_from_chat_id": None,
        "forward_from_chat_title": None,
        "forward_date": None,
        "media_type": None,
        "media_file_id": None,
        "media_file_name": None,
    }
    row.update(over)
    return row


def test_redact_dsn_hides_credentials() -> None:
    from partner_message_summarisation.db import redact_dsn

    out = redact_dsn("postgres://user:secret@db.example.com:5432/partner_message_summarisation")
    assert "secret" not in out and "user" not in out
    assert "db.example.com" in out and "partner_message_summarisation" in out


def test_schema_init_is_idempotent(db: PartnerMessageSummarisationDB) -> None:
    _run(db.close())
    _run(db.connect())  # second connect re-runs schema init without error
    db2 = PartnerMessageSummarisationDB(dsn=DSN)
    _run(db2.connect())  # independent connection against existing schema
    _run(db2.close())


def test_insert_dedup_on_chat_message_pair(db: PartnerMessageSummarisationDB) -> None:
    _run(db.upsert_chat(-100123, "SISC <> AI Builders", "supergroup"))
    assert _run(db.insert_message(_msg_row())) is True
    assert _run(db.insert_message(_msg_row())) is False  # duplicate ignored
    pending = _run(db.fetch_pending())
    assert len(pending) == 1
    assert pending[0]["chat_title"] == "SISC <> AI Builders"


def test_state_transition_pending_to_completed(db: PartnerMessageSummarisationDB) -> None:
    _run(db.upsert_chat(-100123, "SISC <> AI Builders", "supergroup"))
    _run(db.insert_message(_msg_row(message_id=1)))
    _run(db.insert_message(_msg_row(message_id=2)))
    pending = _run(db.fetch_pending())
    run_id = _run(db.start_run(datetime.now(timezone.utc), datetime.now(timezone.utc)))
    _run(db.mark_completed([m["id"] for m in pending], run_id))
    assert _run(db.fetch_pending()) == []
    runs = _run(db.last_runs(1))
    assert runs[0]["status"] == "running"
    _run(
        db.succeed_run(
            run_id, message_count=2, chat_count=1,
            llm_provider="gemini", llm_latency_ms=1234, report_parts=1,
        )
    )
    runs = _run(db.last_runs(1))
    assert runs[0]["status"] == "success"
    assert runs[0]["message_count"] == 2
    window_end = _run(db.get_last_success_window_end())
    assert window_end is not None


def test_window_query_uses_last_success(db: PartnerMessageSummarisationDB) -> None:
    assert _run(db.get_last_success_window_end()) is None
    end1 = datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc)
    end2 = datetime(2026, 9, 18, 11, 0, tzinfo=timezone.utc)
    r1 = _run(db.start_run(end1, end1))
    _run(db.succeed_run(r1, message_count=0, chat_count=0, llm_provider=None,
                        llm_latency_ms=None, report_parts=None))
    r2 = _run(db.start_run(end1, end2))
    _run(db.fail_run(r2, "both LLM providers failed"))
    assert _run(db.get_last_success_window_end()) == end1


def test_failed_run_records_error_and_messages_stay_pending(db: PartnerMessageSummarisationDB) -> None:
    _run(db.upsert_chat(-100123, "SISC <> AI Builders", "supergroup"))
    _run(db.insert_message(_msg_row()))
    run_id = _run(db.start_run(datetime.now(timezone.utc), datetime.now(timezone.utc)))
    _run(db.fail_run(run_id, "boom"))
    runs = _run(db.last_runs(1))
    assert runs[0]["status"] == "failed" and runs[0]["error"] == "boom"
    assert len(_run(db.fetch_pending())) == 1


def test_advisory_lock_exclusivity() -> None:
    import asyncio

    async def scenario() -> None:
        db1 = PartnerMessageSummarisationDB(dsn=DSN)
        db2 = PartnerMessageSummarisationDB(dsn=DSN)
        await db1.connect()
        await db2.connect()
        try:
            assert await db1.try_advisory_lock() is True
            assert await db2.try_advisory_lock() is False  # second instance loses
            await db1.unlock_advisory()
            assert await db2.try_advisory_lock() is True  # free after release
        finally:
            await db1.close()
            await db2.close()

    asyncio.run(scenario())


def test_get_last_collected_message_id(db: PartnerMessageSummarisationDB) -> None:
    assert _run(db.get_last_collected_message_id(-100123)) is None
    _run(db.upsert_chat(-100123, "SISC <> AI Builders", "supergroup"))
    _run(db.insert_message(_msg_row(message_id=10)))
    _run(db.insert_message(_msg_row(message_id=11)))
    assert _run(db.get_last_collected_message_id(-100123)) == 11
