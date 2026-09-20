# tg_sentinel — Telegram Operations Toolkit

A multi-service Telegram platform: the main access bot runs alongside
independent long-running services (AI discussion enrichment, Gmail/Calendar
automation, a Luma event reminder, and a partner-group summarisation
pipeline). Each service is its own process with its own log, started by one
idempotent orchestrator.

## Services

| Service | What it does | Entry point | Started by | Status |
|---|---|---|---|---|
| **Main bot** (`bot.py`) | Group access via survey + admin approval, AI enrichment of admin-group discussions (Gemini → OpenAI fallback), Drive uploads, audio transcription | `python bot.py` | `start_all.sh` | Active |
| **Partner message summarisation** | Pyrogram user-account listener archives `SISC <>` group messages to PostgreSQL; nightly LLM digest sent to the admin group at 19:00 SGT | `python -m partner_message_summarisation.bot` | `start_all.sh` | Active |
| **Luma reminder** | Cron job that sends Luma event reminders to the admin group | `python luma_reminder.py` | crontab (`luma_reminder.sh`) | Active (cron) |
| **Gmail + Calendar** | Email importance triage, meeting reminders, calendar sync | `python -m gmail_calendar.bot` | — | **On hold** (kept in repo, not started) |
| **Luma scraper** | Utility: scrape a Luma event's start date from a public URL | `python luma_scraper.py <url>` | on demand | Active |

## Setup

1. Create the `.env` from `.env.example` and fill in the secrets:
   `BOT_TOKEN`, `ADMIN_GROUP_ID`, `TARGET_GROUP_ID`, `GOOGLE_DRIVE_*`,
   `GEMINI_API_KEY`, `OPENAI_API_KEY`, `TELEGRAM_API_KEY`, `TELEGRAM_HASH`,
   `FIRECRAWL_API_KEY` — plus `DATABASE_URL` for the summarisation service.
2. Install dependencies:
   ```bash
   python -m venv venv
   venv/bin/pip install -r requirements.txt
   ```
3. One-time login for the summarisation listener (user-account session):
   ```bash
   python -m partner_message_summarisation.login
   ```
   See `partner_message_summarisation/README.md` for its Postgres and
   deployment details.
4. Verify the AI/search API keys are working (free metadata endpoints only —
   model lists and credit usage; no generation calls, zero cost):
   ```bash
   venv/bin/python check_api_keys.py
   ```
   Checks `GEMINI_API_KEY`, `OPENAI_API_KEY` (validity + configured
   enrichment model availability) and `FIRECRAWL_API_KEY` (validity +
   remaining credits). Exits non-zero if any key fails.

## Running

```bash
bash start_all.sh    # starts everything; safe to re-run (skips running services)
```

- Re-running never double-spawns: each service is guarded by `pgrep`.
- **Restart flow**: `kill <pid>` (SIGTERM-safe), then `bash start_all.sh` again — killed services come back, running ones are untouched.
- Per-service scripts for solo restarts: `bot_script.sh` (main bot),
  `partner_message_summarisation/partner_message_summarisation_bot.sh`,
  `gmail_calendar/gmail_calendar_bot.sh` (gmail — on hold), `luma_reminder.sh` (cron job).
- Auto-start after a VPS reboot (optional): add `@reboot bash /root/tg_sentinel/start_all.sh`
  to `crontab -e`. The repo is expected at `/root/tg_sentinel` on the server.
- Logs: `<service>_output.log` / `script_tg_sentinel_output.log` in the project root.

## Usage

### Main bot
1. Users start the bot with `/start` and are guided through a survey
2. Admins receive join requests in the admin group and approve/reject
3. Approved users receive their group invitation
4. Admin-group discussions are enriched automatically (summaries, Drive uploads, transcripts)

### Partner message summarisation
- Messages from groups matching `SISC_GROUP_REGEX` (default `^SISC <>`) are
  archived continuously (with bounded catch-up after downtime)
- Every day at `SENTINEL_SUMMARY_TIME` (default 19:00 Asia/Singapore) the
  pending messages are summarized into a digest and delivered to
  `ADMIN_GROUP_ID`; empty days are silent; failed deliveries retry next run
- Inspect with `python -m partner_message_summarisation.status`

## Project Structure

```
bot.py                      Main bot entry point
ai_enrichment.py            AI enrichment (runs inside the main bot process)
audio_transcribe.py         Audio transcription (main bot feature)
upload_to_google_drive.py   Drive uploads (main bot feature)
database.py                 Main bot SQLite state
config.py                   All environment configuration (per-feature blocks)
utils.py                    Shared helpers
partner_message_summarisation/   Group listener + nightly digest service (see its README)
gmail_calendar/             Gmail + Calendar service (on hold)
luma_reminder.py            Luma event reminder (cron)
luma_scraper.py             Luma event date scraper (utility)
start_all.sh                Idempotent orchestrator for long-running services
check_api_keys.py           API key health check (free endpoints, zero cost)
bot_script.sh               Main bot standalone launcher
openspec/                   Capability specs + archived change history
TELEGRAM_INTELLIGENCE_PIPELINE_PLAN.md   Design notes for the summarisation pipeline
```

## Notes

- Secrets (`.env`, `*.session`) are git-ignored — treat session files as
  account credentials.
- The main bot and the summarisation deliverer share `BOT_TOKEN` without
  conflict: only the bot polls updates; the service only sends via MTProto.
