# SISC + Serendip `/start` Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Soft-gate SISC join behind a thin SISC+Serendip intro, unlock Serendip Mini App launch for group members, and send onboarding user messages via Telegram Rich Messages.

**Architecture:** Keep survey/admin/invite logic in `bot.py`. Add a raw Bot API `sendRichMessage` helper (`rich_messages.py`), centralize user-facing markdown in `onboarding_copy.py`, and extract pure `/start` branch resolution for tests. Inline keyboards stay PTB `reply_markup`.

**Tech Stack:** Python 3, `python-telegram-bot` 22.7+, `requests`, Telegram Bot API `sendRichMessage` (Rich Markdown), `pytest` + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-11-15-sisc-serendip-start-onboarding-design.md`

## Global Constraints

- Soft-gate: survey starts only after `apply_join` callback, never automatically on `/start` for new users.
- Always send thin intro on `/start`, then the state-specific next step.
- Members: Launch Serendip (`WebAppInfo(url=MINI_APP_URL)`) + Open SISC group (`url`).
- `MINI_APP_URL` required at config load; example/production value `https://serendip.sisc.club`.
- User-facing onboarding DMs use Rich Messages; no silent fallback to plain `sendMessage`.
- Do not change survey questions, admin approve/reject mechanics, or invite-link helper behavior.
- Out of scope: group welcome batches, LinkedIn, audio/admin ops polish, full vision main menu.
- Prefer smaller new modules over growing `bot.py` further for copy + HTTP.

## File structure

| Path | Responsibility |
|------|----------------|
| `config.py` | Load/validate `MINI_APP_URL` |
| `.env.example` | Document `MINI_APP_URL` |
| `rich_messages.py` | `send_rich_message` → Bot API `sendRichMessage` |
| `onboarding_copy.py` | Intro / question / status markdown builders |
| `bot.py` | `/start` routing, `apply_join`, wire rich sends for survey + user approval/rejection DMs |
| `tests/conftest.py` | `sys.path` root insert |
| `tests/test_rich_messages.py` | Payload + error behavior |
| `tests/test_onboarding_copy.py` | Copy builders |
| `tests/test_start_routing.py` | Pure branch resolver + apply resets survey |

---

### Task 1: `MINI_APP_URL` config

**Files:**
- Modify: `config.py` (after `TARGET_SUB_GROUP_ID` block, ~line 36)
- Modify: `.env.example`
- Test: `tests/test_config_mini_app_url.py`

**Interfaces:**
- Produces: `MINI_APP_URL: str` (non-empty, from env)

- [ ] **Step 1: Write the failing test**

```python
# tests/conftest.py
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
```

```python
# tests/test_config_mini_app_url.py
import importlib
import os

import pytest


def test_mini_app_url_required(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:tok")
    monkeypatch.setenv("ADMIN_GROUP_ID", "-1001")
    monkeypatch.setenv("TARGET_GROUP_ID", "-1002")
    monkeypatch.setenv("TARGET_SUB_GROUP_ID", "-1003")
    monkeypatch.setenv("GOOGLE_DRIVE_MAIN_FOLDER_ID", "x")
    monkeypatch.setenv("GOOGLE_DRIVE_DISCUSSION_INSIGHTS_FOLDER_ID", "x")
    monkeypatch.setenv("GOOGLE_DRIVE_TRANSCRIPTIONS_FOLDER_ID", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("TELEGRAM_API_KEY", "x")
    monkeypatch.setenv("TELEGRAM_HASH", "x")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "x")
    monkeypatch.delenv("MINI_APP_URL", raising=False)
    with pytest.raises(ValueError, match="MINI_APP_URL"):
        import config as cfg

        importlib.reload(cfg)
```

Note: if reloading `config` is too coupled to other required env vars already loaded in the process, instead extract a tiny helper in `config.py`:

```python
def require_mini_app_url(raw: str | None) -> str:
    if not raw or not raw.strip():
        raise ValueError("MINI_APP_URL not found in environment variables")
    return raw.strip()
```

and unit-test that helper only (preferred if full reload is painful).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_mini_app_url.py -v`  
Expected: FAIL (helper/module missing or no raise)

- [ ] **Step 3: Implement config**

```python
# config.py — add after TARGET_SUB_GROUP_ID validation

def require_mini_app_url(raw: str | None) -> str:
    if not raw or not raw.strip():
        raise ValueError("MINI_APP_URL not found in environment variables")
    return raw.strip()


MINI_APP_URL = require_mini_app_url(os.getenv("MINI_APP_URL"))
```

```bash
# .env.example — add near bot vars
MINI_APP_URL=https://serendip.sisc.club
```

Also add `MINI_APP_URL=https://serendip.sisc.club` to the operator’s real `.env` (gitignored) before running the bot.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_mini_app_url.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.py .env.example tests/conftest.py tests/test_config_mini_app_url.py
git commit -m "$(cat <<'EOF'
feat: require MINI_APP_URL for Serendip launch button

EOF
)"
```

---

### Task 2: Rich Messages HTTP helper

**Files:**
- Create: `rich_messages.py`
- Test: `tests/test_rich_messages.py`

**Interfaces:**
- Consumes: `BOT_TOKEN` (or token argument), `requests.Session`
- Produces:
  - `API_TMPL = "https://api.telegram.org/bot{token}/{method}"`
  - `async def send_rich_message(*, bot_token: str, chat_id: int, markdown: str, reply_markup: dict | None, session) -> dict`  
    Posts JSON `{"chat_id", "rich_message": {"markdown": ...}, optional "reply_markup"}`.  
    On non-OK Telegram response: raise `RuntimeError` with status + body (no silent fallback).  
    Returns parsed JSON `result` object on success.
  - Sync variant is fine if easier for unit tests; if sync, bot handlers call via `asyncio.to_thread` or use sync `requests` inside async (match repo style — LinkedIn uses sync `requests`; for bot handlers prefer async wrapper that calls sync in `asyncio.to_thread`).

Preferred signature for bot use:

```python
async def send_rich_message(
    bot_token: str,
    chat_id: int,
    markdown: str,
    reply_markup: dict | None = None,
    session=None,
) -> dict:
    ...
```

Serialize PTB keyboards with `reply_markup.to_dict()` at the call site.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rich_messages.py
from unittest.mock import MagicMock

import pytest

from rich_messages import send_rich_message


@pytest.mark.asyncio
async def test_send_rich_message_payload():
    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=200,
        text='{"ok":true,"result":{"message_id":1}}',
        json=lambda: {"ok": True, "result": {"message_id": 1}},
    )
    result = await send_rich_message(
        bot_token="tok",
        chat_id=42,
        markdown="# Hello",
        reply_markup={"inline_keyboard": []},
        session=session,
    )
    assert result["message_id"] == 1
    url = session.post.call_args.args[0]
    assert url.endswith("/sendRichMessage")
    body = session.post.call_args.kwargs["json"]
    assert body["chat_id"] == 42
    assert body["rich_message"] == {"markdown": "# Hello"}
    assert body["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_send_rich_message_raises_on_api_error():
    session = MagicMock()
    session.post.return_value = MagicMock(
        status_code=400,
        text='{"ok":false,"description":"Bad Request"}',
        json=lambda: {"ok": False, "description": "Bad Request"},
    )
    with pytest.raises(RuntimeError, match="Bad Request"):
        await send_rich_message(
            bot_token="tok",
            chat_id=42,
            markdown="x",
            reply_markup=None,
            session=session,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rich_messages.py -v`  
Expected: FAIL import or missing function

- [ ] **Step 3: Implement helper**

```python
# rich_messages.py
from __future__ import annotations

import asyncio
from typing import Any, Optional

import requests

API_TMPL = "https://api.telegram.org/bot{token}/{method}"


def _post_rich_message(
    bot_token: str,
    chat_id: int,
    markdown: str,
    reply_markup: Optional[dict],
    session: requests.Session,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": {"markdown": markdown},
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    resp = session.post(
        API_TMPL.format(token=bot_token, method="sendRichMessage"),
        json=payload,
        timeout=30,
    )
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(
            f"sendRichMessage non-JSON response status={resp.status_code} body={resp.text}"
        ) from exc
    if resp.status_code != 200 or not data.get("ok"):
        raise RuntimeError(
            f"sendRichMessage failed status={resp.status_code} body={resp.text}"
        )
    return data["result"]


async def send_rich_message(
    bot_token: str,
    chat_id: int,
    markdown: str,
    reply_markup: Optional[dict] = None,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    owns_session = session is None
    sess = session or requests.Session()
    try:
        return await asyncio.to_thread(
            _post_rich_message, bot_token, chat_id, markdown, reply_markup, sess
        )
    finally:
        if owns_session:
            sess.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rich_messages.py -v`  
Expected: PASS  
If `@pytest.mark.asyncio` fails, add `pytest-asyncio` or use `asyncio.run` in a sync test wrapper — prefer installing `pytest-asyncio` in the venv for this repo’s tests.

- [ ] **Step 5: Commit**

```bash
git add rich_messages.py tests/test_rich_messages.py
git commit -m "$(cat <<'EOF'
feat: add sendRichMessage helper for onboarding copy

EOF
)"
```

---

### Task 3: Onboarding copy builders

**Files:**
- Create: `onboarding_copy.py`
- Test: `tests/test_onboarding_copy.py`

**Interfaces:**
- Produces:
  - `INTRO_MARKDOWN: str` — thin intro from spec draft
  - `survey_question_markdown(index: int, total: int, question: str) -> str`
  - `survey_complete_markdown() -> str`
  - `pending_approval_markdown() -> str`
  - `approved_invite_markdown(invite_link: str) -> str`
  - `rejection_markdown(reason: str) -> str`
  - `member_followup_markdown() -> str` — short line under intro for members (optional; keyboard carries actions)

Exact strings (use these unless copy review tweaks):

```python
INTRO_MARKDOWN = """# Welcome

## SISC
The Super-Individual Secret Club is a tight-knit community for curious minds who create the future with AI. Founders, investors, operators, researchers, and artists gather here to stay ahead and collaborate with intention.

**What we stand for**
- **Urgency** — stay AI-ready as the space moves fast
- **A safe space** — explore hard topics privately
- **Serendipity** — meet brilliant minds and prototype the future

[Learn more about SISC](https://www.sisc.club/)

---

## Serendip
Serendip helps you find people who matter inside Telegram, by intent, not by profile.

**To use Serendip, you need SISC access first.**
"""


def survey_question_markdown(index: int, total: int, question: str) -> str:
    # index is 0-based
    return (
        f"## Question {index + 1} of {total}\n\n"
        f"{question}\n\n"
        f"_Reply with your answer as text._"
    )


def survey_complete_markdown() -> str:
    return (
        "## Application received\n\n"
        "Thanks for completing the questions.\n\n"
        "**What happens next**\n"
        "- Admins review your answers\n"
        "- You get a DM when approved or rejected\n"
        "- After you join SISC, use /start again to open Serendip"
    )


def pending_approval_markdown() -> str:
    return (
        "## Pending review\n\n"
        "Your join request is waiting for admin review.\n\n"
        "You will get a DM when there is an update."
    )


def approved_invite_markdown(invite_link: str) -> str:
    return (
        "## You are approved\n\n"
        "Welcome to SISC. Use this one-time invite to join the group:\n\n"
        f"{invite_link}\n\n"
        "After you join, send /start again to launch Serendip."
    )


def rejection_markdown(reason: str) -> str:
    return (
        "## Request not approved\n\n"
        f"**Reason:** {reason}\n\n"
        "You can send /start again later if you want to re-apply."
    )


def member_followup_markdown() -> str:
    return (
        "## You are in\n\n"
        "Open Serendip below, or jump back to the SISC group."
    )
```

- [ ] **Step 1: Write failing tests** asserting intro contains `Serendip`, `SISC access`, and `sisc.club`; question builder uses `1 of`; rejection includes reason.

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement `onboarding_copy.py`** with the strings above

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Commit**

```bash
git add onboarding_copy.py tests/test_onboarding_copy.py
git commit -m "$(cat <<'EOF'
feat: add thin SISC and Serendip onboarding copy

EOF
)"
```

---

### Task 4: Pure `/start` branch resolver + Apply CTA wiring

**Files:**
- Modify: `bot.py` (`start`, new `handle_apply_join`, handler registration)
- Test: `tests/test_start_routing.py`

**Interfaces:**
- Produces:
  - `APPLY_JOIN_CALLBACK = "apply_join"`
  - `def resolve_start_branch(*, is_member: bool, state: UserState) -> str`  
    Returns one of: `"member"`, `"approved_invite"`, `"pending"`, `"in_survey"`, `"apply"`  
    Mapping:
    - `is_member` → `"member"`
    - `APPROVED` → `"approved_invite"`
    - `PENDING_APPROVAL` or `PENDING_REJECTION` → `"pending"`
    - `IN_SURVEY` → `"in_survey"`
    - else (`IDLE`, `REJECTED`, unknown) → `"apply"`

- [ ] **Step 1: Write failing matrix tests**

```python
# tests/test_start_routing.py
from database import UserState
from bot import resolve_start_branch


def test_resolve_start_branch_matrix():
    assert resolve_start_branch(is_member=True, state=UserState.IDLE) == "member"
    assert (
        resolve_start_branch(is_member=False, state=UserState.APPROVED)
        == "approved_invite"
    )
    assert (
        resolve_start_branch(is_member=False, state=UserState.PENDING_APPROVAL)
        == "pending"
    )
    assert (
        resolve_start_branch(is_member=False, state=UserState.PENDING_REJECTION)
        == "pending"
    )
    assert (
        resolve_start_branch(is_member=False, state=UserState.IN_SURVEY) == "in_survey"
    )
    assert resolve_start_branch(is_member=False, state=UserState.IDLE) == "apply"
    assert resolve_start_branch(is_member=False, state=UserState.REJECTED) == "apply"
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement resolver + rewrite `start`**

```python
# bot.py — add imports
from telegram import WebAppInfo
from config import MINI_APP_URL  # add to existing config import
from rich_messages import send_rich_message
from onboarding_copy import (
    INTRO_MARKDOWN,
    approved_invite_markdown,
    member_followup_markdown,
    pending_approval_markdown,
    survey_question_markdown,
)

APPLY_JOIN_CALLBACK = "apply_join"


def resolve_start_branch(*, is_member: bool, state: UserState) -> str:
    if is_member:
        return "member"
    if state == UserState.APPROVED:
        return "approved_invite"
    if state in (UserState.PENDING_APPROVAL, UserState.PENDING_REJECTION):
        return "pending"
    if state == UserState.IN_SURVEY:
        return "in_survey"
    return "apply"
```

Replace `start()` body after get-or-create user:

1. Detect `is_member` via existing `get_chat_member(TARGET_GROUP_ID, user_id)` try/except (same status filter as today).
2. `branch = resolve_start_branch(is_member=is_member, state=user_data.state)`
3. Always `await send_rich_message(BOT_TOKEN, user_id, INTRO_MARKDOWN, ...)` — for `apply` attach keyboard with Apply button; for others intro without that keyboard.
4. Branch:
   - `member`: send `member_followup_markdown()` with keyboard  
     `[InlineKeyboardButton("Launch Serendip", web_app=WebAppInfo(url=MINI_APP_URL))]`  
     `[InlineKeyboardButton("Open SISC group", url=group_link)]`  
     (resolve `group_link` as today’s username/invite_link logic)
   - `approved_invite`: existing `revoke_and_create_invite_link` + `approved_invite_markdown(link)` rich send; keep admin notify on missing invite rights
   - `pending`: rich `pending_approval_markdown()`
   - `in_survey`: rich `survey_question_markdown(user_data.current_question, len(SURVEY_QUESTIONS), SURVEY_QUESTIONS[user_data.current_question])` (and SISC-source keyboard if on that question)
   - `apply`: intro already had Apply keyboard; do **not** set `IN_SURVEY` here

Remove uses of `INTRO_TEXT` from `start`.

```python
async def handle_apply_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data != APPLY_JOIN_CALLBACK:
        return
    user_id = update.effective_user.id
    username = update.effective_user.username
    user_data = db.get_user(user_id) or db.create_user(user_id, username)
    user_data.state = UserState.IN_SURVEY
    user_data.current_question = 0
    user_data.answers = {}
    db.update_user(user_data)
    await send_rich_message(
        BOT_TOKEN,
        user_id,
        survey_question_markdown(0, len(SURVEY_QUESTIONS), SURVEY_QUESTIONS[0]),
    )
```

Register **before** the catch-all survey option handler:

```python
application.add_handler(
    CallbackQueryHandler(handle_apply_join, pattern=rf"^{APPLY_JOIN_CALLBACK}$")
)
application.add_handler(CallbackQueryHandler(handle_survey_option_selection))
```

- [ ] **Step 4: Run routing tests → PASS**

Run: `pytest tests/test_start_routing.py -v`

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_start_routing.py
git commit -m "$(cat <<'EOF'
feat: soft-gate /start with intro CTA and member Mini App launch

EOF
)"
```

---

### Task 5: Rich survey question + completion messages

**Files:**
- Modify: `bot.py` — `handle_survey_response`, `handle_survey_option_selection`
- Test: extend `tests/test_onboarding_copy.py` if needed; optional light test that builders are used (not required if Task 3 covers strings)

**Interfaces:**
- Consumes: `survey_question_markdown`, `survey_complete_markdown`, `send_rich_message`
- Admin notify of new applications stays `send_message` (plain/admin card) unless you already touched it — **keep admin card as `send_message`** per YAGNI (spec: optional).

- [ ] **Step 1: Identify every user-facing survey reply in those two handlers** that currently uses `reply_text` for questions / completion / “use buttons” prompts.

- [ ] **Step 2: Replace user DMs** with:

```python
await send_rich_message(
    BOT_TOKEN,
    update.effective_user.id,  # or query.from_user.id
    survey_question_markdown(
        user_data.current_question,
        len(SURVEY_QUESTIONS),
        SURVEY_QUESTIONS[user_data.current_question],
    ),
    reply_markup=reply_markup.to_dict() if reply_markup else None,
)
```

For completion:

```python
await send_rich_message(
    BOT_TOKEN,
    update.effective_user.id,
    survey_complete_markdown(),
)
```

Keep “Please send text only” / “select from buttons” as rich one-liners or short markdown using the same helper (still no silent plain fallback).

- [ ] **Step 3: Manual sanity** — or add a unit test that monkeypatches `send_rich_message` and drives one survey advance if easy; otherwise rely on copy tests + Task 4 routing.

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "$(cat <<'EOF'
feat: send survey questions as Rich Messages

EOF
)"
```

---

### Task 6: Rich approval / rejection / re-invite user DMs

**Files:**
- Modify: `bot.py` — `handle_admin_decision` user notifies (~610–666), `handle_rejection_reason` user notify (~766–769), and any other user invite resend paths in `start` already covered in Task 4

**Interfaces:**
- Consumes: `approved_invite_markdown`, `rejection_markdown`, `send_rich_message`

- [ ] **Step 1: Replace**

```python
await context.bot.send_message(
    chat_id=user_id,
    text=f"Your join request has been approved! Click here to join: {invite_link}",
)
```

with:

```python
await send_rich_message(
    BOT_TOKEN,
    user_id,
    approved_invite_markdown(invite_link),
)
```

Same for “Here’s a new invite link…” resend path and rejection:

```python
await send_rich_message(
    BOT_TOKEN,
    user_data.user_id,
    rejection_markdown(rejection_reason),
)
```

Leave admin-group messages as plain `send_message`.

- [ ] **Step 2: Commit**

```bash
git add bot.py
git commit -m "$(cat <<'EOF'
feat: send approval and rejection DMs as Rich Messages

EOF
)"
```

---

### Task 7: Verification + cleanup

**Files:**
- Modify: `bot.py` — delete unused `INTRO_TEXT` if nothing references it
- Modify: help text if it still says survey starts on `/start` without Apply (private `/help` ~888+)

- [ ] **Step 1: Grep**

```bash
rg "INTRO_TEXT|ParseMode.MARKDOWN" bot.py
rg "send_rich_message|apply_join|MINI_APP_URL" bot.py config.py
```

Remove dead `INTRO_TEXT`. Update private help copy to mention Apply + Serendip gate if it still describes the old flow.

- [ ] **Step 2: Run full unit suite for this feature**

```bash
pytest tests/test_config_mini_app_url.py tests/test_rich_messages.py tests/test_onboarding_copy.py tests/test_start_routing.py -v
```

Expected: all PASS

- [ ] **Step 3: Manual smoke (operator)**

1. Set `MINI_APP_URL=https://serendip.sisc.club` in `.env`, restart bot.  
2. Non-member `/start` → thin intro + Apply (no Q1 yet).  
3. Tap Apply → Q1 rich.  
4. Complete survey → pending rich.  
5. Approve → invite rich mentioning Serendip `/start`.  
6. Member `/start` → intro + Launch Serendip + Open group.

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "$(cat <<'EOF'
chore: drop legacy intro text and align /help with soft-gate

EOF
)"
```

---

## Spec coverage check

| Spec requirement | Task |
|------------------|------|
| Thin SISC + Serendip intro every `/start` | 3, 4 |
| Soft-gate Apply CTA | 4 |
| Member Mini App + group link | 1, 4 |
| `MINI_APP_URL` env | 1 |
| Rich Messages for survey/status/approval/rejection | 2, 5, 6 |
| No silent plain fallback | 2 |
| Keep survey/admin/invite logic | 4–6 |
| Unit tests routing + helper | 2, 4 |
| Rejected can re-apply | 4 (`apply` branch) |
| `PENDING_REJECTION` treated as pending | 4 |

## Placeholder / consistency notes

- Callback constant: `apply_join` / `APPLY_JOIN_CALLBACK` everywhere.
- Branch names: `member` | `approved_invite` | `pending` | `in_survey` | `apply`.
- Helper: `send_rich_message(...)` in `rich_messages.py`.
- Copy module: `onboarding_copy.py`.
