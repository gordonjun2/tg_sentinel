# Proposal: partner-message-summarisation

## Why

Valuable conversations in the partner (SISC `<>&hellip;`) Telegram groups scroll past unnoticed — there is no way to review what happened across all groups each day without reading every chat manually. A nightly, LLM-generated digest delivered to the admin group turns raw group chatter into a short, prioritized daily briefing, and a persistent message archive makes the history queryable.

## What Changes

- New standalone long-running service package `partner_message_summarisation/` (mirrors the `gmail_calendar/` conventions; entry point `python -m partner_message_summarisation.bot`), fully decoupled from the main bot process.
- **Continuous ingestion**: a Pyrogram *user-account* client (one-time interactive login, session file under `partner_message_summarisation/data/`) listens to all groups whose title matches `^SISC <>` (configurable regex) and stores every message — text, sender, reply/forward/media metadata — into hosted PostgreSQL with `(chat_id, message_id)` dedup.
- **Bounded catch-up**: on startup, per-chat history backfill (default 200 msgs/chat) recovers messages missed while the process was down; first-ever run for a chat starts from "now" (no history import).
- **Daily digest**: a scheduler loop fires at 19:00 Asia/Singapore (configurable), fetches all pending messages, builds per-group transcripts, and produces a structured `DailyDigest` (highlights + ranked insights with source-group attribution) via Gemini primary / instructor+OpenAI fallback.
- **Delivery & commit semantics**: the digest renders as plain text, splits into ≤4,096-char ordered parts, and is sent to `ADMIN_GROUP_ID` by a bot-token Pyrogram client (MTProto send-only). Messages are marked `completed` only after **all** parts are delivered; any failure leaves them `pending` so the next run retries (at-least-once). Concurrent runs are excluded by a Postgres advisory lock.
- **New dependency**: `psycopg[binary]>=3.2` (async Postgres driver, raw-SQL style — no ORM).
- **Config additions** to root `config.py` / `.env.example`: `DATABASE_URL` (required, fail-fast), `SISC_GROUP_REGEX`, `SENTINEL_TIMEZONE`, `SENTINEL_SUMMARY_TIME`, model overrides, catch-up/transcript caps, `SENTINEL_DRY_RUN`.
- **Observability**: `summary_runs` table (run history, provider, latency, counts, errors) and a tiny `python -m partner_message_summarisation.status` CLI (backlog, last runs, monitored chats).
- Deployment script `partner_message_summarisation/partner_message_summarisation_bot.sh` (nohup pattern) + README with login/VPS/runbook instructions.

Assumptions recorded: "partner" groups = the SISC `<>&hellip;` groups described in `TELEGRAM_INTELLIGENCE_PIPELINE_PLAN.md`; Open decisions D1–D6 resolved to the plan's recommended defaults (psycopg, English plain-text report, `CREATE TABLE IF NOT EXISTS` migrations, 200-msg catch-up bound, silent skip on empty days, no `/run_digest` command in v1).

## Capabilities

### New Capabilities

- `partner-message-ingestion`: Captures messages from Telegram groups whose titles match the configured partner-group regex into PostgreSQL in real time — group discovery, full metadata capture, `(chat_id, message_id)` dedup, bounded catch-up backfill after downtime, and failed-insert retry.
- `daily-digest`: Produces and delivers the nightly partner digest — 19:00 Asia/Singapore scheduling, pending-message window selection under an advisory lock, transcript building with caps/chunking, dual-provider LLM summarization into a structured digest, plain-text rendering split ≤4,096 chars, delivery to the admin group, and completed/pending state transitions with run auditing.

### Modified Capabilities

_(none — this change introduces only new capabilities; no existing spec requirements change)_

## Impact

- **New code**: `partner_message_summarisation/` package — `config.py`, `db.py`, `groups.py`, `listener.py`, `scheduler.py`, `summarizer.py`, `report.py`, `notifier.py`, `bot.py`, `login.py`, `status.py`, `partner_message_summarisation_bot.sh`, `README.md`, `tests/`.
- **Modified files**: root `config.py` (new env block), `.env.example`, `requirements.txt` (one new line: `psycopg[binary]>=3.2`), `.gitignore` (verify `*.session` coverage; optionally add `partner_message_summarisation/data/`).
- **Explicitly untouched**: `bot.py`, `database.py`, `ai_enrichment.py`.
- **External systems**: hosted PostgreSQL (new provisioning + `DATABASE_URL`), Telegram user-account session (new one-time login), existing `BOT_TOKEN`/`ADMIN_GROUP_ID`/LLM API keys reused (bot getUpdates vs MTProto send do not conflict).
- **Operational**: one new long-running process on the VPS; advisory lock assumes a single instance (A4).
