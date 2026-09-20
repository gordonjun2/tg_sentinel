# partner_message_summarisation — Telegram Intelligence Pipeline

Ingests messages from the partner (`SISC <>…`) Telegram groups into hosted
PostgreSQL and delivers an LLM-generated daily digest to the admin group at
19:00 Asia/Singapore.

Entry points:

| Command | Purpose |
|---|---|
| `python -m partner_message_summarisation.login` | One-time interactive login (creates the user session) |
| `python -m partner_message_summarisation.bot` | Long-running service (ingest + daily digest) |
| `python -m partner_message_summarisation.status` | Backlog / last runs / monitored chats |

## Setup

### 1. Hosted PostgreSQL

Provision a hosted Postgres instance reachable from the VPS with TLS. Create a
least-privilege role (`CREATE/DROP` needed only for first boot — after schema
creation `SELECT/INSERT/UPDATE` suffices) and put the DSN in `.env`:

```
DATABASE_URL=postgres://user:password@host:5432/partner_message_summarisation
```

The schema (`tg_chats`, `tg_messages`, `summary_runs`) is created idempotently
at startup — no migration step. The DSN is never logged in full (only
`host/dbname`).

### 2. One-time user login

Bots usually can't read partner-group history, so ingestion uses **your user
account** via a Pyrogram file session:

```bash
python -m partner_message_summarisation.login   # enter phone + code once
chmod 600 partner_message_summarisation/data/sentinel_listener.session   # session = account access
```

Run this locally or once in a tmux session on the VPS. Deploy the produced
session file with the code. Session files are git-ignored; treat them as
secrets.

### 3. Configuration

All vars live in the root `.env` (see `.env.example`): `DATABASE_URL`
(required), `SISC_GROUP_REGEX` (default `^SISC <>`), `SENTINEL_TIMEZONE`
(default `Asia/Singapore`), `SENTINEL_SUMMARY_TIME` (default `19:00`),
`SENTINEL_DIGEST_MODEL` / `SENTINEL_OPENAI_DIGEST_MODEL`,
`SENTINEL_MAX_CATCHUP_PER_CHAT` (200), `SENTINEL_MAX_MSG_CHARS` (800),
`SENTINEL_MAX_TRANSCRIPT_CHARS` (120000), `SENTINEL_DRY_RUN`.

### 4. Deploy (VPS)

This service is started together with the main bot by the idempotent orchestrator:

```bash
bash start_all.sh    # main bot + this service; skips anything already running
# logs: tail -f /root/tg_sentinel/partner_message_summarisation_output.log
```

- **Restart just this service**: `kill <pid>` then `bash partner_message_summarisation/partner_message_summarisation_bot.sh`
- **Restart the whole stack**: `kill <pids>` then `bash start_all.sh` — running services are skipped, killed ones restart
- **Auto-start after VPS reboot** (optional): add to crontab (`crontab -e`):
  ```
  @reboot bash /root/tg_sentinel/start_all.sh
  ```
- `start_all.sh` warns (non-blocking) if the session file or `DATABASE_URL` is missing; the main bot is unaffected either way
- gmail_calendar is on hold — kept in the repo but not started by `start_all.sh` (re-enable via the commented block there)

The service is SIGTERM-safe: `kill <pid>` leaves any in-flight batch `pending` (never half-completed), and missed messages are recovered by the bounded catch-up on next start.

## Runbook

- **Session expired / re-login**: `python -m partner_message_summarisation.login` again (tmux on
  the VPS or locally + copy the file); restart the service.
- **Changed `SISC_GROUP_REGEX`**: restart required — group discovery runs at
  startup only.
- **Forced out-of-schedule run**: set `SENTINEL_SUMMARY_TIME` a couple of
  minutes ahead and restart; revert afterwards.
- **Test without sending**: `SENTINEL_DRY_RUN=true` logs the full report and
  treats the run as delivered (messages get completed — messages will not be
  re-sent on the next real run).
- **Backlog inspection**: `python -m partner_message_summarisation.status` — pending counts per
  chat, oldest pending age, last 7 runs, monitored chats.
- **Missed messages after long downtime**: recovery is bounded by
  `SENTINEL_MAX_CATCHUP_PER_CHAT` (default 200/chat); beyond that, messages
  are permanently missed (inherent Telegram limitation, logged as a warning).
- **Duplicate digest** (rare): can happen if the process dies between the
  Telegram send and the completion update (at-least-once delivery).

## Design

See `openspec/changes/partner-message-summarisation/` (proposal, specs,
design) and `TELEGRAM_INTELLIGENCE_PIPELINE_PLAN.md` for the full
architecture. Tests: `venv/bin/python -m pytest partner_message_summarisation/tests/` (DB
integration tests need `PARTNER_MESSAGE_SUMMARISATION_TEST_DATABASE_URL`).

## Post-v1 backlog

- `/run_digest` admin command in the main bot
- systemd unit replacing the nohup script
- prompt tuning from the first real digests
