# LinkedIn → Telegram Forwarder Design

**Date:** 2026-11-13  
**Status:** Approved (design dialogue)  
**Company page:** https://www.linkedin.com/company/super-individual-secret-club  
**Telegram target:** `TARGET_GROUP_ID` from `.env` (SISC main group)

## Problem

SISC posts updates on its LinkedIn company page. Those posts should appear in the Telegram community group with the same commentary text and attached images, soon after LinkedIn publishes them.

Community Management API access is applied for but not yet approved. The app already has Auth Client ID and Primary Client Secret.

## Goals

- Detect new organic organization posts via LinkedIn official Community Management APIs (Posts + Images).
- Forward new posts to Telegram `TARGET_GROUP_ID` with:
  - Same commentary text (plain text; no Markdown parse mode).
  - Attached images downloaded and re-uploaded (single photo or media group).
  - LinkedIn post permalink always appended on the last line.
- Seed-only first run: remember existing posts; send nothing until a truly new post appears.
- Run as a cron poller every 5 minutes (same operational shape as Luma discovery).

## Non-goals (v1)

- LinkedIn webhooks / long-running daemon.
- Editing or deleting Telegram messages when LinkedIn edits/deletes a post.
- Forwarding polls or documents.
- Downloading or uploading LinkedIn videos to Telegram.
- Comment/like sync, analytics, or posting back to LinkedIn.

## Architecture

Cron poller modeled on `luma_discovery.py` / `luma_discovery.sh`.

```text
crontab */5
  → linkedin_forwarder.sh
    → linkedin_forwarder.py
        → fcntl lock
        → refresh LinkedIn token if needed
        → GET Posts by organization author
        → diff vs seen state
        → first run: seed seen, exit
        → else: map + send new posts to Telegram
        → mark seen after success or intentional skip
```

Not started by `start_all.sh`.

### Components

| Piece | Responsibility |
|---|---|
| `linkedin_oauth.py` | One-shot OAuth: authorize URL, exchange code, persist tokens, optionally list administered orgs |
| `linkedin_forwarder.py` | Cron entry: lock, refresh, poll, diff, forward, state |
| `linkedin_forwarder.sh` | VPS wrapper: venv, tee to `linkedin_forwarder_output.log` |
| `linkedin/` package (optional split) | HTTP client, token store, post mapper, Telegram send helpers — extract from the main module if it grows past a readable size |
| `data/linkedin_forwarder_state.json` | `seeded` flag + `seen` map of post URNs |
| `data/linkedin_tokens.json` | Access/refresh tokens + expiry metadata (writable by cron refresh; prefer over rewriting `.env`) |
| `data/linkedin_forwarder.lock` | Exclusive lock so overlapping cron runs exit cleanly |

### Data flow

1. Acquire lock; if held, exit 0.
2. Ensure valid access token (refresh if expired/near expiry/401).
3. Fetch recent posts for `urn:li:organization:{LINKEDIN_ORGANIZATION_ID}`.
4. If not seeded: write all returned post IDs to `seen`, set `seeded=true`, exit.
5. For each unseen post (oldest first among the new set):
   - Classify content type.
   - Forward or intentionally skip.
   - On success/skip, mark seen and persist state incrementally.
6. Prune `seen` to a capped size (keep newest ~500 IDs).

## Auth and configuration

### Environment

| Variable | Purpose |
|---|---|
| `LINKEDIN_CLIENT_ID` | App client ID |
| `LINKEDIN_CLIENT_SECRET` | App client secret |
| `LINKEDIN_ORGANIZATION_ID` | Numeric org ID for SISC company page |
| `LINKEDIN_REDIRECT_URI` | Must match the app’s Auth settings (OAuth helper) |
| `BOT_TOKEN` | Existing Telegram bot token |
| `TARGET_GROUP_ID` | Existing SISC Telegram group |

Tokens live in `data/linkedin_tokens.json` after `linkedin_oauth.py` (and after automatic refresh). Client ID/secret stay in `.env`.

### OAuth

- Page admin completes LinkedIn OAuth once via `linkedin_oauth.py`.
- Required product: Community Management API (pending approval).
- Scopes: at minimum `r_organization_social` to read org posts; include org-admin read scope(s) needed to resolve `LINKEDIN_ORGANIZATION_ID` if not set manually (e.g. `rw_organization_admin` / `r_organization_admin` as LinkedIn’s current product docs require).
- Access token ~60 days; refresh token ~365 days when LinkedIn issues programmatic refresh tokens for the app.
- Each cron run refreshes when needed and atomically rewrites the token file.
- Refresh failure: log clearly, exit non-zero, do not mark posts seen; operator re-runs `linkedin_oauth.py`.

### API conventions

- Base: `https://api.linkedin.com/rest/`
- Headers: `Authorization: Bearer …`, `X-Restli-Protocol-Version: 2.0.0`, `LinkedIn-Version: YYYYMM` pinned in one constant.
- Posts finder: author = organization URN, `sortBy=CREATED`, reader view, paginate with a small `count` sufficient for “what’s new since last poll” (plus recovery window).

## Content mapping

Permalink for every forwarded post:

`https://www.linkedin.com/feed/update/{postUrn}`

where `{postUrn}` is `urn:li:share:…` or `urn:li:ugcPost:…`.

| LinkedIn content | Telegram behavior |
|---|---|
| Text only | `sendMessage`: body + permalink |
| Single image | Images API `downloadUrl` → `sendPhoto`; caption = body + permalink |
| Multi-image | Download each → `sendMediaGroup`; first item caption = body + permalink |
| Article / link | `sendMessage`: body + article URL (if present) + permalink |
| Video | `sendMessage`: body + permalink only (no video download) |
| Poll / document / unknown | Log skip; mark seen |

### Caption rules

- Plain text only (Telegram parse mode off) so underscores/URLs in LinkedIn copy do not break.
- Always append permalink on its own last line.
- If over Telegram caption/message limits: truncate body, keep permalink.

### Seen marking

- Mark seen only after successful Telegram send, or after intentional skip.
- Download/Telegram failure: leave unseen; retry next cron.
- Partial media-group failure: do not mark seen; retry whole post next run.

## Scheduling and ops

- Crontab: `*/5 * * * * /root/tg_sentinel/linkedin_forwarder.sh`
- If LinkedIn rate-limits in practice, change crontab to `*/15` (no code change required).
- Log file: `linkedin_forwarder_output.log`
- CLI flags:
  - `--dry-run`: auth + fetch + diff; no Telegram; no writes to `linkedin_forwarder_state.json`. Token refresh may still update `linkedin_tokens.json` so an expired access token does not block the dry fetch.
  - `--seed-only`: write seed state only (mark current posts seen); never send to Telegram

### Error handling

| Case | Behavior |
|---|---|
| Lock held | Exit 0, log “already running” |
| Refresh token invalid | Exit 1, loud log; no seen updates |
| LinkedIn 429 | Short backoff once, then exit; next cron retries |
| Telegram send failure | Do not mark seen |
| CMA not approved yet | Live calls fail; offline tests + `--dry-run` still ship |

v1 may log-only for auth failures; wiring `admin_alert` is optional polish, not required for correctness.

## Testing

Offline tests with fixture Posts/Images payloads (no live LinkedIn):

- Content-type classification (text, image, multiImage, article, video, poll/document).
- Seed vs forward behavior on empty vs seeded state.
- Caption + permalink formatting and truncation.
- Intentional skips still mark seen.
- Token refresh persistence helpers with mocked HTTP.

Live smoke (after CMA approval):

1. Run `linkedin_oauth.py` as page admin; set org ID.
2. Run forwarder once → seed, no Telegram messages.
3. Publish a test company post with image → within 5 minutes, Telegram group receives text + image + permalink.
4. Re-run immediately → no duplicate.

## Dependencies on existing repo patterns

- Shell/cron layout: `luma_discovery.sh`, `luma_reminder.sh`
- State + lock: `luma_discovery.py` (`fcntl`, JSON `seen`, atomic `*.tmp` + `os.replace`)
- Telegram HTTP Bot API: `luma_discovery.py` `send_telegram*` helpers (extend with photo/media group)
- Config: `config.py` / `.env.example` for new LinkedIn vars; reuse `BOT_TOKEN`, `TARGET_GROUP_ID`
- README: document service row + crontab + OAuth one-shot

## Open external dependency

Implementation can land before Community Management API approval. End-to-end forwarding is blocked until LinkedIn approves the product and org-admin OAuth can obtain `r_organization_social`.
