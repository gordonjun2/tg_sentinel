import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from ai_enrichment import (
    MessageBuffer,
    classify_worthiness,
    compute_content_hash,
    extract_urls,
    generate_enrichment_reply,
    is_semantically_duplicate,
    serialize_messages,
)


def test_compute_content_hash_deterministic():
    msgs = [{"message_id": 1}, {"message_id": 2}]
    h1 = compute_content_hash(msgs)
    h2 = compute_content_hash(msgs)
    assert h1 == h2
    assert len(h1) == 64


def test_compute_content_hash_different():
    msgs_a = [{"message_id": 1}]
    msgs_b = [{"message_id": 2}]
    assert compute_content_hash(msgs_a) != compute_content_hash(msgs_b)


def test_extract_urls():
    text = "Check this out https://example.com/article and http://test.io"
    urls = extract_urls(text)
    assert len(urls) == 2
    assert "https://example.com/article" in urls


def test_extract_urls_none():
    assert extract_urls("no urls here") == []


def test_serialize_messages():
    msgs = [
        {"username": "alice", "text": "hello"},
        {"first_name": "Bob", "text": "hi"},
    ]
    result = serialize_messages(msgs)
    assert "alice: hello" in result
    assert "Bob: hi" in result


def test_serialize_messages_skips_empty():
    msgs = [
        {"username": "alice", "text": "hello"},
        {"username": "bob", "text": ""},
    ]
    result = serialize_messages(msgs)
    assert "alice: hello" in result
    assert "bob" not in result


def test_message_buffer_add_and_get():
    buf = MessageBuffer()
    buf.add_message({"message_id": 1, "text": "a"})
    buf.add_message({"message_id": 2, "text": "b"})
    unprocessed = buf.get_unprocessed()
    assert len(unprocessed) == 2


def test_message_buffer_mark_processed():
    buf = MessageBuffer()
    buf.add_message({"message_id": 1, "text": "a"})
    buf.add_message({"message_id": 2, "text": "b"})
    buf.add_message({"message_id": 3, "text": "c"})
    buf.mark_processed_up_to(2)
    unprocessed = buf.get_unprocessed()
    assert len(unprocessed) == 1
    assert unprocessed[0]["message_id"] == 3


def test_message_buffer_context_window_limit():
    buf = MessageBuffer()
    for i in range(1, 15):
        buf.add_message({"message_id": i, "text": f"msg{i}"})

    with patch("ai_enrichment.AI_CONTEXT_WINDOW", 5):
        window = buf.get_context_window()
        assert len(window) == 5
        assert window[0]["message_id"] == 10


def test_message_buffer_clear_old():
    buf = MessageBuffer()
    for i in range(1, 250):
        buf.add_message({"message_id": i, "text": f"m{i}"})
    buf.clear_old_messages(keep_last=50)
    assert len(buf._messages) <= 150


def test_is_semantically_duplicate_exact():
    recent = [("AI chips", "hash1", "2025-01-01")]
    assert is_semantically_duplicate("AI chips", recent) is True


def test_is_semantically_duplicate_different():
    recent = [("AI chips", "hash1", "2025-01-01")]
    assert is_semantically_duplicate("restaurant reviews", recent) is False


def test_is_semantically_duplicate_high_overlap():
    recent = [("AI semiconductor demand resilience", "hash1", "2025-01-01")]
    assert is_semantically_duplicate("AI semiconductor demand", recent) is True


def test_is_semantically_duplicate_empty():
    assert is_semantically_duplicate("topic", []) is False
    assert is_semantically_duplicate("", [("topic", "h", "d")]) is False


@patch("ai_enrichment.gemini_client")
def test_classify_worthiness_worthy(mock_client):
    mock_response = MagicMock()
    mock_response.text = '{"should_reply": true, "confidence": 0.9, "reason": "technical_discussion", "topic": "AI chips", "search_queries": ["AI chip demand"]}'
    mock_client.models.generate_content.return_value = mock_response

    result = classify_worthiness("User: TSMC revenue is up")
    assert result["should_reply"] is True
    assert result["confidence"] > 0.5


@patch("ai_enrichment.gemini_client")
def test_classify_worthiness_not_worthy(mock_client):
    mock_response = MagicMock()
    mock_response.text = '{"should_reply": false, "confidence": 0.2, "reason": "not_worthy", "topic": "", "search_queries": []}'
    mock_client.models.generate_content.return_value = mock_response

    result = classify_worthiness("User: haha nice one")
    assert result["should_reply"] is False


@patch("ai_enrichment.gemini_client")
def test_classify_worthiness_malformed_response(mock_client):
    mock_response = MagicMock()
    mock_response.text = "not json at all"
    mock_client.models.generate_content.return_value = mock_response

    result = classify_worthiness("anything")
    assert result["should_reply"] is False
    assert result["reason"] == "classification_error"


@patch("ai_enrichment.gemini_client")
def test_generate_enrichment_reply_valid(mock_client):
    mock_response = MagicMock()
    mock_response.text = "TSMC reported strong earnings driven by AI demand."
    mock_client.models.generate_content.return_value = mock_response

    result = generate_enrichment_reply("User: TSMC stock up", "TSMC earnings report...")
    assert result is not None
    assert "TSMC" in result


@patch("ai_enrichment.gemini_client")
def test_generate_enrichment_reply_no_reply(mock_client):
    mock_response = MagicMock()
    mock_response.text = "NO_REPLY"
    mock_client.models.generate_content.return_value = mock_response

    result = generate_enrichment_reply("User: lol", "nothing useful")
    assert result is None


def test_database_enrichment_tables():
    from database import Database

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        test_db = Database(db_path)
        state = test_db.get_enrichment_state()
        assert state["last_processed_message_id"] == 0
        assert state["is_enabled"] is True

        test_db.update_enrichment_state(last_processed_message_id=42)
        state = test_db.get_enrichment_state()
        assert state["last_processed_message_id"] == 42

        test_db.update_enrichment_state(is_enabled=False)
        state = test_db.get_enrichment_state()
        assert state["is_enabled"] is False

        assert test_db.is_content_hash_processed("abc123") is False

        test_db.record_processed_window([1, 2, 3], "abc123", True, "technical")
        assert test_db.is_content_hash_processed("abc123") is True

        test_db.record_enrichment_reply([1, 2], "def456", "AI chips", 100)
        topics = test_db.get_recent_reply_topics()
        assert len(topics) == 1
        assert topics[0][0] == "AI chips"
    finally:
        os.unlink(db_path)
