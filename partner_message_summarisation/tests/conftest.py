"""Hermetic env for partner_message_summarisation tests.

Root ``config`` fail-fasts on required secrets at import; tests must not
depend on a developer's real ``.env``, so every required var gets a dummy
default before any partner_message_summarisation module is imported. Tests that hit real
services are env-gated separately (see test_db.py).
"""

from __future__ import annotations

import os

_DUMMY_REQUIRED = {
    "BOT_TOKEN": "test-token",
    "ADMIN_GROUP_ID": "-1000000001",
    "TARGET_GROUP_ID": "-1000000002",
    "GOOGLE_DRIVE_MAIN_FOLDER_ID": "folder",
    "GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID": "folder",
    "GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID": "folder",
    "GEMINI_API_KEY": "test-gemini",
    "OPENAI_API_KEY": "test-openai",
    "TELEGRAM_API_KEY": "12345",
    "TELEGRAM_HASH": "test-hash",
    "FIRECRAWL_API_KEY": "test-firecrawl",
    "DATABASE_URL": "postgres://test:test@localhost:5432/partner_message_summarisation_test",
}

for _key, _value in _DUMMY_REQUIRED.items():
    os.environ.setdefault(_key, _value)
