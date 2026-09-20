"""Telegram intelligence pipeline for tg_sentinel.

Long-running service that:
  * Listens (user-account Pyrogram session) to groups whose title matches
    ``SISC_GROUP_REGEX`` and archives every message into hosted PostgreSQL
  * Backfills messages missed during downtime (bounded per-chat catch-up)
  * Once a day at ``SENTINEL_SUMMARY_TIME`` in ``SENTINEL_TIMEZONE``,
    summarizes all pending messages into a structured summary via Gemini
    (OpenAI/instructor fallback)
  * Delivers the plain-text report to the admin group via a bot-token
    Pyrogram client and only then marks the messages completed

Entry point: ``python -m partner_message_summarisation.bot``
One-time login (user session): ``python -m partner_message_summarisation.login``
Backlog/run inspection: ``python -m partner_message_summarisation.status``
"""
