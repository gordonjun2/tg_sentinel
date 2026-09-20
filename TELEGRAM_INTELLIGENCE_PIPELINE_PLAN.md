# Telegram Intelligence Pipeline — Implementation Plan

> **Status:** Plan only — no implementation yet.
> **Scope:** New `partner_message_summarisation` package inside `tg_sentinel` that ingests messages from Telegram groups titled `SISC <>…`, stores them in hosted PostgreSQL, and delivers an LLM-generated daily digest to the admin group at 19:00 Asia/Singapore.
> **Reference:** `/Users/gordonjun/Desktop/Projects/cryptopulse/run_cryptopulse.py` (Pyrogram user-account ingestion pattern).

---

## 1. What exists today (inspection findings)

### 1.1 tg_sentinel inventory

| Concern | Status | Location / evidence |
|---|---|---|
| Telegram bot (user-facing) | ✅ python-telegram-bot v22, `Application.run_polling()` | `bot.py` (1,775 lines), commands registered in `main()` |
| Pyrogram | ✅ `pyrogram==2.0.106` + `TgCrypto` (venv), user monkey-patch of `utils.get_peer_type` | `bot.py:60-78`, `gmail_calendar/telegram_notifier.py:28-37` |
| Bot-token Pyrogram client (send-only) | ✅ `Client(name, api_id, api_hash, bot_token=BOT_TOKEN)` | `gmail_calendar/telegram_notifier.py:40-51` (`make_client`) |
| Standalone long-running service package | ✅ Proven pattern | `gmail_calendar/` — `__init__.py`, `bot.py` (asyncio loops + `_run_with_backoff`), `db.py`, `llm.py`, `telegram_notifier.py`, `tests/`, `README.md`, `gmail_calendar_bot.sh` |
| Config handling | ✅ `config.py` with `load_dotenv()`, fail-fast `ValueError`s for required vars, defaults for optional | `config.py:1-71` |
| Database | ⚠️ SQLite only (`data/bot_data.db`, `gmail_calendar/data/gmail_calendar.db`), raw SQL + context-managed cursor + `RLock` | `database.py`, `gmail_calendar/db.py` |
| PostgreSQL | ❌ **None.** No driver (`psycopg`/`asyncpg`) in `requirements.txt` or venv. `SQLAlchemy 2.0.43` is present in the venv **only transitively** (via langgraph) — not pinned, must not be relied on | venv inspection |
| LLM client | ✅ Dual-LLM pattern: Gemini `response_schema` primary → `instructor.from_openai(OpenAI)` fallback; Pydantic models; lazy client singletons; `temperature=0.2` | `gmail_calendar/llm.py`, also `ai_enrichment.py` |
| Scheduling | ✅ In-process asyncio polling loops; **no** cron/APScheduler | `gmail_calendar/bot.py:48-123` |
| Timezone | ✅ `pytz` already in `requirements.txt` | `requirements.txt:3` |
| Logging | ✅ `logging.basicConfig(format="%(asctime)s %(levelname)s [%(name)s] %(message)s", level=INFO, stream=sys.stdout)` + `logger = logging.getLogger(__name__)` | `gmail_calendar/bot.py:35-40` |
| Testing | ✅ pytest, module-scoped fixtures, `tmp_path`-based DB tests | `gmail_calendar/tests/test_db.py` |
| Deployment | ✅ VPS at `/root/tg_sentinel`, `venv`, `nohup python -m <pkg>.bot` + `disown`, tee'd log file | `gmail_calendar/gmail_calendar_bot.sh` |
| Secrets hygiene | ✅ `.env` git-ignored (`.gitignore:138`), `*.session` git-ignored (`.gitignore:216`) | `.gitignore` |
| Migrations framework | ❌ None — schema created idempotently with `CREATE TABLE IF NOT EXISTS` at startup | `gmail_calendar/db.py:62-135` |

**Key env vars already present in `.env`:** `BOT_TOKEN`, `ADMIN_GROUP_ID`, `TELEGRAM_API_KEY`, `TELEGRAM_HASH`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `GEMINI_ENRICHMENT_MODEL`, `OPENAI_ENRICHMENT_MODEL` — exactly the credentials this pipeline needs for delivery and LLM. Only the **PostgreSQL DSN** and **group-selection regex** are new.

### 1.2 Reusability of `run_cryptopulse.py`

| run_cryptopulse.py element | Lines | Verdict |
|---|---|---|
| Pyrogram monkey-patch `utils.get_peer_type` | 536-546 | **Reuse verbatim** — already the convention in `bot.py` and `gmail_calendar/telegram_notifier.py` |
| User-account client `Client("text_listener", TELEGRAM_API_KEY, TELEGRAM_HASH)` (no bot token) | 549 | **Adapt** — new session name, session file under package `data/` dir |
| `@app.on_message(filters.chat(CHAT_ID_LIST))` handler | 776-791 | **Adapt** — chat list built dynamically from title regex instead of hardcoded IDs |
| `asyncio.Queue` + worker fan-out | 552, 478-495 | **Adapt (simplified)** — single ingest worker; DB insert is fast, no NUM_WORKERS needed |
| `get_chat_id_name_dict()` (resolve titles via `app.get_chat`) | 972-979 | **Adapt** — replaced by `get_dialogs()` scan (regex needs titles, not IDs) |
| Instructor structured-output (`CoinSentiment`/`SentimentAnalysis` + `create_instructor_client`) | 556-591 | **Adapt concept** — but use tg_sentinel's dual Gemini/OpenAI pattern (`gmail_calendar/llm.py`) instead of OpenAI-only |
| Long-report sending | n/a | Not present — new `split_report()` utility required (Telegram 4,096-char limit) |
| Graceful shutdown (cancel tasks, `gather(..., return_exceptions=True)`, stop clients) | 1062-1114 | **Superseded** — `gmail_calendar/bot.py:126-154` has a cleaner `asyncio.run(main())` + `finally` pattern; prefer that |
| `signal.signal` handler calling `app.stop()` | 983-992 | Discard — unsafe from a signal context; use SIGTERM-safe asyncio shutdown (§9.4) |
| telebot/aiogram/Binance/PubSub/market-cap | — | **Discard** — not applicable; tg_sentinel uses PTB + Pyrogram only |
| Forward-to-aggregator-group | 784-789 | **Discard** — tg_sentinel stores to Postgres instead of forwarding |

---

## 2. Proposed architecture

```text
                         ┌──────────────────────────────────────────────────┐
                         │  partner_message_summarisation service  (python -m partner_message_summarisation.bot)      │
                         │                                                  │
  Telegram (MTProto)     │  ┌────────────┐   ┌───────────────┐              │
  SISC <> groups ────────┼─▶│ Pyrogram   │──▶│ ingest worker │──────┐       │
  (USER ACCOUNT session) │  │ listener   │   │ (dedup insert)│      │       │
                         │  └────────────┘   └───────────────┘      ▼       │
                         │        │                          ┌───────────┐  │      ┌──────────────┐
                         │        │ catch-up on              │  hosted   │  │      │   Telegram   │
                         │        │ startup (bounded)        │PostgreSQL │◀┼──────┤  Bot (MTProto│
                         │        ▼                          │ tg_messages│ │      │  send-only)  │
                         │  ┌────────────┐                  │ tg_chats   │ │      └──────▲───────┘
                         │  │ scheduler  │  19:00 SGT       │ summary_   │ │             │
                         │  │ loop       │──────────────────▶ runs        │ │             │
                         │  └────────────┘  build window    └───────────┘  │             │
                         │        │                                        │             │
                         │        ▼                                        │             │
                         │  ┌────────────────┐   ┌──────────────────┐      │             │
                         │  │ summarizer.py  │──▶│ report.py        │──────┼─────────────┘
                         │  │ Gemini→OpenAI  │   │ format + split   │      │  deliver to
                         │  │ Pydantic model │   │ ≤4096-char parts │      │  ADMIN_GROUP_ID
                         │  └────────────────┘   └──────────────────┘      │
                         └──────────────────────────────────────────────────┘
```

**End-to-end data flow**

1. **Ingest (continuous).** A Pyrogram **user-account** client, logged in once interactively, runs `on_message` handlers filtered to the set of chat IDs whose title matches `^SISC <>`. Each matching message is inserted into `tg_messages` with `status='pending'`. Duplicate `(chat_id, message_id)` rows are ignored via `ON CONFLICT DO NOTHING`.
2. **Store.** Hosted PostgreSQL (new dependency: `psycopg[binary]` v3, async). All useful metadata captured (§5).
3. **Summarize (daily 19:00 Asia/Singapore).** A scheduler loop computes the next 19:00 SGT with `pytz` and sleeps. At fire time it takes an advisory lock, fetches all `pending` messages, groups them by source chat, and calls the LLM (Gemini primary, instructor/OpenAI fallback) with a per-group transcript. Output is a Pydantic-validated `DailyDigest`.
4. **Deliver.** A separate Pyrogram **bot** client (same `BOT_TOKEN` as the main bot, MTProto send-only — see §3) sends the formatted report to `ADMIN_GROUP_ID`, split into ordered ≤4,096-char parts.
5. **Commit.** Only after **all** parts are delivered: mark the batch `completed`, write a `summary_runs` success row. On any failure the messages stay `pending` and the next run retries.

### Account separation (explicit)

| | **Reader** | **Deliverer** |
|---|---|---|
| Identity | Your personal Telegram **user account** | **Bot** (`BOT_TOKEN`) |
| Client | `Client(SENTINEL_SESSION_NAME, TELEGRAM_API_KEY, TELEGRAM_HASH)` — no `bot_token` | `Client(SENTINEL_BOT_SESSION_NAME, TELEGRAM_API_KEY, TELEGRAM_HASH, bot_token=BOT_TOKEN)` |
| Purpose | Read `SISC <>` group messages (bots often can't see full group history / aren't members) | Send digest to `ADMIN_GROUP_ID` only |
| Session file | `partner_message_summarisation/data/sentinel_listener.session` | `partner_message_summarisation/data/sentinel_bot.session` |
| Login | One-time interactive `python -m partner_message_summarisation.login` (phone + code), then headless | None (bot token auth) |
| Lives in | Same `partner_message_summarisation` process | Same `partner_message_summarisation` process |

Coexistence note: `bot.py` already polls `getUpdates` with `BOT_TOKEN` over the HTTP Bot API; the deliverer uses MTProto with the same token purely for `send_message`. These do not conflict (only concurrent `getUpdates` consumers conflict). Both processes must **never** both poll updates.

---

## 3. Files and modules

New package `partner_message_summarisation/` mirroring the `gmail_calendar/` conventions:

| File | Action | Responsibility |
|---|---|---|
| `partner_message_summarisation/__init__.py` | new | Docstring: purpose + entry point `python -m partner_message_summarisation.bot` |
| `partner_message_summarisation/config.py` | new | Pipeline-specific config constants read from env (§4). Imports shared secrets from root `config.py` where they already exist (`BOT_TOKEN`, `ADMIN_GROUP_ID`, `TELEGRAM_API_KEY`, `TELEGRAM_HASH`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, models) — **do not duplicate** |
| `partner_message_summarisation/db.py` | new | `PartnerMessageSummarisationDB` class — async connection pool, schema init, ingest/dedup, window queries, state transitions, `summary_runs` (§5). Style follows `gmail_calendar/db.py` but async |
| `partner_message_summarisation/groups.py` | new | `discover_target_chats(client)` — `get_dialogs()` scan, regex match on title, upsert into `tg_chats`; `title_matches()` helper |
| `partner_message_summarisation/listener.py` | new | Pyrogram user-account client factory + `on_message` handler + bounded catch-up fetch (§7) |
| `partner_message_summarisation/scheduler.py` | new | `next_run_at(now)` (19:00 SGT via pytz) + `scheduler_loop` (§8) |
| `partner_message_summarisation/summarizer.py` | new | `DailyDigest`/`Insight` Pydantic models, transcript builder, dual-LLM call (§9) |
| `partner_message_summarisation/report.py` | new | `render_report(digest)` + `split_report(text, limit=4096)` (§10) |
| `partner_message_summarisation/notifier.py` | new | Bot Pyrogram client factory (mirror `gmail_calendar/telegram_notifier.py:make_client`) + `deliver_report()` with per-part retry (§10) |
| `partner_message_summarisation/bot.py` | new | Entry point: start both clients, run ingest + scheduler loops, graceful shutdown (§11) |
| `partner_message_summarisation/login.py` | new | One-shot interactive login to create the user session file |
| `partner_message_summarisation/status.py` | new | Tiny CLI: `python -m partner_message_summarisation.status` prints backlog counts, last runs (observability) |
| `partner_message_summarisation/partner_message_summarisation_bot.sh` | new | Deployment script mirroring `gmail_calendar/gmail_calendar_bot.sh` |
| `partner_message_summarisation/tests/test_report.py` | new | Split/render unit tests |
| `partner_message_summarisation/tests/test_scheduler.py` | new | Next-run computation tests |
| `partner_message_summarisation/tests/test_db.py` | new | Schema/dedup/state-transition tests (env-gated real Postgres) |
| `partner_message_summarisation/tests/test_summarizer.py` | new | Transcript builder + grouping + fake-LLM merge tests |
| `partner_message_summarisation/README.md` | new | Setup: Postgres DSN, session login, run instructions |
| `config.py` (root) | **modify** | Append new env vars (§4) — follows the existing per-feature convention (`GMAIL_*` block, lines 142-186) |
| `.env.example` (root) | **modify** | Document new vars |
| `requirements.txt` | **modify** | Add `psycopg[binary]>=3.2` (§12 dependency decision) |
| `.gitignore` | verify | Already covers `*.session` (line 216) and `.env` (line 138); optionally add `partner_message_summarisation/data/` for safety |

**Not modified:** `bot.py`, `database.py`, `ai_enrichment.py` — the pipeline is fully decoupled; the main bot process is untouched.

---

## 4. Configuration

Append to root `config.py` (mirroring the gmail_calendar block style — optional vars get defaults, DSN fails fast):

```python
# ---------------------------------------------------------------------------
# Telegram intelligence pipeline (partner_message_summarisation package)
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")          # required: postgres://user:pass@host:5432/db
SISC_GROUP_REGEX = os.getenv("SISC_GROUP_REGEX", r"^SISC <>")
SENTINEL_TIMEZONE = os.getenv("SENTINEL_TIMEZONE", "Asia/Singapore")
SENTINEL_SUMMARY_TIME = os.getenv("SENTINEL_SUMMARY_TIME", "19:00")   # HH:MM local tz
SENTINEL_DIGEST_MODEL = os.getenv("SENTINEL_DIGEST_MODEL", "gemini-2.5-flash")
SENTINEL_OPENAI_DIGEST_MODEL = os.getenv("SENTINEL_OPENAI_DIGEST_MODEL", "gpt-5-mini")
SENTINEL_SESSION_NAME = os.getenv(
    "SENTINEL_SESSION_NAME",
    os.path.join(_PROJECT_ROOT, "partner_message_summarisation/data/sentinel_listener"),
)
SENTINEL_BOT_SESSION_NAME = os.getenv(
    "SENTINEL_BOT_SESSION_NAME",
    os.path.join(_PROJECT_ROOT, "partner_message_summarisation/data/sentinel_bot"),
)
SENTINEL_MAX_CATCHUP_PER_CHAT = int(os.getenv("SENTINEL_MAX_CATCHUP_PER_CHAT", "200"))
SENTINEL_MAX_MSG_CHARS = int(os.getenv("SENTINEL_MAX_MSG_CHARS", "800"))      # per message in transcript
SENTINEL_MAX_TRANSCRIPT_CHARS = int(os.getenv("SENTINEL_MAX_TRANSCRIPT_CHARS", "120000"))
SENTINEL_DRY_RUN = os.getenv("SENTINEL_DRY_RUN", "false").lower() == "true"   # log report, don't send
```

Security rules:
- `DATABASE_URL` must never be logged; `db.py` logs only a redacted form (`host/dbname`).
- Session files are secrets (they grant account access) — keep under `partner_message_summarisation/data/`, git-ignored, `chmod 600` documented in README.
- No new hard-coded credentials anywhere (cryptopulse's `private.ini` plaintext-secret pattern must **not** be copied).

---

## 5. PostgreSQL schema

Created idempotently at startup (project has no migration framework; follow `gmail_calendar/db.py`'s `CREATE TABLE IF NOT EXISTS` convention — see Open Decision D3).

```sql
CREATE TABLE IF NOT EXISTS tg_chats (
    chat_id      BIGINT PRIMARY KEY,          -- Telegram chat id (negative for groups)
    title        TEXT NOT NULL,
    chat_type    TEXT NOT NULL,               -- 'group' | 'supergroup' | 'channel'
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tg_messages (
    id                  BIGSERIAL PRIMARY KEY,
    chat_id             BIGINT NOT NULL REFERENCES tg_chats(chat_id),
    chat_title          TEXT,                 -- denormalized snapshot at ingest time
    message_id          BIGINT NOT NULL,      -- Telegram message id within the chat
    -- sender (may be absent for anonymous admins / channels)
    sender_user_id      BIGINT,
    sender_username     TEXT,
    sender_display_name TEXT,
    -- content
    message_text        TEXT,                 -- message.text
    caption             TEXT,                 -- message.caption
    -- timing
    message_date        TIMESTAMPTZ NOT NULL, -- Telegram server-side date
    -- reply metadata
    reply_to_message_id BIGINT,
    -- forward metadata
    is_forward          BOOLEAN NOT NULL DEFAULT FALSE,
    forward_from_name   TEXT,
    forward_from_chat_id   BIGINT,
    forward_from_chat_title TEXT,
    forward_date        TIMESTAMPTZ,
    -- media
    media_type          TEXT,                 -- 'photo','video','document','audio','voice','sticker','animation','location','contact', ...
    media_file_id       TEXT,                 -- reference only; no binary download
    media_file_name     TEXT,
    -- processing state
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','completed')),
    collected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ,
    summary_run_id      BIGINT,
    CONSTRAINT uq_tg_messages_chat_message UNIQUE (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_tg_messages_status_date
    ON tg_messages (status, message_date);

CREATE TABLE IF NOT EXISTS summary_runs (
    id               BIGSERIAL PRIMARY KEY,
    window_start     TIMESTAMPTZ NOT NULL,   -- exclusive start (last successful window_end or epoch)
    window_end       TIMESTAMPTZ NOT NULL,   -- run trigger time (SGT)
    status           TEXT NOT NULL DEFAULT 'running'
                     CHECK (status IN ('running','success','failed')),
    message_count    INTEGER,
    chat_count       INTEGER,
    llm_provider     TEXT,                   -- 'gemini' | 'openai'
    llm_latency_ms   INTEGER,
    report_parts     INTEGER,
    error            TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ
);
```

Design notes:
- `UNIQUE (chat_id, message_id)` is the dedup constraint; ingest uses `INSERT ... ON CONFLICT ON CONSTRAINT uq_tg_messages_chat_message DO NOTHING RETURNING id` — a returned row means "new".
- Denormalized `chat_title` preserves history if a group is renamed later.
- `status` has exactly two values: messages are never claimed as `processing` because a single run owns them under an advisory lock (§11); `summary_run_id` links completed messages to the run that consumed them (auditability).
- `summary_runs.window_end` of the last `success` row defines the next run's window start for reporting; the actual message selection is always `status='pending'` so nothing is ever lost even if window bookkeeping drifts.

---

## 6. Pyrogram authentication & session management

Adapted from `run_cryptopulse.py:536-549` and `gmail_calendar/telegram_notifier.py:28-51`:

- Apply the repo-standard monkey-patch **once** in `partner_message_summarisation/listener.py` (module import time), identical to `bot.py:60-78`.
- **User client** (`listener.make_user_client()`): `Client(SENTINEL_SESSION_NAME, api_id=int(TELEGRAM_API_KEY), api_hash=TELEGRAM_HASH)` — no `bot_token`. Root `config.py` stores `TELEGRAM_API_KEY` as a string; cast to `int` here (cryptopulse's configparser cast does not apply since tg_sentinel uses env vars).
- **Bot client** (`notifier.make_bot_client()`): `Client(SENTINEL_BOT_SESSION_NAME, api_id=..., api_hash=..., bot_token=BOT_TOKEN)`.
- `partner_message_summarisation/login.py` runs the user client with `async with` and calls `app.get_me()`, printing the logged-in account, then exits — the sole interactive step. Document in `partner_message_summarisation/README.md`: run locally once, deploy the produced `.session` file to the VPS (or run `login.py` on the VPS in a tmux session once).
- Session strings are **not** used (repo convention is file sessions; file sessions are already git-ignored).

---

## 7. Real-time ingestion, group selection & dedup

### 7.1 Group discovery (`groups.py`)

`run_cryptopulse.py` hardcodes `CHAT_ID_LIST` (config.py:69-90). tg_sentinel needs title-based selection:

```python
def title_matches(title: str, pattern: str = SISC_GROUP_REGEX) -> bool:
    return bool(re.match(pattern, title or ""))

async def discover_target_chats(user_client, db) -> list[PyrogramChat]:
    targets = []
    async for dialog in user_client.get_dialogs():
        chat = dialog.chat
        if chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL) \
           and title_matches(chat.title):
            targets.append(chat)
            await db.upsert_chat(chat.id, chat.title, chat.type.value)
    return targets
```

- Run at startup after `user_client.start()`.
- The handler uses `filters.chat([c.id for c in targets])` for cheap server-side prefiltering **plus** a `title_matches` re-check per message (titles can be renamed mid-run; on rename, the check fails → message ignored, and the next restart re-discovers).
- `tg_chats.last_seen` is bumped on every ingested message; `is_active=False` marks chats that no longer match (kept for report-name stability).

### 7.2 Handler (`listener.py`)

```python
@user_app.on_message(filters.chat(target_ids) & ~filters.SERVICE)
async def on_group_message(client, message):
    await db.insert_message(serialize_message(message))   # ON CONFLICT DO NOTHING
```

`serialize_message()` maps Pyrogram `Message` → row dict (§5 columns):
- `sender`: prefer `message.from_user` (id/username/`first_name+last_name`); if absent (anonymous admin/channel post) try `message.author_signature` or `message.sender_chat.title`.
- `text = message.text or message.caption` (both stored in their own columns as well).
- `media_type` from `message.media` enum name; `media_file_id` via `getattr(message.<type>, "file_id", None)` guarded by `try/except`.
- Forward fields from `message.forward_date`, `message.forward_from`, `message.forward_from_chat`.
- Handler wraps `insert_message` in `try/except` that logs and pushes the payload to a bounded in-memory retry deque (max ~1,000, drained every 60 s). **Risk R4:** if the process dies before retry, that message is only recoverable via catch-up (§7.3).

### 7.3 Catch-up / resume after downtime (bounded backfill)

Pyrogram handlers are live-only. On startup, per target chat:

```python
async def catchup_chat(client, db, chat, limit=SENTINEL_MAX_CATCHUP_PER_CHAT):
    last = await db.get_last_collected_message_id(chat.id)   # NULL → skip backfill (first run)
    if last is None:
        return 0
    count = 0
    async for msg in client.get_chat_history(chat.id, limit=limit):
        if msg.id <= last:
            break
        await db.insert_message(serialize_message(msg)); count += 1
    return count
```

- Bounded by `SENTINEL_MAX_CATCHUP_PER_CHAT`; excess downtime beyond that is logged as a warning (messages permanently missed — inherent Telegram limitation, documented).
- First-ever run for a chat starts from "now" (no history import) — keeps the DB clean and predictable.

---

## 8. Daily scheduling & timezone (`scheduler.py`)

Follow the `gmail_calendar/bot.py` loop style — no cron, no APScheduler:

```python
def next_run_at(now: datetime, tz: ZoneInfo-like = pytz.timezone(SENTINEL_TIMEZONE),
                hhmm: str = SENTINEL_SUMMARY_TIME) -> datetime:
    hh, mm = map(int, hhmm.split(":"))
    candidate = now.astimezone(tz).replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now.astimezone(tz):
        candidate += timedelta(days=1)
    return candidate

async def scheduler_loop(db, bot_client):
    while True:
        target = next_run_at(datetime.now(tz=pytz.UTC))
        logger.info("Next digest run at %s", target.isoformat())
        await asyncio.sleep(max(0, (target - datetime.now(tz=pytz.UTC))).total_seconds())
        try:
            await run_daily_summary(db, bot_client)     # §11
        except Exception:
            logger.exception("Daily summary failed — messages stay pending")
        # loop recomputes; if run finished before 19:00+ε same day, sleep targets tomorrow
```

- `pytz` is already a dependency (`requirements.txt:3`). Asia/Singapore has no DST, so the naive "replace hour and add a day" arithmetic is safe; the helper is still unit-tested (§13).
- Optional env override `SENTINEL_SUMMARY_TIME` supports testing (e.g. `HH:MM` a few minutes away) without code changes.
- Guard against missed fires while the process was asleep/suspended: if `scheduler_loop` wakes significantly past target (clock jump), it simply runs immediately — acceptable for a digest.

---

## 9. LLM summarization (`summarizer.py`)

### 9.1 Structured output models (Pydantic, mirrors `gmail_calendar/llm.py:28-46`)

```python
class Insight(BaseModel):
    title: str = Field(..., description="Short headline of the insight")
    detail: str = Field(..., description="2-4 sentence explanation with concrete specifics")
    source_groups: list[str] = Field(..., description="Exact names of the SISC groups this came from")
    importance: float = Field(..., ge=0.0, le=1.0)

class DailyDigest(BaseModel):
    highlights: list[str] = Field(..., description="0-5 one-line top takeaways")
    insights: list[Insight] = Field(...)
```

### 9.2 Transcript construction

`build_transcript(messages_by_chat)` produces, per group:

```text
=== GROUP: SISC <> AI Builders ===
[09:14] Alice (@alice): <text up to SENTINEL_MAX_MSG_CHARS>
[09:20] Bob: <...>  (↪ replying to Alice)  (⤵ forwarded from News Source)  [photo: report.pdf]
```

- Chronological per group; groups concatenated in a deterministic order (by message count desc).
- If total exceeds `SENTINEL_MAX_TRANSCRIPT_CHARS`, split into **multiple LLM calls** (chunk at group boundaries) and merge the resulting `DailyDigest` objects (concatenate, dedupe near-identical insights by title similarity, re-sort by importance, truncate `source_groups` unions). Merge logic is pure-Python and unit-tested with a fake LLM.
- Each chunk prompt explicitly instructs: only attribute `source_groups` from that chunk's group headers.

### 9.3 System prompt (draft)

```text
You are the daily analyst for SISC (Super-Individual Secret Club), an invite-only
tech/AI community. You receive chronological transcripts from several Telegram
groups whose names start with "SISC <>".

Produce a DailyDigest:
- insights: the genuinely important items (project launches, notable discussions,
  decisions, opportunities, events, member wins). Skip chatter, greetings, memes,
  logistics noise. Each insight MUST list the exact group name(s) it came from in
  source_groups. Do not invent group names that are not in the transcript.
- highlights: at most 5 punchy one-liners for someone who read nothing today.
- importance 0.0-1.0 (1.0 = the whole club should act on this today).
Be specific: name people/projects/links when they appear. Write in clear English.
```

### 9.4 Provider call (dual pattern — reuse `gmail_calendar/llm.py` shape)

- Primary: `google-genai` `client.models.generate_content(..., response_schema=DailyDigest, response_mime_type="application/json", temperature=0.2)` with `SENTINEL_DIGEST_MODEL`.
- Fallback: `instructor.from_openai(OpenAI())` with `SENTINEL_OPENAI_DIGEST_MODEL`, `max_retries=2`.
- Lazy client singletons; `llm_provider` + `llm_latency_ms` recorded into `summary_runs`.
- If **both** providers fail → raise → run marked `failed`, messages stay `pending` (retry next day or on manual trigger).

---

## 10. Report formatting & bot delivery (`report.py`, `notifier.py`)

### 10.1 Rendering

```text
📜 SISC Daily Digest — Fri 18 Sep 2026 (SGT)
Window: 17 Sep 19:00 → 18 Sep 19:00 SGT
Groups: 4 · Messages: 213

⭐ Highlights
• ...
• ...

🔍 Insights
1. <title>  (0.82)
   <detail>
   Source: SISC <> AI Builders, SISC <> Founders

🧾 Coverage
SISC <> AI Builders — 96 msgs · SISC <> Founders — 58 msgs · ...
```

Deterministic ASCII/emoji plain text (no parse mode → immune to Markdown-breaking group titles; consistent with `gmail_calendar` formatter style which sends plain text).

### 10.2 Splitting under the 4,096-char limit

`split_report(text, limit=4096) -> list[str]`:

1. Split points in priority order: between numbered insight blocks → blank lines → single newlines → hard character cut.
2. Never split inside an insight's `detail` if avoidable; if a single insight exceeds the limit alone, hard-split it with a `…(cont)` marker.
3. Prefix every part after the first with `(Part i/N)` and suffix the first with `(Part 1/N)` when N > 1 — order-preserving and section-safe.
4. Pure function → heavily unit-tested (§13).

### 10.3 Delivery

```python
async def deliver_report(bot_client, parts) -> bool:
    if SENTINEL_DRY_RUN:
        logger.info("DRY RUN — report (%d parts):\n%s", len(parts), "".join(parts))
        return True
    sent = []
    for i, part in enumerate(parts):
        for attempt in range(3):                      # per-part retry, exponential 2/8s
            try:
                msg = await bot_client.send_message(ADMIN_GROUP_ID, part)
                sent.append(msg.id); break
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
            except Exception:
                logger.warning("part %d/%d attempt %d failed", i+1, len(parts), attempt+1)
                await asyncio.sleep(2 ** attempt * 4)
        else:
            return False                              # a part failed → whole delivery failed
    logger.info("Delivered %d parts (%s)", len(parts), sent)
    return True
```

- Uses the **bot** Pyrogram client (§3 table) sending to `ADMIN_GROUP_ID` — bot must be a member/admin of the admin group (already true: the main bot posts there).
- Ordered sequential sends; a failure anywhere returns `False` and the caller leaves all messages `pending`.

---

## 11. Orchestration, state transitions & idempotency (`bot.py`)

Entry point mirrors `gmail_calendar/bot.py:126-162`:

```python
async def main():
    db = PartnerMessageSummarisationDB()                       # async pool + schema init
    user_client = make_user_client();  await user_client.start()
    bot_client  = make_bot_client();   await bot_client.start()
    try:
        targets = await discover_target_chats(user_client, db)
        await catchup_all(user_client, db, targets)
        await asyncio.gather(
            retry_loop(db),                # drains in-memory failed-insert deque
            scheduler_loop(db, bot_client),
        )
    finally:
        await user_client.stop(); await bot_client.stop(); await db.close()
```

**Run algorithm (`run_daily_summary`)** — the idempotency core:

1. `SELECT pg_try_advisory_lock(hashtext('partner_message_summarisation_daily_summary'))` — prevents concurrent runs / double instances. If not acquired: log + skip.
2. Insert `summary_runs(status='running', window_start=last_success.window_end|epoch, window_end=now(SGT))`.
3. `SELECT * FROM tg_messages WHERE status='pending' ORDER BY chat_id, message_date` — the batch. If empty: mark run `success` with counts 0 (optional `SKIP_EMPTY` behavior — send nothing; Decision D5).
4. Group by `chat_id` (title from `tg_chats`), build transcripts (§9.2), call LLM (§9.3-9.4).
5. Render + split + deliver (§10). **Only after** `deliver_report() == True`:
   - `UPDATE tg_messages SET status='completed', processed_at=now(), summary_run_id=<run.id> WHERE id = ANY(<batch ids>)`
   - `UPDATE summary_runs SET status='success', completed_at=now(), message_count=..., chat_count=..., llm_*, report_parts=...`
6. Any exception or failed delivery → `UPDATE summary_runs SET status='failed', error=...`; messages remain `pending` → next run naturally retries (at-least-once delivery; duplicate report possible only if the process dies **between** send and the UPDATE — accepted risk R3, mitigated by making the gap one statement).

**State transition diagram:** `pending ──(delivered)──▶ completed`; `pending` is the only retry state; no `processing` state (single-owner runs under advisory lock).

**Graceful shutdown:** `asyncio.run(main())` + `KeyboardInterrupt` (convention) plus a `loop.add_signal_handler(SIGTERM, ...)` that cancels the gather — ensures a mid-delivery SIGTERM leaves rows `pending` (never half-completed). In-flight insert retry deque is lost, recovered by catch-up.

---

## 12. Dependency decision

| Option | Assessment |
|---|---|
| **`psycopg[binary]>=3.2` (recommended)** | Official Postgres driver, native async support, raw-SQL friendly (matches repo's no-ORM style), connection pool built in. One new package. |
| `asyncpg` | Equally valid; slightly faster; no `RETURNING`-style caveat differences that matter here. Acceptable alternative. |
| SQLAlchemy async | Already in venv **transitively** (not in requirements) — do **not** rely on it; pinning + learning an ORM contradicts repo style. Rejected. |
| SQLite (no new dep) | Contradicts the explicit hosted-PostgreSQL requirement. Rejected. |

`requirements.txt` gets exactly one new line: `psycopg[binary]>=3.2`.

---

## 13. Testing strategy

Framework: **pytest** (matches `gmail_calendar/tests/`).

| Layer | Files | Approach |
|---|---|---|
| Unit — pure logic | `tests/test_report.py` | `split_report`: exact-4096 boundaries, oversized single insight, ordering + `(Part i/N)` markers, no mid-word cuts when avoidable; `render_report` snapshot with fixed digest |
| Unit — time | `tests/test_scheduler.py` | `next_run_at`: before/after 19:00, exactly 19:00, timezone conversion UTC↔SGT, `HH:MM` override |
| Unit — grouping/merge | `tests/test_summarizer.py` | `build_transcript` ordering/truncation/forward-reply annotation; chunking at group boundaries; `merge_digests` dedupe with a stubbed fake LLM function (inject the call) |
| Integration — DB | `tests/test_db.py` | Real PostgreSQL via env-gated fixture: `@pytest.mark.skipif(not os.getenv("PARTNER_MESSAGE_SUMMARISATION_TEST_DATABASE_URL"), ...)`. Covers: schema init idempotence, dedup `ON CONFLICT` returns nothing on second insert, state transition update, `summary_runs` window query, advisory lock behavior |
| Integration — discovery | `tests/test_groups.py` | `title_matches` regex matrix (`SISC <> AI Builders` ✅, `sisc <>` case-sensitivity ❌ by design, `SISC Discussion` ❌); `serialize_message` from constructed fake Pyrogram message objects |
| Manual/live | `login.py`, `SENTINEL_DRY_RUN=true` | Session creation; full pipeline with real LLM + dry-run report printed to logs before first real send |

No test ever hits Telegram live except the documented manual dry-run; CI-friendly (DB tests skip cleanly when no DSN).

---

## 14. Deployment & operations

1. Provision hosted PostgreSQL; create a least-privilege role (`CREATE/DROP` only needed for first boot — after schema creation, `SELECT/INSERT/UPDATE` suffices).
2. Add to VPS `.env` (`/root/tg_sentinel/.env`): `DATABASE_URL`, `SISC_GROUP_REGEX`, optional tuning vars.
3. Run `python -m partner_message_summarisation.login` once (locally or via tmux on VPS) to create `partner_message_summarisation/data/sentinel_listener.session`; copy to VPS.
4. Add bot client to nothing new — `ADMIN_GROUP_ID` membership already exists.
5. Deploy with `bash partner_message_summarisation/partner_message_summarisation_bot.sh` (clone of `gmail_calendar/gmail_calendar_bot.sh`: `cd /root/tg_sentinel`, `source venv/bin/activate`, `nohup python -m partner_message_summarisation.bot >> partner_message_summarisation_output.log 2>&1 &`, `disown`).
6. Operational runbook items in `partner_message_summarisation/README.md`: session expiry/re-login, changing `SISC_GROUP_REGEX` (restart required — discovery runs at startup), forcing an out-of-schedule run via `SENTINEL_SUMMARY_TIME` + restart, and `python -m partner_message_summarisation.status` for backlog inspection.
7. Optional (not in scope): systemd unit replacing the nohup script for auto-restart — flagged as an operational improvement consistent with how the other services currently run.

---

## 15. Observability & metrics

- **Structured logging** in repo format; per-event lines: message ingested (`chat`, `message_id`, `new|dup`), catch-up counts, run start/end with window, LLM provider/latency, delivery part count and message ids.
- **`summary_runs` table** is the primary metrics store: run duration, message/chat counts, provider, latency, parts, errors — queryable history without extra tooling.
- **`partner_message_summarisation/status.py`** prints: pending backlog (total + per chat + oldest pending age), last 7 runs with status, active monitored chats.
- **Derived alerts (log-grep-able):** `Daily summary failed`, `part %d/%d attempt %d failed`, `backfill exceeded limit`, `both LLM providers failed`.
- Suggested counters to log each run: `messages_collected_24h`, `duplicates_skipped`, `pending_after_run` (should be 0), `llm_latency_ms`, `report_parts`.

---

## 16. Assumptions, open decisions & risks

**Assumptions**
- A1. Your user account is a member of all `SISC <>` groups (required for `get_dialogs()` discovery and message flow).
- A2. SISC groups are regular groups/supergroups (not channels-only) so `from_user` sender info exists; channel-posted messages fall back to `sender_chat`.
- A3. The hosted Postgres is reachable from the VPS with TLS; DSN provided as `DATABASE_URL`.
- A4. One `partner_message_summarisation` instance runs at a time (advisory lock protects against accidental doubles).

**Open decisions (confirm before implementation)**
- D1. Driver: `psycopg[binary]` (recommended) vs `asyncpg`.
- D2. Report language/style: English plain-text draft above — confirm tone/emoji level.
- D3. Migrations: idempotent `CREATE TABLE IF NOT EXISTS` at boot (repo convention) is sufficient for v1; adopt a migration tool only if schema churn is expected.
- D4. Catch-up depth default 200 msgs/chat — confirm acceptable.
- D5. Empty-day behavior: default **silently skip** (no "no activity" message) — confirm.
- D6. Whether the digest should also be triggerable via an admin command in the existing bot (`/run_digest`) — nice-to-have, out of v1 scope, would touch `bot.py`.

**Risks**
- R1. **Pyrogram 2.0.106 (not `pyrotgfork`)**: user-session flood waits and occasional disconnections; mitigated by retry loop + catch-up, but extended outages > catch-up bound lose messages permanently (inherent).
- R2. Group title renames between restarts change `source_groups` naming in reports; mitigated by `tg_chats` title snapshot per message and by restricting regex anchoring to `^SISC <>`.
- R3. Crash between Telegram send and the `completed` UPDATE → duplicate digest next run (at-least-once). Accepted; single-statement gap minimizes it.
- R4. In-memory insert-retry deque is lost on crash; catch-up recovers unless the process stayed down past the catch-up bound.
- R5. LLM cost/latency on very active days; mitigated by transcript caps, chunked calls, and cheap default model (`gemini-2.5-flash`).
- R6. `get_dialogs()` on an account with many dialogs is slow on startup (one-time, acceptable) and requires dialog privacy settings to permit it.
- R7. Hosted-Postgres connectivity blips during ingest — insert retry deque + startup backoff; run blocked (stays `pending`) if DB is down at 19:00.

---

## 17. Phased implementation checklist

Ordered; each phase is independently verifiable. ✱ = modifies existing file.

- [ ] **Phase 0 — Prerequisites.** Provision hosted PostgreSQL; obtain DSN. Verify `psycopg` availability on VPS venv.
- [ ] **Phase 1 — Config & scaffold.**
  - ✱ `config.py`: append partner_message_summarisation block (§4).
  - ✱ `.env.example`: add `DATABASE_URL`, `SISC_GROUP_REGEX`, `SENTINEL_*` vars.
  - ✱ `requirements.txt`: add `psycopg[binary]>=3.2`.
  - New `partner_message_summarisation/__init__.py`, `partner_message_summarisation/config.py`.
- [ ] **Phase 2 — Database layer.** New `partner_message_summarisation/db.py` (schema §5, `insert_message`, `upsert_chat`, `get_last_collected_message_id`, `fetch_pending`, `mark_completed`, `start_run/succeed_run/fail_run`, advisory lock helpers) + `partner_message_summarisation/tests/test_db.py`.
- [ ] **Phase 3 — Ingestion.** New `partner_message_summarisation/groups.py` (discovery + `title_matches`), `partner_message_summarisation/listener.py` (monkey-patch, user client factory, `serialize_message`, handler, `catchup_chat`), `partner_message_summarisation/login.py`; `tests/test_groups.py`. *Verify:* run listener locally with dry DB inserts, confirm dedup.
- [ ] **Phase 4 — Scheduler.** New `partner_message_summarisation/scheduler.py` (`next_run_at`, `scheduler_loop`) + `tests/test_scheduler.py`. *Verify:* with `SENTINEL_SUMMARY_TIME` set 2 minutes ahead, loop fires.
- [ ] **Phase 5 — Summarizer.** New `partner_message_summarisation/summarizer.py` (models, transcript builder, chunk/merge, dual-LLM call) + `tests/test_summarizer.py`. *Verify:* feed fixture transcripts through `SENTINEL_DRY_RUN` LLM call.
- [ ] **Phase 6 — Report & delivery.** New `partner_message_summarisation/report.py` (`render_report`, `split_report`), `partner_message_summarisation/notifier.py` (bot client, `deliver_report`) + `tests/test_report.py`. *Verify:* dry-run rendering; live send of a synthetic report to `ADMIN_GROUP_ID`.
- [ ] **Phase 7 — Orchestration.** New `partner_message_summarisation/bot.py` (`main()` per §11, `run_daily_summary`, SIGTERM handling), `partner_message_summarisation/status.py`. *Verify:* end-to-end dry run; kill mid-run → rows stay `pending`; next run completes.
- [ ] **Phase 8 — Deployment & docs.** New `partner_message_summarisation/partner_message_summarisation_bot.sh`, `partner_message_summarisation/README.md` (login, VPS steps, runbook). Deploy to VPS, observe first scheduled run.
- [ ] **Phase 9 — Hardening (post-v1).** Tune prompt from first real digests; add `/run_digest` admin command (D6) if wanted; monitor R1/R5.

---

*Plan prepared from inspection of `tg_sentinel` (bot.py, config.py, database.py, ai_enrichment.py, gmail_calendar/*, docs/, .env.example, venv, .gitignore) and `cryptopulse/run_cryptopulse.py` + `config.py`. No code was changed.*
