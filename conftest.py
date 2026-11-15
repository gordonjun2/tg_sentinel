"""Hermetic defaults so pytest can import config without a real .env.

Root modules fail-fast on missing secrets at import. CI and local runs
without secrets need these setdefaults before collection imports them.
"""

from __future__ import annotations

import os

_DUMMY_REQUIRED = {
    "BOT_TOKEN": "test-token",
    "ADMIN_GROUP_ID": "-1000000001",
    "TARGET_GROUP_ID": "-1000000002",
    "TARGET_SUB_GROUP_ID": "-1000000003",
    "MINI_APP_URL": "https://serendip.example.test",
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
