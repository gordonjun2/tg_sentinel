"""Partner-group discovery by title regex.

Replaces cryptopulse's hardcoded ``CHAT_ID_LIST`` with a title-based scan:
at startup the user account's dialogs are matched against
``SISC_GROUP_REGEX`` and every hit is upserted into ``tg_chats``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pyrogram.enums import ChatType

from .config import SISC_GROUP_REGEX
from .db import PartnerMessageSummarisationDB

logger = logging.getLogger(__name__)

_MONITORABLE_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL)


def title_matches(title: str | None, pattern: str = SISC_GROUP_REGEX) -> bool:
    """True when a chat title matches the partner-group regex."""
    if not title:
        return False
    return bool(re.match(pattern, title))


async def discover_target_chats(user_client: Any, db: PartnerMessageSummarisationDB) -> list[Any]:
    """Scan dialogs and upsert every title-matching chat. Returns targets."""
    targets = []
    async for dialog in user_client.get_dialogs():
        chat = dialog.chat
        if (
            chat is not None
            and getattr(chat, "type", None) in _MONITORABLE_TYPES
            and title_matches(getattr(chat, "title", None))
        ):
            targets.append(chat)
            await db.upsert_chat(chat.id, chat.title, chat.type.value)
            logger.info("Monitoring %s (%s)", chat.title, chat.id)
    logger.info("Discovered %d partner group(s)", len(targets))
    return targets
