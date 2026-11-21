#!/usr/bin/env python3
"""One-shot Pyrogram tool: DM speed-networking guests about the event.

Reads the Luma guests CSV, keeps every guest who answered "For sure" under
"Sign up for our speed networking session?", then — from a **user account**
(one-time interactive login on first run) — sends each guest a private message.
No one is added to any group.

Usage::

    # preview: parses the CSV, no Telegram login, no messages sent
    python mass_group_invite.py

    # send to the first 5 as a test batch
    python mass_group_invite.py --send --limit 5

    # full run
    python mass_group_invite.py --send

The script sleeps between users, respects FloodWait (up to --max-flood-wait
seconds), and is safe to re-run with --resume-from <previous report>. A CSV
report of every action is written next to this script afterwards.

Note: the session file (default ``group_inviter.session`` in the project
root) grants full account access — keep it secret and out of git.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import random
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from pyrogram import Client, utils
from pyrogram.errors import (
    FloodWait,
    PeerIdInvalid,
    RPCError,
    UsernameInvalid,
    UsernameNotOccupied,
)
from pyrogram.types import User

from config import TELEGRAM_API_KEY, TELEGRAM_HASH

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("mass_group_invite")


# [Pyrogram] Monkey Patch — repo-standard (bot.py, luma_reminder.py, listener.py)
def get_peer_type(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"


utils.get_peer_type = get_peer_type

_PROJECT_ROOT = Path(__file__).resolve().parent

DEFAULT_CSV = _PROJECT_ROOT / (
    "Singapore AI Communities Unite_ The Future of Work - Guests - "
    "2026-11-20-03-21-38.csv"
)
DEFAULT_INVITE_LINK = "https://t.me/+8K0S6_YwwGg4YzE1"
EVENT_URL = "https://luma.com/j74qk3fy"

SIGNUP_COLUMN = "Sign up for our speed networking session?"
SIGNUP_ANSWER = "For sure"
HANDLE_COLUMN = "Telegram Handle"

# Telegram username rules: 5-32 chars, starts with a letter, letters/digits/_
HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_PLACEHOLDERS = {"", "-", "na", "n/a", "n.a.", "none", "nil", "null", "yes", "no"}


def normalize_handle(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return (username, None) on success or (None, reason) for one CSV cell."""
    if raw is None:
        return None, "empty cell"
    h = raw.strip().strip("'").strip()
    h = re.sub(r"^(https?://)?t\.me/", "", h, flags=re.IGNORECASE).strip("'").strip()
    h = h.lstrip("@").strip()

    if h.lower() in _PLACEHOLDERS:
        return None, "placeholder value"
    if " " in h:
        return None, "contains spaces (free-text, not a username)"
    if "/" in h:
        return None, "looks like a URL/path, not a username"
    if h.startswith("+"):
        return None, "phone number, not a username"
    if not HANDLE_RE.match(h):
        return None, "invalid username format"
    return h, None


def load_targets(csv_path: Path) -> tuple[list[dict], list[dict], int]:
    """Parse the guests CSV -> (targets, skipped rows, total 'For sure' guests)."""
    targets: list[dict] = []
    skipped: list[dict] = []
    seen: dict[str, str] = {}  # lower(username) -> name of first guest
    total_for_sure = 0

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "?").strip()
            if (row.get(SIGNUP_COLUMN) or "").strip() != SIGNUP_ANSWER:
                continue
            total_for_sure += 1

            raw_handle = row.get(HANDLE_COLUMN)
            username, reason = normalize_handle(raw_handle)
            if username is None:
                skipped.append({"name": name, "raw": (raw_handle or "").strip(), "reason": reason})
                continue

            key = username.lower()
            if key in seen:
                skipped.append(
                    {
                        "name": name,
                        "raw": (raw_handle or "").strip(),
                        "reason": f"duplicate username (same as {seen[key]})",
                    }
                )
                continue
            seen[key] = name

            first_name = (row.get("first_name") or name.split(" ")[0] or "there").strip()
            targets.append({"name": name, "username": username, "first_name": first_name})

    return targets, skipped, total_for_sure


def invite_dm_text(first_name: str, invite_link: str) -> str:
    return (
        f"Hi {first_name}! 👋\n\n"
        f"Thank you for indicating that you are interested in the speed "
        f"networking session at \"Singapore AI Communities Unite: The Future "
        f"of Work\" ({EVENT_URL}). We are glad to have you on board.\n\n"
        f"We would like to gather all the speed networking participants in one "
        f"Telegram group. This way, we can share updates and logistics, and you "
        f"can get to know the other participants before the event. Please join "
        f"us here:\n"
        f"{invite_link}\n\n"
        f"See you at the event! 🎉"
    )


async def with_flood_retry(op: Callable[[], object], max_wait: int, label: str):
    """Run one Telegram operation, waiting out FloodWait as long as it is
    below ``max_wait``; raise FloodAbort if the wait exceeds the cap."""
    while True:
        try:
            return await op()
        except FloodWait as e:
            if e.value > max_wait:
                raise FloodAbort(f"FloodWait of {e.value}s exceeds the {max_wait}s cap")
            logger.warning("FloodWait %ds — waiting, then retrying %s", e.value, label)
            await asyncio.sleep(e.value)


class FloodAbort(Exception):
    """A FloodWait longer than the cap — stop the whole run."""


async def process_one(client: Client, args: argparse.Namespace, target: dict) -> tuple[str, str]:
    """DM one guest. Returns (action, detail)."""
    username = target["username"]
    try:
        user = await with_flood_retry(
            lambda: client.get_users(username), args.max_flood_wait, f"resolve {username}"
        )
    except FloodAbort:
        raise
    except (UsernameNotOccupied, UsernameInvalid):
        return "resolve_failed", "no such Telegram user"
    except RPCError as e:
        return "resolve_failed", f"{type(e).__name__}: {e}"

    text = invite_dm_text(target["first_name"], args.invite_link)

    async def _dm():
        return await client.send_message(user.id, text)

    async def _dm_by_name():
        return await client.send_message(username, text)

    for op in (_dm, _dm_by_name):
        try:
            await with_flood_retry(op, args.max_flood_wait, f"DM {username}")
            return "dmed", ""
        except FloodAbort:
            raise
        except PeerIdInvalid:
            continue  # try the username-string variant
        except RPCError as e:
            return "dm_failed", f"{type(e).__name__}: {e}"

    return "dm_failed", "could not resolve peer for DM"


def load_actioned(report_path: Path) -> set[str]:
    """Usernames already actioned (added/DMed) in a previous report — to skip."""
    done = {"added", "dmed", "already_member", "True"}  # 'True' = pre-fix reports
    actioned: set[str] = set()
    with open(report_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("action") in done and row.get("username"):
                actioned.add(row["username"].strip().lower())
    return actioned


async def run(args: argparse.Namespace) -> None:
    targets, skipped, total_for_sure = load_targets(Path(args.csv))
    if args.resume_from:
        actioned = load_actioned(Path(args.resume_from))
        targets = [t for t in targets if t["username"].lower() not in actioned]
        logger.info("Resume: skipping %d already-actioned guest(s) from %s",
                    len(actioned), Path(args.resume_from).name)
    if args.limit:
        targets = targets[: args.limit]

    mode = "DRY RUN (no messages sent — add --send)" if not args.send else "SEND"
    logger.info("Mode: %s", mode)
    logger.info(
        "'%s' guests: %d total -> %d usable username(s), %d skipped/invalid",
        SIGNUP_ANSWER, total_for_sure, len(targets), len(skipped),
    )
    if not args.send:
        logger.info("Preview of what would be processed:")

    for t in (targets if not args.send else []):
        logger.info("  [process] %-28s (%s)", t["username"], t["name"])
    for s in (skipped if not args.send else []):
        logger.info("  [skip]    %-28s (%s) — %s", s["raw"], s["name"], s["reason"])

    results: list[dict] = []
    counts: dict[str, int] = {}
    abort_reason: Optional[str] = None

    if args.send:
        async with Client(args.session, api_id=TELEGRAM_API_KEY, api_hash=TELEGRAM_HASH) as client:
            me = await client.get_me()
            logger.info(
                "Logged in as %s (@%s) — DMs will come from THIS account.",
                getattr(me, "first_name", "?"), getattr(me, "username", "?"),
            )
            logger.info("Starting in 5s — Ctrl+C now to cancel.")
            await asyncio.sleep(5)

            for i, t in enumerate(targets, 1):
                if abort_reason:
                    results.append(
                        {"name": t["name"], "telegram_raw": t["username"],
                         "username": t["username"], "action": "not_run", "detail": abort_reason}
                    )
                    continue
                try:
                    action, detail = await process_one(client, args, t)
                except FloodAbort as e:
                    abort_reason = str(e)
                    action, detail = "flood_aborted", abort_reason
                    logger.error("%s — stopping. Re-run later; use --resume-from to skip already-DMed guests.", abort_reason)

                counts[action] = counts.get(action, 0) + 1
                results.append(
                    {"name": t["name"], "telegram_raw": t["username"],
                     "username": t["username"], "action": action, "detail": detail}
                )
                logger.info("[%d/%d] %-28s (%s): %s %s", i, len(targets), t["username"], t["name"], action, detail)
                # throttle: base delay plus random jitter so sends aren't evenly spaced
                await asyncio.sleep(args.delay + random.uniform(0, args.delay * 0.5))

    # ---- report CSV -------------------------------------------------------
    report_path = _PROJECT_ROOT / time.strftime("invite_report_%Y%m%d-%H%M%S.csv")
    with open(report_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "telegram_raw", "username", "action", "detail"])
        writer.writeheader()
        writer.writerows(results)
        for s in skipped:
            writer.writerow({"name": s["name"], "telegram_raw": s["raw"],
                             "username": "", "action": "skipped", "detail": s["reason"]})

    logger.info("Summary: %s", counts or "nothing done (dry run)")
    logger.info("Report written: %s", report_path.name)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="DM 'For sure' speed-networking guests about the event.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--csv", default=str(DEFAULT_CSV), help="Luma guests CSV path")
    p.add_argument("--session", default="group_inviter",
                   help="Pyrogram session name (interactive login on first run)")
    p.add_argument("--invite-link", default=DEFAULT_INVITE_LINK,
                   help="Group join link included in the DM")
    p.add_argument("--limit", type=int, default=0,
                   help="Process at most N guests (0 = all) — useful for a test batch")
    p.add_argument("--resume-from", default=None,
                   help="Previous invite_report CSV — skip guests already DMed there")
    p.add_argument("--delay", type=float, default=30.0,
                   help="Base seconds between DMs (+0-50%% random jitter)")
    p.add_argument("--max-flood-wait", type=int, default=600,
                   help="Abort if Telegram asks to wait longer than this (seconds)")
    p.add_argument("--send", action="store_true",
                   help="Actually send DMs (default is a dry run, no login)")
    return p.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        logger.info("Interrupted.")
