#!/usr/bin/env python3
"""LinkedIn company-page → Telegram forwarder cron job."""

import argparse
import fcntl
import logging
import os
import sys
import time

import requests

from linkedin.client import LinkedInClient
from linkedin.forward import process_posts
from linkedin.state import load_state
from linkedin.tokens import ensure_access_token

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_ROOT, "data", "linkedin_forwarder_state.json")
TOKEN_FILE = os.path.join(_ROOT, "data", "linkedin_tokens.json")
LOCK_FILE = os.path.join(_ROOT, "data", "linkedin_forwarder.lock")
REFRESH_SKEW = 300.0

_REQUIRED_ENV = (
    "LINKEDIN_CLIENT_ID",
    "LINKEDIN_CLIENT_SECRET",
    "LINKEDIN_ORGANIZATION_ID",
)


def linkedin_env_ready() -> bool:
    """True when all required LinkedIn .env keys are non-empty."""
    for key in _REQUIRED_ENV:
        if not (os.getenv(key) or "").strip():
            return False
    return True


def run(args) -> int:
    # Imported here so missing LinkedIn .env keys can early-exit before config loads.
    from config import BOT_TOKEN, TARGET_GROUP_ID

    client_id = (os.getenv("LINKEDIN_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("LINKEDIN_CLIENT_SECRET") or "").strip()
    org_id = (os.getenv("LINKEDIN_ORGANIZATION_ID") or "").strip()
    if not os.path.exists(TOKEN_FILE):
        logger.error("Missing %s — run linkedin_oauth.py first", TOKEN_FILE)
        return 1

    session = requests.Session()
    try:
        access = ensure_access_token(
            TOKEN_FILE,
            client_id,
            client_secret,
            session,
            now=time.time(),
            skew_seconds=REFRESH_SKEW,
        )
    except Exception as exc:
        logger.error("Token ensure failed: %s", exc)
        return 1

    li = LinkedInClient(session, access)
    try:
        posts = li.list_organization_posts(org_id, count=50)
    except Exception as exc:
        logger.error("List posts failed: %s", exc)
        return 1

    state = load_state(STATE_FILE)
    if args.seed_only:
        state["seeded"] = False
        state["seen"] = {}

    handled = process_posts(
        state=state,
        posts=posts,
        li_client=li,
        bot_token=BOT_TOKEN,
        chat_id=TARGET_GROUP_ID,
        session=session,
        state_path=STATE_FILE,
        dry_run=args.dry_run,
    )
    logger.info("Run complete; handled=%d dry_run=%s", handled, args.dry_run)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LinkedIn → Telegram forwarder")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()

    if not linkedin_env_ready():
        missing = [k for k in _REQUIRED_ENV if not (os.getenv(k) or "").strip()]
        logger.info(
            "LinkedIn forwarder skipped: missing .env key(s) %s",
            ", ".join(missing),
        )
        return 0

    os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.info("Another linkedin_forwarder run is active; exiting")
        return 0
    try:
        return run(args)
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
