"""Gmail watcher loop.

Polls Cloud Pub/Sub for new-mail notifications, walks ``history.list`` to
discover new message IDs, fetches each message, scores importance, and
forwards important emails to the admin group.

Design notes
------------
* Pub/Sub is configured as a **pull** subscription — no public HTTPS endpoint
  required on the VPS. See ``docs/gcp-setup.md``.
* ``history.list`` returns only changes since the last ``historyId`` we
  persisted. If that history is too old, Gmail returns HTTP 404 and we fall
  back to a full sync via ``messages.list``.
* Each processed message is recorded in ``gmail_processed_messages`` for
  dedup + audit; even skipped/low-score messages are stored.
* A cheap regex pre-filter skips obvious noise (no-reply@, mailer-daemon@,
  promo TLDs) before paying for an LLM call.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from email.utils import parseaddr
from typing import Any

from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import pubsub_v1
from googleapiclient.errors import HttpError

from .db import GmailCalendarDB
from .llm import score_email
from .telegram_notifier import format_email_notification, send_to_admin
from config import (
    GOOGLE_GMAIL_CAL_PROJECT_ID,
    GOOGLE_PUBSUB_SUBSCRIPTION,
    GMAIL_IMPORTANCE_THRESHOLD,
)

logger = logging.getLogger(__name__)

# Cheap pre-filter — anything matching here never reaches the LLM.
_NOISE_FROM_PATTERNS = [
    re.compile(r"no-?reply@", re.I),
    re.compile(r"mailer-?daemon@", re.I),
    re.compile(r"postmaster@", re.I),
    re.compile(r"donotreply@", re.I),
    re.compile(r"unsubscribe@", re.I),
    re.compile(r"newsletter@", re.I),
    re.compile(r"promo(tion|s)?@", re.I),
    re.compile(r"marketing@", re.I),
    re.compile(r"deals@", re.I),
    re.compile(r"notifications?@", re.I),
    re.compile(r"updates?@", re.I),
    re.compile(r"(notify|alert)@", re.I),
]
_NOISE_SUBJECT_PATTERNS = [
    re.compile(r"\b(unsubscribe|manage preferences|view in browser)\b", re.I),
]


def _is_obvious_noise(from_addr: str, subject: str) -> bool:
    for pat in _NOISE_FROM_PATTERNS:
        if pat.search(from_addr):
            return True
    for pat in _NOISE_SUBJECT_PATTERNS:
        if pat.search(subject):
            return True
    return False


def _decode_body(payload: dict | None) -> str:
    """Best-effort extract of plain-text body from a Gmail message payload."""
    if not payload:
        return ""
    # Prefer text/plain part.
    parts = payload.get("parts") or []
    for p in parts:
        mime = p.get("mimeType", "")
        if mime == "text/plain" and p.get("body", {}).get("data"):
            return _b64url_decode(p["body"]["data"])
    # No multipart — maybe the top-level payload is text/plain.
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _b64url_decode(payload["body"]["data"])
    # Fall back to first text part of any kind.
    for p in parts:
        if p.get("mimeType", "").startswith("text/") and p.get("body", {}).get("data"):
            return _b64url_decode(p["body"]["data"])
    return ""


def _b64url_decode(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")


def _extract_headers(payload: dict | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for h in (payload or {}).get("headers", []):
        name = h.get("name", "").lower()
        if name:
            headers[name] = h.get("value", "")
    return headers


def _parse_message(msg: dict) -> dict[str, Any]:
    payload = msg.get("payload") or {}
    headers = _extract_headers(payload)
    from_addr = parseaddr(headers.get("from", ""))[1] or headers.get("from", "")
    return {
        "message_id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "from": from_addr,
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "body": _decode_body(payload),
    }


# --- Pub/Sub ---------------------------------------------------------------


class GmailWatcher:
    def __init__(
        self,
        gmail_service,
        db: GmailCalendarDB,
        subscription_path: str | None = None,
    ) -> None:
        self.gmail = gmail_service
        self.db = db
        self.subscriber = pubsub_v1.SubscriberClient()
        if not GOOGLE_GMAIL_CAL_PROJECT_ID:
            raise RuntimeError(
                "GOOGLE_GMAIL_CAL_PROJECT_ID is required for Pub/Sub pull"
            )
        self.subscription_path = (
            subscription_path
            or self.subscriber.subscription_path(
                GOOGLE_GMAIL_CAL_PROJECT_ID, GOOGLE_PUBSUB_SUBSCRIPTION
            )
        )

    # -- Pub/Sub pull --------------------------------------------------------

    def _pull_notifications(self, max_messages: int = 10) -> list[tuple[dict, str]]:
        """Pull up to ``max_messages`` notifications. Returns (payload, ack_id)."""
        try:
            response = self.subscriber.pull(
                request={
                    "subscription": self.subscription_path,
                    "max_messages": max_messages,
                },
                timeout=10.0,
            )
        except (GoogleAPICallError, TimeoutError) as exc:
            logger.warning("Pub/Sub pull failed: %s", exc)
            return []

        out: list[tuple[dict, str]] = []
        for received in response.received_messages:
            raw = received.message.data.decode("utf-8", errors="replace")
            try:
                import json

                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Malformed Pub/Sub payload: %s", raw[:200])
                continue
            out.append((payload, received.ack_id))
        return out

    def _ack(self, ack_ids: list[str]) -> None:
        if not ack_ids:
            return
        try:
            self.subscriber.acknowledge(
                request={
                    "subscription": self.subscription_path,
                    "ack_ids": ack_ids,
                }
            )
        except GoogleAPICallError as exc:
            logger.warning("Pub/Sub acknowledge failed: %s", exc)

    # -- History + message fetch --------------------------------------------

    def _list_new_message_ids(self, start_history_id: str) -> list[str]:
        """Walk history.list pagination for messages added since start."""
        new_ids: list[str] = []
        page_token: str | None = None
        while True:
            req = self.gmail.users().history().list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                pageToken=page_token,
            )
            resp = req.execute()
            for hist in resp.get("history", []):
                for added in hist.get("messagesAdded", []):
                    msg_id = added.get("message", {}).get("id")
                    if msg_id and msg_id not in new_ids:
                        new_ids.append(msg_id)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return new_ids

    def _full_sync(self) -> list[str]:
        """Last-resort full sync via messages.list when history is too old."""
        logger.info("Performing full Gmail sync (historyId was stale).")
        self.db.mark_gmail_full_sync()
        new_ids: list[str] = []
        page_token: str | None = None
        while True:
            req = self.gmail.users().messages().list(
                userId="me", maxResults=100, pageToken=page_token
            )
            resp = req.execute()
            for m in resp.get("messages", []):
                new_ids.append(m["id"])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return new_ids

    def _fetch_message(self, message_id: str) -> dict | None:
        try:
            return (
                self.gmail.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as exc:
            logger.warning("messages.get(%s) failed: %s", message_id, exc)
            return None

    # -- Main iteration -----------------------------------------------------

    async def run_once(self, tg_client) -> int:
        """Process one batch of notifications. Returns # of forwarded emails."""
        notifications = self._pull_notifications()
        if not notifications:
            return 0

        state = self.db.get_gmail_state()
        if state is None:
            # First ever run — seed with the latest historyId from the
            # most recent Pub/Sub message and ack everything else.
            latest_history_id = notifications[-1][0].get("historyId")
            if latest_history_id:
                self.db.init_gmail_state(latest_history_id)
            self._ack([ack for _, ack in notifications])
            logger.info("Seeded Gmail state with historyId=%s", latest_history_id)
            return 0

        start_history_id = state["history_id"]
        # The newest historyId we observe across this batch becomes the
        # starting point for the next iteration.
        latest_history_id = start_history_id
        for payload, _ in notifications:
            hid = payload.get("historyId")
            if hid:
                latest_history_id = hid

        # Discover what's new.
        try:
            new_message_ids = self._list_new_message_ids(start_history_id)
        except HttpError as exc:
            if exc.status_code == 404:
                logger.warning(
                    "historyId %s no longer available — falling back to full sync",
                    start_history_id,
                )
                new_message_ids = self._full_sync()
                # After full sync we cannot trust start_history_id; reset to
                # whatever Gmail reports as current.
                profile = (
                    self.gmail.users().getProfile(userId="me").execute()
                )
                latest_history_id = profile.get("historyId", latest_history_id)
            else:
                raise

        forwarded = 0
        for mid in new_message_ids:
            if self.db.is_message_processed(mid):
                continue
            await self._process_message(mid, tg_client) and (forwarded := forwarded + 1)

        # Persist the cursor and ack the Pub/Sub messages — only after the
        # batch is fully processed. If we crashed mid-batch the unacked
        # notifications will be redelivered (idempotent thanks to dedup).
        self.db.set_gmail_history_id(latest_history_id)
        self._ack([ack for _, ack in notifications])
        return forwarded

    async def _process_message(self, message_id: str, tg_client) -> bool:
        """Score and possibly forward a single message. Returns True if forwarded."""
        raw = self._fetch_message(message_id)
        if raw is None:
            # Network/API hiccup — don't record so it retries next pass.
            return False

        parsed = _parse_message(raw)
        from_addr = parsed["from"]
        subject = parsed["subject"]

        # Cheap noise filter — record as low/skipped without paying LLM cost.
        if _is_obvious_noise(from_addr, subject):
            self.db.record_message(
                message_id=message_id,
                thread_id=parsed["thread_id"],
                from_addr=from_addr,
                subject=subject,
                score=0.0,
                category="low",
                reasoning="Pre-filter: obvious automated noise",
                forwarded=False,
            )
            return False

        result = await asyncio.get_event_loop().run_in_executor(
            None, score_email, parsed
        )
        if result is None:
            # Both LLMs failed — do NOT forward on uncertainty, but record
            # what we know so we don't retry forever.
            self.db.record_message(
                message_id=message_id,
                thread_id=parsed["thread_id"],
                from_addr=from_addr,
                subject=subject,
                score=None,
                category=None,
                reasoning="LLM scoring failed",
                forwarded=False,
            )
            return False

        should_forward = result.score >= GMAIL_IMPORTANCE_THRESHOLD
        if should_forward:
            text = format_email_notification(
                email=parsed,
                score=result.score,
                category=result.category,
                reasoning=result.reasoning,
                summary=result.summary,
            )
            await send_to_admin(tg_client, text)

        self.db.record_message(
            message_id=message_id,
            thread_id=parsed["thread_id"],
            from_addr=from_addr,
            subject=subject,
            score=result.score,
            category=result.category,
            reasoning=result.reasoning,
            forwarded=should_forward,
        )
        return should_forward

    # -- Watch renewal ------------------------------------------------------

    def renew_watch(self) -> None:
        """Call ``users.watch`` so Gmail keeps publishing to our Pub/Sub topic.

        Pub/Sub push from Gmail needs the watch renewed every ~7 days; we
        renew if we're within 24h of expiry (or if we have no watch yet).
        """
        from config import GOOGLE_PUBSUB_TOPIC

        if not GOOGLE_GMAIL_CAL_PROJECT_ID or not GOOGLE_PUBSUB_TOPIC:
            logger.warning("Cannot renew watch — project/topic not configured")
            return

        topic = f"projects/{GOOGLE_GMAIL_CAL_PROJECT_ID}/topics/{GOOGLE_PUBSUB_TOPIC}"
        try:
            resp = (
                self.gmail.users()
                .watch(
                    userId="me",
                    body={
                        "topicName": topic,
                        "labelIds": ["INBOX"],
                        "labelFilterBehavior": "INCLUDE",
                    },
                )
                .execute()
            )
            expiration = str(resp.get("expiration", ""))
            history_id = str(resp.get("historyId", ""))
            self.db.set_gmail_watch_expiration(expiration)
            state = self.db.get_gmail_state()
            if state is None and history_id:
                self.db.init_gmail_state(history_id)
            elif history_id:
                # Don't rewind the cursor.
                pass
            logger.info("Watch renewed — expiration=%s", expiration or "?")
        except HttpError as exc:
            logger.error("Watch renewal failed: %s", exc)
        except NotFound as exc:
            logger.error("Pub/Sub topic not found: %s", exc)


def should_renew_watch(db: GmailCalendarDB, within_ms: int = 86_400_000) -> bool:
    """True if watch is missing or expires within ``within_ms`` (default 24h)."""
    import time

    state = db.get_gmail_state()
    if state is None or not state.get("watch_expiration"):
        return True
    try:
        expiration_ms = int(state["watch_expiration"])
    except (TypeError, ValueError):
        return True
    return expiration_ms <= (time.time() * 1000) + within_ms
