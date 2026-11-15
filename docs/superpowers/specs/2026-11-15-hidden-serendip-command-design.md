# Hidden `/serendip` Dogfood Design

**Date:** 2026-11-15  
**Status:** Approved (implemented)  
**Repo:** `tg_sentinel`  
**Primary code:** `bot.py`, `onboarding_copy.py`  
**Related:** `2026-11-15-sisc-serendip-start-onboarding-design.md`

## Problem

The new SISC + Serendip `/start` flow advertises Serendip (intro section, member Launch button, post-approval welcome animation, help copy) to every user. We want to dogfood Serendip in production with a few people on the real gate bot and prod Mini App (`https://serendip.sisc.club`), without the majority noticing Serendip yet.

A second “proxy” bot cannot open a working prod Mini App session: Telegram signs `initData` with the launching bot’s token, and Serendip validates a single `TELEGRAM_BOT_TOKEN`.

## Goals

- Keep public `/start` as a SISC join / status flow only.
- Expose Serendip pitch + Launch only via an unlisted `/serendip` command on the **same** production gate bot (so Mini App auth works).
- Testers can be mixed: some already in SISC, some still applying.
- Flip to public later by restoring Serendip surfaces on `/start` (out of scope for this change’s “done,” but design must not paint into a corner).

## Non-goals

- Telegram-ID allowlist or feature-flag service.
- Second bot / dual-token Serendip auth.
- Locking down the Mini App URL itself (anyone who already knows `serendip.sisc.club` can still open it).
- Changing survey questions, approval logic, or invite-link mechanics.
- Registering `/serendip` in BotFather / `setMyCommands` (that would advertise it).

## Decision

| Topic | Choice |
|-------|--------|
| Discovery control | Obscurity: unlisted `/serendip` |
| Bot | Production gate bot (same token as Mini App auth) |
| Mini App URL | Existing `MINI_APP_URL` → `https://serendip.sisc.club` |
| Post-approval animation | **Only** on `/serendip`, never on Approve / resend-invite |
| Public `/start` member keyboard | Open SISC group only (no Launch Serendip) |

## Behavior

### `/start` (private chat)

Unchanged routing for apply / pending / approved invite / in-survey / rejected.

Changes:

1. Intro Rich message is **SISC-only** (no `## Serendip` section, no “need SISC for Serendip” gate line).
2. Member branch: follow-up copy does **not** mention Serendip; keyboard is **Open SISC group** only when a group link exists (no Launch button).
3. Status / invite / survey-complete copy does **not** tell users to open Serendip via `/start`.

SISC hero animation on non-member `/start` stays.

### `/serendip` (private chat, unlisted)

1. Resolve membership via existing `get_chat_member(TARGET_GROUP_ID, …)` (same as `/start`).
2. Always send `SERENDIP_WELCOME_ANIM`, then Serendip pitch Rich message (content moved out of public intro).
3. If member: inline keyboard **Launch Serendip** (`WebAppInfo(url=MINI_APP_URL)`), plus **Open SISC group** when a group link exists.
4. If not member: short gate Rich message (join SISC first; use `/start` to apply or check status). No Launch button.
5. Do **not** start the survey from `/serendip`.
6. Do **not** add `serendip` to `user_commands` / `setMyCommands`.

Handler registration: `CommandHandler("serendip", …)` only. Operators tell testers the command out of band.

### Approve / resend invite

Remove `send_animation(…, SERENDIP_WELCOME_ANIM)` from approve and already-approved resend paths. Keep invite Rich message only, without Serendip CTA wording.

### `/help` (private)

Describe SISC join flow only. Do not list `/serendip` or mention Launch Serendip / Mini App.

## Copy ownership

| Surface | Serendip content? |
|---------|-------------------|
| `/start` intro | No (SISC only) |
| `/start` member follow-up | No |
| Survey complete / approved invite / pending / rejection | No Serendip CTAs |
| `/serendip` pitch + Launch | Yes |
| Post-approval DM | Invite only; no Serendip animation |
| Private `/help` | No Serendip |

Brand bullet **Serendipity** under SISC values may remain (community value name, not the Mini App product pitch).

## Security note

This is not access control. Anyone who learns `/serendip` and is a SISC member can Launch. Acceptable for a small dogfood. For a hard gate later, switch to allowlist or public launch when ready.

## Files to change

| File | Change |
|------|--------|
| `onboarding_copy.py` | Split SISC intro vs Serendip pitch; strip Serendip CTAs from status/member/help-related strings |
| `bot.py` | `/serendip` handler; `/start` member keyboard/copy; remove approval animations; `/help` text; register handler without `setMyCommands` entry |
| Tests (`test_onboarding_copy.py`, `test_start_routing.py`, new `/serendip` tests as needed) | Assert public surfaces omit Serendip product CTA; `/serendip` member vs non-member |

## Rollout

1. Deploy `tg_sentinel` with the above.
2. Share `/serendip` with the four testers only.
3. Members: `/serendip` → Launch. Non-members: finish join via `/start`, then `/serendip`.
4. When ready for everyone: restore Serendip on `/start` (intro + member Launch + optional approval animation) and/or list the command.

## Open questions

None. Approve animation: only on `/serendip` (confirmed).
