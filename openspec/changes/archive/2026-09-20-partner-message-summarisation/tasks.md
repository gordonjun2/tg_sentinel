# Tasks: partner-message-summarisation

## 1. Prerequisites & scaffold

- [x] 1.1 Add `psycopg[binary]>=3.2` to `requirements.txt` and install in venv
- [x] 1.2 Append `partner_message_summarisation` env block to root `config.py` (§4 of plan: `DATABASE_URL` fail-fast, `SISC_GROUP_REGEX`, `SENTINEL_TIMEZONE`, `SENTINEL_SUMMARY_TIME`, model overrides, session names, catch-up/transcript caps, `SENTINEL_DRY_RUN`) reusing existing shared vars (`BOT_TOKEN`, `ADMIN_GROUP_ID`, `TELEGRAM_API_KEY`, `TELEGRAM_HASH`, LLM keys/models)
- [x] 1.3 Update `.env.example` with the new `DATABASE_URL` and `SENTINEL_*` vars (documented, no real secrets)
- [x] 1.4 Create `partner_message_summarisation/__init__.py` (docstring + entry point note) and `partner_message_summarisation/config.py` (imports shared secrets from root config; no duplication)

## 2. Database layer

- [x] 2.1 Implement `partner_message_summarisation/db.py`: async pool init/close, redacted-DSN logging, idempotent schema creation (`tg_chats`, `tg_messages`, `summary_runs` per §5)
- [x] 2.2 Implement ingest ops: `insert_message` with `ON CONFLICT DO NOTHING` returning new/dup signal, `upsert_chat`, `get_last_collected_message_id`, `bump_chat_last_seen`
- [x] 2.3 Implement run ops: `fetch_pending` (ordered), `mark_completed` (batch, with `processed_at` + `summary_run_id`), `start_run`/`succeed_run`/`fail_run`, `get_last_success_window_end`, advisory lock acquire/release helpers
- [x] 2.4 Add `partner_message_summarisation/tests/test_db.py` (env-gated `PARTNER_MESSAGE_SUMMARISATION_TEST_DATABASE_URL`): schema idempotence, dedup second insert is dup, state transitions, window query, advisory lock exclusivity

## 3. Ingestion

- [x] 3.1 Implement `partner_message_summarisation/groups.py`: `title_matches()` regex helper + `discover_target_chats()` dialog scan upserting into `tg_chats`; add `partner_message_summarisation/tests/test_groups.py` (regex matrix: `SISC <> AI Builders` ✅, `SISC Discussion` ❌, etc.)
- [x] 3.2 Implement `partner_message_summarisation/listener.py`: repo-standard `get_peer_type` monkey-patch, user client factory (no bot token, `int(TELEGRAM_API_KEY)` cast), `serialize_message()` (sender fallbacks, text/caption, reply/forward/media fields) — unit-test `serialize_message` with fake Pyrogram message objects in `test_groups.py` or a new `test_listener.py`
- [x] 3.3 Implement live `on_message` handler (chat filter + `title_matches` re-check, excludes service messages, `last_seen` bump) and bounded `catchup_chat`/`catchup_all` (skip chats without history, cap at `SENTINEL_MAX_CATCHUP_PER_CHAT`, warn on gap)
- [x] 3.4 Implement in-memory failed-insert retry deque (bounded ~1,000, drained every 60 s) and `partner_message_summarisation/login.py` one-shot interactive session creator
- [ ] 3.5 Verify locally: run listener against a real test group, confirm live insert, dedup on replay, and catch-up after restart

## 4. Scheduler

- [x] 4.1 Implement `partner_message_summarisation/scheduler.py`: `next_run_at()` (pytz, `SENTINEL_TIMEZONE`/`SENTINEL_SUMMARY_TIME`, rolls to tomorrow when past) and `scheduler_loop()` (sleep → run → recompute; immediate run on wake-past-target)
- [x] 4.2 Add `partner_message_summarisation/tests/test_scheduler.py`: before/after/exactly target time, UTC↔SGT conversion, `HH:MM` override
- [ ] 4.3 Verify: set `SENTINEL_SUMMARY_TIME` two minutes ahead, confirm loop fires

## 5. Summarizer

- [x] 5.1 Implement `partner_message_summarisation/summarizer.py` Pydantic models (`Insight`, `DailyDigest`) and `build_transcript()` (chronological per chat, sender/time/reply/forward annotation, per-message truncation, chat-boundary chunking at `SENTINEL_MAX_TRANSCRIPT_CHARS`)
- [x] 5.2 Implement `merge_digests()` (concat, dedupe near-identical titles, re-sort by importance, union `source_groups`) and the system prompt with source-group attribution restricted to transcript groups
- [x] 5.3 Implement dual-provider call (Gemini `response_schema` primary → instructor/OpenAI fallback, lazy singletons, `temperature=0.2`) returning provider + latency; raise when both fail
- [x] 5.4 Add `partner_message_summarisation/tests/test_summarizer.py`: transcript ordering/truncation/annotations, chunking, merge dedupe with injected fake LLM
- [ ] 5.5 Verify: feed fixture transcripts through a `SENTINEL_DRY_RUN` LLM call

## 6. Report & delivery

- [x] 6.1 Implement `partner_message_summarisation/report.py`: `render_report()` (date, window, counts, highlights, numbered insights with sources, coverage) and `split_report(text, limit=4096)` (section-boundary priority, `…(cont)` hard-split fallback, `(Part i/N)` labels)
- [x] 6.2 Add `partner_message_summarisation/tests/test_report.py`: exact-boundary split, oversized single insight, ordering + labels, no content loss on concatenation, render snapshot
- [x] 6.3 Implement `partner_message_summarisation/notifier.py`: bot Pyrogram client factory (mirrors `gmail_calendar/telegram_notifier.make_client`), `deliver_report()` with ordered sends, per-part exponential-backoff retry, `FloodWait` respect, `SENTINEL_DRY_RUN` log-only path
- [ ] 6.4 Verify: dry-run rendering of a synthetic report; live send of a synthetic report to `ADMIN_GROUP_ID`

## 7. Orchestration

- [x] 7.1 Implement `partner_message_summarisation/bot.py` `main()`: init DB + start both clients, discover chats, catch-up, gather retry + scheduler loops, graceful shutdown (SIGTERM-safe; mid-run kill leaves rows `pending`)
- [x] 7.2 Implement `run_daily_summary()`: advisory lock → start run row → fetch pending (empty → silent success) → transcripts/LLM → render/split/deliver → on success mark batch `completed` + succeed run; any failure → fail run with error, messages stay `pending`
- [x] 7.3 Implement `partner_message_summarisation/status.py` CLI: pending backlog (total/per chat/oldest age), last 7 runs, monitored chats
- [ ] 7.4 Verify end-to-end dry run: kill mid-delivery → rows stay `pending`; next run completes and marks them

## 8. Deployment & docs

- [x] 8.1 Create `partner_message_summarisation/partner_message_summarisation_bot.sh` mirroring `gmail_calendar/gmail_calendar_bot.sh` (nohup + disown + tee'd log)
- [x] 8.2 Create `partner_message_summarisation/README.md`: Postgres provisioning/DSN, one-time login + session handling (chmod 600), deploy steps, runbook (session expiry, regex change requires restart, forced out-of-schedule run, `status` CLI)
- [x] 8.3 Verify `.gitignore` covers `*.session` and add `partner_message_summarisation/data/` entry; confirm `bot.py`, `database.py`, `ai_enrichment.py` untouched
- [x] 8.4 Create idempotent `start_all.sh` orchestrator: pgrep-guarded starts for main bot + partner_message_summarisation (gmail_calendar kept but on hold via commented block; luma_reminder stays cron-managed); document restart flow (`kill <pid>` → rerun) and optional `@reboot` cron in the README

## 9. Live rollout

- [ ] 9.1 Deploy to VPS: provision hosted Postgres role, add VPS `.env` vars, copy/create session, run deploy script
- [ ] 9.2 Observe first scheduled run via logs + `summary_runs`; tune prompt from first real digests (post-v1 hardening backlog: `/run_digest` command, systemd unit)
