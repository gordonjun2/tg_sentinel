"""Bot-token Pyrogram client  + summary delivery with per-part retry.

Mirrors ``gmail_calendar/telegram_notifier.make_client`` (same BOT_TOKEN,
api_id/api_hash, repo-standard monkey-patch) but is used purely for
MTProto ``send_message`` — no update polling, so it does not conflict
with the main bot's ``getUpdates`` loop.
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import Client, enums, errors, utils

from .config import ADMIN_GROUP_ID, BOT_TOKEN, SENTINEL_DRY_RUN, TELEGRAM_API_KEY, TELEGRAM_HASH

logger = logging.getLogger(__name__)

MAX_PART_ATTEMPTS = 3


# [Pyrogram] Monkey patch — repo-standard (bot.py, telegram_notifier.py).
def _get_peer_type(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    if peer_id_str.startswith("-100"):
        return "channel"
    return "chat"


utils.get_peer_type = _get_peer_type


def make_bot_client(name: str = None) -> Client:  # type: ignore[assignment]
    """Bot-identity MTProto client (send-only)."""
    from .config import SENTINEL_BOT_SESSION_NAME

    return Client(
        name or SENTINEL_BOT_SESSION_NAME,
        api_id=int(TELEGRAM_API_KEY),
        api_hash=TELEGRAM_HASH,
        bot_token=BOT_TOKEN,
    )


async def deliver_report(bot_client: Client, parts: list[str]) -> bool:
    """Send report parts in order to the admin group (HTML parse mode).

    Per-part retry with exponential backoff; a ``FloodWait`` waits at
    least the requested period first. Returns False as soon as any part
    exhausts its attempts (caller leaves the batch pending).
    """
    if SENTINEL_DRY_RUN:
        logger.info(
            "DRY RUN — report (%d part(s)):\n%s", len(parts), "".join(parts)
        )
        return True

    sent: list[int] = []
    for i, part in enumerate(parts):
        for attempt in range(MAX_PART_ATTEMPTS):
            try:
                msg = await bot_client.send_message(
                    ADMIN_GROUP_ID, part, parse_mode=enums.ParseMode.HTML
                )
                sent.append(msg.id)
                break
            except errors.FloodWait as exc:
                logger.warning(
                    "FloodWait %ss before part %d/%d", exc.value, i + 1, len(parts)
                )
                await asyncio.sleep(exc.value + 1)
            except Exception as exc:  # noqa: BLE001 — retry, fail after budget
                logger.warning(
                    "part %d/%d attempt %d failed: %s",
                    i + 1, len(parts), attempt + 1, exc,
                )
                await asyncio.sleep(2**attempt * 4)
        else:
            logger.error(
                "Delivery failed at part %d/%d after %d attempts",
                i + 1, len(parts), MAX_PART_ATTEMPTS,
            )
            return False
    logger.info("Delivered %d part(s) (%s)", len(parts), sent)
    return True
