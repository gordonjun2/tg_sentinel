# Hidden `/serendip` Dogfood Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep public `/start` SISC-only and move Serendip pitch, welcome animation, and Launch Mini App behind an unlisted `/serendip` command on the production gate bot.

**Architecture:** Split copy in `onboarding_copy.py` into SISC intro vs Serendip pitch. `/start` stops advertising Serendip and drops the member Launch button. New `serendip` command handler reuses membership checks + `MINI_APP_URL` WebApp button. Approve/resend paths stop sending `SERENDIP_WELCOME_ANIM`. Do not register `/serendip` in `setMyCommands`.

**Tech Stack:** Python 3, `python-telegram-bot`, existing `rich_messages.send_rich_message` / `send_animation`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-11-15-hidden-serendip-command-design.md`

## File structure

| Path | Responsibility |
|------|----------------|
| `onboarding_copy.py` | SISC-only intro; Serendip pitch + gate/member follow-ups; strip Serendip CTAs from status copy |
| `bot.py` | `/serendip` handler; `/start` member keyboard; remove approval Serendip animation; `/help`; register handler without commands menu |
| `tests/test_onboarding_copy.py` | Assert public copy has no Serendip product CTA; Serendip pitch exists |
| `tests/test_start_routing.py` | Optional pure helper for `/serendip` member vs gate if extracted |

---

### Task 1: Split onboarding copy (TDD)

**Files:**
- Modify: `onboarding_copy.py`
- Modify: `tests/test_onboarding_copy.py`

- [ ] **Step 1: Rewrite failing copy tests**

Replace `tests/test_onboarding_copy.py` with:

```python
from onboarding_copy import (
    INTRO_MARKDOWN,
    SERENDIP_PITCH_MARKDOWN,
    approved_invite_markdown,
    member_followup_markdown,
    pending_approval_markdown,
    rejection_markdown,
    serendip_gate_markdown,
    serendip_member_followup_markdown,
    survey_complete_markdown,
    survey_question_markdown,
)


def test_intro_is_sisc_only_no_serendip_product():
    assert "SISC" in INTRO_MARKDOWN
    assert "sisc.club" in INTRO_MARKDOWN
    assert "## Serendip" not in INTRO_MARKDOWN
    assert "Mini App" not in INTRO_MARKDOWN
    assert "SISC access first" not in INTRO_MARKDOWN


def test_serendip_pitch_has_product_and_gate():
    assert "## Serendip" in SERENDIP_PITCH_MARKDOWN
    assert "Mini App" in SERENDIP_PITCH_MARKDOWN
    assert "SISC" in SERENDIP_PITCH_MARKDOWN


def test_public_status_copy_omits_serendip_cta():
    assert "Serendip" not in survey_complete_markdown()
    assert "Serendip" not in approved_invite_markdown("https://t.me/+x")
    assert "Serendip" not in member_followup_markdown()
    assert "Serendip" not in pending_approval_markdown()
    assert "Serendip" not in rejection_markdown("Nope")


def test_serendip_command_copy():
    assert "join SISC" in serendip_gate_markdown().lower() or "SISC" in serendip_gate_markdown()
    assert "/start" in serendip_gate_markdown()
    assert "Serendip" in serendip_member_followup_markdown()


def test_survey_question_uses_one_based_of_total():
    text = survey_question_markdown(0, 3, "Why do you want to join?")
    assert "1 of 3" in text
    assert "Why do you want to join?" in text
    assert "Reply with your answer as text." in text


def test_survey_question_omits_text_hint_for_button_questions():
    text = survey_question_markdown(
        5, 8, "How do you know about SISC?", text_reply_hint=False
    )
    assert "How do you know about SISC?" in text
    assert "Choose an option below." in text
    assert "Reply with your answer as text." not in text


def test_rejection_includes_reason():
    text = rejection_markdown("Not a fit right now")
    assert "Not a fit right now" in text
    assert "Request not approved" in text


def test_survey_complete_and_pending_and_member_followup():
    assert "Application received" in survey_complete_markdown()
    assert "Pending review" in pending_approval_markdown()
    assert "You are in" in member_followup_markdown()


def test_approved_invite_includes_link():
    link = "https://t.me/+example"
    text = approved_invite_markdown(link)
    assert link in text
    assert "You are approved" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/gordonjun/Desktop/Projects/tg_sentinel && ./venv/bin/pytest tests/test_onboarding_copy.py -v
```

Expected: FAIL (missing symbols and/or old intro still contains `## Serendip`).

- [ ] **Step 3: Update `onboarding_copy.py`**

Replace file contents with:

```python
INTRO_MARKDOWN = """# Welcome

## SISC
The Super-Individual Secret Club is a tight-knit community for curious minds who create the future with AI. Founders, investors, operators, researchers, and artists gather here to stay ahead and collaborate with intention.

**What we stand for**
- **Urgency** — stay AI-ready as the space moves fast
- **A safe space** — explore hard topics privately
- **Serendipity** — meet brilliant minds and prototype the future

[Learn more about SISC](https://www.sisc.club/)
"""

SERENDIP_PITCH_MARKDOWN = """# Serendip

## Serendip
Serendip is a Telegram Mini App that helps the right people find each other. Share your personality, goals, and intentions, and AI surfaces meaningful matches for you.

**To use Serendip, you need SISC access first.**
"""


def survey_question_markdown(
    index: int, total: int, question: str, text_reply_hint: bool = True
) -> str:
    hint = (
        "_Reply with your answer as text._"
        if text_reply_hint
        else "_Choose an option below._"
    )
    return f"## Question {index + 1} of {total}\n\n{question}\n\n{hint}"


def survey_complete_markdown() -> str:
    return (
        "## Application received\n\n"
        "Thanks for completing the questions.\n\n"
        "**What happens next**\n"
        "- Admins review your answers\n"
        "- You get a DM when approved or rejected\n"
        "- After you join SISC, send /start again if you need the group link"
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
        "After you join, send /start again if you need help finding the group."
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
        "Jump back to the SISC group below."
    )


def serendip_gate_markdown() -> str:
    return (
        "## SISC access required\n\n"
        "Serendip is for SISC members.\n\n"
        "Send /start to apply or check your join status."
    )


def serendip_member_followup_markdown() -> str:
    return (
        "## Launch Serendip\n\n"
        "Open the Mini App below, or jump back to the SISC group."
    )
```

Note: keep the SISC value bullet **Serendipity** (brand value). Product pitch lives only in `SERENDIP_PITCH_MARKDOWN`.

- [ ] **Step 4: Run copy tests**

```bash
./venv/bin/pytest tests/test_onboarding_copy.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add onboarding_copy.py tests/test_onboarding_copy.py
git commit -m "$(cat <<'EOF'
refactor: keep Serendip pitch off public /start copy

EOF
)"
```

---

### Task 2: Pure `/serendip` branch helper (TDD)

**Files:**
- Modify: `bot.py` (near `resolve_start_branch`)
- Modify: `tests/test_start_routing.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_start_routing.py`:

```python
from bot import resolve_serendip_branch


def test_resolve_serendip_branch():
    assert resolve_serendip_branch(is_member=True) == "member"
    assert resolve_serendip_branch(is_member=False) == "gate"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./venv/bin/pytest tests/test_start_routing.py::test_resolve_serendip_branch -v
```

Expected: FAIL (`ImportError` / attribute missing).

- [ ] **Step 3: Add helper in `bot.py`**

Next to `resolve_start_branch`:

```python
def resolve_serendip_branch(*, is_member: bool) -> str:
    if is_member:
        return "member"
    return "gate"
```

- [ ] **Step 4: Run routing tests**

```bash
./venv/bin/pytest tests/test_start_routing.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot.py tests/test_start_routing.py
git commit -m "$(cat <<'EOF'
feat: add resolve_serendip_branch helper

EOF
)"
```

---

### Task 3: Wire `/serendip` + strip Serendip from `/start` / approve / help

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Update imports from `onboarding_copy`**

Ensure `bot.py` imports:

```python
from onboarding_copy import (
    INTRO_MARKDOWN,
    SERENDIP_PITCH_MARKDOWN,
    approved_invite_markdown,
    member_followup_markdown,
    pending_approval_markdown,
    rejection_markdown,
    serendip_gate_markdown,
    serendip_member_followup_markdown,
    survey_complete_markdown,
    survey_question_markdown,
)
```

(Keep any other existing imports from that module if present.)

- [ ] **Step 2: Change `/start` member branch**

In `start()`, replace the member keyboard block so it does **not** add Launch Serendip:

```python
    if branch == "member":
        member_rows = []
        if group_link:
            member_rows.append(
                [InlineKeyboardButton("Open SISC group", url=group_link)]
            )
        member_markup = (
            InlineKeyboardMarkup(member_rows) if member_rows else None
        )
        await send_rich_message(
            BOT_TOKEN,
            user_id,
            member_followup_markdown(),
            reply_markup=member_markup.to_dict() if member_markup else None,
        )
        return
```

If `send_rich_message` requires `reply_markup` to be omitted rather than `None`, match the existing helper signature (pass only when `member_rows` non-empty).

- [ ] **Step 3: Add `serendip_command` handler**

Add near `start` / after `help_command` is fine; prefer after `start`:

```python
async def serendip_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Unlisted dogfood entry: Serendip pitch + Launch for SISC members."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    username = update.effective_user.username

    user_data = db.get_user(user_id)
    if not user_data:
        user_data = db.create_user(user_id, username)

    is_member = False
    group_link = None
    try:
        member = await context.bot.get_chat_member(TARGET_GROUP_ID, user_id)
        if member.status not in ["left", "kicked", "banned"]:
            is_member = True
            chat = await context.bot.get_chat(TARGET_GROUP_ID)
            if chat.username:
                group_link = f"https://t.me/{chat.username}"
            else:
                group_link = chat.invite_link
    except Exception:
        pass

    branch = resolve_serendip_branch(is_member=is_member)

    await send_animation(BOT_TOKEN, user_id, SERENDIP_WELCOME_ANIM)
    await send_rich_message(BOT_TOKEN, user_id, SERENDIP_PITCH_MARKDOWN)

    if branch == "member":
        rows = [
            [
                InlineKeyboardButton(
                    "Launch Serendip", web_app=WebAppInfo(url=MINI_APP_URL)
                )
            ]
        ]
        if group_link:
            rows.append(
                [InlineKeyboardButton("Open SISC group", url=group_link)]
            )
        await send_rich_message(
            BOT_TOKEN,
            user_id,
            serendip_member_followup_markdown(),
            reply_markup=InlineKeyboardMarkup(rows).to_dict(),
        )
        return

    await send_rich_message(BOT_TOKEN, user_id, serendip_gate_markdown())
```

- [ ] **Step 4: Remove Serendip animation from approve / resend**

In the approve path and the “already approved, resend invite” path, delete both lines:

```python
await send_animation(BOT_TOKEN, user_id, SERENDIP_WELCOME_ANIM)
```

Keep `send_rich_message(..., approved_invite_markdown(invite_link))`.

`SERENDIP_WELCOME_ANIM` import remains (used by `/serendip`).

- [ ] **Step 5: Update private `/help` text**

Replace the private-chat `help_text` with SISC-only wording (no `/serendip`, no Mini App):

```python
        help_text = """
*Available Commands*

/start - Open SISC intro
• Apply to join SISC (survey starts after you tap Apply)
• Check pending / rejected status
• Members: Open SISC group

/help - Show this help message with command list

*How to Join*
1. Use /start to see the intro
2. Tap *Apply to join SISC* to start the survey
3. Answer all survey questions
4. Wait for admin approval
5. Once approved, you'll receive an invite link
6. After you join the group, /start again for the group link

Note: Each invite link can only be used once and expires after use.
"""
```

- [ ] **Step 6: Register handler; do not add to `user_commands`**

After the `/start` handler registration:

```python
    application.add_handler(
        CommandHandler(
            "serendip", serendip_command, filters=private_chat_filter
        )
    )
```

Leave `user_commands` as only `start` and `help`.

- [ ] **Step 7: Run full unit suite for touched areas**

```bash
./venv/bin/pytest tests/test_onboarding_copy.py tests/test_start_routing.py tests/test_onboarding_media.py tests/test_rich_messages.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add bot.py
git commit -m "$(cat <<'EOF'
feat: hide Serendip behind unlisted /serendip

EOF
)"
```

---

### Task 4: Spec status + manual dogfood checklist

**Files:**
- Modify: `docs/superpowers/specs/2026-11-15-hidden-serendip-command-design.md` (Status → Approved / Implemented)

- [ ] **Step 1: Mark spec status**

Set header `**Status:**` to `Approved (implemented)`.

- [ ] **Step 2: Manual check after deploy**

1. Non-tester `/start`: SISC intro only; no Launch Serendip; no `## Serendip` product section.
2. Member `/start`: group link only.
3. Approve a user: invite DM, **no** Serendip animation.
4. Tester types `/serendip`: animation + pitch; member gets Launch; non-member gets gate + `/start` hint.
5. `/` command menu: no `serendip` entry.
6. Launch opens `https://serendip.sisc.club` and authenticates (real bot token).

- [ ] **Step 3: Commit docs**

```bash
git add docs/superpowers/specs/2026-11-15-hidden-serendip-command-design.md docs/superpowers/plans/2026-11-15-hidden-serendip-command.md
git commit -m "$(cat <<'EOF'
docs: hidden /serendip dogfood spec and plan

EOF
)"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| SISC-only `/start` intro | Task 1 + 3 |
| No Launch on `/start` member | Task 3 |
| Strip Serendip CTAs from status copy | Task 1 |
| Unlisted `/serendip` with animation + pitch | Task 3 |
| Member Launch + optional group link | Task 3 |
| Non-member gate, no survey start | Task 3 |
| No approve/resend Serendip animation | Task 3 |
| `/help` without Serendip / `/serendip` | Task 3 |
| Not in `setMyCommands` | Task 3 |
| Prod Mini App via real bot | unchanged `MINI_APP_URL` + WebApp button |
