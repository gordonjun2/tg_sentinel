import hashlib
import json
import logging
import re
from typing import Optional
import asyncio
from urlextract import URLExtract

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
- 0.8-1.0: Highly substantive AND technically deep — research paper with methodology/results, major tech announcement with engineering detail, in-depth technical analysis with specific data, architecture breakdowns, benchmark results
- 0.65-0.79: Good — technical blog post with practical depth, dev tool or API launch with real usage detail, startup/product launch with novel technical approach, tech industry analysis with concrete data points
- 0.4-0.64: Borderline — general business/finance news about tech companies (no technical depth), celebrity/entertainment news, light opinion pieces, brief tech news without substance, political news tangentially related to tech
- 0.0-0.39: Not worth it — non-tech news (sports, lifestyle, gossip), paywall/empty content, social media posts, trivial content, memes, transaction pages, profile pages, raw blockchain data, listicles without depth

CRITICAL: High scores (0.7+) require genuine TECHNICAL depth or significant tech-industry impact. General news about tech companies (earnings reports, executive changes, legal disputes) without technical substance should score 0.4-0.6. Non-tech topics (politics, sports, entertainment, lifestyle, health, crime) should score below 0.4 regardless of article quality.

Few-shot examples:

Example 1 — Score: 0.92
Content: A research paper introducing a new LLM quantization method, including mathematical formulations, benchmark comparisons across multiple models (Llama 3, Mistral, Gemma), latency measurements, and ablation studies showing 2.3x inference speedup with <1% accuracy loss.
Output: {"score": 0.92, "reason": "Research paper with deep technical content: methodology, benchmarks, and quantitative results on LLM quantization", "topic": "New LLM quantization method with 2.3x speedup", "search_queries": ["LLM quantization methods 2025", "model compression inference speedup"]}

Example 2 — Score: 0.78
Content: A detailed blog post by an engineering team explaining their migration from a monolith to microservices, covering service boundaries, event-driven architecture choices, specific technologies used (Kafka, gRPC), observability stack, and lessons learned with concrete metrics (p99 latency dropped from 800ms to 120ms).
Output: {"score": 0.78, "reason": "Technical blog post with practical engineering depth, specific architecture decisions, and measurable outcomes", "topic": "Microservices migration with measurable performance improvements", "search_queries": ["monolith to microservices migration patterns", "event-driven architecture Kafka gRPC"]}

Example 3 — Score: 0.52
Content: A news article reporting that Apple's revenue increased 15% in Q3, discussing iPhone sales figures, services growth, and analyst reactions. No technical content about products or engineering.
Output: {"score": 0.52, "reason": "General business/financial news about a tech company with no technical depth or engineering substance", "topic": "Apple Q3 earnings report", "search_queries": []}

Example 4 — Score: 0.30
Content: An article about a celebrity's new diet and workout routine, with quotes from their trainer and meal plan details.
Output: {"score": 0.30, "reason": "Non-tech celebrity/lifestyle content with zero relevance to tech/AI/startup community", "topic": "Celebrity fitness routine", "search_queries": []}

Example 5 — Score: 0.25
Content: A political news article about election polling results and campaign strategies in an upcoming national election. No tech policy or tech industry implications discussed.
Output: {"score": 0.25, "reason": "Pure political news with no tech angle, no technical depth, irrelevant to tech community", "topic": "National election polling", "search_queries": []}

Example 6 — Score: 0.85
Content: A detailed announcement of a new open-source framework for building AI agents, including architecture overview, code examples, comparison with LangChain/CrewAI, supported LLM providers, tool-use patterns, and benchmark results on standard agent evaluation suites.
Output: {"score": 0.85, "reason": "Major tech product launch with substantial technical detail: architecture, code examples, benchmarks, and competitive analysis", "topic": "New open-source AI agent framework", "search_queries": ["AI agent frameworks comparison 2025", "open source agent orchestration tools"]}

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

ENRICHMENT_REPLY_SYSTEM_PROMPT = """You are a helpful explainer bot in a tech community group chat. Your job is to add quick, useful context so everyone in the group can follow along — even people who aren't deep into the topic.

Given a group conversation and some retrieved context, write a short reply that explains things simply.

AUDIENCE:
- Write for a general audience, not just engineers or domain experts.
- When a technical term or concept comes up, briefly explain what it means in plain language.
- Avoid jargon and acronyms unless you also define them.
- Imagine you're explaining to a smart friend who doesn't work in tech.

FORMAT — follow this structure exactly:
1. One short intro sentence that summarizes what's going on, in everyday language
2. Up to 3 bullet points using •, each covering one key takeaway. Use fewer if the topic is simple.
3. One "Source:" line with the single best source link

FORMATTING:
- Use **bold** for key terms or important phrases so they stand out when scanning.
- Use __italic__ for secondary emphasis or sub-terms.
- Do NOT use backticks for emphasis.

STYLE RULES:
- Max 1 emoji per bullet, only if it genuinely aids scanning. Skip emoji if none fits.
- Each bullet: ONE clear point, not a run-on sentence.
- Pick the single most authoritative source. Format: Source: [title](url)
- Do NOT cite sources inline in the body text.
- Be factual, informative, friendly, and approachable.
- Do NOT ask follow-up questions or give opinions.
- Do NOT repeat what was already said in the conversation.
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


_url_extractor = URLExtract()


def _clean_url(url: str) -> str:
    while url and url[-1] in ".,;:!?" and not re.search(r"\.\w{2,4}$", url):
        url = url[:-1]
    return url


def extract_urls(text: str) -> list:
    return [_clean_url(u) for u in _url_extractor.find_urls(text)]


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


class URLScore(BaseModel):
    score: float
    reason: str
    topic: str
    search_queries: list[str] = Field(default_factory=list)


class WorthinessResult(BaseModel):
    should_reply: bool
    confidence: float
    reason: str
    topic: str
    search_queries: list[str] = Field(default_factory=list)


def _classify_worthiness_openai(context_text: str) -> WorthinessResult:
    client = _create_openai_instructor()
    result = client.chat.completions.create(
        response_model=WorthinessResult,
        messages=[
            {"role": "system", "content": WORTHINESS_SYSTEM_PROMPT},
            {"role": "user", "content": context_text},
        ],
        max_retries=2,
    )
    return result


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
        client = _create_gemini_instructor()
        if client:
            result = client.chat.completions.create(
                response_model=WorthinessResult,
                messages=[
                    {"role": "system", "content": WORTHINESS_SYSTEM_PROMPT},
                    {"role": "user", "content": context_text},
                ],
                max_retries=2,
            )
            logger.info("[Enrichment] classify_worthiness succeeded via Gemini (instructor)")
            return result.model_dump()
    except Exception as e:
        logger.warning(f"[Enrichment] Gemini classify_worthiness failed: {e}")

    if openai_client:
        try:
            result = _classify_worthiness_openai(context_text)
            logger.info(
                "[Enrichment] classify_worthiness succeeded via OpenAI (fallback)"
            )
            return result.model_dump()
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
_SOURCE_HEADER_RE = re.compile(r"^Sources?:\s*(.*)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"__(.+?)__(?!\w)")


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

    bold_segments: list[str] = []
    italic_segments: list[str] = []

    def _save_bold(m: re.Match) -> str:
        idx = len(bold_segments)
        bold_segments.append(m.group(1))
        return f"\x00B{idx}\x00"

    def _save_italic(m: re.Match) -> str:
        idx = len(italic_segments)
        italic_segments.append(m.group(1))
        return f"\x00I{idx}\x00"

    body = _BOLD_RE.sub(_save_bold, body)
    body = _ITALIC_RE.sub(_save_italic, body)

    body = _escape_md_v2(body)

    for idx, text in enumerate(bold_segments):
        body = body.replace(f"\x00B{idx}\x00", f"*{_escape_md_v2(text)}*")
    for idx, text in enumerate(italic_segments):
        body = body.replace(f"\x00I{idx}\x00", f"_{_escape_md_v2(text)}_")

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
                formatted_sources.append(f"[{link_text}]({link_url})")
            else:
                formatted_sources.append(_escape_md_v2(line))

        if len(formatted_sources) == 1:
            sources_block = f"*Source*: {formatted_sources[0]}"
        else:
            bulleted = [f"  • {s}" for s in formatted_sources]
            sources_block = "\\-\\-\\-\n*Sources*:\n" + "\n".join(bulleted)
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


def _score_url_content_openai(url: str, content: str) -> URLScore:
    prompt = f"URL: {url}\n\nContent:\n{content[:6000]}"
    client = _create_openai_instructor()
    result = client.chat.completions.create(
        response_model=URLScore,
        messages=[
            {"role": "system", "content": URL_SCORING_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_retries=2,
    )
    return result


def score_url_content(url: str, content: str) -> URLScore:
    prompt = f"URL: {url}\n\nContent:\n{content[:6000]}"
    try:
        client = _create_gemini_instructor()
        if client:
            result = client.chat.completions.create(
                response_model=URLScore,
                messages=[
                    {"role": "system", "content": URL_SCORING_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_retries=2,
            )
            logger.info(
                f"[Enrichment] score_url_content succeeded via Gemini (instructor): score={result.score:.2f} ({result.reason})"
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
    confidence_score: Optional[float] = Field(default=None, ge=0, le=1)
    question: Optional[str] = None
    options: Optional[list[str]] = Field(default=None, min_length=2, max_length=5)
    allows_multiple_answers: Optional[bool] = None


POLL_EVALUATION_SYSTEM_PROMPT = """You are a community engagement assistant for a high-signal tech/AI/startup community called SISC (Super-Individual Secret Club).

Given a group conversation, its topic, and some retrieved context, decide whether this topic would spark fun, engaging discussion via a poll.

A GOOD poll candidate:
- The topic has genuine opinion splits — reasonable people can disagree
- There are clear alternatives, choices, or debates people would enjoy weighing in on
- The question invites strong takes and personal experience, not just factual recall
- Examples: tool/framework preferences, prediction-based questions, approach comparisons, "hot take" topics where devs have real opinions

A BAD poll candidate:
- Topics that are purely factual with no opinion angle (e.g. "What is the capital of France?")
- Topics where there is a clearly correct answer (e.g. "Which sorting algorithm is O(n log n)?")
- Announcements or news that people would just read, not debate
- Topics too niche for most people to have a personal take on
- Topics where all options would be essentially the same
- The conversation was just casual chatter with no substantive angle
- Company funding announcements, product releases without a debate angle

KEY TEST: Would two reasonable, informed people naturally disagree about this? If the answer is clearly one-sided or purely factual, do NOT create a poll.

TONE: Keep it fun, light-hearted, and community-friendly. The question should feel like a friend asking, not a survey. Use casual phrasing. Feel free to be playful.

RULES:
- Provide 2-4 options (DO NOT include "Others" — it will be added automatically)
- Each option should be short (under 8 words ideally)
- For "pick one" or "which is best" questions, set allows_multiple_answers to false
- For "which ones interest you" or "select all that apply" questions, set allows_multiple_answers to true
- Be VERY CONSERVATIVE — only ~15-20% of topics should get a poll. When in doubt, set should_create_poll to false.
- If should_create_poll is false, leave question, options, and allows_multiple_answers as null.

Few-shot examples:

Example 1 — GOOD poll (genuine opinion split):
Topic: Discussion about whether to use TypeScript or Python for a new AI startup's backend. People are debating type safety vs. iteration speed.
Output: {"should_create_poll": true, "confidence_score": 0.88, "question": "Building an AI startup backend — what's your go-to?", "options": ["TypeScript all the way", "Python for ML flexibility", "Go for performance", "Mix and match per service"], "allows_multiple_answers": false}

Example 2 — GOOD poll (prediction-based debate):
Topic: Conversation about whether AI agents will replace most SaaS tools within 3 years. Strong arguments on both sides.
Output: {"should_create_poll": true, "confidence_score": 0.85, "question": "Will AI agents kill most SaaS tools by 2028?", "options": ["Absolutely, adapt or die", "Nah, SaaS will evolve alongside", "Only the simple ones", "Too early to tell"], "allows_multiple_answers": false}

Example 3 — BAD poll (factual news, no debate):
Topic: A company just raised a $50M Series B. The conversation is about the funding round details.
Output: {"should_create_poll": false, "confidence_score": 0.2, "question": null, "options": null, "allows_multiple_answers": null}
Reason: Funding announcements are factual news — no opinion angle or debate. People would just read about it, not argue.

Example 4 — BAD poll (correct answer exists):
Topic: Technical discussion about which data structure has O(1) lookup time.
Output: {"should_create_poll": false, "confidence_score": 0.1, "question": null, "options": null, "allows_multiple_answers": null}
Reason: This has a clearly correct answer (hash table). Not opinion-based — people would just state the fact.

Example 5 — GOOD poll (approach comparison with real opinions):
Topic: Debate about whether to self-host LLMs or use APIs. Cost, control, latency, and privacy are all being discussed.
Output: {"should_create_poll": true, "confidence_score": 0.82, "question": "Self-host your LLMs or just use APIs?", "options": ["Self-host for control", "APIs all the way", "Hybrid approach", "Depends on the use case"], "allows_multiple_answers": false}

Example 6 — BAD poll (casual chatter, no substance):
Topic: People chatting about the weather, weekend plans, and food recommendations.
Output: {"should_create_poll": false, "confidence_score": 0.05, "question": null, "options": null, "allows_multiple_answers": null}
Reason: Casual social chatter with no substantive angle to build a poll from.

Example 7 — BAD poll (product release, no debate):
Topic: A new version of a framework was released with feature list and changelog. People are reading the release notes.
Output: {"should_create_poll": false, "confidence_score": 0.15, "question": null, "options": null, "allows_multiple_answers": null}
Reason: Product release is factual information. Unless there's a specific controversial feature being debated, this doesn't warrant a poll."""


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

    ctx_window_state = db.get_context_window_enrichment_state()
    if not ctx_window_state["is_context_window_enabled"] and len(context_window) > 1:
        context_window = context_window[-1:]

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
        context_window_state = db.get_context_window_enrichment_state()
        if not context_window_state["is_context_window_enabled"]:
            logger.info(
                "[Enrichment] No URL content and context window enrichment disabled. Skipping."
            )
            db.record_processed_window(
                [msg["message_id"] for msg in context_window],
                content_hash,
                False,
                "no_url_content_cw_disabled",
            )
            return

        total_words = len(total_text.split())
        if total_words < 20:
            logger.info(
                f"[Enrichment] Skipping: insufficient substance ({total_words} words). Threshold: 20 words."
            )
            db.record_processed_window(
                [msg["message_id"] for msg in context_window],
                content_hash,
                False,
                "insufficient_substance",
            )
            return

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
            logger.info(f"[Enrichment] Poll created for topic: '{topic}' (score={poll_result.confidence_score})")
        else:
            logger.info(f"[Enrichment] No poll created for topic: '{topic}' (score={getattr(poll_result, 'confidence_score', 'N/A')}, should_create_poll={getattr(poll_result, 'should_create_poll', 'N/A')})")
    except Exception as e:
        logger.error(f"[Enrichment] Poll evaluation/sending failed: {e}")
