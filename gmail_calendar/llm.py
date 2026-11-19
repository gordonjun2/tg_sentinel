"""Email importance scoring via OpenAI.

Returns a strict Pydantic-validated result.
"""

from __future__ import annotations

import logging
from typing import Optional

import instructor
from openai import OpenAI
from pydantic import BaseModel, Field

from config import (
    OPENAI_API_KEY,
    OPENAI_ENRICHMENT_MODEL,
)
from llm_chain import LLMChainError, run_llm_chain

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

_openai_client: Optional[OpenAI] = None


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


def _score_with_openai(email: dict) -> ImportanceResult:
    client = _get_openai()
    if client is None:
        raise RuntimeError("OpenAI client unavailable (no OPENAI_API_KEY)")
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


def score_email(email: dict) -> ImportanceResult | None:
    """Run OpenAI. Returns None if it failed.

    Returning None means "no signal" — caller should treat that as
    NOT important (don't forward on uncertainty).
    """
    try:
        outcome = run_llm_chain(
            "email_importance",
            [
                ("openai", OPENAI_ENRICHMENT_MODEL, lambda: _score_with_openai(email)),
            ],
        )
        return outcome.result
    except LLMChainError as exc:
        logger.error("email importance scoring failed on all providers: %s", exc)
        return None
