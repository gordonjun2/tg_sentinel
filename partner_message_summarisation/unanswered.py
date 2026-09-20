"""Detect partner messages the admins have not replied to.

Pure-Python, no LLM: within one summary window, a message is *unanswered*
when its sender is not one of the configured admins and no admin message
follows it in the same chat (any later admin message — direct reply or
plain follow-up — counts as the admins having responded).

Messages with no sender id (rare: anonymous posts) are treated as
partner messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import SENTINEL_ADMINS, SENTINEL_MAX_MSG_CHARS

_ADMIN_IDS = frozenset(SENTINEL_ADMINS)


@dataclass(frozen=True)
class UnansweredMessage:
    chat_title: str
    sender: str
    message_date: datetime
    excerpt: str


def _sender_label(row: dict) -> str:
    name = row.get("sender_display_name") or row.get("sender_username")
    return str(name) if name else "Unknown sender"


def find_unanswered_by_chat(
    ordered: list[tuple[str, list[dict]]],
) -> list[UnansweredMessage]:
    """Scan per-chat message lists (chronological) for hanging messages.

    ``ordered`` mirrors the transcript ordering: ``(chat_title, rows)``
    with rows already sorted by (message_date, message_id).
    """
    unanswered: list[UnansweredMessage] = []
    for chat_title, rows in ordered:
        admin_seen = False
        hanging: list[tuple[dict, str]] = []
        for row in reversed(rows):
            sender_id = row.get("sender_user_id")
            if sender_id is not None and int(sender_id) in _ADMIN_IDS:
                admin_seen = True
                continue
            if admin_seen:
                continue
            body = (
                row.get("message_text") or row.get("caption") or ""
            ).strip().replace("\n", " ")
            if not body and not row.get("media_type"):
                continue  # e.g. service message — nothing hanging
            if len(body) > SENTINEL_MAX_MSG_CHARS:
                body = body[:SENTINEL_MAX_MSG_CHARS] + "…"
            hanging.append((row, body or "[media]"))
        unanswered.extend(
            UnansweredMessage(
                chat_title=chat_title,
                sender=_sender_label(row),
                message_date=row["message_date"],
                excerpt=excerpt,
            )
            for row, excerpt in reversed(hanging)
        )
    return unanswered
