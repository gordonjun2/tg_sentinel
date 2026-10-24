"""Cost-ordered LLM provider chain with a circuit breaker.

Providers are tried cheapest-first. When a provider fails, the next one
in the chain takes over automatically. Repeated failures — or permanent
ones such as a retired model returning 404 — open a circuit that skips
the provider for a cooldown window, so calls stop burning retries on a
dead endpoint and go straight to the next provider.

Total failure (LLMChainError) happens ONLY when every attempt in the
chain is unavailable — i.e. each provider either failed when tried or
was skipped because its circuit was open.

Each attempt is a ``(provider, model, thunk)`` tuple. Every failed
attempt fires the failure listener (default: admin Telegram alert via
admin_alert.py) with the provider, model, task, error, and the next
fallback provider if any.

Usage:
    outcome = run_llm_chain("score_url", [
        ("gemini", "gemini-3.8-flash", lambda: call_gemini()),
        ("openai", "gpt-5-mini", lambda: call_openai()),
    ])
    outcome.result, outcome.provider
"""

import logging
import threading
import time
from collections import namedtuple
from typing import Callable, Optional, Sequence

logger = logging.getLogger(__name__)

PROVIDER_CHAIN: Sequence[str] = ("gemini", "openai")

FAILURE_THRESHOLD = 2
COOLDOWN_SECONDS = 300
PERMANENT_COOLDOWN_SECONDS = 1800

PERMANENT_ERROR_MARKERS = (
    "404",
    "not_found",
    "not found",
    "no longer available",
    "deprecated",
    "does not exist",
    "unsupported",
)


class LLMChainError(RuntimeError):
    def __init__(self, task: str, errors):
        self.errors = list(errors)
        detail = " | ".join(self.errors) or "no provider available"
        super().__init__(f"[{task}] all LLM providers failed: {detail}")


ChainOutcome = namedtuple("ChainOutcome", ["result", "provider"])

_lock = threading.Lock()
_health: dict[str, dict] = {}

_failure_listener: Optional[Callable] = None


def set_failure_listener(listener: Optional[Callable]) -> None:
    """Set the callback fired on every failed attempt.

    The listener is called with keyword args:
    provider, model, task, error, fallback (next provider name or None).

    Passing None restores the default listener, which delivers the
    failure to the admin Telegram group via admin_alert.py.
    """
    global _failure_listener
    _failure_listener = listener


def _default_failure_listener(**kwargs) -> None:
    try:
        from admin_alert import notify_llm_failure

        notify_llm_failure(**kwargs)
    except Exception:
        logger.debug("LLM failure alert could not be delivered", exc_info=True)


def _notify_failure(provider: str, model: str, task: str, error: Exception, fallback: Optional[str]) -> None:
    listener = _failure_listener or _default_failure_listener
    try:
        listener(
            provider=provider,
            model=model,
            task=task,
            error=error,
            fallback=fallback,
        )
    except Exception:
        logger.debug("LLM failure listener raised", exc_info=True)


def _entry(name: str) -> dict:
    return _health.setdefault(
        name, {"failures": 0, "open_until": 0.0, "last_error": ""}
    )


def provider_available(name: str) -> bool:
    with _lock:
        return time.monotonic() >= _entry(name)["open_until"]


def record_success(name: str) -> None:
    with _lock:
        entry = _entry(name)
        entry["failures"] = 0
        entry["open_until"] = 0.0
        entry["last_error"] = ""


def record_failure(name: str, exc: Exception) -> None:
    message = str(exc)
    lowered = message.lower()
    permanent = any(marker in lowered for marker in PERMANENT_ERROR_MARKERS)
    with _lock:
        entry = _entry(name)
        entry["failures"] += 1
        entry["last_error"] = message[:300]
        should_open = permanent or entry["failures"] >= FAILURE_THRESHOLD
        if should_open:
            cooldown = (
                PERMANENT_COOLDOWN_SECONDS if permanent else COOLDOWN_SECONDS
            )
            entry["open_until"] = time.monotonic() + cooldown
    if should_open:
        logger.warning(
            f"[LLM chain] circuit OPEN for {name} for {cooldown}s "
            f"(permanent={permanent}): {message[:200]}"
        )


def reset_provider_health() -> None:
    with _lock:
        _health.clear()


def run_llm_chain(task: str, attempts: Sequence[tuple]) -> ChainOutcome:
    """Run ``attempts`` in order, skipping circuit-broken providers.

    Each attempt is a ``(provider, model, thunk)`` tuple; the thunk
    should raise on failure and return the raw result on success
    (including "successful" empty results such as NO_REPLY). Returns a
    ChainOutcome; raises LLMChainError only when every attempt fails or
    is skipped.
    """
    errors: list[str] = []
    for idx, (provider, model, call) in enumerate(attempts):
        if not provider_available(provider):
            remaining = max(0, int(_entry(provider)["open_until"] - time.monotonic()))
            logger.info(
                f"[LLM chain] {task}: skipping {provider} "
                f"(circuit open, {remaining}s left)"
            )
            continue
        try:
            result = call()
        except Exception as exc:
            record_failure(provider, exc)
            errors.append(f"{provider}: {type(exc).__name__}: {str(exc)[:300]}")
            logger.warning(
                f"[LLM chain] {task}: {provider}/{model} failed, trying next provider: "
                f"{type(exc).__name__}: {str(exc)[:200]}"
            )
            fallback = attempts[idx + 1][0] if idx + 1 < len(attempts) else None
            _notify_failure(provider, model, task, exc, fallback)
            continue
        record_success(provider)
        logger.info(f"[LLM chain] {task}: succeeded via {provider}")
        return ChainOutcome(result, provider)
    raise LLMChainError(task, errors)
