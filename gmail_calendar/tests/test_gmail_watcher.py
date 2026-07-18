"""Tests for gmail_watcher: noise filter, message parsing, history.list walk."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gmail_calendar.gmail_watcher import (
    GmailWatcher,
    _b64url_decode,
    _decode_body,
    _extract_headers,
    _is_obvious_noise,
    _parse_message,
)


# ---- noise filter ----------------------------------------------------------


@pytest.mark.parametrize(
    "from_addr,subject,expected",
    [
        ("no-reply@accounts.google.com", "Security alert", True),
        ("noreply@github.com", "PR merge", True),
        ("mailer-daemon@example.com", "Delivery failed", True),
        ("newsletter@startup.co", "Weekly digest", True),
        ("alice@company.com", "Re: contract review", False),
        ("support@stripe.com", "Action required: payout failed", False),
    ],
)
def test_is_obvious_noise(from_addr: str, subject: str, expected: bool) -> None:
    assert _is_obvious_noise(from_addr, subject) is expected


# ---- message parsing -------------------------------------------------------


def test_b64url_decode_handles_missing_padding() -> None:
    # "Hello" base64url-encoded is "SGVsbG8" (no padding)
    assert _b64url_decode("SGVsbG8") == "Hello"


def test_decode_body_prefers_text_plain() -> None:
    text_part = {
        "mimeType": "text/plain",
        "body": {"data": _b64url("Hi there")},
    }
    html_part = {
        "mimeType": "text/html",
        "body": {"data": _b64url("<p>Ignore me</p>")},
    }
    payload = {"mimeType": "multipart/alternative", "parts": [text_part, html_part]}
    assert _decode_body(payload) == "Hi there"


def test_decode_body_falls_back_to_top_level() -> None:
    payload = {"mimeType": "text/plain", "body": {"data": _b64url("Top level")}}
    assert _decode_body(payload) == "Top level"


def test_decode_body_empty() -> None:
    assert _decode_body(None) == ""
    assert _decode_body({}) == ""


def test_extract_headers_lowercases_keys() -> None:
    payload = {
        "headers": [
            {"name": "From", "value": "Alice <a@b.com>"},
            {"name": "Subject", "value": "Hi"},
        ]
    }
    headers = _extract_headers(payload)
    assert headers["from"] == "Alice <a@b.com>"
    assert headers["subject"] == "Hi"


def test_parse_message_extracts_core_fields() -> None:
    raw = {
        "id": "msg-1",
        "threadId": "thr-1",
        "snippet": "First 100 chars",
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "Subject", "value": "Lunch tomorrow?"},
                {"name": "Date", "value": "Fri, 18 Jul 2026 12:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64url("Sure, 12:30 works.")},
        },
    }
    parsed = _parse_message(raw)
    assert parsed["message_id"] == "msg-1"
    assert parsed["thread_id"] == "thr-1"
    assert parsed["from"] == "alice@example.com"
    assert parsed["subject"] == "Lunch tomorrow?"
    assert parsed["snippet"] == "First 100 chars"
    assert parsed["body"] == "Sure, 12:30 works."


# ---- history.list walk ----------------------------------------------------


def _http_error(status: int):
    """Build a minimal stand-in for googleapiclient.errors.HttpError."""
    from googleapiclient.errors import HttpError

    resp = MagicMock(status=status, status_code=status, reason=f"HTTP {status}")
    return HttpError(resp, b"{}")


def test_list_new_message_ids_paginates_and_dedups() -> None:
    service = MagicMock()
    list_req = MagicMock()
    list_req.execute.side_effect = [
        {
            "history": [
                {"messagesAdded": [{"message": {"id": "m1"}}, {"message": {"id": "m2"}}]},
            ],
            "nextPageToken": "page2",
        },
        {
            "history": [
                {"messagesAdded": [{"message": {"id": "m2"}}, {"message": {"id": "m3"}}]},
            ],
        },
    ]
    service.users.return_value.history.return_value.list.return_value = list_req

    watcher = _make_watcher(service)
    new_ids = watcher._list_new_message_ids("100")
    assert new_ids == ["m1", "m2", "m3"]


def test_list_new_message_ids_raises_404_for_full_sync_path() -> None:
    """404 from history.list must propagate so the caller can fall back to full sync."""
    from googleapiclient.errors import HttpError

    service = MagicMock()
    list_req = MagicMock()
    list_req.execute.side_effect = _http_error(404)
    service.users.return_value.history.return_value.list.return_value = list_req

    watcher = _make_watcher(service)
    with pytest.raises(HttpError) as exc_info:
        watcher._list_new_message_ids("100")
    assert exc_info.value.status_code == 404


def test_full_sync_walks_all_pages() -> None:
    service = MagicMock()
    list_req = MagicMock()
    list_req.execute.side_effect = [
        {"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "p2"},
        {"messages": [{"id": "m3"}]},
    ]
    service.users.return_value.messages.return_value.list.return_value = list_req

    watcher = _make_watcher(service)
    ids = watcher._full_sync()
    assert ids == ["m1", "m2", "m3"]


# ---- helpers --------------------------------------------------------------


def _b64url(s: str) -> str:
    import base64

    return base64.urlsafe_b64encode(s.encode()).rstrip(b"=").decode()


def _make_watcher(service) -> GmailWatcher:
    """Construct a GmailWatcher with project-id pre-set via env-patching."""
    import gmail_calendar.gmail_watcher as gw

    # Patch the module-level config check so we don't need real GCP creds.
    saved = gw.GOOGLE_GMAIL_CAL_PROJECT_ID
    gw.GOOGLE_GMAIL_CAL_PROJECT_ID = "test-project"
    db = MagicMock()
    db.get_gmail_state.return_value = None
    try:
        watcher = GmailWatcher.__new__(GmailWatcher)
        watcher.gmail = service
        watcher.db = db
        # Skip __init__ which would try to create a real subscriber client.
        return watcher
    finally:
        gw.GOOGLE_GMAIL_CAL_PROJECT_ID = saved
