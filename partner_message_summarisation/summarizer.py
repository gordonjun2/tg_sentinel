"""LLM summarization: transcripts → structured DailyDigest.

Dual-provider pattern mirroring ``gmail_calendar/llm.py``: Gemini
``response_schema`` primary, ``instructor.from_openai(OpenAI)`` fallback,
lazy client singletons, ``temperature=0.2``. The sync SDK calls are
offloaded to a thread so the shared asyncio loop (live ingest handlers)
is never blocked.

Pure-Python pieces (transcript building, chat-boundary chunking, digest
merge, attribution sanitization) are separate functions so they can be
unit-tested with a fake LLM.
"""

from __future__ import annotations

import asyncio
import logging
import time
from difflib import SequenceMatcher
from typing import Optional

import pytz
from pydantic import BaseModel, Field

from .config import (
    GEMINI_API_KEY,
    OPENAI_API_KEY,
    SENTINEL_DIGEST_MODEL,
    SENTINEL_MAX_MSG_CHARS,
    SENTINEL_MAX_TRANSCRIPT_CHARS,
    SENTINEL_OPENAI_DIGEST_MODEL,
    SENTINEL_TIMEZONE,
)

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.8


# --- structured output models (mirrors gmail_calendar/llm.py:28-46) ----------


class Insight(BaseModel):
    title: str = Field(..., description="Short headline of the insight")
    detail: str = Field(
        ..., description="2-4 sentence explanation with concrete specifics"
    )
    source_groups: list[str] = Field(
        ..., description="Exact names of the SISC groups this came from"
    )
    importance: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0.0-1.0; 1.0 = the whole club should act on this today",
    )


class DailyDigest(BaseModel):
    highlights: list[str] = Field(
        default_factory=list,
        description="0-5 one-line top takeaways",
    )
    insights: list[Insight] = Field(default_factory=list)


SYSTEM_PROMPT = """\
You are the daily analyst for SISC (Super-Individual Secret Club), an invite-only
tech/AI community. You receive chronological transcripts from several Telegram
groups whose names start with "SISC <>".

Produce a DailyDigest:
- insights: the genuinely important items (project launches, notable discussions,
  decisions, opportunities, events, member wins). Skip chatter, greetings, memes,
  logistics noise. Each insight MUST list the exact group name(s) it came from in
  source_groups. Do not invent group names that are not in the transcript.
- highlights: at most 5 punchy one-liners for someone who read nothing today.
- importance 0.0-1.0 (1.0 = the whole club should act on this today).
Be specific: name people/projects/links when they appear. Write in clear English.
"""


# --- transcript construction (§9.2) -------------------------------------------


def _fmt_time(dt, tz: pytz.BaseTzInfo) -> str:
    return dt.astimezone(tz).strftime("%H:%M")


def _sender_label(row: dict) -> str:
    name = row.get("sender_display_name") or row.get("sender_username")
    if not name:
        return "?"
    username = row.get("sender_username")
    if username and row.get("sender_display_name"):
        return f"{row['sender_display_name']} (@{username})"
    if username:
        return f"@{username}"
    return str(name)


def _message_line(
    row: dict,
    tz: pytz.BaseTzInfo,
    max_chars: int,
    reply_lookup: dict[int, str],
) -> str:
    body = (row.get("message_text") or "").strip().replace("\n", " ")
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    parts = [f"[{_fmt_time(row['message_date'], tz)}] {_sender_label(row)}:"]
    if body:
        parts.append(body)
    annotations = []
    reply_id = row.get("reply_to_message_id")
    if reply_id and reply_id in reply_lookup:
        annotations.append(f"(↪ replying to {reply_lookup[reply_id]})")
    if row.get("is_forward"):
        origin = (
            row.get("forward_from_name")
            or row.get("forward_from_chat_title")
            or "unknown"
        )
        annotations.append(f"(⤵ forwarded from {origin})")
    if row.get("media_type"):
        media = row["media_file_name"] or row["media_type"].lower()
        annotations.append(f"[{media}]")
    if annotations:
        parts.append(" ".join(annotations))
    return " ".join(parts)


def build_chat_block(chat_title: str, messages: list[dict]) -> str:
    """Transcript block for one chat: header + chronological lines."""
    tz = pytz.timezone(SENTINEL_TIMEZONE)
    ordered = sorted(messages, key=lambda m: (m["message_date"], m["message_id"]))
    by_id = {m["message_id"]: _sender_label(m) for m in ordered}
    lines = [_message_line(m, tz, SENTINEL_MAX_MSG_CHARS, by_id) for m in ordered]
    return f"=== GROUP: {chat_title} ===\n" + "\n".join(lines)


def _split_oversized_block(block: str) -> list[str]:
    """Split a single chat block that alone exceeds the cap into headered parts."""
    header, _, rest = block.partition("\n")
    lines = rest.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    size = len(header) + 1
    for line in lines:
        if current and size + len(line) + 1 > SENTINEL_MAX_TRANSCRIPT_CHARS:
            chunks.append(header + " (cont.)\n" + "\n".join(current))
            current, size = [], len(header) + 9
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append(header + "\n" + "\n".join(current))
    return chunks


def chunk_transcripts(messages_by_chat: list[tuple[str, list[dict]]]) -> list[str]:
    """Pack whole chat blocks into chunks under the transcript cap.

    ``messages_by_chat`` must be pre-ordered deterministically (the caller
    sorts by message count desc). A single oversized chat is split across
    multiple headered parts so the cap always holds.
    """
    blocks = [build_chat_block(title, msgs) for title, msgs in messages_by_chat]
    expanded: list[str] = []
    for block in blocks:
        if len(block) > SENTINEL_MAX_TRANSCRIPT_CHARS:
            expanded.extend(_split_oversized_block(block))
        else:
            expanded.append(block)

    chunks: list[str] = []
    current = ""
    for block in expanded:
        candidate = f"{block}\n\n" if current else block
        if current and len(current) + len(candidate) > SENTINEL_MAX_TRANSCRIPT_CHARS:
            chunks.append(current.rstrip("\n"))
            current = block
        else:
            current += candidate
    if current:
        chunks.append(current.rstrip("\n"))
    return chunks


# --- merge + attribution (§9.2) -------------------------------------------------


def _titles_similar(a: str, b: str) -> bool:
    return (
        SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()
        >= SIMILARITY_THRESHOLD
    )


def merge_digests(digests: list[DailyDigest]) -> DailyDigest:
    """Merge per-chunk digests: concat, dedupe near-identical insights,
    re-sort by importance, cap highlights at 5 (spread across chunks)."""
    highlights: list[str] = []
    for i in range(5):
        for digest in digests:
            if i < len(digest.highlights) and len(highlights) < 5:
                if digest.highlights[i] not in highlights:
                    highlights.append(digest.highlights[i])

    insights: list[Insight] = []
    for digest in digests:
        for insight in digest.insights:
            merged = False
            for existing in insights:
                if _titles_similar(existing.title, insight.title):
                    existing.source_groups = sorted(
                        set(existing.source_groups) | set(insight.source_groups)
                    )
                    existing.importance = max(existing.importance, insight.importance)
                    merged = True
                    break
            if not merged:
                insights.append(insight.model_copy())
    insights.sort(key=lambda i: i.importance, reverse=True)
    return DailyDigest(highlights=highlights, insights=insights)


def sanitize_digest(
    digest: DailyDigest, allowed_groups: set[str]
) -> DailyDigest:
    """Restrict insight attribution to groups present in the transcript.

    Group names not in ``allowed_groups`` are dropped from ``source_groups``
    (case-insensitive match); insights left with no valid source are removed.
    """
    allowed_lower = {g.casefold(): g for g in allowed_groups}
    kept: list[Insight] = []
    for insight in digest.insights:
        valid = sorted(
            {
                allowed_lower[g.casefold()]
                for g in insight.source_groups
                if g.casefold() in allowed_lower
            }
        )
        if not valid:
            logger.warning(
                "Dropped insight %r — no valid source group in %s",
                insight.title, insight.source_groups,
            )
            continue
        insight.source_groups = valid
        kept.append(insight)
    return DailyDigest(
        highlights=digest.highlights,
        insights=kept,
    )


# --- provider calls (dual pattern, §9.4) ----------------------------------------

_gemini_client = None
_openai_client = None


def _get_gemini():
    global _gemini_client
    if _gemini_client is None and GEMINI_API_KEY:
        from google import genai

        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _get_openai():
    global _openai_client
    if _openai_client is None and OPENAI_API_KEY:
        from openai import OpenAI

        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _call_gemini(transcript: str) -> DailyDigest:
    client = _get_gemini()
    if client is None:
        raise RuntimeError("Gemini client unavailable (no GEMINI_API_KEY)")
    from google.genai import types

    response = client.models.generate_content(
        model=SENTINEL_DIGEST_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=DailyDigest,
            temperature=0.2,
        ),
        contents=transcript,
    )
    data = response.parsed
    if isinstance(data, DailyDigest):
        return data
    if isinstance(data, dict):
        return DailyDigest.model_validate(data)
    return DailyDigest.model_validate_json(response.text)


def _call_openai(transcript: str) -> DailyDigest:
    client = _get_openai()
    if client is None:
        raise RuntimeError("OpenAI client unavailable (no OPENAI_API_KEY)")
    import instructor

    patched = instructor.from_openai(client)
    return patched.chat.completions.create(
        model=SENTINEL_OPENAI_DIGEST_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        response_model=DailyDigest,
        temperature=0.2,
        max_retries=2,
    )


def _summarize_chunk(transcript: str) -> tuple[DailyDigest, str, int]:
    """Summarize one transcript chunk. Returns (digest, provider, latency_ms).

    Raises when both providers fail so the run is marked failed and the
    messages stay pending (§9.4).
    """
    start = time.monotonic()
    errors: list[str] = []
    for provider, call in (("gemini", _call_gemini), ("openai", _call_openai)):
        try:
            digest = call(transcript)
            latency = int((time.monotonic() - start) * 1000)
            return digest, provider, latency
        except Exception as exc:  # noqa: BLE001 — fall through to fallback
            logger.warning("%s digest failed: %s", provider, exc)
            errors.append(f"{provider}: {exc}")
    raise RuntimeError(
        "both LLM providers failed — " + " | ".join(errors)
    )


async def generate_digest(
    chunks: list[str],
) -> tuple[DailyDigest, str, int]:
    """Summarize all transcript chunks and merge into one digest.

    Returns (merged_digest, providers_used, total_latency_ms); provider is
    e.g. ``"gemini"`` or ``"gemini+openai"`` when chunks split across
    providers. Calls run in a thread so the event loop stays responsive.
    """
    digests: list[DailyDigest] = []
    providers: list[str] = []
    total_ms = 0
    for chunk in chunks:
        digest, provider, latency = await asyncio.to_thread(
            _summarize_chunk, chunk
        )
        digests.append(digest)
        providers.append(provider)
        total_ms += latency
    return merge_digests(digests), "+".join(dict.fromkeys(providers)), total_ms
