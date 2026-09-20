"""Tests for admin-unanswered message detection."""

from __future__ import annotations

from datetime import datetime, timezone

from partner_message_summarisation import unanswered as ua
from partner_message_summarisation.unanswered import find_unanswered_by_chat

YUNA, GORDON, ELMER = 6838780049, 131837449, 788105004


def _msg(
    message_id: int,
    sender_user_id: int | None,
    text: str,
    hour: int,
    **over,
) -> dict:
    row = {
        "message_id": message_id,
        "sender_user_id": sender_user_id,
        "sender_display_name": "Alice",
        "sender_username": "alice",
        "message_text": text,
        "caption": None,
        "message_date": datetime(2026, 9, 18, hour, 0, tzinfo=timezone.utc),
        "media_type": None,
    }
    row.update(over)
    return row


def _chat(title: str, rows: list[dict]) -> tuple[str, list[dict]]:
    return (title, rows)


def test_partner_messages_after_last_admin_are_unanswered() -> None:
    ordered = [
        _chat(
            "SISC <> A",
            [
                _msg(1, GORDON, "admin hello", 9),
                _msg(2, 555, "partner question", 10),
                _msg(3, 555, "partner follow-up", 11),
            ],
        )
    ]
    items = find_unanswered_by_chat(ordered)
    assert [i.excerpt for i in items] == ["partner question", "partner follow-up"]
    assert all(i.chat_title == "SISC <> A" for i in items)
    assert items[0].sender == "Alice"


def test_admin_reply_closes_partner_message() -> None:
    ordered = [
        _chat(
            "SISC <> A",
            [
                _msg(1, 555, "partner asks", 9),
                _msg(2, YUNA, "admin answers", 10),
                _msg(3, 555, "thanks!", 11),
            ],
        )
    ]
    items = find_unanswered_by_chat(ordered)
    assert [i.excerpt for i in items] == ["thanks!"]


def test_any_configured_admin_counts() -> None:
    ordered = [
        _chat(
            "SISC <> A",
            [
                _msg(1, 555, "question", 9),
                _msg(2, ELMER, "answer", 10),
            ],
        )
    ]
    assert find_unanswered_by_chat(ordered) == []


def test_chats_are_independent() -> None:
    ordered = [
        _chat("SISC <> A", [_msg(1, 555, "hanging in A", 9)]),
        _chat(
            "SISC <> B",
            [_msg(2, 555, "q", 9), _msg(3, GORDON, "a", 10)],
        ),
    ]
    items = find_unanswered_by_chat(ordered)
    assert [i.excerpt for i in items] == ["hanging in A"]
    assert items[0].chat_title == "SISC <> A"


def test_no_sender_id_treated_as_partner() -> None:
    ordered = [_chat("SISC <> A", [_msg(1, None, "anonymous ask", 9)])]
    items = find_unanswered_by_chat(ordered)
    assert len(items) == 1
    assert items[0].sender == "Alice"


def test_media_only_message_uses_media_marker() -> None:
    ordered = [
        _chat(
            "SISC <> A",
            [_msg(1, 555, None, 9, media_type="PHOTO")],
        )
    ]
    items = find_unanswered_by_chat(ordered)
    assert items[0].excerpt == "[media]"


def test_caption_used_when_no_text() -> None:
    ordered = [
        _chat(
            "SISC <> A",
            [_msg(1, 555, None, 9, caption="look at this", media_type="PHOTO")],
        )
    ]
    items = find_unanswered_by_chat(ordered)
    assert items[0].excerpt == "look at this"


def test_service_messages_skipped() -> None:
    ordered = [_chat("SISC <> A", [_msg(1, 555, None, 9)])]
    assert find_unanswered_by_chat(ordered) == []


def test_long_excerpt_truncated(monkeypatch) -> None:
    monkeypatch.setattr(ua, "SENTINEL_MAX_MSG_CHARS", 10)
    ordered = [_chat("SISC <> A", [_msg(1, 555, "x" * 100, 9)])]
    items = find_unanswered_by_chat(ordered)
    assert items[0].excerpt == "x" * 10 + "…"
