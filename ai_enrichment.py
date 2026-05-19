import hashlib
import json
import logging
import re
from typing import Optional
import asyncio

import requests as http_requests
import trafilatura
from ddgs import DDGS
from google import genai
from google.genai import types
from openai import OpenAI
from pydantic import BaseModel, Field
import instructor
from telegram import LinkPreviewOptions

from config import (
    GEMINI_API_KEY,
    AI_CONTEXT_WINDOW,
    GEMINI_ENRICHMENT_MODEL,
    FIRECRAWL_API_KEY,
    OPENAI_API_KEY,
    OPENAI_ENRICHMENT_MODEL,
)
from database import db

logger = logging.getLogger(__name__)

URL_SCORE_THRESHOLD = 0.65

URL_SCORING_SYSTEM_PROMPT = """You are evaluating whether the content from a URL is worth enriching for a high-signal tech/AI/startup community.

Score the content from 0.0 to 1.0:
- 0.8-1.0: Highly substantive — research paper, major tech announcement, detailed analysis, significant news event
- 0.65-0.79: Good — technical blog post, product launch, industry news with depth
- 0.4-0.64: Borderline — light news, brief announcements, opinion pieces with limited facts
- 0.0-0.39: Not worth it — paywall/empty content, social media posts, trivial content, memes, transaction pages, profile pages, raw blockchain data

Respond with ONLY valid JSON (no markdown, no code fences):
{"score": 0.0-1.0, "reason": "brief explanation", "topic": "brief topic description", "search_queries": ["query1", "query2"]}

search_queries should be 1-3 specific queries to find additional context about this topic. Leave as [] if the fetched content is already comprehensive."""

WORTHINESS_SYSTEM_PROMPT = """You are a conversation classifier for a high-signal tech/AI/startup community group.

You will receive the last N messages from a Telegram group conversation. Your job is to determine whether this conversation would benefit from additional context enrichment.

A conversation is WORTHY of enrichment ONLY if it meets ALL of these conditions:
- There is substantive discussion (not just a topic mention), AND
- External context would meaningfully improve understanding, AND
- The topic is specific enough to enrich with factual information

Specific worthy examples:
1. A URL was shared that points to a meaningful article, paper, or resource about technology, AI, startups, economics, geopolitics, science, or emerging trends.
2. Users are having a multi-turn technical discussion where specific facts, data, or background would add value.
3. The discussion touches on a concrete newsworthy event or industry development with clear factual dimensions.

A conversation is NOT worthy if ANY of these apply:
- It's casual chatting, jokes, greetings, or social planning
- The discussion is already self-contained and doesn't need external context
- It's opinions without factual grounding needed
- Short reactions or acknowledgments
- Event logistics (meetups, scheduling)
- The topic is too vague or underspecified to enrich meaningfully
- Single-sentence topic mentions with no depth, follow-up questions, or specifics (e.g. "explain quantum computing")
- Topic drops without a question, request, or substantive discussion
- Messages that just name a topic in under ~10 words total without elaboration

URL HANDLING:
- If URLs are present, a <url_previews> section will be provided with fetched content from those URLs.
- Use the URL preview content to judge whether the URLs point to something genuinely enriching (articles, papers, announcements, detailed analyses) or low-value pages (raw transaction pages, blockchain explorers, profile pages, social media posts with no substance, etc.).
- A URL alone is NOT enough to classify as worthy. The URL's content must actually point to substantive educational or informational material that would benefit from enrichment.

Be CONSERVATIVE. When in doubt, classify as not_worthy. A false negative (missing a worthy topic) is far less harmful than a false positive (enriching trivial chatter).

Respond with ONLY valid JSON (no markdown, no code fences):
{"should_reply": true/false, "confidence": 0.0-1.0, "reason": "url_shared" | "technical_discussion" | "macro_trends" | "not_worthy", "topic": "brief topic description", "search_queries": ["query1", "query2"]}

If a URL is present AND its content is genuinely enriching, set reason to "url_shared" and include the URL topic in search_queries.
If no URL but technical/newsworthy with substantive depth, set reason to "technical_discussion" or "macro_trends" and provide 2-3 specific search queries.
If URLs point to low-value content with no substantive discussion around them, set reason to "not_worthy".
If not worthy for any reason, set reason to "not_worthy", confidence below 0.4, topic to "", and search_queries to []."""

ENRICHMENT_REPLY_SYSTEM_PROMPT = """You are a concise context-enrichment assistant for a high-signal tech community.

Given a group conversation and retrieved context, write a brief enrichment reply.

FORMAT RULES:
- Structure the reply as a short introductory sentence followed by bullet points (•) for key facts, features, or details.
- Use emoji sparingly as bullet prefixes ONLY when they genuinely aid scannability (e.g. 🔍 for search-related, 🛠️ for tools, ⚡ for highlights, 📊 for data). Do NOT emoji-spam — max 1 emoji per bullet, skip emoji if none fits naturally.
- Aim for 4-8 bullet points max. Each bullet should be ONE concise point, not a run-on sentence.
- If the topic is simple enough that bullets add no value, a short paragraph is fine.

CONTENT RULES:
- Be factual and informative, not conversational
- Do NOT ask follow-up questions
- Do NOT give opinions or say "I think"
- Do NOT cite sources inline within the text. Write the enrichment content cleanly without any links or source names in the body.
- After the body text, add a blank line, then "Sources:" on its own line, followed by bullet points. ALWAYS use bullet points for sources regardless of count. Example:\nSources:\n• [name1](url1)\n• [name2](url2)
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
admin_buffer = MessageBuffer()


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
        resp = http_requests.post(
            "https://api.firecrawl.dev/v2/search",
            headers={
                "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "limit": max_results,
                "scrapeOptions": {
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        web_results = []
        if isinstance(data, dict):
            web_results = data.get("web", [])
        elif isinstance(data, list):
            web_results = data
        search_results = []
        for r in web_results:
            title = r.get("title", "")
            url = r.get("url", "")
            content = (r.get("markdown", "") or r.get("description", ""))[:3000]
            search_results.append({"title": title, "url": url, "content": content})
        has_content = any(r["content"] for r in search_results)
        if not has_content:
            logger.warning(
                f"[Enrichment] Firecrawl returned {len(search_results)} results but all have empty content"
            )
            return []
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

openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)


def _classify_worthiness_openai(context_text: str) -> dict:
    response = openai_client.responses.create(
        model=OPENAI_ENRICHMENT_MODEL,
        instructions=WORTHINESS_SYSTEM_PROMPT,
        input=context_text,
    )
    raw = response.output_text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def _generate_enrichment_reply_openai(
    context_text: str, retrieved_context: str
) -> Optional[str]:
    prompt = f"""<conversation>
{context_text}
</conversation>

<retrieved_context>
{retrieved_context}
</retrieved_context>

Based on the conversation and the retrieved context, write a concise enrichment reply. If the context doesn't add meaningful value, respond with exactly: NO_REPLY"""

    response = openai_client.responses.create(
        model=OPENAI_ENRICHMENT_MODEL,
        instructions=ENRICHMENT_REPLY_SYSTEM_PROMPT,
        input=prompt,
    )
    reply = response.output_text.strip()
    if reply == "NO_REPLY" or not reply:
        return None
    return reply


def classify_worthiness(context_text: str) -> dict:
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_ENRICHMENT_MODEL,
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
        result = json.loads(raw)
        logger.info("[Enrichment] classify_worthiness succeeded via Gemini")
        return result
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"[Enrichment] Gemini classify_worthiness failed: {e}")

    if openai_client:
        try:
            result = _classify_worthiness_openai(context_text)
            logger.info(
                "[Enrichment] classify_worthiness succeeded via OpenAI (fallback)"
            )
            return result
        except Exception as e:
            logger.error(
                f"[Enrichment] OpenAI classify_worthiness fallback also failed: {e}"
            )

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
    prompt = f"""<conversation>
{context_text}
</conversation>

<retrieved_context>
{retrieved_context}
</retrieved_context>

Based on the conversation and the retrieved context, write a concise enrichment reply. If the context doesn't add meaningful value, respond with exactly: NO_REPLY"""

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_ENRICHMENT_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=ENRICHMENT_REPLY_SYSTEM_PROMPT,
                temperature=0.3,
            ),
            contents=prompt,
        )
        reply = response.text.strip()
        if reply == "NO_REPLY" or not reply:
            return None
        logger.info("[Enrichment] generate_enrichment_reply succeeded via Gemini")
        return reply
    except Exception as e:
        logger.warning(f"[Enrichment] Gemini generate_enrichment_reply failed: {e}")

    if openai_client:
        try:
            result = _generate_enrichment_reply_openai(context_text, retrieved_context)
            if result:
                logger.info(
                    "[Enrichment] generate_enrichment_reply succeeded via OpenAI (fallback)"
                )
            return result
        except Exception as e:
            logger.error(
                f"[Enrichment] OpenAI generate_enrichment_reply fallback also failed: {e}"
            )

    return None


_MD_V2_SPECIAL = set("_*[]()~`>#+-=|{}.!\\")


def _escape_md_v2(text: str) -> str:
    escaped = []
    for ch in text:
        if ch in _MD_V2_SPECIAL:
            escaped.append("\\")
        escaped.append(ch)
    return "".join(escaped)


_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_BULLET_RE = re.compile(r"^[\s]*[•\-]\s*", re.MULTILINE)
_SOURCE_HEADER_RE = re.compile(r"^Sources:\s*(.*)$", re.MULTILINE)


def format_reply_for_telegram(raw_reply: str) -> str:
    source_match = _SOURCE_HEADER_RE.search(raw_reply)
    if source_match:
        body = raw_reply[: source_match.start()].strip()
        inline_content = source_match.group(1).strip()
        after_content = raw_reply[source_match.end() :].strip()
        parts_list = []
        if inline_content:
            parts_list.append(inline_content)
        if after_content:
            parts_list.append(after_content)
        sources_section = "\n".join(parts_list)
    else:
        body = raw_reply.strip()
        sources_section = ""

    body = _escape_md_v2(body)

    if sources_section:
        lines = sources_section.split("\n")
        formatted_sources = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            line = _BULLET_RE.sub("", line)
            link_match = _LINK_RE.search(line)
            if link_match:
                link_text = _escape_md_v2(link_match.group(1))
                link_url = link_match.group(2)
                formatted_sources.append(f"  \\- [{link_text}]({link_url})")
            else:
                formatted_sources.append(f"  \\- {_escape_md_v2(line)}")
        sources_block = "\\-\\-\\-\n*Sources*:\n" + "\n".join(formatted_sources)
    else:
        sources_block = ""

    parts = [body]
    if sources_block:
        parts.append(sources_block)
    return "\n\n".join(parts)


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


class URLScore(BaseModel):
    score: float
    reason: str
    topic: str
    search_queries: list[str] = Field(default_factory=list)


def _score_url_content_openai(url: str, content: str) -> URLScore:
    prompt = f"URL: {url}\n\nContent:\n{content[:6000]}"
    response = openai_client.responses.create(
        model=OPENAI_ENRICHMENT_MODEL,
        instructions=URL_SCORING_SYSTEM_PROMPT,
        input=prompt,
    )
    raw = response.output_text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```\w*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    data = json.loads(raw)
    return URLScore(**data)


def score_url_content(url: str, content: str) -> URLScore:
    prompt = f"URL: {url}\n\nContent:\n{content[:6000]}"
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_ENRICHMENT_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=URL_SCORING_SYSTEM_PROMPT,
                temperature=0.1,
            ),
            contents=prompt,
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        result = URLScore(**data)
        logger.info(
            f"[Enrichment] score_url_content succeeded via Gemini: score={result.score:.2f} ({result.reason})"
        )
        return result
    except Exception as e:
        logger.warning(f"[Enrichment] Gemini score_url_content failed: {e}")

    if openai_client:
        try:
            result = _score_url_content_openai(url, content)
            logger.info(
                f"[Enrichment] score_url_content succeeded via OpenAI (fallback): score={result.score:.2f}"
            )
            return result
        except Exception as e:
            logger.error(
                f"[Enrichment] OpenAI score_url_content fallback also failed: {e}"
            )

    return URLScore(score=0.0, reason="scoring_error", topic="", search_queries=[])


class PollEvaluation(BaseModel):
    should_create_poll: bool
    question: Optional[str] = None
    options: Optional[list[str]] = Field(default=None, min_length=2, max_length=5)
    allows_multiple_answers: Optional[bool] = None


POLL_EVALUATION_SYSTEM_PROMPT = """You are a community engagement assistant for a high-signal tech/AI/startup community called SISC (Super-Individual Secret Club).

Given a group conversation, its topic, and some retrieved context, decide whether this topic would spark fun, engaging discussion via a poll.

A GOOD poll candidate:
- The topic has clear alternatives, choices, or opinions people would enjoy weighing in on
- The question can be phrased in a fun, casual, or light-hearted way
- It invites community participation and gets people talking
- Examples: "Which tool do you actually use?", "What's your take on X?", "If you could only pick one...", "Which trend are you betting on?"

A BAD poll candidate:
- Topics that are purely factual with no opinion angle
- Topics too niche or technical for most people to have a take on
- Topics where all options would be essentially the same
- The conversation was just casual chatter with no substantive angle

TONE: Keep it fun, light-hearted, and community-friendly. The question should feel like a friend asking, not a survey. Use casual phrasing. Feel free to be playful.

RULES:
- Provide 2-4 options (DO NOT include "Others" — it will be added automatically)
- Each option should be short (under 8 words ideally)
- For "pick one" or "which is best" questions, set allows_multiple_answers to false
- For "which ones interest you" or "select all that apply" questions, set allows_multiple_answers to true
- Be CONSERVATIVE — roughly only 30% of topics should get a poll. When in doubt, set should_create_poll to false.
- If should_create_poll is false, leave question, options, and allows_multiple_answers as null."""


def _create_gemini_instructor():
    return instructor.from_genai(gemini_client, model=GEMINI_ENRICHMENT_MODEL)


def _create_openai_instructor():
    if not openai_client:
        return None
    return instructor.from_openai(openai_client, model=OPENAI_ENRICHMENT_MODEL)


def evaluate_poll_opportunity(
    context_text: str, topic: str, retrieved_context: str
) -> Optional[PollEvaluation]:
    user_message = f"""<conversation>
{context_text}
</conversation>

<topic>{topic}</topic>

<retrieved_context>
{retrieved_context[:4000]}
</retrieved_context>

Based on the conversation and context above, would this topic make for a fun, engaging poll?"""

    try:
        client = _create_gemini_instructor()
        if client:
            result = client.chat.completions.create(
                response_model=PollEvaluation,
                messages=[
                    {"role": "system", "content": POLL_EVALUATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                max_retries=2,
            )
            logger.info(
                "[Enrichment] evaluate_poll_opportunity succeeded via Gemini (instructor)"
            )
            return result
    except Exception as e:
        logger.warning(f"[Enrichment] Gemini poll evaluation failed: {e}")

    try:
        client = _create_openai_instructor()
        if client:
            result = client.chat.completions.create(
                response_model=PollEvaluation,
                messages=[
                    {"role": "system", "content": POLL_EVALUATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                max_retries=2,
            )
            logger.info(
                "[Enrichment] evaluate_poll_opportunity succeeded via OpenAI (instructor fallback)"
            )
            return result
    except Exception as e:
        logger.error(f"[Enrichment] OpenAI poll evaluation fallback also failed: {e}")

    return None


async def send_discussion_poll(
    bot, chat_id: int, question: str, options: list[str], allows_multiple_answers: bool
) -> None:
    poll_options = list(options)
    poll_options.append("Others (drop in chat!)")
    try:
        await bot.send_poll(
            chat_id=chat_id,
            question=question,
            options=poll_options,
            is_anonymous=False,
            allows_multiple_answers=allows_multiple_answers,
            disable_notification=True,
        )
        logger.info(
            f"[Enrichment] Poll sent: '{question}' with {len(poll_options)} options (multi={allows_multiple_answers})"
        )
    except Exception as e:
        logger.error(f"[Enrichment] Failed to send poll: {e}")


async def process_enrichment(
    message_data: dict, bot, group_type: str = "target"
) -> None:
    if group_type == "admin":
        state = db.get_admin_enrichment_state()
        if not state["is_enabled_admin"]:
            return
        active_buffer = admin_buffer
    else:
        state = db.get_enrichment_state()
        if not state["is_enabled"]:
            return
        active_buffer = buffer

    active_buffer.add_message(message_data)
    active_buffer.clear_old_messages()

    context_window = active_buffer.get_context_window()
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

    total_text = " ".join(
        msg.get("text", "") for msg in context_window if msg.get("text")
    )
    total_words = len(total_text.split())
    has_url = any(
        extract_urls(msg.get("text", "")) for msg in context_window if msg.get("text")
    )
    if total_words < 20 and not has_url:
        logger.info(
            f"[Enrichment] Skipping: insufficient substance ({total_words} words, no URL). Threshold: 20 words."
        )
        db.record_processed_window(
            [msg["message_id"] for msg in context_window],
            content_hash,
            False,
            "insufficient_substance",
        )
        return

    loop = asyncio.get_running_loop()

    urls = []
    for msg in context_window:
        if msg.get("text"):
            urls.extend(extract_urls(msg["text"]))

    url_contents: dict[str, str] = {}
    if urls:
        logger.info(
            f"[Enrichment] Fetching {len(urls[:2])} URLs: {urls[:2]}"
        )
        for url in urls[:2]:
            content = await loop.run_in_executor(None, fetch_url_content, url)
            if content:
                url_contents[url] = content
                logger.info(
                    f"[Enrichment]   Fetched {len(content)} chars from {url}"
                )
            else:
                logger.info(f"[Enrichment]   No content extracted from {url}")

    retrieved_context = ""
    topic = ""
    reason = "unknown"

    if url_contents:
        # URL-first path: score each fetched URL, pick the best
        best_score_result: Optional[URLScore] = None
        for url, content in url_contents.items():
            score_result = await loop.run_in_executor(
                None, score_url_content, url, content
            )
            logger.info(
                f"[Enrichment] URL score for {url[:80]}: {score_result.score:.2f} ({score_result.reason})"
            )
            if best_score_result is None or score_result.score > best_score_result.score:
                best_score_result = score_result

        if best_score_result.score < URL_SCORE_THRESHOLD:
            logger.info(
                f"[Enrichment] Best URL score {best_score_result.score:.2f} below threshold {URL_SCORE_THRESHOLD}. Skipping."
            )
            db.record_processed_window(
                [msg["message_id"] for msg in context_window],
                content_hash,
                False,
                "url_score_below_threshold",
            )
            return

        topic = best_score_result.topic
        search_queries = best_score_result.search_queries
        reason = "url_shared"
        logger.info(
            f"[Enrichment] URL score sufficient ({best_score_result.score:.2f}). Topic: '{topic}'"
        )

        recent_topics = db.get_recent_reply_topics(limit=10)
        if is_semantically_duplicate(topic, recent_topics):
            logger.info(
                f"[Enrichment] Skipping duplicate topic: '{topic}' (matched against recent {len(recent_topics)} topics)"
            )
            return

        # Build retrieved context from fetched URL content
        url_previews = ""
        for url, content in url_contents.items():
            url_previews += f"\n\n--- Content from {url} ---\n{content}"
        retrieved_context = url_previews

        # Augment with web search if queries provided
        if search_queries:
            logger.info(
                f"[Enrichment] Running {len(search_queries[:3])} search queries to augment URL content: {search_queries[:3]}"
            )
            seen_urls = set(url_contents.keys())
            for query in search_queries[:3]:
                results = await loop.run_in_executor(None, search_web, query, 3)
                logger.info(f"[Enrichment]   Query '{query}': {len(results)} results")
                for r in results:
                    if r["url"] not in seen_urls:
                        seen_urls.add(r["url"])
                        logger.info(
                            f"[Enrichment]     Result: title='{r['title'][:80]}' url='{r['url'][:80]}' content_len={len(r['content'])} content_preview='{r['content'][:120]}'"
                        )
                        retrieved_context += f"\n- {r['title']} ({r['url']}): {r['content']}\n"
                await asyncio.sleep(1)

        db.record_processed_window(
            [msg["message_id"] for msg in context_window],
            content_hash,
            True,
            reason,
        )

    else:
        # Non-URL path: use classify_worthiness on conversation context
        classification = await loop.run_in_executor(
            None, classify_worthiness, context_text
        )

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

        if reason != "url_shared" and not db.get_context_window_enrichment_state()["is_context_window_enabled"]:
            logger.info(
                f"[Enrichment] Context window trigger disabled. Skipping non-URL reason='{reason}'."
            )
            return

        if not should_reply or confidence < 0.8:
            logger.info(
                f"[Enrichment] Not worthy (confidence={confidence:.2f} < 0.8 or should_reply={should_reply}). Skipping."
            )
            return

        recent_topics = db.get_recent_reply_topics(limit=10)
        if is_semantically_duplicate(topic, recent_topics):
            logger.info(
                f"[Enrichment] Skipping duplicate topic: '{topic}' (matched against recent {len(recent_topics)} topics)"
            )
            return

        if search_queries:
            logger.info(
                f"[Enrichment] Running {len(search_queries[:3])} web search queries: {search_queries[:3]}"
            )
            all_results = []
            for query in search_queries[:3]:
                results = await loop.run_in_executor(None, search_web, query, 3)
                all_results.extend(results)
                logger.info(f"[Enrichment]   Query '{query}': {len(results)} results")
                await asyncio.sleep(1)

            seen_urls: set[str] = set()
            for r in all_results:
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    logger.info(
                        f"[Enrichment]     Result: title='{r['title'][:80]}' url='{r['url'][:80]}' content_len={len(r['content'])} content_preview='{r['content'][:120]}'"
                    )
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
        logger.info("[Enrichment] LLM returned NO_REPLY. Skipping.")
        return

    logger.info(
        f"[Enrichment] Reply generated ({len(reply_text)} chars): {reply_text[:120]}..."
    )

    try:
        formatted_text = format_reply_for_telegram(reply_text)
        sent_message = await bot.send_message(
            chat_id=message_data["chat_id"],
            text=formatted_text,
            parse_mode="MarkdownV2",
            disable_notification=True,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception as e:
        if (
            "parse entities" in str(e).lower()
            or "Can't parse" in str(e)
            or "can't be parsed" in str(e).lower()
        ):
            logger.warning(
                f"[Enrichment] MarkdownV2 parse failed, retrying as plain text: {e}"
            )
            sent_message = await bot.send_message(
                chat_id=message_data["chat_id"],
                text=reply_text,
                disable_notification=True,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        else:
            raise

    last_msg = context_window[-1]
    active_buffer.mark_processed_up_to(last_msg["message_id"])

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

    try:
        poll_result = await loop.run_in_executor(
            None, evaluate_poll_opportunity, context_text, topic, retrieved_context
        )
        if (
            poll_result
            and poll_result.should_create_poll
            and poll_result.question
            and poll_result.options
        ):
            await send_discussion_poll(
                bot,
                message_data["chat_id"],
                poll_result.question,
                poll_result.options,
                poll_result.allows_multiple_answers or False,
            )
            logger.info(f"[Enrichment] Poll created for topic: '{topic}'")
        else:
            logger.info(f"[Enrichment] No poll created for topic: '{topic}'")
    except Exception as e:
        logger.error(f"[Enrichment] Poll evaluation/sending failed: {e}")
