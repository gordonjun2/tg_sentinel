# Gmail + Google Calendar Integration

Long-running polling service that forwards important Gmail emails and 30-min
meeting reminders to the Telegram admin group.

## Quick start

### 1. GCP project + Pub/Sub setup

Follow [`../docs/gcp-setup.md`](../docs/gcp-setup.md). TL;DR:

- Enable **Gmail API** and **Cloud Pub/Sub API** on your GCP project.
- Create a Pub/Sub topic (default name: `gmail-events`).
- Create a **pull** subscription on that topic (default: `gmail-events-pull`).
- Grant `gmail-api-push@system.gserviceaccount.com` the role
  **Pub/Sub Publisher** on the topic.
- Add the Gmail + Calendar readonly scopes to your OAuth consent screen.

### 2. Create OAuth credentials for this integration

This integration uses its **own** OAuth client, separate from the Drive
uploader's `credentials.json`:

1. APIs & Services → Credentials → **Create Credentials** → **OAuth client ID**
2. Type: **Desktop app**, name: `tg-sentinel-gmail-cal`
3. Download the JSON, save as **`gmail_cal_credentials.json`** in the project root

See [`../docs/gcp-setup.md`](../docs/gcp-setup.md) for full details.

### 3. Authorize once on your laptop

```bash
# from project root, with venv active
python -m gmail_calendar.auth
```

A browser opens; pick the Google account that owns the Gmail mailbox and
calendars. After consent, a `gmail_cal_token.json` file is written next to
`gmail_cal_credentials.json`.

> The `prompt="consent"` flag in the flow forces a fresh refresh token —
> without it Google omits refresh tokens on repeat grants and the VPS will
> silently fail to refresh after 1 hour.

### 4. Ship the token to the VPS

```bash
scp gmail_cal_token.json root@your-vps:/root/tg_sentinel/
```

(You do **not** need to ship `gmail_cal_credentials.json` to the VPS.)

### 5. Configure env

Add to `.env` (all have defaults except `GOOGLE_GMAIL_CAL_PROJECT_ID`):

```env
GOOGLE_GMAIL_CAL_PROJECT_ID=your_gcp_project_id
GOOGLE_PUBSUB_TOPIC=gmail-events
GOOGLE_PUBSUB_SUBSCRIPTION=gmail-events-pull
GMAIL_IMPORTANCE_THRESHOLD=0.5
```

### 6. Install deps + run

```bash
pip install -r requirements.txt   # adds google-cloud-pubsub

# Foreground, for first-run debugging:
python -m gmail_calendar.bot

# Production (background):
bash gmail_calendar/gmail_calendar_bot.sh
```

## Architecture

```
gmail_calendar/
├── auth.py              # OAuth bootstrap + Gmail/Calendar service factory
├── db.py                # SQLite schema + accessors (own gmail_calendar.db)
├── llm.py               # Gemini + OpenAI importance scoring
├── telegram_notifier.py # Pyrogram client + message formatters
├── gmail_watcher.py     # Pub/Sub pull → history.list → LLM score → admin group
├── calendar_sync.py     # events.list(syncToken) across all calendars
├── meeting_reminder.py  # 30-min window query → reminder dispatch
└── bot.py               # entry point: 4 asyncio loops
```

Four independent loops run concurrently with per-loop try/except + exponential
backoff, so a Calendar API outage never takes down Gmail forwarding.

## Tuning

| Env var | Default | Effect |
|---|---|---|
| `GMAIL_IMPORTANCE_THRESHOLD` | `0.5` | Raise to `0.75` for high-signal only |
| `GMAIL_POLL_INTERVAL_SECONDS` | `60` | How often to pull from Pub/Sub |
| `CALENDAR_POLL_INTERVAL_SECONDS` | `60` | How often to run incremental sync |
| `REMINDER_CHECK_INTERVAL_SECONDS` | `60` | How often to scan for due reminders |
| `REMINDER_LEAD_MINUTES` | `30` | How far ahead to send meeting reminders |

## Files written at runtime

All gitignored:

- `gmail_cal_credentials.json` — OAuth client secrets (downloaded from GCP)
- `gmail_cal_token.json` — OAuth refresh token (project root)
- `gmail_calendar/data/gmail_calendar.db` — SQLite state
- `gmail_calendar/data/gmail_calendar_bot.session` — Pyrogram session

## Stopping

```bash
pkill -f "python -m gmail_calendar.bot"
```
