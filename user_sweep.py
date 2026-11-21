"""New-user sweep: keeps `group_members` in sync with the target groups and
sends the welcome message to anyone not yet welcomed.

The Bot API cannot list group members, and members added directly or via an
invite link never touch the survey flow, so we enumerate each group with a
Pyrogram bot client (the bot must be an admin) and upsert every member.
Realtime join events (see bot.py) insert rows immediately; this sweep is the
safety net that catches everyone else, and the welcome rides on it: any row
with ``welcomed = 0`` gets welcomed in a per-group batch.

Run once by hand (e.g. to backfill)::

    python user_sweep.py            # sweep + welcome new members
    python user_sweep.py --silent   # sweep only, mark everyone as welcomed
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Awaitable, Callable, Optional

from pyrogram import Client, utils
from pyrogram.errors import FloodWait

from config import (
    BOT_TOKEN,
    TARGET_GROUP_ID,
    TARGET_SUB_GROUP_ID,
    TELEGRAM_API_KEY,
    TELEGRAM_HASH,
    USER_SWEEP_INTERVAL_MINUTES,
    WELCOME_MESSAGES,
)
from database import db as default_db

logger = logging.getLogger(__name__)

TARGET_GROUP_IDS = (TARGET_GROUP_ID, TARGET_SUB_GROUP_ID)


def get_peer_type(peer_id: int) -> str:
    """Pyrogram's default peer-type guess breaks on newer Telegram ID ranges."""
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"


utils.get_peer_type = get_peer_type


def build_welcome_names(members: list) -> str:
    """Markdown mention list ("@a, @b, and [Name](tg://user?id=1)") for people
    exposing ``username``, ``id`` and ``full_name``."""

    def esc(text: str) -> str:
        return (
            text.replace("_", "\\_")
            .replace("*", "\\*")
            .replace("[", "\\[")
            .replace("`", "\\`")
        )

    seen = set()
    mentions = []
    for u in members:
        key = u.username or u.id
        if key in seen:
            continue
        seen.add(key)
        if u.username:
            mentions.append(f"@{esc(u.username)}")
        else:
            mentions.append(f"[{esc(u.full_name)}](tg://user?id={u.id})")

    if not mentions:
        return ""
    if len(mentions) == 1:
        return mentions[0]
    if len(mentions) == 2:
        return f"{mentions[0]} and {mentions[1]}"
    return ", ".join(mentions[:-1]) + f", and {mentions[-1]}"


async def enumerate_group_members(client: Client, group_id: int) -> list:
    """Every current member of a group as Pyrogram ChatMember objects.

    Raises on any failure so the caller never treats a partial listing as the
    full membership."""
    for attempt in range(3):
        try:
            return [m async for m in client.get_chat_members(group_id)]
        except FloodWait as e:
            if attempt == 2:
                raise
            await asyncio.sleep(e.value + 1)
    return []


def record_members(database, group_id: int, members: list, silent: bool) -> int:
    """Upsert enumerated members and mark absentees as left. Returns the
    number of members new to the group (or rejoining)."""
    new = 0
    present = set()
    for m in members:
        user = m.user
        if user is None or user.is_deleted:
            continue
        present.add(user.id)
        if database.upsert_group_member(
            group_id,
            user.id,
            user.username,
            user.first_name,
            user.last_name,
            is_bot=bool(user.is_bot),
            status=str(getattr(m.status, "value", m.status)),
            welcomed=silent,
        ):
            new += 1

    # Never wipe a group because of an empty listing.
    if present:
        left = database.mark_absent_members_left(group_id, present)
        if left:
            logger.info(f"User sweep: {left} member(s) left {group_id}")
    return new


async def send_welcomes(bot, database, group_id: int) -> int:
    """Welcome every unwelcomed member of a group in one message. Members are
    only marked welcomed once the message was actually sent."""
    pending = await asyncio.to_thread(database.get_unwelcomed_members, group_id)
    if not pending:
        return 0

    people = [
        SimpleNamespace(
            id=r["user_id"],
            username=r["username"],
            full_name=" ".join(
                p for p in (r["first_name"], r["last_name"]) if p
            ) or str(r["user_id"]),
        )
        for r in pending
    ]
    names = build_welcome_names(people)
    if not names:
        return 0

    text = random.choice(WELCOME_MESSAGES).format(names=names)
    try:
        await bot.send_message(chat_id=group_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to send welcome to {group_id}: {e}")
        return 0
    await asyncio.to_thread(
        database.mark_members_welcomed, group_id, [r["user_id"] for r in pending]
    )
    return len(pending)


async def sweep_once(
    get_client: Callable[[], Awaitable[Client]],
    bot,
    database=default_db.users,
    silent: bool = False,
) -> None:
    """One pass over every target group: sync members, then welcome."""
    try:
        client = await get_client()
    except Exception as e:
        logger.error(f"User sweep: could not start Pyrogram client: {e}")
        client = None

    for group_id in TARGET_GROUP_IDS:
        # A group we've never recorded is a backfill: record without welcoming
        # hundreds of existing members.
        group_silent = silent or (
            await asyncio.to_thread(database.count_group_members, group_id) == 0
        )
        if client is not None:
            try:
                members = await enumerate_group_members(client, group_id)
                new = await asyncio.to_thread(
                    record_members, database, group_id, members, group_silent
                )
                logger.info(
                    f"User sweep {group_id}: {len(members)} members, {new} new"
                    + (" (silent)" if group_silent else "")
                )
            except Exception as e:
                logger.error(f"User sweep failed for {group_id}: {e}")
        # Realtime join events may have left unwelcomed rows even if the
        # enumeration above failed, so always try the welcome.
        if not silent:
            await send_welcomes(bot, database, group_id)


async def user_sweep_loop(
    get_client: Callable[[], Awaitable[Client]],
    bot,
) -> None:
    """Run the sweep on a wall-clock aligned interval (same cadence the old
    welcome batching used)."""
    interval = USER_SWEEP_INTERVAL_MINUTES
    try:
        while True:
            now = datetime.now(timezone.utc)
            wait = (interval - 1 - now.minute % interval) * 60 + (60 - now.second)
            await asyncio.sleep(wait)
            try:
                await sweep_once(get_client, bot)
            except Exception:
                logger.exception("User sweep iteration failed")
    except asyncio.CancelledError:
        pass


async def _main(silent: bool) -> None:
    import telegram

    async with telegram.Bot(BOT_TOKEN) as bot, Client(
        "user_sweep_cli",
        api_id=TELEGRAM_API_KEY,
        api_hash=TELEGRAM_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,
    ) as client:

        async def get_client() -> Client:
            return client

        await sweep_once(get_client, bot, silent=silent)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--silent", action="store_true", help="record members without sending welcomes"
    )
    asyncio.run(_main(parser.parse_args().silent))
