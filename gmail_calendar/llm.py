"""Email importance scoring via Gemini (primary) with OpenAI fallback.

Mirrors the dual-LLM pattern from ``luma_reminder.py``: Gemini first, fall
back to OpenAI on failure. Returns a strict Pydantic-validated result.
"""

from __future__ import annotations

import logging
from typing import Optional

import instructor
from google import genai
from google.genai import types
from openai import OpenAI
from pydantic import BaseModel, Field

from config import (
    GEMINI_API_KEY,
    GEMINI_ENRICHMENT_MODEL,
    OPENAI_API_KEY,
    OPENAI_ENRICHMENT_MODEL,
)

logger = logging.getLogger(__name__)


class ImportanceResult(BaseModel):
    """Structured output for the triage LLM call."""

    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Importance score between 0.0 (noise) and 1.0 (urgent).",
    )
    category: str = Field(
        ..., description="One of: 'low', 'medium', 'high'."
    )
    reasoning: str = Field(
        ..., description="One short sentence explaining the score."
    )
    summary: str = Field(
        ..., description="One-sentence summary of what the email is about."
    )


SYSTEM_PROMPT = """\
You triage incoming Gmail for the admin of a tech/AI community (SISC).
Score each email 0.0-1.0 for "should this interrupt the admin's Telegram group?":

  0.0-0.3  LOW     marketing, newsletters, promos, automated digests,
                   community announcements, product updates, weekly roundups.
  0.4-0.6  MEDIUM  FYI work comms, scheduled reports, CI/CD notifications,
                   calendar pings, social-media notifications, receipts.
  0.7-1.0  HIGH    personal 1:1 emails, action-required, billing/payment,
                   security alerts, meeting invites from real people,
                   client/customer communications, approvals, contracts.

Rules:
- Default unknown automated traffic to LOW.
- Sender reputation matters: a real human address beats a no-reply domain.
- Be skeptical of subject lines with marketing triggers (FREE, ACT NOW, % OFF,
 newsletter, digest, recap, weekly).
"""


# --- clients (lazy) ---------------------------------------------------------

_gemini_client: Optional[genai.Client] = None
_openai_client: Optional[OpenAI] = None


def _get_gemini() -> Optional[genai.Client]:
    global _gemini_client
    if _gemini_client is None and GEMINI_API_KEY:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _get_openai() -> Optional[OpenAI]:
    global _openai_client
    if _openai_client is None and OPENAI_API_KEY:
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client


def _user_prompt(email: dict) -> str:
    return (
        f"From: {email.get('from', '')}\n"
        f"Subject: {email.get('subject', '')}\n"
        f"Date: {email.get('date', '')}\n"
        f"Snippet (first 500 chars of body):\n"
        f"{(email.get('snippet') or '')[:500]}\n\n"
        f"Return strict JSON per the schema."
    )


def _score_with_gemini(email: dict) -> ImportanceResult | None:
    client = _get_gemini()
    if client is None:
        return None
    try:
        response = client.models.generate_content(
            model=GEMINI_ENRICHMENT_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=ImportanceResult,
                temperature=0.2,
            ),
            contents=_user_prompt(email),
        )
        # When response_schema is set, the SDK validates and parses.
        data = response.parsed
        if isinstance(data, ImportanceResult):
            return data
        # Fall through to manual parse if parsed is a dict.
        if isinstance(data, dict):
            return ImportanceResult.model_validate(data)
        return ImportanceResult.model_validate_json(response.text)
    except Exception as exc:
        logger.warning("Gemini scoring failed: %s", exc)
        return None


def _score_with_openai(email: dict) -> ImportanceResult | None:
    client = _get_openai()
    if client is None:
        return None
    try:
        # instructor patches the OpenAI SDK to return the Pydantic model.
        patched = instructor.from_openai(client)
        return patched.chat.completions.create(
            model=OPENAI_ENRICHMENT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(email)},
            ],
            response_model=ImportanceResult,
            temperature=0.2,
            max_retries=2,
        )
    except Exception as exc:
        logger.warning("OpenAI scoring failed: %s", exc)
        return None


def score_email(email: dict) -> ImportanceResult | None:
    """Run Gemini then OpenAI. Returns None only if both failed.

    Returning None means "no signal" — caller should treat that as
    NOT important (don't forward on uncertainty).
    """
    result = _score_with_gemini(email)
    if result is not None:
        return result
    return _score_with_openai(email)
