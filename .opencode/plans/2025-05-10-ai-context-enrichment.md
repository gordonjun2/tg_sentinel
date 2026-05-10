# AI Context-Enrichment Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a silent AI context-enrichment assistant that monitors TARGET_GROUP_ID conversations and replies only when it can add genuinely useful context, summaries, or relevant information.

**Architecture:** A rolling message buffer captures group messages. After each new message, the full unprocessed window is sent to Gemini to classify worthiness. If worthy, the bot retrieves relevant context (URL scraping or web search via Firecrawl + DuckDuckGo), generates a concise enrichment reply, and posts it. Deduplication via content hashing and a processed-message tracker prevents spam.

**Tech Stack:** python-telegram-bot 20.7, google-genai (Gemini 2.5 Flash), Firecrawl API, duckduckgo-search, trafilatura, SQLite, httpx/requests

---

## File Structure

| File | Responsibility |
|------|---------------|
| `ai_enrichment.py` (new) | Core enrichment logic: context buffer, worthiness classifier, context retrieval, summarizer, deduplication |
| `database.py` (modify) | Add tables: `enrichment_context` (processed windows), `enrichment_replies` (reply history) |
| `config.py` (modify) | Add all enrichment config constants |
| `bot.py` (modify) | Register message listener in TARGET_GROUP_ID, add toggle commands, wire enrichment pipeline |
| `.env.example` (modify) | Add `FIRECRAWL_API_KEY` |
| `requirements.txt` (modify) | Add `duckduckgo-search`, `trafilatura`, `firecrawl-py` |

---

### Task 1: Add Config Constants

**Files:**
- Modify: `config.py` (append after line 58)
- Modify: `.env.example` (append)

- [ ] **Step 1: Add FIRECRAWL_API_KEY to config.py**

Add to `config.py` after the `TELEGRAM_HASH` block (after line 58):

```python
FIRECRAWL_API_KEY = os.getenv('FIRECRAWL_API_KEY')
if not FIRECRAWL_API_KEY:
    raise ValueError("FIRECRAWL_API_KEY not found in environment variables")

AI_CONTEXT_WINDOW = int(os.getenv('AI_CONTEXT_WINDOW', '8'))
AI_ENRICHMENT_MODEL = os.getenv('AI_ENRICHMENT_MODEL', 'gemini-2.5-flash')
AI_ENRICHMENT_ENABLED_DEFAULT = os.getenv('AI_ENRICHMENT_ENABLED_DEFAULT', 'true').lower() == 'true'
```

- [ ] **Step 2: Add FIRECRAWL_API_KEY to .env.example**

Append to `.env.example`:

```
FIRECRAWL_API_KEY=<your_firecrawl_api_key>
AI_CONTEXT_WINDOW=8
AI_ENRICHMENT_MODEL=gemini-2.5-flash
AI_ENRICHMENT_ENABLED_DEFAULT=true
```

- [ ] **Step 3: Add dependencies to requirements.txt**

Append to `requirements.txt`:

```
duckduckgo-search
trafilatura
firecrawl-py
```

- [ ] **Step 4: Install new dependencies**

Run: `source venv/bin/activate && pip install duckduckgo-search trafilatura firecrawl-py`

Expected: All packages install successfully.

- [ ] **Step 5: Commit**

```bash
git add config.py .env.example requirements.txt
git commit -m "feat: add AI enrichment config and dependencies"
```

---

### Task 2: Add Database Tables for Enrichment State

**Files:**
- Modify: `database.py`

- [ ] **Step 1: Add `enrichment_state`, `enrichment_replies`, and `enrichment_processed_windows` table creation in `_init_db`**

Inside `_init_db`, after the `transcription_status` table block (after line 133 `conn.commit()`), add:

```python
            cursor.execute('''
                SELECT name FROM sqlite_master WHERE type='table' AND name='enrichment_state'
            ''')
            table_exists = cursor.fetchone() is not None

            if not table_exists:
                cursor.execute('''
                    CREATE TABLE enrichment_state (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        last_processed_message_id INTEGER,
                        is_enabled BOOLEAN DEFAULT 1,
                        updated_at TEXT
                    )
                ''')
                cursor.execute('''
                    INSERT INTO enrichment_state (last_processed_message_id, is_enabled, updated_at)
                    VALUES (0, ?, ?)
                ''', (AI_ENRICHMENT_ENABLED_DEFAULT, datetime.now(timezone.utc).isoformat()))

            cursor.execute('''
                SELECT name FROM sqlite_master WHERE type='table' AND name='enrichment_replies'
            ''')
            table_exists = cursor.fetchone() is not None

            if not table_exists:
                cursor.execute('''
                    CREATE TABLE enrichment_replies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_ids TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        topic TEXT,
                        reply_message_id INTEGER,
                        created_at TEXT NOT NULL
                    )
                ''')

            cursor.execute('''
                SELECT name FROM sqlite_master WHERE type='table' AND name='enrichment_processed_windows'
            ''')
            table_exists = cursor.fetchone() is not None

            if not table_exists:
                cursor.execute('''
                    CREATE TABLE enrichment_processed_windows (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message_ids TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        evaluated_at TEXT NOT NULL,
                        should_reply BOOLEAN NOT NULL,
                        reason TEXT
                    )
                ''')

            conn.commit()
```

Also add `from config import AI_ENRICHMENT_ENABLED_DEFAULT` at the top of `database.py` (after the existing imports).

- [ ] **Step 2: Add Database methods for enrichment**

Add these methods to the `Database` class in `database.py`, after the `complete_transcription` method (after line 337):

```python
    def get_enrichment_state(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT last_processed_message_id, is_enabled FROM enrichment_state ORDER BY id DESC LIMIT 1'
            )
            row = cursor.fetchone()
            if row:
                return {
                    'last_processed_message_id': row[0],
                    'is_enabled': bool(row[1])
                }
            return {'last_processed_message_id': 0, 'is_enabled': True}

    def update_enrichment_state(self, last_processed_message_id: int = None, is_enabled: bool = None) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            updates = []
            params = []
            if last_processed_message_id is not None:
                updates.append('last_processed_message_id = ?')
                params.append(last_processed_message_id)
            if is_enabled is not None:
                updates.append('is_enabled = ?')
                params.append(is_enabled)
            updates.append('updated_at = ?')
            params.append(datetime.now(timezone.utc).isoformat())
            params.append(1)
            cursor.execute(
                f'UPDATE enrichment_state SET {", ".join(updates)} WHERE id = ?',
                params
            )
            conn.commit()

    def record_enrichment_reply(self, message_ids: list, content_hash: str, topic: str, reply_message_id: int) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO enrichment_replies (message_ids, content_hash, topic, reply_message_id, created_at) VALUES (?, ?, ?, ?, ?)',
                (json.dumps(message_ids), content_hash, topic, reply_message_id, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()

    def is_content_hash_processed(self, content_hash: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM enrichment_processed_windows WHERE content_hash = ? UNION SELECT id FROM enrichment_replies WHERE content_hash = ? LIMIT 1",
                (content_hash, content_hash)
            )
            return cursor.fetchone() is not None

    def record_processed_window(self, message_ids: list, content_hash: str, should_reply: bool, reason: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO enrichment_processed_windows (message_ids, content_hash, evaluated_at, should_reply, reason) VALUES (?, ?, ?, ?, ?)',
                (json.dumps(message_ids), content_hash, datetime.now(timezone.utc).isoformat(), should_reply, reason)
            )
            conn.commit()

    def get_recent_reply_topics(self, limit: int = 10) -> list:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT topic, content_hash, created_at FROM enrichment_replies ORDER BY created_at DESC LIMIT ?',
                (limit,)
            )
            return cursor.fetchall()
```

- [ ] **Step 3: Verify database initializes correctly**

Run: `source venv/bin/activate && python -c "from database import db; state = db.get_enrichment_state(); print(state)"`

Expected: `{'last_processed_message_id': 0, 'is_enabled': True}`

- [ ] **Step 4: Commit**

```bash
git add database.py
git commit -m "feat: add enrichment state and dedup tables to database"
```

---

### Task 3: Create the AI Enrichment Module

**Files:**
- Create: `ai_enrichment.py`

This is the largest task. It contains: context buffer management, worthiness classifier, URL content extraction, web search, summarizer, and deduplication.

- [ ] **Step 1: Create `ai_enrichment.py` with imports and constants**

```python
import hashlib
import json
import logging
import re
from typing import Optional
import asyncio

import requests
import trafilatura
from duckduckgo_search import DDGS
from firecrawl import FirecrawlApp
from google import genai
from google.genai import types

from config import (
    GEMINI_API_KEY,
    AI_CONTEXT_WINDOW,
    AI_ENRICHMENT_MODEL,
    FIRECRAWL_API_KEY,
)
from database import db

logger = logging.getLogger(__name__)

WORTHINESS_SYSTEM_PROMPT = """You are a conversation classifier for a high-signal tech/AI/startup community group.

You will receive the last N messages from a Telegram group conversation. Your job is to determine whether this conversation would benefit from additional context enrichment.

A conversation is WORTHY of enrichment if ANY of these apply:
1. A URL was shared that points to a meaningful article, paper, or resource about technology, AI, startups, economics, geopolitics, science, or emerging trends.
2. Users are discussing a technical topic, new technology, newsworthy event, or industry development that would benefit from factual context or background information.
3. The discussion touches on macro trends, industry analysis, or specialized knowledge where additional facts/context would improve understanding.

A conversation is NOT worthy if:
- It's casual chatting, jokes, greetings, or social planning
- The discussion is already self-contained and doesn't need external context
- It's opinions without factual grounding needed
- Short reactions or acknowledgments
- Event logistics (meetups, scheduling)
- The topic is too vague or underspecified to enrich meaningfully

Respond with ONLY valid JSON (no markdown, no code fences):
{"should_reply": true/false, "confidence": 0.0-1.0, "reason": "url_shared" | "technical_discussion" | "macro_trends" | "not_worthy", "topic": "brief topic description", "search_queries": ["query1", "query2"]}

If a URL is present, set reason to "url_shared" and include the URL topic in search_queries.
If no URL but technical/newsworthy, set reason to "technical_discussion" or "macro_trends" and provide 2-3 specific search queries.
If not worthy, set reason to "not_worthy", confidence below 0.5, topic to "", and search_queries to []."""

ENRICHMENT_REPLY_SYSTEM_PROMPT = """You are a concise context-enrichment assistant for a high-signal tech community.

Given a group conversation and retrieved context, write a brief enrichment reply.
Rules:
- Be factual and informative, not conversational
- Do NOT ask follow-up questions
- Do NOT give opinions or say "I think"
- Keep it under 6 sentences unless the topic genuinely requires more
- Cite sources inline when using web search results
- Do NOT repeat what was already said in the conversation
- Write in a neutral, informative tone
- If the context doesn't add meaningful value, respond with exactly: NO_REPLY"""
```

- [ ] **Step 2: Add context buffer and hashing functions**

Append to `ai_enrichment.py`:

```python
class MessageBuffer:
    def __init__(self):
        self._messages = []
        self._last_replied_index = -1

    def add_message(self, message_data: dict) -> None:
        self._messages.append(message_data)

    def get_unprocessed(self) -> list:
        start = self._last_replied_index + 1
        return self._messages[start:]

    def get_context_window(self) -> list:
        unprocessed = self.get_unprocessed()
        if not unprocessed:
            return []
        return unprocessed[-AI_CONTEXT_WINDOW:]

    def mark_processed_up_to(self, message_id: int) -> None:
        for i, msg in enumerate(self._messages):
            if msg['message_id'] == message_id:
                self._last_replied_index = i
                break

    def clear_old_messages(self, keep_last: int = 100) -> None:
        if len(self._messages) > keep_last * 2:
            cutoff = len(self._messages) - keep_last
            removed = self._messages[:cutoff]
            self._messages = self._messages[cutoff:]
            self._last_replied_index -= len(removed)
            if self._last_replied_index < -1:
                self._last_replied_index = -1


buffer = MessageBuffer()


def compute_content_hash(messages: list) -> str:
    content = json.dumps(
        [msg['message_id'] for msg in messages], sort_keys=True
    )
    return hashlib.sha256(content.encode()).hexdigest()


def extract_urls(text: str) -> list:
    url_pattern = re.compile(
        r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\-.?&=+#%]*'
    )
    return url_pattern.findall(text)


def serialize_messages(messages: list) -> str:
    lines = []
    for msg in messages:
        sender = msg.get('username', msg.get('first_name', 'Unknown'))
        text = msg.get('text', '')
        if text:
            lines.append(f"{sender}: {text}")
    return "\n".join(lines)
```

- [ ] **Step 3: Add URL content extraction and web search**

Append to `ai_enrichment.py`:

```python
def fetch_url_content(url: str) -> Optional[str]:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        content = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
            max_tree_size=50000
        )
        if content and len(content) > 500:
            return content[:8000]
        return None
    except Exception as e:
        logger.error(f"Failed to fetch URL content for {url}: {e}")
        return None


def search_web_firecrawl(query: str, max_results: int = 5) -> list:
    try:
        app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
        results = app.search(query, limit=max_results)
        search_results = []
        data = results.get('data', results if isinstance(results, list) else [])
        for r in data:
            search_results.append({
                'title': r.get('title', r.get('metadata', {}).get('title', '')),
                'url': r.get('url', r.get('metadata', {}).get('sourceURL', '')),
                'content': r.get('markdown', r.get('content', ''))[:3000]
            })
        return search_results
    except Exception as e:
        logger.error(f"Firecrawl search failed for '{query}': {e}")
        return []


def search_web_duckduckgo(query: str, max_results: int = 5) -> list:
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    'title': r.get('title', ''),
                    'url': r.get('href', ''),
                    'content': r.get('body', '')
                })
        return results
    except Exception as e:
        logger.error(f"DuckDuckGo search failed for '{query}': {e}")
        return []


def search_web(query: str, max_results: int = 5) -> list:
    firecrawl_results = search_web_firecrawl(query, max_results)
    if firecrawl_results:
        return firecrawl_results
    return search_web_duckduckgo(query, max_results)
```

- [ ] **Step 4: Add Gemini classifier and summarizer**

Append to `ai_enrichment.py`:

```python
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def classify_worthiness(context_text: str) -> dict:
    try:
        response = gemini_client.models.generate_content(
            model=AI_ENRICHMENT_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=WORTHINESS_SYSTEM_PROMPT,
                temperature=0.1,
            ),
            contents=context_text
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```\w*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
        return json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Worthiness classification failed: {e}")
        return {
            "should_reply": False,
            "confidence": 0.0,
            "reason": "classification_error",
            "topic": "",
            "search_queries": []
        }


def generate_enrichment_reply(context_text: str, retrieved_context: str) -> Optional[str]:
    try:
        prompt = f"""<conversation>
{context_text}
</conversation>

<retrieved_context>
{retrieved_context}
</retrieved_context>

Based on the conversation and the retrieved context, write a concise enrichment reply. If the context doesn't add meaningful value, respond with exactly: NO_REPLY"""

        response = gemini_client.models.generate_content(
            model=AI_ENRICHMENT_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=ENRICHMENT_REPLY_SYSTEM_PROMPT,
                temperature=0.3,
            ),
            contents=prompt
        )
        reply = response.text.strip()
        if reply == "NO_REPLY" or not reply:
            return None
        return reply
    except Exception as e:
        logger.error(f"Enrichment reply generation failed: {e}")
        return None


def is_semantically_duplicate(topic: str, recent_topics: list) -> bool:
    if not topic or not recent_topics:
        return False
    topic_lower = topic.lower()
    for recent_topic, recent_hash, _ in recent_topics:
        if recent_topic and recent_topic.lower() == topic_lower:
            return True
        recent_words = set(recent_topic.lower().split()) if recent_topic else set()
        topic_words = set(topic_lower.split())
        if recent_words and topic_words:
            overlap = len(recent_words & topic_words) / max(len(recent_words | topic_words), 1)
            if overlap > 0.7:
                return True
    return False
```

- [ ] **Step 5: Add the main enrichment pipeline**

Append to `ai_enrichment.py`:

```python
async def process_enrichment(message_data: dict, bot) -> None:
    state = db.get_enrichment_state()
    if not state['is_enabled']:
        return

    buffer.add_message(message_data)
    buffer.clear_old_messages()

    context_window = buffer.get_context_window()
    if not context_window:
        return

    content_hash = compute_content_hash(context_window)

    if db.is_content_hash_processed(content_hash):
        return

    context_text = serialize_messages(context_window)

    classification = await asyncio.get_event_loop().run_in_executor(
        None, classify_worthiness, context_text
    )

    should_reply = classification.get('should_reply', False)
    confidence = classification.get('confidence', 0.0)
    reason = classification.get('reason', 'unknown')
    topic = classification.get('topic', '')
    search_queries = classification.get('search_queries', [])

    db.record_processed_window(
        [msg['message_id'] for msg in context_window],
        content_hash,
        should_reply,
        reason
    )

    if not should_reply or confidence < 0.6:
        return

    recent_topics = db.get_recent_reply_topics(limit=10)
    if is_semantically_duplicate(topic, recent_topics):
        logger.info(f"Skipping duplicate topic: {topic}")
        return

    retrieved_context = ""
    urls = []
    for msg in context_window:
        if msg.get('text'):
            urls.extend(extract_urls(msg['text']))

    if urls and reason == "url_shared":
        for url in urls[:2]:
            content = await asyncio.get_event_loop().run_in_executor(
                None, fetch_url_content, url
            )
            if content:
                retrieved_context += f"\n\n--- Content from {url} ---\n{content}"
    elif search_queries:
        all_results = []
        for query in search_queries[:3]:
            results = await asyncio.get_event_loop().run_in_executor(
                None, search_web, query, 3
            )
            all_results.extend(results)
            await asyncio.sleep(1)

        seen_urls = set()
        for r in all_results:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                retrieved_context += f"\n- {r['title']} ({r['url']}): {r['content']}\n"

    if not retrieved_context:
        return

    reply_text = await asyncio.get_event_loop().run_in_executor(
        None, generate_enrichment_reply, context_text, retrieved_context
    )

    if not reply_text:
        return

    try:
        sent_message = await bot.send_message(
            chat_id=message_data['chat_id'],
            text=reply_text,
            disable_notification=True
        )

        last_msg = context_window[-1]
        buffer.mark_processed_up_to(last_msg['message_id'])

        db.record_enrichment_reply(
            [msg['message_id'] for msg in context_window],
            content_hash,
            topic,
            sent_message.message_id
        )
        db.update_enrichment_state(
            last_processed_message_id=last_msg['message_id']
        )

        logger.info(f"Enrichment reply sent for topic: {topic}")

    except Exception as e:
        logger.error(f"Failed to send enrichment reply: {e}")
```

- [ ] **Step 6: Verify the module imports correctly**

Run: `source venv/bin/activate && python -c "from ai_enrichment import buffer, classify_worthiness, search_web_duckduckgo; print('OK')"`

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add ai_enrichment.py
git commit -m "feat: add AI enrichment module with classifier, search, and pipeline"
```

---

### Task 4: Wire Enrichment into Bot

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Add import and message handler**

At the top of `bot.py`, add after the existing imports (after line 25):

```python
from ai_enrichment import process_enrichment, buffer
```

Add the group message handler function in `bot.py`, after the `handle_audio_upload` function (after line 1265):

```python
async def handle_target_group_message(update: Update,
                                      context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not update.message.text:
        return

    message_data = {
        'message_id': update.message.message_id,
        'chat_id': update.effective_chat.id,
        'user_id': update.effective_user.id if update.effective_user else None,
        'username': update.effective_user.username if update.effective_user else None,
        'first_name': update.effective_user.first_name if update.effective_user else None,
        'text': update.message.text,
        'date': update.message.date.isoformat() if update.message.date else None,
    }

    asyncio.create_task(process_enrichment(message_data, context.bot))
```

- [ ] **Step 2: Add toggle commands**

Add after `handle_target_group_message`:

```python
async def enable_enrichment_command(update: Update,
                                    context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    db.update_enrichment_state(is_enabled=True)
    await update.message.reply_text("AI context enrichment enabled.")


async def disable_enrichment_command(update: Update,
                                     context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    db.update_enrichment_state(is_enabled=False)
    await update.message.reply_text("AI context enrichment disabled.")


async def enrichment_status_command(update: Update,
                                    context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_GROUP_ID:
        return
    state = db.get_enrichment_state()
    status = "enabled" if state['is_enabled'] else "disabled"
    await update.message.reply_text(
        f"AI context enrichment is {status}.\n"
        f"Last processed message ID: {state['last_processed_message_id']}"
    )
```

- [ ] **Step 3: Register handlers in `main()`**

In the `main()` function, add handler registrations. After the existing `check_transcription_status` command handler registration (after line 1375), add:

```python
    application.add_handler(
        CommandHandler("enable_insights",
                       enable_enrichment_command,
                       filters=admin_group_filter))
    application.add_handler(
        CommandHandler("disable_insights",
                       disable_enrichment_command,
                       filters=admin_group_filter))
    application.add_handler(
        CommandHandler("insights_status",
                       enrichment_status_command,
                       filters=admin_group_filter))
```

Then, **before** the `CallbackQueryHandler` registrations (before line 1388), add the TARGET_GROUP message handler:

```python
    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=TARGET_GROUP_ID) & filters.TEXT
            & ~filters.COMMAND,
            handle_target_group_message))
```

- [ ] **Step 4: Update admin commands list**

In the `main()` function, update the `admin_commands` list (around line 1303) to include:

```python
    admin_commands = [("help", "Show admin commands and features"),
                      ("export", "Export user data to CSV"),
                      ("stats", "Show current statistics"),
                      ("transcribe_audio", "Transcribe an audio file"),
                      ("check_transcription_status",
                       "Check status of ongoing transcription"),
                      ("enable_insights", "Enable AI context enrichment"),
                      ("disable_insights", "Disable AI context enrichment"),
                      ("insights_status",
                       "Check AI enrichment status")]
```

- [ ] **Step 5: Verify bot starts without errors**

Run: `source venv/bin/activate && timeout 10 python bot.py 2>&1 || true`

Expected: Bot starts up and begins polling. No import errors. (It may fail to fully connect if there are network issues, but no Python import/syntax errors.)

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat: wire AI enrichment pipeline into bot with toggle commands"
```

---

### Task 5: Integration Tests

**Files:**
- Create: `test_enrichment.py`

- [ ] **Step 1: Create test file**

```python
import json
import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock
from ai_enrichment import (
    MessageBuffer, compute_content_hash, extract_urls,
    serialize_messages, classify_worthiness,
    generate_enrichment_reply, is_semantically_duplicate,
)


def test_compute_content_hash_deterministic():
    msgs = [{'message_id': 1}, {'message_id': 2}]
    h1 = compute_content_hash(msgs)
    h2 = compute_content_hash(msgs)
    assert h1 == h2
    assert len(h1) == 64


def test_compute_content_hash_different():
    msgs_a = [{'message_id': 1}]
    msgs_b = [{'message_id': 2}]
    assert compute_content_hash(msgs_a) != compute_content_hash(msgs_b)


def test_extract_urls():
    text = "Check this out https://example.com/article and http://test.io"
    urls = extract_urls(text)
    assert len(urls) == 2
    assert "https://example.com/article" in urls


def test_extract_urls_none():
    assert extract_urls("no urls here") == []


def test_serialize_messages():
    msgs = [
        {'username': 'alice', 'text': 'hello'},
        {'first_name': 'Bob', 'text': 'hi'},
    ]
    result = serialize_messages(msgs)
    assert "alice: hello" in result
    assert "Bob: hi" in result


def test_serialize_messages_skips_empty():
    msgs = [
        {'username': 'alice', 'text': 'hello'},
        {'username': 'bob', 'text': ''},
    ]
    result = serialize_messages(msgs)
    assert "alice: hello" in result
    assert "bob" not in result


def test_message_buffer_add_and_get():
    buf = MessageBuffer()
    buf.add_message({'message_id': 1, 'text': 'a'})
    buf.add_message({'message_id': 2, 'text': 'b'})
    unprocessed = buf.get_unprocessed()
    assert len(unprocessed) == 2


def test_message_buffer_mark_processed():
    buf = MessageBuffer()
    buf.add_message({'message_id': 1, 'text': 'a'})
    buf.add_message({'message_id': 2, 'text': 'b'})
    buf.add_message({'message_id': 3, 'text': 'c'})
    buf.mark_processed_up_to(2)
    unprocessed = buf.get_unprocessed()
    assert len(unprocessed) == 1
    assert unprocessed[0]['message_id'] == 3


def test_message_buffer_context_window_limit():
    buf = MessageBuffer()
    for i in range(1, 15):
        buf.add_message({'message_id': i, 'text': f'msg{i}'})

    with patch('ai_enrichment.AI_CONTEXT_WINDOW', 5):
        window = buf.get_context_window()
        assert len(window) == 5
        assert window[0]['message_id'] == 10


def test_message_buffer_clear_old():
    buf = MessageBuffer()
    for i in range(1, 250):
        buf.add_message({'message_id': i, 'text': f'm{i}'})
    buf.clear_old_messages(keep_last=50)
    assert len(buf._messages) <= 150


def test_is_semantically_duplicate_exact():
    recent = [("AI chips", "hash1", "2025-01-01")]
    assert is_semantically_duplicate("AI chips", recent) is True


def test_is_semantically_duplicate_different():
    recent = [("AI chips", "hash1", "2025-01-01")]
    assert is_semantically_duplicate("restaurant reviews", recent) is False


def test_is_semantically_duplicate_high_overlap():
    recent = [("AI semiconductor demand resilience", "hash1", "2025-01-01")]
    assert is_semantically_duplicate("AI semiconductor demand", recent) is True


def test_is_semantically_duplicate_empty():
    assert is_semantically_duplicate("topic", []) is False
    assert is_semantically_duplicate("", [("topic", "h", "d")]) is False


@patch('ai_enrichment.gemini_client')
def test_classify_worthiness_worthy(mock_client):
    mock_response = MagicMock()
    mock_response.text = '{"should_reply": true, "confidence": 0.9, "reason": "technical_discussion", "topic": "AI chips", "search_queries": ["AI chip demand"]}'
    mock_client.models.generate_content.return_value = mock_response

    result = classify_worthiness("User: TSMC revenue is up")
    assert result['should_reply'] is True
    assert result['confidence'] > 0.5


@patch('ai_enrichment.gemini_client')
def test_classify_worthiness_not_worthy(mock_client):
    mock_response = MagicMock()
    mock_response.text = '{"should_reply": false, "confidence": 0.2, "reason": "not_worthy", "topic": "", "search_queries": []}'
    mock_client.models.generate_content.return_value = mock_response

    result = classify_worthiness("User: haha nice one")
    assert result['should_reply'] is False


@patch('ai_enrichment.gemini_client')
def test_classify_worthiness_malformed_response(mock_client):
    mock_response = MagicMock()
    mock_response.text = 'not json at all'
    mock_client.models.generate_content.return_value = mock_response

    result = classify_worthiness("anything")
    assert result['should_reply'] is False
    assert result['reason'] == 'classification_error'


@patch('ai_enrichment.gemini_client')
def test_generate_enrichment_reply_valid(mock_client):
    mock_response = MagicMock()
    mock_response.text = "TSMC reported strong earnings driven by AI demand."
    mock_client.models.generate_content.return_value = mock_response

    result = generate_enrichment_reply("User: TSMC stock up", "TSMC earnings report...")
    assert result is not None
    assert "TSMC" in result


@patch('ai_enrichment.gemini_client')
def test_generate_enrichment_reply_no_reply(mock_client):
    mock_response = MagicMock()
    mock_response.text = "NO_REPLY"
    mock_client.models.generate_content.return_value = mock_response

    result = generate_enrichment_reply("User: lol", "nothing useful")
    assert result is None


def test_database_enrichment_tables():
    from database import Database

    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        test_db = Database(db_path)
        state = test_db.get_enrichment_state()
        assert state['last_processed_message_id'] == 0
        assert state['is_enabled'] is True

        test_db.update_enrichment_state(last_processed_message_id=42)
        state = test_db.get_enrichment_state()
        assert state['last_processed_message_id'] == 42

        test_db.update_enrichment_state(is_enabled=False)
        state = test_db.get_enrichment_state()
        assert state['is_enabled'] is False

        assert test_db.is_content_hash_processed("abc123") is False

        test_db.record_processed_window([1, 2, 3], "abc123", True, "technical")
        assert test_db.is_content_hash_processed("abc123") is True

        test_db.record_enrichment_reply([1, 2], "def456", "AI chips", 100)
        topics = test_db.get_recent_reply_topics()
        assert len(topics) == 1
        assert topics[0][0] == "AI chips"
    finally:
        os.unlink(db_path)
```

- [ ] **Step 2: Run the tests**

Run: `source venv/bin/activate && python -m pytest test_enrichment.py -v`

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add test_enrichment.py
git commit -m "test: add integration tests for AI enrichment feature"
```

---

### Task 6: Final Verification and Cleanup

**Files:** None new

- [ ] **Step 1: Verify full bot startup**

Run: `source venv/bin/activate && timeout 10 python bot.py 2>&1 || true`

Expected: No import errors or tracebacks on startup.

- [ ] **Step 2: Verify new database tables created**

Run: `source venv/bin/activate && python -c "from database import db; print(db.get_enrichment_state()); print('Tables OK')"`

Expected: Prints state dict and "Tables OK"

- [ ] **Step 3: Verify all tests pass**

Run: `source venv/bin/activate && python -m pytest test_enrichment.py -v`

Expected: All tests pass.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete AI context-enrichment feature"
```

---

## Summary

| Task | Description | New/Modified Files |
|------|-------------|-------------------|
| 1 | Config, env, dependencies | `config.py`, `.env.example`, `requirements.txt` |
| 2 | Database tables & methods | `database.py` |
| 3 | Core enrichment module | `ai_enrichment.py` (new) |
| 4 | Bot integration & handlers | `bot.py` |
| 5 | Tests | `test_enrichment.py` (new) |
| 6 | Final verification | None |

**Key design decisions:**
- **Message buffer resets** after each bot reply — new messages build a fresh context window
- **Evaluates after every new message** — the full unprocessed window is sent to Gemini each time
- **Deduplication** via SHA-256 content hash of message IDs + semantic topic overlap check
- **No cooldown** — relies on deduplication and the worthiness classifier's precision
- **Firecrawl primary search**, DuckDuckGo fallback, trafilatura for URL extraction
- **Toggle via** `/enable_insights`, `/disable_insights`, `/insights_status` in admin group
