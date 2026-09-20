# Design: partner-message-summarisation

## Context

`tg_sentinel` runs a python-telegram-bot main process (`bot.py`) plus proven standalone-service packages (`gmail_calendar/`: asyncio loops, config block in root `config.py`, raw-SQL SQLite, dual Gemini→OpenAI LLM client, pytest, nohup deploy scripts). Pyrogram 2.0.106 + TgCrypto are already installed with the repo-standard `utils.get_peer_type` monkey-patch; a bot-token Pyrogram send-only client already exists (`gmail_calendar/telegram_notifier.py`). PostgreSQL is **not** present today (no driver; SQLAlchemy exists only transitively and must not be relied on). There is no migration framework — schemas are created idempotently at startup. Full inspection detail: `TELEGRAM_INTELLIGENCE_PIPELINE_PLAN.md` §1.

## Goals / Non-Goals

**Goals:**

- Fully decoupled new service package `partner_message_summarisation/` — main bot untouched.
- Reliable at-least-once digest delivery: nothing silently lost; failures retry next run.
- Reuse existing repo patterns (dual-LLM client shape, config block convention, loop/bot/shell structure) rather than introducing frameworks.
- Clear operational surface: one login step, one deploy script, one status CLI, audit table.

**Non-Goals:**

- `/run_digest` admin command in the main bot (deferred; would touch `bot.py`).
- systemd unit, metrics dashboards, message media download, history import, multi-instance HA.
- ORM adoption or a migration tool for v1.

## Decisions

1. **Postgres driver: `psycopg[binary]>=3.2` (async)** — official driver, native async, raw-SQL friendly, built-in pool; matches the repo's no-ORM style. *Alternatives:* `asyncpg` (equally valid, rejected only to keep one obvious choice), SQLAlchemy async (transitive dep, not pinned — rejected), SQLite (contradicts hosted-Postgres requirement — rejected).
2. **Account separation: user-account reader + bot-token deliverer, same process.** Bots often can't read partner-group history/aren't members, so a one-time interactive login (`python -m partner_message_summarisation.login`) creates a file session under `partner_message_summarisation/data/`. The deliverer reuses `BOT_TOKEN` over MTProto purely for `send_message` — no conflict with the main bot's `getUpdates` polling (only concurrent pollers conflict); this process never polls updates. *Alternative considered:* session strings — rejected (repo convention + `.gitignore` already covers file sessions).
3. **Title-based discovery at startup, per-message re-check.** `get_dialogs()` scan filtered by `^SISC <>` regex (configurable) replaces cryptopulse's hardcoded chat IDs; a `title_matches` re-check per message handles mid-run renames (renamed chat's messages are ignored until next restart re-discovers). Title snapshots per message keep report attribution stable across renames.
4. **Schema: three tables (`tg_chats`, `tg_messages`, `summary_runs`), `UNIQUE (chat_id, message_id)` dedup, two-state `pending→completed`.** No `processing` state: a single run owns the batch under `pg_try_advisory_lock`, so the only retry state is `pending` — crash anywhere leaves rows `pending` and the next run retries. Denormalized `chat_title` preserves history across renames. `summary_runs` is both audit log and the window-start source (though selection is always `status='pending'`, so bookkeeping drift loses nothing).
5. **Migrations: idempotent `CREATE TABLE IF NOT EXISTS` at boot** — repo convention, sufficient for a stable v1 schema. *Alternative:* Alembic — rejected for now (schema churn not expected).
6. **Catch-up: bounded per-chat history backfill (default 200) on startup, first-run chats start from "now".** Live Pyrogram handlers are not persistent, so some backfill is required; unbounded backfill on first run would pollute the DB and cost LLM tokens. Excess downtime is logged as permanently missed (inherent Telegram limitation).
7. **Summarization: dual-provider structured output reusing the `gmail_calendar/llm.py` shape** — Gemini `response_schema` primary (`gemini-2.5-flash`), `instructor.from_openai(OpenAI)` fallback (`gpt-5-mini`), lazy singletons, `temperature=0.2`; provider + latency recorded per run. Transcript caps (800 chars/message, 120k total) with chunking at chat boundaries and pure-Python digest merge keep cost/latency bounded on busy days.
8. **Report: deterministic plain text, no parse mode**, split ≤4,096 chars at section boundaries with `(Part i/N)` labels — immune to Markdown-breaking group titles, consistent with `gmail_calendar` formatter style.
9. **Delivery commit: `completed` UPDATE only after all parts delivered; failure path is one statement away.** Crash between send and UPDATE → duplicate digest next run (at-least-once; accepted, minimized by making the gap a single statement + advisory lock). Delivery uses ordered sequential sends, per-part exponential backoff, `FloodWait` respect, `SENTINEL_DRY_RUN` for safe testing.
10. **Config: append a `partner_message_summarisation` block to root `config.py`** following the per-feature convention (`GMAIL_*` block); only `DATABASE_URL` is fail-fast required, everything else defaults. `DATABASE_URL` never logged; session files stay under `partner_message_summarisation/data/` (git-ignored).
11. **Startup: idempotent `start_all.sh` orchestrator.** The main bot and this service start together via one command, each guarded by `pgrep -f` so re-running never double-spawns — critical because two instances would fight over the same Pyrogram session file. Restart flow stays the operator's existing habit: `kill <pid>` (SIGTERM-safe for this service) then re-run the script. gmail_calendar stays in the repo but on hold (commented launch block); luma_reminder remains cron-managed and outside the orchestrator. Optional `@reboot` cron line auto-starts the stack after a VPS reboot. *Alternatives considered:* extending `bot_script.sh` (couples restarts of unrelated services), in-process integration into `bot.py` like `ai_enrichment` (contradicts the decoupling decision), systemd units (deferred to post-v1 backlog).

## Risks / Trade-offs

- [Pyrogram 2.0.106 flood waits / disconnects on user session] → retry loop + catch-up backfill; documented inherent loss beyond catch-up bound.
- [Group renamed between restarts changes report attribution] → per-message title snapshot; regex anchored to `^SISC <>`.
- [Crash between Telegram send and `completed` UPDATE → duplicate digest] → accepted at-least-once risk; gap minimized to one statement; advisory lock prevents concurrent double-sends.
- [In-memory insert-retry deque lost on crash] → startup catch-up recovers unless downtime exceeded the bound.
- [LLM cost/latency on very active days] → per-message + total transcript caps, chat-boundary chunking, cheap default model.
- [Slow `get_dialogs()` startup on accounts with many dialogs] → one-time per start, acceptable; documented.
- [Postgres blips at 19:00] → ingest retry deque + startup backoff; run stays unexecuted, messages remain `pending`.
- [Both LLM providers fail] → run marked `failed`, messages stay `pending`, retried next day/trigger; alert log line.

## Migration Plan

Phased build (config/scaffold → db layer → ingestion → scheduler → summarizer → report/delivery → orchestration → deploy/docs), each independently verifiable per `tasks.md`; detailed steps in `TELEGRAM_INTELLIGENCE_PIPELINE_PLAN.md` §17. Deploy: provision hosted Postgres → add `DATABASE_URL` (+ tuning vars) to VPS `.env` → run `python -m partner_message_summarisation.login` once → `bash partner_message_summarisation/partner_message_summarisation_bot.sh` (nohup pattern). Rollback: kill the process and `disown` removal — schema is additive, main bot and its SQLite data are untouched; `tg_messages`/`summary_runs` may be dropped without affecting anything else.

## Open Questions

None blocking. Open decisions D1–D6 from the plan are resolved to its recommended defaults (psycopg, English plain-text report, idempotent-DDL migrations, 200-msg catch-up, silent empty-day skip, no admin trigger command in v1). `/run_digest` remains a documented post-v1 candidate.
