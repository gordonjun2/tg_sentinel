"""Config for the partner_message_summarisation pipeline.

Shared secrets/credentials (BOT_TOKEN, ADMIN_GROUP_ID, TELEGRAM_API_KEY,
TELEGRAM_HASH, LLM keys) are imported from the root ``config`` — never
duplicated here. Only pipeline-specific settings are re-exported.
"""

from __future__ import annotations

from config import (  # noqa: F401  (re-exported for partner_message_summarisation modules)
    ADMIN_GROUP_ID,
    BOT_TOKEN,
    DATABASE_URL,
    GEMINI_API_KEY,
    OPENAI_API_KEY,
    SISC_GROUP_REGEX,
    SENTINEL_BOT_SESSION_NAME,
    SENTINEL_DIGEST_MODEL,
    SENTINEL_DRY_RUN,
    SENTINEL_MAX_CATCHUP_PER_CHAT,
    SENTINEL_MAX_MSG_CHARS,
    SENTINEL_MAX_TRANSCRIPT_CHARS,
    SENTINEL_OPENAI_DIGEST_MODEL,
    SENTINEL_SESSION_NAME,
    SENTINEL_SUMMARY_TIME,
    SENTINEL_TIMEZONE,
    TELEGRAM_API_KEY,
    TELEGRAM_HASH,
)

if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL not found in environment variables "
        "(required by the partner_message_summarisation pipeline — see .env.example)"
    )

DATABASE_URL = str(DATABASE_URL)

# Pipeline-specific: admin identities (Telegram user IDs) whose replies to
# partner messages count as "answered" in the unanswered-messages section.
SENTINEL_ADMINS: dict[int, str] = {
    6838780049: "Yuna",
    131837449: "Gordon",
    788105004: "Elmer",
}
