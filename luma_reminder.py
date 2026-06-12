#!/usr/bin/env python3
"""Luma event reminder cron job. Run daily via crontab."""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Optional

from google import genai
from google.genai import types
from openai import OpenAI
from pyrogram import Client, utils

from config import (
    BOT_TOKEN,
    GEMINI_API_KEY,
    GEMINI_ENRICHMENT_MODEL,
    OPENAI_API_KEY,
    OPENAI_ENRICHMENT_MODEL,
    TARGET_GROUP_ID,
    TELEGRAM_API_KEY,
    TELEGRAM_HASH,
)
from luma_scraper import SGT, get_event_details

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# [Pyrogram] Monkey Patch — same as bot.py:68-78
def get_peer_type(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"


utils.get_peer_type = get_peer_type

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

REMINDER_SYSTEM_PROMPT = """\
You are the hype person for a tech/AI community (SISC).
Write a fun, punchy Telegram reminder for an upcoming event.
Rules:
- Keep it under 100 words
- Use 2-3 relevant emojis
- Include the event date/time in SGT (Singapore Time)
- Do NOT include any URLs or links
- Plain text only — no Markdown or formatting syntax
- Energetic, witty, playful tone — make people WANT to show up
- Mild humor is encouraged (puns, light jokes, hype energy)
- Do NOT invent any event details beyond what is provided
"""

_LUMA_RE = re.compile(r"https?://(?:lu\.ma|luma\.com)/[^\s\"'<>]+")


def extract_luma_url(text: str) -> Optional[str]:
    m = _LUMA_RE.search(text)
    return m.group(0) if m else None


def generate_reminder_message(event_dt: datetime, event_name: str, event_desc: str) -> Optional[str]:
    friendly_time = event_dt.strftime("%A, %d %B %Y at %I:%M %p SGT")
    user_prompt = (
        f"Event title: {event_name}\n"
        f"Event description: {event_desc}\n"
        f"Event date/time: {friendly_time}\n\n"
        f"Write a fun reminder message for this event."
    )
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_ENRICHMENT_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=REMINDER_SYSTEM_PROMPT,
                temperature=0.7,
            ),
            contents=user_prompt,
        )
        return response.text.strip() or None
    except Exception as e:
        logger.warning(f"Gemini failed: {e}")

    if openai_client:
        try:
            response = openai_client.responses.create(
                model=OPENAI_ENRICHMENT_MODEL,
                instructions=REMINDER_SYSTEM_PROMPT,
                input=user_prompt,
            )
            return response.output_text.strip() or None
        except Exception as e:
            logger.warning(f"OpenAI fallback failed: {e}")

    logger.error("Both Gemini and OpenAI failed to generate reminder.")
    return None


PINNED_IDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pinned_ids.json")


def _read_pinned_ids() -> list:
    try:
        with open(PINNED_IDS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


async def _check_luma_in_message(message) -> Optional[tuple]:
    content = (message.text or "") + " " + (message.caption or "")
    luma_url = extract_luma_url(content)
    if not luma_url:
        return None

    logger.info(f"Found luma URL in pinned message {message.id}: {luma_url}")
    details = await asyncio.get_event_loop().run_in_executor(
        None, get_event_details, luma_url
    )
    if details is None:
        logger.warning(f"Could not parse event details from {luma_url}")
        return None

    logger.info(f"Event: {details['name']}, start: {details['start_dt']}")
    return details["start_dt"], details["name"], details["description"], luma_url, message.id


async def get_pinned_luma_event(client: Client) -> Optional[tuple]:
    checked_ids = set()
    ids_to_check = []

    chat = await client.get_chat(TARGET_GROUP_ID)
    if chat.pinned_message:
        checked_ids.add(chat.pinned_message.id)
        result = await _check_luma_in_message(chat.pinned_message)
        if result:
            return result

    stored_ids = _read_pinned_ids()
    for mid in stored_ids:
        if mid not in checked_ids:
            ids_to_check.append(mid)
            checked_ids.add(mid)
        if len(checked_ids) >= 10:
            break

    if not ids_to_check:
        logger.info("No luma URL found in any pinned message.")
        return None

    messages = await client.get_messages(TARGET_GROUP_ID, ids_to_check)
    if not isinstance(messages, list):
        messages = [messages]

    for message in messages:
        result = await _check_luma_in_message(message)
        if result:
            return result

    logger.info("No luma URL found in any pinned message.")
    return None


async def main() -> None:
    today = datetime.now(SGT).date()
    logger.info(f"Luma reminder job running. Today (SGT): {today}")

    async with Client(
        "luma_reminder_bot",
        api_id=TELEGRAM_API_KEY,
        api_hash=TELEGRAM_HASH,
        bot_token=BOT_TOKEN,
    ) as client:
        result = await get_pinned_luma_event(client)
        if result is None:
            logger.info("Nothing to do.")
            return

        event_dt, event_name, event_desc, luma_url, source_msg_id = result
        delta_days = (event_dt.date() - today).days
        logger.info(f"Event date: {event_dt.date()}, delta: {delta_days} day(s)")

        if delta_days not in (1, 7):
            logger.info(f"No reminder needed (delta={delta_days}, need 1 or 7).")
            return

        logger.info(f"Event is {delta_days} day(s) away. Generating reminder...")
        reminder_text = generate_reminder_message(event_dt, event_name, event_desc)
        if not reminder_text:
            logger.error("Failed to generate reminder. Aborting.")
            return

        await client.send_message(
            chat_id=TARGET_GROUP_ID,
            text=reminder_text,
            reply_to_message_id=source_msg_id,
        )
        logger.info("Reminder sent successfully.")


if __name__ == "__main__":
    asyncio.run(main())
