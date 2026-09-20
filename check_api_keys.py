"""API key health check. Verifies keys via free metadata endpoints only — no generation calls, zero cost."""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
GEMINI_ENRICHMENT_MODEL = os.getenv("GEMINI_ENRICHMENT_MODEL", "gemini-2.5-flash")
OPENAI_ENRICHMENT_MODEL = os.getenv("OPENAI_ENRICHMENT_MODEL", "gpt-5-mini")

TIMEOUT = 15


def mask(key: str) -> str:
    if not key:
        return "MISSING"
    return f"{key[:6]}...{key[-4:]}" if len(key) > 10 else "***"


def check_gemini(key: str) -> tuple[bool, str]:
    resp = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": key, "pageSize": 1000},
        timeout=TIMEOUT,
    )
    if resp.status_code == 200:
        names = {m.get("name", "").removeprefix("models/") for m in resp.json().get("models", [])}
        model_status = (
            "ok" if GEMINI_ENRICHMENT_MODEL in names else f"NOT FOUND (configured: {GEMINI_ENRICHMENT_MODEL})"
        )
        return True, f"valid, enrichment model {model_status}, {len(names)} models available"
    detail = ""
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except Exception:
        pass
    return False, f"HTTP {resp.status_code} {detail}".strip()


def check_openai(key: str) -> tuple[bool, str]:
    resp = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=TIMEOUT,
    )
    if resp.status_code == 200:
        ids = {m.get("id") for m in resp.json().get("data", [])}
        model_status = (
            "ok" if OPENAI_ENRICHMENT_MODEL in ids else f"NOT FOUND (configured: {OPENAI_ENRICHMENT_MODEL})"
        )
        return True, f"valid, enrichment model {model_status}, {len(ids)} models available"
    detail = ""
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except Exception:
        pass
    return False, f"HTTP {resp.status_code} {detail}".strip()


def check_firecrawl(key: str) -> tuple[bool, str]:
    for url in (
        "https://api.firecrawl.dev/v2/team/credit-usage",
        "https://api.firecrawl.dev/v1/team/credit-usage",
    ):
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            plan_credits = data.get("planCredits", "?")
            remaining = data.get("remainingCredits", data.get("creditsRemaining", "?"))
            return True, f"valid, plan credits={plan_credits}, remaining={remaining}"
        if resp.status_code != 404:
            break
    return False, f"HTTP {resp.status_code} {resp.text[:200]}".strip()


CHECKS = [
    ("GEMINI", GEMINI_API_KEY, check_gemini),
    ("OPENAI", OPENAI_API_KEY, check_openai),
    ("FIRECRAWL", FIRECRAWL_API_KEY, check_firecrawl),
]


def main() -> int:
    print("API Key Health Check")
    print("=" * 60)
    failures = 0
    for name, key, checker in CHECKS:
        print(f"\n[{name}] key={mask(key) if key else 'MISSING'}")
        if not key:
            print("  FAIL: not set in environment")
            failures += 1
            continue
        try:
            ok, msg = checker(key)
        except requests.RequestException as e:
            ok, msg = False, f"network error: {e}"
        print(f"  {'PASS' if ok else 'FAIL'}: {msg}")
        failures += 0 if ok else 1
    print("\n" + "=" * 60)
    total = len(CHECKS)
    print(f"Result: {total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
