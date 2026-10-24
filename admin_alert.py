"""Admin Telegram alerts for LLM failures.

Called by the default llm_chain failure listener on every failed
attempt. Messages state the provider, model, task, error, and the next
fallback provider. Failures are throttled per (provider, model) so an
outage storm does not flood the admin group; suppressed failures are
counted and reported in the next delivered alert.
"""

import logging
import threading
import time

import requests

from config import ADMIN_GROUP_ID, BOT_TOKEN

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
SEND_TIMEOUT = 10
THROTTLE_SECONDS = 60.0
MAX_ERROR_CHARS = 400

_lock = threading.Lock()
_last_sent: dict[tuple, float] = {}
_suppressed: dict[tuple, int] = {}


def _format_message(provider, model, task, error, fallback, suppressed) -> str:
    if fallback:
        next_line = f"Falling back to: {fallback}"
    else:
        next_line = "No further provider in chain — task FAILED"
    lines = [
        "⚠️ LLM call failed",
        f"Provider: {provider}",
        f"Model: {model}",
        f"Task: {task}",
        next_line,
    ]
    if suppressed:
        lines.append(f"(+{suppressed} repeat failure(s) suppressed)")
    lines.append(f"Error: {str(error)[:MAX_ERROR_CHARS]}")
    return "\n".join(lines)


def notify_llm_failure(provider, model, task, error, fallback=None) -> None:
    """Send one throttled admin alert about a failed LLM attempt.

    Never raises — alerting must not break the LLM call path.
    """
    if not BOT_TOKEN or not ADMIN_GROUP_ID:
        logger.warning(
            "Admin alert skipped (BOT_TOKEN/ADMIN_GROUP_ID missing): "
            f"{provider}/{model} failed for {task}"
        )
        return

    key = (provider, model)
    now = time.monotonic()
    should_send = False
    suppressed = 0
    with _lock:
        last = _last_sent.get(key)
        if last is None or (now - last) >= THROTTLE_SECONDS:
            should_send = True
            _last_sent[key] = now
            suppressed = _suppressed.pop(key, 0)
        else:
            _suppressed[key] = _suppressed.get(key, 0) + 1
    if not should_send:
        logger.info(
            f"Admin alert throttled for {provider}/{model} "
            f"(last sent {now - last:.0f}s ago)"
        )
        return

    text = _format_message(provider, model, task, error, fallback, suppressed)
    try:
        resp = requests.post(
            TELEGRAM_API_URL.format(token=BOT_TOKEN),
            json={
                "chat_id": ADMIN_GROUP_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=SEND_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning(
                f"Admin alert send returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
    except Exception as exc:
        logger.warning(f"Admin alert send failed: {exc}")
