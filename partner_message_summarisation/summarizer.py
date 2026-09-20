"""LLM summarization: transcripts → structured PartnerMessageSummary.

Dual-provider pattern mirroring ``gmail_calendar/llm.py``: Gemini
``response_schema`` primary, ``instructor.from_openai(OpenAI)`` fallback,
lazy client singletons, ``temperature=0.2``. The sync SDK calls are
offloaded to a thread so the shared asyncio loop (live ingest handlers)
is never blocked.

Pure-Python pieces (transcript building, chat-boundary chunking, summary
merge, attribution sanitization) are separate functions so they can be
unit-tested with a fake LLM.
"""

from __future__ import annotations

import asyncio
import logging
import time

from pydantic import BaseModel, Field

from .config import (
    GEMINI_API_KEY,
    OPENAI_API_KEY,
    SENTINEL_DIGEST_MODEL,
    SENTINEL_MAX_MSG_CHARS,
    SENTINEL_MAX_TRANSCRIPT_CHARS,
    SENTINEL_OPENAI_DIGEST_MODEL,
)
from .timeutil import fmt_sentinel

logger = logging.getLogger(__name__)


# --- structured output models (mirrors gmail_calendar/llm.py:28-46) ----------


class GroupSummary(BaseModel):
    group_name: str = Field(
        ...,
        description="Exact group name as it appears in the '=== GROUP: ... ===' header",
    )
    summary: list[str] = Field(
        ...,
        description="Bullet points of what happened in this group's conversation — "
        "each point ONE concrete item naming who said/asked/proposed what",
    )
    has_signal: bool = Field(
        ...,
        description="Whether the conversation carries anything actionable or "
        "informational for the admins. Set false ONLY when it is purely "
        "thanks/greetings/acknowledgements/emoji with no substance. "
        "When in doubt, set true.",
    )


class PartnerMessageSummary(BaseModel):
    groups: list[GroupSummary] = Field(
        default_factory=list,
        description="One entry per group present in the transcript",
    )


class UnansweredPoint(BaseModel):
    group_name: str = Field(
        ..., description="Exact chat title as provided in the input"
    )
    point: str = Field(
        ...,
        description="One line naming the person and what they want, ask or flag",
    )


class UnansweredSummary(BaseModel):
    points: list[UnansweredPoint] = Field(default_factory=list)


SYSTEM_PROMPT = """\
You are the daily analyst for SISC (Super-Individual Secret Club), an invite-only
tech/AI community. You receive chronological transcripts from several Telegram
groups whose names start with "SISC <>".

Produce a PartnerMessageSummary with one GroupSummary entry per group found in the transcript:
- group_name: copy the group name EXACTLY as it appears in its "=== GROUP: ... ==="
  header. Never invent or alter group names.
- summary: bullet points covering what happened in that group's conversation —
  the substance (decisions, questions, proposals, events, requests, notable news),
  each point explicitly attributing statements to the people who made them
  (e.g. "Gordon Oh asked Han to introduce panelists", "Cordi asked about
  timelines"). Write 2-8 short bullet points, one concrete point each; use the
  most active participants' names. Skip pure chatter, greetings, memes and
  logistics noise. If a group's transcript is only noise, one bullet is enough.
- has_signal: false when the group's conversation is ONLY pleasantries — thanks,
  greetings, acknowledgements, emoji, memes — i.e. nothing actionable or
  informational for the admins. Anything with substance (a question, request,
  decision, event, update) means has_signal stays true. When in doubt, true.
Write in clear English.
"""

UNANSWERED_SYSTEM_PROMPT = """\
You assist the admins of SISC (Super-Individual Secret Club). You receive a
list of partner messages that no admin has replied to yet. Rewrite the list
as short third-person bullets for the admin team:
- point: ONE line naming the person and what they want, ask, propose or flag,
  e.g. "Shyar Me wants to clarify what the panel format is" or "Cordi is
  still deciding whether she can join the panel". Combine several messages
  from the same person in the same group into a single bullet.
- group_name: copy the chat title EXACTLY as provided in [brackets].
Do not invent people, requests or details. Keep the same information the
messages convey; write in clear English.
"""


# --- transcript construction (§9.2) -------------------------------------------


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
    max_chars: int,
    reply_lookup: dict[int, str],
) -> str:
    body = (row.get("message_text") or "").strip().replace("\n", " ")
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    speaker = _sender_label(row)
    annotations = []
    if row.get("is_forward"):
        origin = (
            row.get("forward_from_name")
            or row.get("forward_from_chat_title")
        )
        if origin:
            # attribute the content to the original author, note the forwarder
            speaker = str(origin)
            annotations.append(f"(⤵ forwarded by {_sender_label(row)})")
        else:
            annotations.append("(⤵ forwarded, original author hidden)")
    parts = [f"[{fmt_sentinel(row['message_date'])}] {speaker}:"]
    if body:
        parts.append(body)
    reply_id = row.get("reply_to_message_id")
    if reply_id and reply_id in reply_lookup:
        annotations.append(f"(↪ replying to {reply_lookup[reply_id]})")
    if row.get("media_type"):
        media = row["media_file_name"] or row["media_type"].lower()
        annotations.append(f"[{media}]")
    if annotations:
        parts.append(" ".join(annotations))
    return " ".join(parts)


def build_chat_block(chat_title: str, messages: list[dict]) -> str:
    """Transcript block for one chat: header + chronological lines."""
    ordered = sorted(messages, key=lambda m: (m["message_date"], m["message_id"]))
    by_id = {m["message_id"]: _sender_label(m) for m in ordered}
    lines = [_message_line(m, SENTINEL_MAX_MSG_CHARS, by_id) for m in ordered]
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


def merge_summaries(summaries: list[PartnerMessageSummary]) -> PartnerMessageSummary:
    """Merge per-chunk summaries: one entry per group (a group split across
    chunks gets its summaries concatenated in chunk order)."""
    groups: list[GroupSummary] = []
    by_name: dict[str, GroupSummary] = {}
    for summary in summaries:
        for group in summary.groups:
            key = group.group_name.strip().casefold()
            existing = by_name.get(key)
            if existing is None:
                existing = group.model_copy()
                groups.append(existing)
                by_name[key] = existing
            else:
                existing.summary = existing.summary + [
                    point
                    for point in group.summary
                    if point.strip() and point not in existing.summary
                ]
    return PartnerMessageSummary(groups=groups)


def sanitize_summary(
    summary: PartnerMessageSummary, allowed_groups: set[str]
) -> PartnerMessageSummary:
    """Restrict group attribution to groups present in the transcript.

    Group names not in ``allowed_groups`` are dropped (case-insensitive
    match); the remaining entries keep transcript order.
    """
    allowed_lower = {g.casefold(): g for g in allowed_groups}
    kept: list[GroupSummary] = []
    for group in summary.groups:
        canonical = allowed_lower.get(group.group_name.strip().casefold())
        if canonical is None:
            logger.warning(
                "Dropped group summary %r — not among transcript groups %s",
                group.group_name, sorted(allowed_groups),
            )
            continue
        group.group_name = canonical
        kept.append(group)
    return PartnerMessageSummary(groups=kept)


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


def _call_gemini(system_prompt: str, schema: type, text: str):
    client = _get_gemini()
    if client is None:
        raise RuntimeError("Gemini client unavailable (no GEMINI_API_KEY)")
    from google.genai import types

    response = client.models.generate_content(
        model=SENTINEL_DIGEST_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2,
        ),
        contents=text,
    )
    data = response.parsed
    if isinstance(data, schema):
        return data
    if isinstance(data, dict):
        return schema.model_validate(data)
    return schema.model_validate_json(response.text)


def _call_openai(system_prompt: str, response_model: type, text: str):
    client = _get_openai()
    if client is None:
        raise RuntimeError("OpenAI client unavailable (no OPENAI_API_KEY)")
    import instructor

    patched = instructor.from_openai(client)
    return patched.chat.completions.create(
        model=SENTINEL_OPENAI_DIGEST_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        response_model=response_model,
        temperature=0.2,
        max_retries=2,
    )


def _dual_provider_call(system_prompt: str, schema: type, text: str):
    """Run ``schema`` LLM extraction on ``text``: Gemini → OpenAI fallback.

    Raises when both providers fail so the run is marked failed and the
    messages stay pending (§9.4).
    """
    start = time.monotonic()
    errors: list[str] = []
    for provider, call in (("gemini", _call_gemini), ("openai", _call_openai)):
        try:
            result = call(system_prompt, schema, text)
            latency = int((time.monotonic() - start) * 1000)
            return result, provider, latency
        except Exception as exc:  # noqa: BLE001 — fall through to fallback
            logger.warning("%s call failed: %s", provider, exc)
            errors.append(f"{provider}: {exc}")
    raise RuntimeError(
        "both LLM providers failed — " + " | ".join(errors)
    )


def _summarize_chunk(transcript: str) -> tuple[PartnerMessageSummary, str, int]:
    """Summarize one transcript chunk. Returns (summary, provider, latency_ms).

    Raises when both providers fail so the run is marked failed and the
    messages stay pending (§9.4).
    """
    return _dual_provider_call(SYSTEM_PROMPT, PartnerMessageSummary, transcript)


async def generate_summary(
    chunks: list[str],
) -> tuple[PartnerMessageSummary, str, int]:
    """Summarize all transcript chunks and merge into one summary.

    Returns (merged summary, providers_used, total_latency_ms); provider is
    e.g. ``"gemini"`` or ``"gemini+openai"`` when chunks split across
    providers. Calls run in a thread so the event loop stays responsive.
    """
    summaries: list[PartnerMessageSummary] = []
    providers: list[str] = []
    total_ms = 0
    for chunk in chunks:
        summary, provider, latency = await asyncio.to_thread(
            _summarize_chunk, chunk
        )
        summaries.append(summary)
        providers.append(provider)
        total_ms += latency
    return merge_summaries(summaries), "+".join(dict.fromkeys(providers)), total_ms


# --- unanswered messages → admin-facing points (§ content part 2) ---------------


def build_unanswered_block(items) -> str:
    """LLM input listing unanswered messages: one ``[chat] sender: text`` line."""
    return "\n".join(
        f"[{item.chat_title}] {item.sender}: {item.excerpt}" for item in items
    )


def _summarize_unanswered_block(
    block: str,
) -> tuple[UnansweredSummary, str, int]:
    return _dual_provider_call(UNANSWERED_SYSTEM_PROMPT, UnansweredSummary, block)


async def summarize_unanswered(
    items: list,
) -> list[UnansweredPoint]:
    """Turn detected unanswered messages into short admin-facing points.

    Empty input short-circuits (no LLM call). The LLM must echo chat titles
    verbatim; points naming unknown chats are dropped (case-insensitive).
    """
    if not items:
        return []
    block = build_unanswered_block(items)
    result, _, _ = await asyncio.to_thread(
        _summarize_unanswered_block, block
    )
    allowed = {item.chat_title.casefold(): item.chat_title for item in items}
    kept: list[UnansweredPoint] = []
    for point in result.points:
        canonical = allowed.get(point.group_name.strip().casefold())
        if canonical is None:
            logger.warning(
                "Dropped unanswered point for unknown chat %r", point.group_name
            )
            continue
        kept.append(
            UnansweredPoint(group_name=canonical, point=point.point.strip())
        )
    return kept
