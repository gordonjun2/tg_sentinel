#!/usr/bin/env python3
"""Luma event reminder cron job. Run daily via crontab."""

import asyncio
import logging
import re
import sys
from datetime import datetime
from typing import Optional

from google import genai
from google.genai import types
from openai import OpenAI
from pyrogram import Client, enums, utils

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
from luma_scraper import SGT, get_event_start_dt

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
You are a friendly community manager for a tech/AI community (SISC).
Write a concise, warm Telegram reminder message for an upcoming event.
Rules:
- Keep it under 120 words
- Use 1-2 relevant emojis maximum
- Include the event date/time in SGT (Singapore Time)
- Include the Luma registration link
- Plain text only — no Markdown or formatting syntax
- Friendly tone, not salesy
- Do NOT invent any event details beyond what is provided
"""

_LUMA_RE = re.compile(r"https?://(?:lu\.ma|luma\.com)/[^\s\"'<>]+")


def extract_luma_url(text: str) -> Optional[str]:
    m = _LUMA_RE.search(text)
    return m.group(0) if m else None


def generate_reminder_message(event_dt: datetime, luma_url: str) -> Optional[str]:
    friendly_time = event_dt.strftime("%A, %d %B %Y at %I:%M %p SGT")
    user_prompt = (
        f"Event date/time: {friendly_time}\n"
        f"Luma event link: {luma_url}\n\n"
        f"Write a reminder message for this event."
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


async def get_pinned_luma_event(client: Client) -> Optional[tuple]:
    async for message in client.search_messages(
        TARGET_GROUP_ID,
        filter=enums.MessagesFilter.PINNED,
    ):
        content = (message.text or "") + " " + (message.caption or "")
        luma_url = extract_luma_url(content)
        if not luma_url:
            continue

        logger.info(f"Found luma URL in pinned message {message.id}: {luma_url}")
        event_dt = await asyncio.get_event_loop().run_in_executor(
            None, get_event_start_dt, luma_url
        )
        if event_dt is None:
            logger.warning(f"Could not parse event date from {luma_url}")
            return None

        logger.info(f"Event start: {event_dt}")
        return event_dt, luma_url

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

        event_dt, luma_url = result
        delta_days = (event_dt.date() - today).days
        logger.info(f"Event date: {event_dt.date()}, delta: {delta_days} day(s)")

        if delta_days not in (1, 7):
            logger.info(f"No reminder needed (delta={delta_days}, need 1 or 7).")
            return

        logger.info(f"Event is {delta_days} day(s) away. Generating reminder...")
        reminder_text = generate_reminder_message(event_dt, luma_url)
        if not reminder_text:
            logger.error("Failed to generate reminder. Aborting.")
            return

        await client.send_message(chat_id=TARGET_GROUP_ID, text=reminder_text)
        logger.info("Reminder sent successfully.")


if __name__ == "__main__":
    asyncio.run(main())
