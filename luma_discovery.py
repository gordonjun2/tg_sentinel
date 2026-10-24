#!/usr/bin/env python3
"""Luma Singapore discovery cron job.

Scans Luma's public discover API for upcoming Tech/AI/Crypto events in
Singapore, diffs against persisted state to find NEW events, enriches
them with an LLM (food provision level + evidence quotes + context),
then writes JSON/MD reports (tmp/luma-sg-events.{json,md}) and sends a
formatted Telegram digest to the admin group.

Design notes:
- Discovery uses the same unauthenticated endpoint luma.com's own web
  app calls (api.luma.com/discover/get-paginated-events) with category
  slugs tech/ai/crypto scoped to Singapore coordinates. There is no
  server-side date filter, so incremental "new" detection is done by
  event api_id diffing against state; the API always returns upcoming
  events from now forward.
- Requests are politely paced (jittered delays) with exponential
  backoff on 403/429 and hard per-run request caps. The run aborts
  (keeping the last good state) if Luma keeps blocking us.
- LLM calls go through llm_chain (gemini -> openai fallback).
"""

import argparse
import fcntl
import html
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from typing import List, Optional

import instructor
import requests
from google import genai
from google.genai import types
from openai import OpenAI
from pydantic import BaseModel, Field

from config import (
    ADMIN_GROUP_ID,
    BOT_TOKEN,
    GEMINI_API_KEY,
    GEMINI_ENRICHMENT_MODEL,
    OPENAI_API_KEY,
    OPENAI_ENRICHMENT_MODEL,
)
from llm_chain import LLMChainError, run_llm_chain
from luma_scraper import SGT, get_event_details

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_PROJECT_ROOT, "data", "luma_discovery_state.json")
LOCK_FILE = os.path.join(_PROJECT_ROOT, "data", "luma_discovery.lock")
REPORT_JSON = os.path.join(_PROJECT_ROOT, "tmp", "luma-sg-events.json")
REPORT_MD = os.path.join(_PROJECT_ROOT, "tmp", "luma-sg-events.md")

TARGET_GROUP_ID = int(
    os.getenv("LUMA_DISCOVERY_TARGET_GROUP_ID", "") or ADMIN_GROUP_ID
)

# ---------------------------------------------------------------------------
# Discovery API client
# ---------------------------------------------------------------------------

API_BASE = "https://api.luma.com"
SG_COORD = {"latitude": 1.29027, "longitude": 103.851959}
CATEGORIES = ("tech", "ai", "crypto")

PAGE_LIMIT = 50
API_DELAY_RANGE = (2.5, 5.0)  # jittered sleep between discovery API calls
SCRAPE_DELAY_RANGE = (3.0, 6.0)  # jittered sleep between event page scrapes
BACKOFF_STEPS = (5, 15, 45)  # exponential backoff on 403/429
MAX_API_REQUESTS_PER_RUN = 60
MAX_SCRAPES_PER_RUN = 80
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class DiscoveryBlocked(RuntimeError):
    """Luma kept returning 403/429 after all backoff attempts."""


class DiscoveryClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "accept": "application/json",
                "origin": "https://luma.com",
            }
        )
        self.request_count = 0

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _get(self, path: str, referer: str, **params) -> dict:
        url = f"{API_BASE}{path}"
        headers = {"referer": referer}
        for attempt, backoff in enumerate((0,) + BACKOFF_STEPS):
            if backoff:
                logger.warning(
                    f"Discovery API throttled, backing off {backoff}s "
                    f"(attempt {attempt + 1}/{len(BACKOFF_STEPS) + 1})"
                )
                self._sleep(backoff)
            if self.request_count >= MAX_API_REQUESTS_PER_RUN:
                raise DiscoveryBlocked(
                    f"API request cap ({MAX_API_REQUESTS_PER_RUN}) reached"
                )
            self.request_count += 1
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=20)
                if resp.status_code in (403, 429):
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        time.sleep(min(int(retry_after), 60))
                    resp.raise_for_status()
                resp.raise_for_status()
                self._sleep(random.uniform(*API_DELAY_RANGE))
                return resp.json()
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in (403, 429):
                    continue
                raise
        raise DiscoveryBlocked(
            f"Luma kept blocking after {len(BACKOFF_STEPS)} backoffs: {url}"
        )

    def discover_category(self, slug: str) -> list:
        """Walk all upcoming events for one category in Singapore."""
        referer = f"https://luma.com/discover/singapore/{slug}"
        entries = []
        cursor = None
        while True:
            params = {
                **SG_COORD,
                "slug": slug,
                "pagination_limit": PAGE_LIMIT,
            }
            if cursor:
                params["pagination_cursor"] = cursor
            data = self._get("/discover/get-paginated-events", referer, **params)
            batch = data.get("entries", [])
            entries.extend(batch)
            logger.info(
                f"[{slug}] fetched page (total {len(entries)}, "
                f"has_more={data.get('has_more')})"
            )
            if not data.get("has_more") or not batch:
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return entries


# ---------------------------------------------------------------------------
# State (seen events + last scan time)
# ---------------------------------------------------------------------------


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        if not isinstance(state.get("seen"), dict):
            state["seen"] = {}
        return state
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 1, "last_scan_at": None, "seen": {}}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_FILE)


def prune_state(state: dict) -> None:
    """Drop seen entries whose events have already ended."""
    now = datetime.now(SGT)
    removed = 0
    for evt_id in list(state["seen"]):
        start_iso = state["seen"][evt_id].get("start_at")
        if not start_iso:
            continue
        try:
            start_dt = datetime.fromisoformat(start_iso).astimezone(SGT)
        except ValueError:
            continue
        if start_dt < now - timedelta(days=1):
            del state["seen"][evt_id]
            removed += 1
    if removed:
        logger.info(f"Pruned {removed} ended event(s) from state")


# ---------------------------------------------------------------------------
# LLM enrichment
# ---------------------------------------------------------------------------

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

LEVEL_LABELS = {
    "explicit": "Explicit (free food stated)",
    "meal": "Meal (hosted meal provided)",
    "bites/canapés": "Bites/Canapes (provided)",
    "light": "Light (coffee/pastries)",
    "none": "none stated",
}

_CATEGORY_ASCII = {"tech": "Tech", "ai": "AI", "crypto": "Crypto"}

# registration_availability enum (from luma.com's own UI labels)
REGISTRATION_LABELS = {
    "open": "Open for registration",
    "waitlist": "Full - waitlist only",
    "sold-out": "Sold out",
    "coming-soon": "Coming soon (not yet open)",
}


def registration_label(entry: dict) -> str:
    ra = entry.get("registration_availability")
    if ra == "open" and entry.get("near_capacity"):
        return "Open (near capacity)"
    return REGISTRATION_LABELS.get(ra, "NA")

_ASCII_FOLDS = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
    "\u00b7": "-",
    "\u2026": "...",
    "\u00a0": " ",
    "\u200b": "",
}


def _ascii(s: str) -> str:
    """Fold unicode punctuation and accented letters to ASCII.

    Everything rendered inside the <pre> table must be ASCII so the
    box-drawing grid aligns in every Telegram monospace font (emoji
    and wide unicode glyphs have unpredictable cell widths).
    """
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    out = []
    for ch in decomposed:
        if ch in _ASCII_FOLDS:
            out.append(_ASCII_FOLDS[ch])
        elif ord(ch) < 128:
            out.append(ch)
        elif unicodedata.combining(ch):
            continue
        # anything else (emoji etc.) is dropped
    return "".join(out)

CATEGORY_LABELS = {"tech": "Tech", "ai": "AI", "crypto": "Crypto"}

ENRICHMENT_SYSTEM_PROMPT = """\
You classify events for a Singapore tech/AI/crypto community (SISC).
You are given an event name and its Luma description. Return:

- is_relevant: true only if the event is genuinely about Tech, AI or
  Crypto — hackathons, developer workshops, AI or crypto meetups,
  startup/founder/investor events, product launches, web3, robotics,
  data/infra topics. False for generic parties with no tech substance
  or off-topic events that slipped into a Luma category (casual
  sports, music, unrelated student clubs, pure religious/cultural
  gatherings).
- level: whether FREE food or drinks are PROVIDED to attendees,
  exactly one of:
  - "explicit": the description explicitly states food or drinks are
    free/provided/included/on us/complimentary ("free food", "F&B
    provided", "drinks on us", "complimentary dinner", "catered
    lunch included")
  - "meal": a hosted meal the organisers clearly provide — private
    dinner, catered lunch for guests, buffet for registered guests,
    "brunch is served" — even if the word "free" is not used
  - "bites/canapés": canapés, light bites, finger food or afternoon
    tea are served/provided to attendees
  - "light": coffee, pastries or dessert are provided ("coffee and
    pastries on us", "breakfast will be served")
  - "none": NO free food provided. This includes: schedule lines like
    "lunch break" or "dinner break" (attendees fend for themselves),
    food/drinks available for purchase ("cash bar", "menu available",
    "grab a coffee at the cafe"), vague mentions without a provider
    ("networking over dinner" at a venue), or no food mention at all.
  When in doubt, choose "none".
- food_evidence: 0-3 VERBATIM quotes from the description (each under
  200 characters) that prove food is provided. Empty list if level is
  "none". Never paraphrase or invent quotes. Never quote a schedule
  line such as "12:00-13:00 Lunch Break" as evidence.
- context: one punchy sentence (max 20 words) describing what the
  event actually is. Plain text, no emojis.
"""


class EventContext(BaseModel):
    is_relevant: bool
    level: str
    food_evidence: List[str] = Field(default_factory=list)
    context: str = ""


# LLMs occasionally return unaccented/shortened variants; map them to the
# canonical levels used in reports. Unknown values fall back to "none".
LEVEL_ALIASES = {
    "explicit": "explicit",
    "meal": "meal",
    "bites/canapés": "bites/canapés",
    "bites/canapes": "bites/canapés",
    "bites": "bites/canapés",
    "canapés": "bites/canapés",
    "canapes": "bites/canapés",
    "light": "light",
    "none": "none",
}


def normalize_level(raw: str) -> str:
    key = _NORM_WS.sub(" ", (raw or "").strip().lower())
    return LEVEL_ALIASES.get(key, "none")


_gemini_instructor = None
_openai_instructor = None


def _create_gemini_instructor():
    global _gemini_instructor
    if _gemini_instructor is None:
        _gemini_instructor = instructor.from_genai(
            gemini_client, model=GEMINI_ENRICHMENT_MODEL
        )
    return _gemini_instructor


def _create_openai_instructor():
    global _openai_instructor
    if not openai_client:
        return None
    if _openai_instructor is None:
        _openai_instructor = instructor.from_openai(
            openai_client, model=OPENAI_ENRICHMENT_MODEL
        )
    return _openai_instructor


def classify_event(name: str, description: str) -> EventContext:
    user_prompt = f"Event title: {name}\n\nEvent description:\n{description[:6000]}"

    def _via_gemini() -> EventContext:
        client = _create_gemini_instructor()
        return client.chat.completions.create(
            response_model=EventContext,
            messages=[
                {"role": "system", "content": ENRICHMENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_retries=2,
        )

    def _via_openai() -> EventContext:
        client = _create_openai_instructor()
        if client is None:
            raise RuntimeError("OpenAI client not configured")
        return client.chat.completions.create(
            response_model=EventContext,
            messages=[
                {"role": "system", "content": ENRICHMENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_retries=2,
        )

    outcome = run_llm_chain(
        "luma_discovery_classify",
        [
            ("gemini", GEMINI_ENRICHMENT_MODEL, _via_gemini),
            ("openai", OPENAI_ENRICHMENT_MODEL, _via_openai),
        ],
    )
    result = outcome.result
    result.level = normalize_level(result.level)
    return result


_NORM_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _NORM_WS.sub(" ", text).lower().strip()


def validate_evidence(
    quotes: List[str], description: str, max_quotes: int = 3
) -> List[str]:
    """Keep only quotes that actually appear in the description."""
    if not quotes or not description:
        return []
    haystack = _normalize(description)
    validated = []
    for quote in quotes:
        if not quote or not quote.strip():
            continue
        needle = _normalize(quote)[:300]
        if needle and needle in haystack:
            validated.append(" ".join(quote.split()))
        if len(validated) >= max_quotes:
            break
    return validated


# ---------------------------------------------------------------------------
# Catalog report (JSON + MD)
# ---------------------------------------------------------------------------

LEVEL_MD = {
    "explicit": "✅ Explicit",
    "meal": "🍽️ Meal",
    "bites/canapés": "🥟 Bites/Canapés",
    "light": "☕ Light",
    "none": "—",
}


def load_catalog() -> list:
    try:
        with open(REPORT_JSON) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_catalog(catalog: list) -> None:
    os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)
    tmp = REPORT_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(catalog, f, indent=1, ensure_ascii=False)
    os.replace(tmp, REPORT_JSON)


def prune_catalog(catalog: list) -> list:
    now = datetime.now(SGT)
    kept = []
    for entry in catalog:
        try:
            start_dt = datetime.fromisoformat(entry["start_iso"]).astimezone(SGT)
        except (KeyError, ValueError):
            continue
        if start_dt >= now - timedelta(days=1):
            kept.append(entry)
    return kept


def format_day(dt: datetime) -> str:
    return dt.strftime("%a %d %b")


def format_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def write_markdown(catalog: list, last_scan: Optional[str], scanned_count: int) -> None:
    by_day = {}
    for entry in catalog:
        by_day.setdefault(entry["date"], []).append(entry)

    counts = {}
    for entry in catalog:
        counts[entry["level"]] = counts.get(entry["level"], 0) + 1

    lines = [
        "# 🍜 Luma Singapore — Tech/AI/Crypto Events with Food",
        "",
        f"**{len(catalog)} upcoming events with food** found on Luma "
        f"({', '.join(CATEGORY_LABELS[c] for c in CATEGORIES)} categories), "
        f"sorted by date & time.",
        "",
        f"> Last scan: {last_scan or '—'} · {scanned_count} upcoming events scanned. "
        "Only events with food stated in their Luma description are listed. "
        "Evidence quotes come from each event's Luma description at time of "
        "scanning. Some events may require RSVP approval or be invite-only — "
        "check the link.",
        "",
        "| Level | Meaning | Count |",
        "|---|---|---|",
        f"| ✅ Explicit | States food is provided/included/on us | {counts.get('explicit', 0)} |",
        f"| 🍽️ Meal | Hosted/served meal provided by organisers (private dinner, catered lunch) | {counts.get('meal', 0)} |",
        f"| 🥟 Bites/Canapés | Canapés, light bites, afternoon tea provided | {counts.get('bites/canapés', 0)} |",
        f"| ☕ Light | Coffee + pastries / dessert provided | {counts.get('light', 0)} |",
        "",
    ]

    for day in sorted(by_day):
        day_events = sorted(by_day[day], key=lambda e: (e["time"], e["name"]))
        lines.append(f"## 📅 {day}")
        lines.append("")
        lines.append("| Cat | Time | Event | Level | Food evidence | Link |")
        lines.append("|-----|------|-------|-------|---------------|------|")
        for entry in day_events:
            evidence = entry["food_evidence"]
            evidence_str = (
                " ".join(evidence)[:160] + ("…" if len(" ".join(evidence)) > 160 else "")
                if evidence
                else "—"
            )
            evidence_str = evidence_str.replace("|", "\\|")
            name = entry["name"].replace("|", "\\|").replace("[", "(").replace("]", ")")
            slug = entry["url"].rstrip("/").split("/")[-1]
            lines.append(
                f"| {entry['category'].upper()} | {entry['time']} | **{name}** "
                f"| {LEVEL_MD.get(entry['level'], entry['level'])} "
                f"| {evidence_str} | [Luma](https://luma.com/{slug}) |"
            )
        lines.append("")

    total_by_cat = {}
    for entry in catalog:
        total_by_cat[entry["category"]] = total_by_cat.get(entry["category"], 0) + 1
    summary = " · ".join(
        f"{CATEGORY_LABELS.get(c, c)}: {n}" for c, n in sorted(total_by_cat.items())
    )
    lines.append("---")
    lines.append(f"*Total: {len(catalog)} events — {summary}*")
    lines.append("")

    tmp = REPORT_MD + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines))
    os.replace(tmp, REPORT_MD)


# ---------------------------------------------------------------------------
# Telegram digest
# ---------------------------------------------------------------------------

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TG_MAX_CHARS = 4000  # leave headroom under the 4096 hard limit


def _display_width(s: str) -> int:
    """Rendered width in a monospace cell grid (emoji count as 2)."""
    w = 0
    for ch in s:
        cp = ord(ch)
        if ch in ("\u200d", "\ufe0f") or unicodedata.combining(ch):
            continue
        if (
            unicodedata.east_asian_width(ch) in ("W", "F")
            or 0x1F000 <= cp <= 0x1FAFF
            or 0x2190 <= cp <= 0x2BFF
        ):
            w += 2
        else:
            w += 1
    return w


def _wrap_width(s: str, width: int) -> List[str]:
    lines: List[str] = []
    for hard_line in s.split("\n"):
        cur = ""
        for word in hard_line.split():
            candidate = word if not cur else cur + " " + word
            if _display_width(candidate) <= width or not cur:
                cur = candidate
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines or [""]


def _event_table(entry: dict) -> str:
    """Box-drawing key/value table for one event (sent inside <pre>).

    All cell content is sanitized to ASCII so the grid aligns in every
    Telegram monospace font.
    """
    start_dt = datetime.fromisoformat(entry["start_iso"]).astimezone(SGT)
    rows = [
        ("Category", _CATEGORY_ASCII.get(entry["category"], entry["category"])),
        ("When", start_dt.strftime("%a %d %b %Y, %H:%M SGT")),
    ]
    if entry.get("venue"):
        rows.append(("Where", entry["venue"]))
    if entry.get("context"):
        rows.append(("What", entry["context"]))
    if entry.get("show_guest_list"):
        rows.append(("Guests", f"{entry.get('guest_count', 0)} registered"))
    else:
        rows.append(("Guests", "NA (hidden by host)"))
    rows.append(("Status", registration_label(entry)))
    rows.append(("Food", LEVEL_LABELS[entry["level"]]))
    if entry.get("food_evidence"):
        rows.append(
            ("Proof", " / ".join(f'"{q}"' for q in entry["food_evidence"][:2]))
        )

    # sanitize to ASCII + collapse whitespace so wrapping/width are exact
    rows = [
        (_ascii(label), " ".join(_ascii(value).split()))
        for label, value in rows
    ]

    value_w = 46
    label_w = max(_display_width(label) for label, _ in rows)
    wrapped = [
        (label, _wrap_width(value, value_w)) for label, value in rows
    ]
    top = "┌" + "─" * (label_w + 2) + "┬" + "─" * (value_w + 2) + "┐"
    sep = "├" + "─" * (label_w + 2) + "┼" + "─" * (value_w + 2) + "┤"
    bottom = "└" + "─" * (label_w + 2) + "┴" + "─" * (value_w + 2) + "┘"
    lines = [top]
    for i, (label, chunks) in enumerate(wrapped):
        if i > 0:
            lines.append(sep)
        for j, chunk in enumerate(chunks):
            lab = label if j == 0 else ""
            pad_l = " " * (label_w - _display_width(lab))
            pad_v = " " * (value_w - _display_width(chunk))
            lines.append(f"│ {lab}{pad_l} │ {chunk}{pad_v} │")
    lines.append(bottom)
    return "\n".join(lines)


def _format_event_block(index: int, entry: dict) -> str:
    table = _event_table(entry)
    url = f"https://luma.com/{entry['url'].rstrip('/').split('/')[-1]}"
    # Links are not clickable inside <pre>, so the event title carries
    # the hyperlink instead (index stays outside the link).
    return (
        f"{index} · "
        f'<a href="{html.escape(url, quote=True)}">'
        f"<b>{html.escape(entry['name'])}</b></a>\n"
        f"<pre>{html.escape(table)}</pre>"
    )


def build_digest(new_entries: list, period_label: str) -> list:
    plural = "event" if len(new_entries) == 1 else "events"
    header = (
        f"🍜 <b>Luma SG — {len(new_entries)} new {plural} with food</b>\n"
        f"<i>🕒 Scanned: {html.escape(period_label)}</i>\n\n"
    )
    messages = []
    current = header
    for idx, entry in enumerate(new_entries, 1):
        block = _format_event_block(idx, entry)
        if len(current) + len(block) + 2 > TG_MAX_CHARS:
            messages.append(current.rstrip() + "\n")
            current = "🍜 <b>Luma SG (cont.)</b>\n\n"
        current += block + "\n\n"
    messages.append(current.rstrip() + "\n")
    return messages


def send_telegram(messages: list) -> bool:
    if not BOT_TOKEN or not TARGET_GROUP_ID:
        logger.warning("Telegram skipped (BOT_TOKEN/TARGET_GROUP_ID missing)")
        return False
    ok = True
    for msg in messages:
        try:
            resp = requests.post(
                TELEGRAM_API_URL.format(token=BOT_TOKEN),
                json={
                    "chat_id": TARGET_GROUP_ID,
                    "text": msg,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                ok = False
                logger.warning(
                    f"Telegram send returned HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
            else:
                logger.info(f"Telegram message sent ({len(msg)} chars)")
        except Exception as exc:
            ok = False
            logger.warning(f"Telegram send failed: {exc}")
        time.sleep(1.5)
    return ok


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def entry_from_api(entry: dict, category: str) -> Optional[dict]:
    event = entry.get("event") or {}
    api_id = event.get("api_id")
    name = (event.get("name") or "").strip()
    start_raw = event.get("start_at")
    if not api_id or not name or not start_raw:
        return None
    try:
        start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(SGT)
    except ValueError:
        return None
    address = (event.get("geo_address_info") or {}).get("address") or ""
    return {
        "event_api_id": api_id,
        "name": name,
        "start_iso": start_dt.isoformat(),
        "date": format_day(start_dt),
        "time": format_time(start_dt),
        "url": event.get("url") or api_id,
        "category": category,
        "venue": address,
        "guest_count": entry.get("guest_count") or 0,
        "show_guest_list": bool(event.get("show_guest_list")),
        "registration_availability": entry.get("registration_availability"),
        "near_capacity": bool((entry.get("ticket_info") or {}).get("is_near_capacity")),
    }


def enrich_event(base: dict, client: DiscoveryClient) -> dict:
    """Scrape description + LLM classify; returns full catalog entry."""
    entry = dict(base)
    url = f"https://luma.com/{base['url'].rstrip('/').split('/')[-1]}"
    client._sleep(random.uniform(*SCRAPE_DELAY_RANGE))
    details = get_event_details(url)
    if details is None:
        logger.warning(f"Scrape failed for {url}; retrying once")
        client._sleep(10)
        details = get_event_details(url)
    description = (details or {}).get("description") or ""
    if details and details.get("name"):
        entry["name"] = details["name"] or entry["name"]

    try:
        result = classify_event(entry["name"], description)
    except LLMChainError as exc:
        logger.error(f"LLM classification failed for {url}: {exc}")
        result = EventContext(
            is_relevant=True, level="none", food_evidence=[], context=""
        )

    evidence = validate_evidence(result.food_evidence, description)
    entry.update(
        {
            "level": result.level if result.is_relevant else "none",
            "food_evidence": evidence,
            "context": result.context.strip(),
            "is_relevant": result.is_relevant,
        }
    )
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description="Luma SG event discovery")
    parser.add_argument("--dry-run", action="store_true", help="no Telegram, no state write")
    parser.add_argument("--no-telegram", action="store_true", help="skip Telegram digest")
    parser.add_argument(
        "--max-enrich", type=int, default=0,
        help="cap number of events enriched this run (0 = no cap)",
    )
    parser.add_argument(
        "--heartbeat", action="store_true",
        help="send a Telegram message even when nothing new is found",
    )
    args = parser.parse_args()

    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.error("Another luma_discovery run is already active; exiting")
        return 1

    try:
        return run(args)
    finally:
        lock.close()


def run(args) -> int:
    state = load_state()
    prune_state(state)
    last_scan = state.get("last_scan_at")
    if last_scan:
        last_scan_sgt = datetime.fromisoformat(last_scan).astimezone(SGT)
        last_scan_display = last_scan_sgt.strftime("%a %d %b %Y, %H:%M SGT")
    else:
        last_scan_display = None
    now_sgt = datetime.now(SGT)
    now_display = now_sgt.strftime("%a %d %b %Y, %H:%M SGT")
    if last_scan_display:
        period_label = f"{last_scan_display} → {now_display}"
    else:
        period_label = f"first scan (full backfill) as of {now_display}"
    logger.info(f"Scan starting (last scan: {last_scan_display or 'never — full backfill'})")

    client = DiscoveryClient()
    try:
        discovered = {}
        for slug in CATEGORIES:
            for entry in client.discover_category(slug):
                base = entry_from_api(entry, slug)
                if base:
                    discovered[base["event_api_id"]] = base
    except DiscoveryBlocked as exc:
        logger.error(f"Discovery aborted: {exc}")
        return 1

    scanned = len(discovered)
    new_ids = [eid for eid in discovered if eid not in state["seen"]]
    # process in chronological order
    new_ids.sort(key=lambda eid: discovered[eid]["start_iso"])
    logger.info(f"Scanned {scanned} upcoming events; {len(new_ids)} new")

    catalog = {e["event_api_id"]: e for e in prune_catalog(load_catalog())}
    now_iso = datetime.now(SGT).isoformat()
    new_relevant = []
    processed_state = {}
    enrich_budget = args.max_enrich if args.max_enrich > 0 else MAX_SCRAPES_PER_RUN

    for i, eid in enumerate(new_ids, 1):
        base = discovered[eid]
        if i > enrich_budget:
            logger.info(
                f"Enrichment cap reached ({enrich_budget}); "
                f"{len(new_ids) - i + 1} events deferred to next run"
            )
            break
        logger.info(
            f"[{i}/{len(new_ids)}] enriching: {base['name'][:60]} "
            f"({base['category']}, {base['date']} {base['time']})"
        )
        try:
            entry = enrich_event(base, client)
        except Exception as exc:
            logger.error(f"Enrichment failed unexpectedly for {eid}: {exc}")
            continue  # not marked seen; retried next run
        if entry.get("is_relevant") and entry.get("level") != "none":
            catalog[eid] = entry
            new_relevant.append(entry)
            logger.info(
                f"  → relevant + food ({entry['level']}), "
                f"evidence={len(entry['food_evidence'])}"
            )
        else:
            logger.info("  → excluded (not relevant or no food)")
        processed_state[eid] = {
            "name": base["name"],
            "first_seen": now_iso,
            "start_at": base["start_iso"],
        }
        # incremental persistence so an aborted run keeps progress
        if not args.dry_run:
            state["seen"].update(processed_state)
            save_state(state)
            processed_state = {}
            save_catalog(list(catalog.values()))
            write_markdown(
                list(catalog.values()), last_scan_display, scanned
            )

    # persist any entries processed after the last incremental save
    if not args.dry_run:
        state["seen"].update(processed_state)
        state["last_scan_at"] = now_iso
        save_state(state)
        save_catalog(list(catalog.values()))
        write_markdown(list(catalog.values()), last_scan_display, scanned)

    logger.info(
        f"Done: {len(new_relevant)} new relevant event(s); "
        f"catalog now {len(catalog)} upcoming; reports written"
    )

    if args.dry_run:
        logger.info("Dry run — skipping Telegram")
        return 0
    if args.no_telegram:
        return 0
    if new_relevant:
        messages = build_digest(new_relevant, period_label)
        send_telegram(messages)
    elif args.heartbeat:
        text = (
            f"🛰 <b>Luma SG Discovery</b> — nothing new · "
            f"{len(catalog)} upcoming tracked\n"
            f"<i>🕒 Scanned: {html.escape(period_label)}</i>"
        )
        send_telegram([text])
    else:
        logger.info("No new relevant events; Telegram skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
