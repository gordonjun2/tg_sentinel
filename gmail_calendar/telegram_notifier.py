"""Pyrogram Telegram client + message formatters for the admin group.

Mirrors the auth pattern from ``luma_reminder.py`` (same Bot Token, same
api_id/api_hash, monkey-patch for ``utils.get_peer_type``).
"""

from __future__ import annotations

import logging
from typing import Optional

from pyrogram import Client, utils

from config import (
    ADMIN_GROUP_ID,
    BOT_TOKEN,
    GMAIL_CALENDAR_SESSION_NAME,
    TELEGRAM_API_KEY,
    TELEGRAM_HASH,
)

logger = logging.getLogger(__name__)


# [Pyrogram] Monkey patch — same as bot.py:68-78 and luma_reminder.py:39-49.
# Needed so Peer resolution handles group IDs correctly on the version pinned
# in this repo.
def _get_peer_type(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    if peer_id_str.startswith("-100"):
        return "channel"
    return "chat"


utils.get_peer_type = _get_peer_type


def make_client(name: str = GMAIL_CALENDAR_SESSION_NAME) -> Client:
    """Construct a Pyrogram client configured for this service.

    The session file lives under ``gmail_calendar/data/`` so it does not
    collide with the bot's own session.
    """
    return Client(
        name,
        api_id=TELEGRAM_API_KEY,
        api_hash=TELEGRAM_HASH,
        bot_token=BOT_TOKEN,
    )


# --- formatters -------------------------------------------------------------


def format_email_notification(
    email: dict,
    score: float,
    category: str,
    reasoning: str,
    summary: str,
) -> str:
    """Render the message pushed to the admin group for an important email."""
    score_pct = f"{score * 100:.0f}"
    cat_label = (category or "").upper()
    from_addr = email.get("from") or "?"
    subject = email.get("subject") or "(no subject)"
    message_id = email.get("message_id") or ""
    thread_id = email.get("thread_id") or message_id
    link = (
        f"https://mail.google.com/mail/u/0/#all/{thread_id}"
        if thread_id
        else "(no link)"
    )
    return (
        f"📧 Important Email — score {score_pct}/100 ({cat_label})\n\n"
        f"From: {from_addr}\n"
        f"Subject: {subject}\n\n"
        f"Summary: {summary}\n\n"
        f"Why it matters: {reasoning}\n\n"
        f"🔗 {link}"
    )


def format_meeting_reminder(event: dict) -> str:
    """Render the 30-minutes-ahead reminder for an upcoming meeting."""
    title = event.get("title") or "(untitled event)"
    start_str = event.get("start_time_display") or event.get("start_time_utc", "")
    end_str = event.get("end_time_display") or event.get("end_time_utc", "")
    link = event.get("meeting_link")
    location = event.get("location")

    lines = [
        "📅 Upcoming Meeting (30 minutes)",
        "",
        title,
        "",
        f"🗓 {start_str.split('T')[0] if 'T' in start_str else start_str}",
        f"🕒 {start_str} – {end_str}",
    ]
    if location:
        lines.append(f"📍 {location}")
    if link:
        lines.append("")
        lines.append(f"🔗 {link}")
    return "\n".join(lines)


async def send_to_admin(client: Client, text: str) -> Optional[int]:
    """Send a message to the admin group. Returns the message_id or None."""
    try:
        msg = await client.send_message(chat_id=ADMIN_GROUP_ID, text=text)
        return msg.id
    except Exception as exc:
        logger.error("Failed to send admin message: %s", exc)
        return None
