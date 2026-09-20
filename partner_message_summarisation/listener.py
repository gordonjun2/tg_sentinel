"""Pyrogram user-account listener: live ingestion + bounded catch-up.

Applies the repo-standard ``utils.get_peer_type`` monkey-patch (same as
``bot.py:60-78`` and ``gmail_calendar/telegram_notifier.py``), builds a
**user-account** client (no bot token — bots often can't see partner-group
history), serializes messages to ``tg_messages`` rows, and recovers gaps
after downtime with a bounded per-chat history backfill.

Failed inserts (transient DB errors) are queued in a bounded in-memory
deque and drained by ``retry_loop`` every 60 s; anything still queued at
crash time is recovered by the next startup's catch-up.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from pyrogram import Client, filters
from pyrogram import utils
from pyrogram.enums import MessageMediaType

from .config import (
    SENTINEL_MAX_CATCHUP_PER_CHAT,
    SISC_GROUP_REGEX,
    TELEGRAM_API_KEY,
    TELEGRAM_HASH,
)
from .db import PartnerMessageSummarisationDB
from .groups import title_matches

logger = logging.getLogger(__name__)

RETRY_DEQUE_MAX = 1000
RETRY_DRAIN_INTERVAL_SECONDS = 60

# [Pyrogram] Monkey patch — repo-standard (bot.py, gmail_calendar).
def _get_peer_type(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    if peer_id_str.startswith("-100"):
        return "channel"
    return "chat"


utils.get_peer_type = _get_peer_type


def make_user_client(name: str = None) -> Client:  # type: ignore[assignment]
    """User-account client for reading partner groups (one-time login)."""
    from .config import SENTINEL_SESSION_NAME

    return Client(
        name or SENTINEL_SESSION_NAME,
        api_id=int(TELEGRAM_API_KEY),
        api_hash=TELEGRAM_HASH,
    )


def _sender_fields(message: Any) -> dict:
    """Sender identity with fallbacks for anonymous admins/channel posts."""
    out = {
        "sender_user_id": None,
        "sender_username": None,
        "sender_display_name": None,
    }
    user = getattr(message, "from_user", None)
    if user is not None:
        out["sender_user_id"] = user.id
        out["sender_username"] = user.username
        name = " ".join(
            part for part in (user.first_name, user.last_name) if part
        ).strip()
        out["sender_display_name"] = name or None
        return out
    # anonymous admin / channel post fallbacks
    sender_chat = getattr(message, "sender_chat", None)
    if sender_chat is not None:
        out["sender_display_name"] = getattr(sender_chat, "title", None)
    signature = getattr(message, "author_signature", None)
    if signature and not out["sender_display_name"]:
        out["sender_display_name"] = signature
    return out


def _forward_fields(message: Any) -> dict:
    out = {
        "is_forward": False,
        "forward_from_name": None,
        "forward_from_chat_id": None,
        "forward_from_chat_title": None,
        "forward_date": None,
    }
    if getattr(message, "forward_date", None) is None:
        return out
    out["is_forward"] = True
    out["forward_date"] = message.forward_date
    fwd_user = getattr(message, "forward_from", None)
    if fwd_user is not None:
        name = " ".join(
            part for part in (fwd_user.first_name, fwd_user.last_name) if part
        ).strip()
        out["forward_from_name"] = name or fwd_user.username
    fwd_chat = getattr(message, "forward_from_chat", None)
    if fwd_chat is not None:
        out["forward_from_chat_id"] = fwd_chat.id
        out["forward_from_chat_title"] = getattr(fwd_chat, "title", None)
    return out


def _media_fields(message: Any) -> dict:
    out = {"media_type": None, "media_file_id": None, "media_file_name": None}
    media = getattr(message, "media", None)
    if media is None:
        return out
    out["media_type"] = getattr(media, "name", None) or str(media)
    try:
        obj = getattr(message, _MEDIA_ATTRS.get(media, ""), None)
        if obj is not None:
            out["media_file_id"] = getattr(obj, "file_id", None)
            out["media_file_name"] = getattr(obj, "file_name", None)
    except Exception:  # noqa: BLE001 — media extraction is best-effort
        pass
    return out


_MEDIA_ATTRS = {
    MessageMediaType.PHOTO: "photo",
    MessageMediaType.VIDEO: "video",
    MessageMediaType.DOCUMENT: "document",
    MessageMediaType.AUDIO: "audio",
    MessageMediaType.VOICE: "voice",
    MessageMediaType.VIDEO_NOTE: "video_note",
    MessageMediaType.ANIMATION: "animation",
    MessageMediaType.STICKER: "sticker",
}


def serialize_message(message: Any, chat_title: str | None = None) -> dict:
    """Map a Pyrogram Message to a ``tg_messages`` row dict."""
    chat = message.chat
    data = {
        "chat_id": chat.id if chat else None,
        "chat_title": chat_title or (chat.title if chat else None),
        "message_id": message.id,
        "message_text": message.text or message.caption or None,
        "caption": message.caption or None,
        "message_date": message.date,
        "reply_to_message_id": message.reply_to_message_id,
    }
    data.update(_sender_fields(message))
    data.update(_forward_fields(message))
    data.update(_media_fields(message))
    return data


class IngestQueue:
    """Bounded in-memory retry deque for failed inserts (§7.2, risk R4)."""

    def __init__(self, maxlen: int = RETRY_DEQUE_MAX) -> None:
        self._deque: deque = deque(maxlen=maxlen)

    def push(self, row: dict) -> None:
        if len(self._deque) == self._deque.maxlen:
            logger.warning("Insert retry queue full — dropping oldest row")
        self._deque.append(row)

    def __len__(self) -> int:
        return len(self._deque)

    async def drain(self, db: PartnerMessageSummarisationDB) -> int:
        """Attempt every queued insert; unfailed rows are re-queued."""
        remaining: deque = deque(maxlen=RETRY_DEQUE_MAX)
        inserted = 0
        while self._deque:
            row = self._deque.popleft()
            try:
                if await db.insert_message(row):
                    inserted += 1
            except Exception as exc:  # noqa: BLE001 — keep draining others
                logger.warning("Retry insert failed for message %s: %s",
                               row.get("message_id"), exc)
                remaining.append(row)
        self._deque = remaining
        return inserted


async def retry_loop(db: PartnerMessageSummarisationDB, queue: IngestQueue) -> None:
    """Drain the failed-insert queue every RETRY_DRAIN_INTERVAL_SECONDS."""
    while True:
        await asyncio.sleep(RETRY_DRAIN_INTERVAL_SECONDS)
        if len(queue):
            try:
                inserted = await queue.drain(db)
                if inserted:
                    logger.info("Retry queue drained: %d inserted", inserted)
            except Exception:  # noqa: BLE001 — loop must survive
                logger.exception("Retry drain failed")


def register_handlers(
    app: Client, db: PartnerMessageSummarisationDB, target_ids: list[int], queue: IngestQueue
) -> None:
    """Attach the live on_message handler for the discovered chat IDs."""

    @app.on_message(filters.chat(target_ids) & ~filters.service)
    async def on_group_message(client: Client, message: Any) -> None:
        if not title_matches(
            getattr(message.chat, "title", None), SISC_GROUP_REGEX
        ):
            return  # renamed mid-run → ignored until next restart re-discovers
        row = serialize_message(message)
        try:
            is_new = await db.insert_message(row)
            logger.info(
                "Ingested %s/%s (%s)",
                row["chat_id"], row["message_id"], "new" if is_new else "dup",
            )
            await db.bump_chat_last_seen(row["chat_id"])
        except Exception as exc:  # noqa: BLE001 — retry queue keeps it
            logger.warning(
                "Insert failed for %s/%s: %s — queued for retry",
                row.get("chat_id"), row.get("message_id"), exc,
            )
            queue.push(row)


async def catchup_chat(
    client: Any, db: PartnerMessageSummarisationDB, chat: Any,
    limit: int = SENTINEL_MAX_CATCHUP_PER_CHAT,
) -> int:
    """Bounded backfill of messages newer than the last stored one."""
    last = await db.get_last_collected_message_id(chat.id)
    if last is None:
        return 0  # first run for this chat: history starts from "now"
    count = 0
    oldest_seen = None
    async for msg in client.get_chat_history(chat.id, limit=limit):
        if msg.id <= last:
            oldest_seen = msg.id
            break
        row = serialize_message(msg, chat_title=chat.title)
        try:
            await db.insert_message(row)
            count += 1
        except Exception as exc:  # noqa: BLE001 — best-effort backfill
            logger.warning("Catch-up insert failed for %s/%s: %s",
                           chat.id, msg.id, exc)
        oldest_seen = msg.id
    if oldest_seen is not None and oldest_seen > last:
        logger.warning(
            "Backfill exceeded limit for chat %s: oldest fetched %s > last "
            "stored %s — messages in between permanently missed",
            chat.id, oldest_seen, last,
        )
    if count:
        logger.info("Catch-up ingested %d message(s) for chat %s", count, chat.id)
    return count


async def catchup_all(client: Any, db: PartnerMessageSummarisationDB, targets: list[Any]) -> int:
    total = 0
    for chat in targets:
        try:
            total += await catchup_chat(client, db, chat)
        except Exception:  # noqa: BLE001 — one chat failing must not stop others
            logger.exception("Catch-up failed for chat %s", chat.id)
    return total
