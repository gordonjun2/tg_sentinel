# SISC + Serendip `/start` Onboarding Design

**Date:** 2026-11-15  
**Status:** Approved (design dialogue)  
**Repo:** `tg_sentinel`  
**Primary code:** `bot.py`  
**Related vision:** `serendip-tg/docs/knowledge/SERENDIP_TG_VISION.md` (Unified Bot Architecture)

## Problem

Newcomers who `/start` the SISC gate bot get a SISC-only intro and immediately enter the survey. They are not told what Serendip is, or that Serendip requires SISC membership first. Existing members who `/start` only get a group link, with no way to launch the Serendip Mini App.

## Goals

- Keep the existing survey → admin approval → invite → join SISC flow.
- On every `/start`, introduce SISC and Serendip in a short, scannable message.
- Soft-gate the survey: show intro + **Apply to join SISC** CTA; start the survey only after the tap.
- After the user is in the SISC group, `/start` offers **Launch Serendip Mini App** plus **Open SISC group**.
- Beautify user-facing onboarding messages with Telegram **Rich Messages** (Bot API `sendRichMessage`), including survey question and status copy.

## Non-goals

- Full vision “main menu” (quiz / matches / profile deep links). One Mini App launch URL is enough.
- Changing survey questions, admin approve/reject logic, invite-link mechanics, or membership source of truth.
- Polishing group welcome-batch messages, LinkedIn forwarder, audio/transcription, or other admin commands in this change.
- Silently falling back from Rich Messages to plain text.

## Decisions (from brainstorming)

| Topic | Choice |
|-------|--------|
| Non-member `/start` | Intro + CTA button, then survey |
| Member `/start` | Welcome + Launch Serendip + Open SISC group |
| Intro frequency | Always show intro, then state-specific next step |
| Mini App URL | Env `MINI_APP_URL`, value `https://serendip.sisc.club` |
| Intro copy | Thin draft from SISC brand + Serendip vision; not a long essay |
| Formatting | Full Rich Messages (`sendRichMessage`), not legacy HTML `parse_mode` only |
| Media delivery | Approach 1: `sendAnimation` then Rich text (not media-inside-rich). No client-capability fallback (API success ≠ client can render rich). |
| Non-member `/start` media | SISC hero GIF (title + tagline only) before intro |
| Post-approval media | Serendip welcome GIF (mascot + onboarding title/body baked below) before invite Rich message |

## `/start` routing

Membership in `TARGET_GROUP_ID` remains the source of truth for “in group”.

```text
/start (private chat only)
    │
    ├─ always send thin Rich intro (SISC + Serendip + gate)
    │
    ├─ IN GROUP ──────────────────► member keyboard:
    │                               • Launch Serendip (WebApp → MINI_APP_URL)
    │                               • Open SISC group (url)
    │
    ├─ APPROVED, not in group ────► revoke/create invite link (existing helper)
    ├─ PENDING_APPROVAL ──────────► “waiting for review” status
    ├─ PENDING_REJECTION ─────────► same as pending (admin still deciding), or wait for reason flow to finish
    ├─ IN_SURVEY ─────────────────► resume current question (rich progress)
    └─ IDLE / REJECTED / new ─────► keyboard: Apply to join SISC
                                       └─ callback → set IN_SURVEY, reset answers, ask Q1
```

Do not auto-start the survey on `/start` for new users. Rejected users may re-apply via the same CTA.

## Thin intro content (Rich Markdown)

One scannable rich message. Exact wording may be tuned at implement time; structure is fixed:

1. Short welcome line  
2. **SISC** — 2–3 short sentences (tight-knit AI community; founders, builders, curious minds)  
3. Short bullet list: Urgency, Safe Space, Serendipity  
4. Link to https://www.sisc.club/  
5. **Serendip** — short blurb: Telegram Mini App; AI matches by personality, goals, and intent  
6. Gate line: to use Serendip, join SISC first  
7. State-specific follow-up via keyboard or a short second message (status / invite / question)

Long SISC brand paragraphs stay on the website, not in the first `/start` bubble.

### Draft intro (starting point)

```markdown
# Welcome

## SISC
The Super-Individual Secret Club is a tight-knit community for curious minds who create the future with AI. Founders, investors, operators, researchers, and artists gather here to stay ahead and collaborate with intention.

**What we stand for**
- **Urgency** — stay AI-ready as the space moves fast
- **A safe space** — explore hard topics privately
- **Serendipity** — meet brilliant minds and prototype the future

[Learn more about SISC](https://www.sisc.club/)

---

## Serendip
Serendip is a Telegram Mini App that helps the right people find each other. Share your personality, goals, and intentions, and AI surfaces meaningful matches for you.

**To use Serendip, you need SISC access first.**
```

## Survey and status beautify

Same questions and branching (`How do you know about SISC?` buttons, referral follow-up). Presentation only:

| Moment | Presentation |
|--------|----------------|
| Each question | Rich message: `## Question N of M` + question text; optional one-line helper |
| Survey complete | Rich “thanks, pending review” with next-steps bullets |
| Approval DM | Rich success + join invite + “after you join, `/start` again to open Serendip” |
| Rejection DM | Clear, calm tone including admin reason |
| Admin approval card | Optional light rich formatting; Approve Main / Sub / Reject buttons unchanged |

## Architecture

```text
bot.py (handlers, routing)
  ├── copy constants (intro / survey / status markdown)
  ├── rich_messages.send_rich_message(...)
  │     └── HTTPS POST api.telegram.org/bot<token>/sendRichMessage
  │           InputRichMessage { markdown }
  │           + reply_markup (PTB InlineKeyboardMarkup serialized)
  └── existing: db, revoke_and_create_invite_link, survey callbacks, admin flow
```

`python-telegram-bot` 22.7 has no `send_rich_message`. Use a thin raw Bot API helper for Rich Messages. Keep PTB for Application, handlers, membership checks, invite links, and inline keyboards.

### Config

- Add `MINI_APP_URL` to `config.py` / `.env` / `.env.example`.
- Required at boot: if unset or empty, raise a clear config error when `config` loads (do not start the bot half-configured).

### Callback

- New callback data for Apply CTA, e.g. `apply_join` (distinct from survey option callbacks).
- Handler sets `UserState.IN_SURVEY`, `current_question = 0`, clears/resets answers as today’s survey start does, then sends Q1 as a rich message.

## Error handling

- Rich Message API failures: log the Telegram error and raise; do not silently degrade to plain `sendMessage`.
- Membership lookup failure: keep current behavior (treat as not confirmed in-group; continue with DB state routing).
- Invite-link permission errors: keep current admin-notify path.

## Testing

- Unit-test `/start` routing matrix with mocked bot + DB: in-group, approved-not-joined, pending, in_survey, new/idle/rejected.
- Unit-test Apply CTA starts survey at Q1.
- Unit-test rich helper request body includes `rich_message.markdown` and optional `reply_markup`.
- No live Telegram network calls in automated tests.

## Files likely to change

- `bot.py` — `/start`, apply callback, survey/status user DMs via rich helper  
- `config.py`, `.env.example` — `MINI_APP_URL`  
- New: `rich_messages.py` (name flexible)  
- New or extended: copy module / constants  
- New: unit tests for routing + rich helper  

## Success criteria

1. Non-member `/start` shows thin SISC + Serendip intro and Apply CTA; survey does not start until Apply.  
2. Pending / mid-survey / approved-not-joined still see intro, then the correct status step.  
3. In-group `/start` shows Launch Serendip (`https://serendip.sisc.club`) and Open SISC group.  
4. Survey questions and completion/approval/rejection user messages use Rich Messages.  
5. Existing admin approval workflow still works end-to-end.  
6. Non-member `/start` sends SISC hero GIF before the Rich intro.  
7. Approval invite DM sends Serendip welcome GIF (copy baked into the image) before the invite Rich message.
