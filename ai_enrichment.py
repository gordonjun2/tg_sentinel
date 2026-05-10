import hashlib
import json
import logging
import re
from typing import Optional
import asyncio

import trafilatura
from ddgs import DDGS
from firecrawl import V1FirecrawlApp
from firecrawl.v1.client import V1ScrapeOptions
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
- Do NOT cite sources inline within the text. Write the enrichment content cleanly without any links or source names in the body.
- After the body text, add a blank line, then list sources. If 1 source: single "Sources:" line using markdown links. If multiple sources, use bullet points. Example with multiple: \nSources:\n• [name1](url1)\n• [name2](url2)
- Each source URL should appear only ONCE in the sources line, never repeated.
- Do NOT repeat what was already said in the conversation
- Write in a neutral, informative tone
- If the context doesn't add meaningful value, respond with exactly: NO_REPLY"""


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
            if msg["message_id"] == message_id:
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
    content = json.dumps([msg["message_id"] for msg in messages], sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


def extract_urls(text: str) -> list:
    url_pattern = re.compile(r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\-.?&=+#%]*")
    return url_pattern.findall(text)


def serialize_messages(messages: list) -> str:
    lines = []
    for msg in messages:
        sender = msg.get("username", msg.get("first_name", "Unknown"))
        text = msg.get("text", "")
        if text:
            lines.append(f"{sender}: {text}")
    return "\n".join(lines)


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
        )
        if content and len(content) > 500:
            return content[:8000]
        return None
    except Exception as e:
        logger.error(f"Failed to fetch URL content for {url}: {e}")
        return None


def search_web_firecrawl(query: str, max_results: int = 5) -> list:
    try:
        app = V1FirecrawlApp(api_key=FIRECRAWL_API_KEY)
        response = app.search(
            query,
            limit=max_results,
            scrape_options=V1ScrapeOptions(formats=["markdown"]),
        )
        search_results = []
        web_results = []
        if hasattr(response, "data") and isinstance(response.data, dict):
            web_results = response.data.get("web", [])
        elif hasattr(response, "data") and isinstance(response.data, list):
            web_results = response.data
        for r in web_results:
            search_results.append(
                {
                    "title": getattr(r, "title", "") or "",
                    "url": getattr(r, "url", "") or "",
                    "content": (getattr(r, "markdown", "") or "")[:3000],
                }
            )
        return search_results
    except Exception as e:
        logger.error(f"Firecrawl search failed for '{query}': {e}")
        return []


def search_web_duckduckgo(query: str, max_results: int = 5) -> list:
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "content": r.get("body", ""),
                    }
                )
        return results
    except Exception as e:
        logger.error(f"DuckDuckGo search failed for '{query}': {e}")
        return []


def search_web(query: str, max_results: int = 5) -> list:
    firecrawl_results = search_web_firecrawl(query, max_results)
    if firecrawl_results:
        return firecrawl_results
    return search_web_duckduckgo(query, max_results)


gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def classify_worthiness(context_text: str) -> dict:
    try:
        response = gemini_client.models.generate_content(
            model=AI_ENRICHMENT_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=WORTHINESS_SYSTEM_PROMPT,
                temperature=0.1,
            ),
            contents=context_text,
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        return json.loads(raw)
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Worthiness classification failed: {e}")
        return {
            "should_reply": False,
            "confidence": 0.0,
            "reason": "classification_error",
            "topic": "",
            "search_queries": [],
        }


def generate_enrichment_reply(
    context_text: str, retrieved_context: str
) -> Optional[str]:
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
            contents=prompt,
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
            overlap = len(recent_words & topic_words) / max(
                len(recent_words | topic_words), 1
            )
            if overlap > 0.7:
                return True
    return False


async def process_enrichment(message_data: dict, bot) -> None:
    state = db.get_enrichment_state()
    if not state["is_enabled"]:
        return

    buffer.add_message(message_data)
    buffer.clear_old_messages()

    context_window = buffer.get_context_window()
    if not context_window:
        return

    content_hash = compute_content_hash(context_window)

    if db.is_content_hash_processed(content_hash):
        logger.info(
            f"[Enrichment] Skipping already processed window hash={content_hash[:12]}... msgs={[m['message_id'] for m in context_window]}"
        )
        return

    context_text = serialize_messages(context_window)

    logger.info(
        f"[Enrichment] Evaluating window ({len(context_window)} msgs, hash={content_hash[:12]}...)"
    )
    for msg in context_window:
        sender = msg.get("username") or msg.get("first_name") or "Unknown"
        text = msg.get("text") or ""
        snippet = text[:100] + ("..." if len(text) > 100 else "")
        logger.info(
            f"[Enrichment]   msg_id={msg['message_id']} from={sender}: {snippet}"
        )

    loop = asyncio.get_running_loop()

    classification = await loop.run_in_executor(None, classify_worthiness, context_text)

    should_reply = classification.get("should_reply", False)
    confidence = classification.get("confidence", 0.0)
    reason = classification.get("reason", "unknown")
    topic = classification.get("topic", "")
    search_queries = classification.get("search_queries", [])

    logger.info(
        f"[Enrichment] Classification: should_reply={should_reply}, confidence={confidence:.2f}, reason={reason}, topic='{topic}', search_queries={search_queries}"
    )

    db.record_processed_window(
        [msg["message_id"] for msg in context_window],
        content_hash,
        should_reply,
        reason,
    )

    if not should_reply or confidence < 0.6:
        logger.info(
            f"[Enrichment] Not worthy (confidence={confidence:.2f} < 0.6 or should_reply={should_reply}). Skipping."
        )
        return

    recent_topics = db.get_recent_reply_topics(limit=10)
    if is_semantically_duplicate(topic, recent_topics):
        logger.info(
            f"[Enrichment] Skipping duplicate topic: '{topic}' (matched against recent {len(recent_topics)} topics)"
        )
        return

    retrieved_context = ""
    urls = []
    for msg in context_window:
        if msg.get("text"):
            urls.extend(extract_urls(msg["text"]))

    if urls and reason == "url_shared":
        logger.info(
            f"[Enrichment] URL enrichment: fetching {len(urls[:2])} urls: {urls[:2]}"
        )
        for url in urls[:2]:
            content = await loop.run_in_executor(None, fetch_url_content, url)
            if content:
                retrieved_context += f"\n\n--- Content from {url} ---\n{content}"
                logger.info(f"[Enrichment]   Fetched {len(content)} chars from {url}")
            else:
                logger.info(f"[Enrichment]   No content extracted from {url}")

    if not retrieved_context and search_queries:
        logger.info(
            f"[Enrichment] URL extraction empty, falling back to web search: {len(search_queries[:3])} queries: {search_queries[:3]}"
        )
        all_results = []
        for query in search_queries[:3]:
            results = await loop.run_in_executor(None, search_web, query, 3)
            all_results.extend(results)
            logger.info(f"[Enrichment]   Query '{query}': {len(results)} results")
            await asyncio.sleep(1)

        seen_urls = set()
        for r in all_results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                retrieved_context += f"\n- {r['title']} ({r['url']}): {r['content']}\n"

    if not retrieved_context:
        logger.info("[Enrichment] No context retrieved. Skipping reply.")
        return

    logger.info(
        f"[Enrichment] Retrieved {len(retrieved_context)} chars of context. Generating reply..."
    )

    reply_text = await loop.run_in_executor(
        None, generate_enrichment_reply, context_text, retrieved_context
    )

    if not reply_text:
        logger.info("[Enrichment] Gemini returned NO_REPLY. Skipping.")
        return

    logger.info(
        f"[Enrichment] Reply generated ({len(reply_text)} chars): {reply_text[:120]}..."
    )

    try:
        sent_message = await bot.send_message(
            chat_id=message_data["chat_id"],
            text=reply_text,
            parse_mode="Markdown",
            disable_notification=True,
        )

        last_msg = context_window[-1]
        buffer.mark_processed_up_to(last_msg["message_id"])

        db.record_enrichment_reply(
            [msg["message_id"] for msg in context_window],
            content_hash,
            topic,
            sent_message.message_id,
        )
        db.update_enrichment_state(last_processed_message_id=last_msg["message_id"])

        logger.info(
            f"[Enrichment] Reply sent (msg_id={sent_message.message_id}) for topic: '{topic}'"
        )

    except Exception as e:
        logger.error(f"[Enrichment] Failed to send reply: {e}")
