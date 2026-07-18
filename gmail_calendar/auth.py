"""OAuth bootstrap + Gmail/Calendar service factory.

Run once locally to produce ``gmail_cal_token.json``::

    python -m gmail_calendar.auth

The flow opens a browser on localhost; after authorization the token file is
written next to ``credentials.json``. SCP it to the VPS.

``prompt="consent"`` forces Google to return a refresh token on every grant —
without it Google omits the refresh token for repeat grants and headless VPS
refresh fails (this is the silent footgun in the existing Drive uploader).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import (
    GMAIL_CALENDAR_CREDENTIALS_FILE,
    GMAIL_CALENDAR_TOKEN_FILE,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _load_token() -> Credentials | None:
    try:
        return Credentials.from_authorized_user_file(
            GMAIL_CALENDAR_TOKEN_FILE, SCOPES
        )
    except FileNotFoundError:
        return None
    except ValueError as exc:
        logger.warning("Token file unreadable, will re-auth: %s", exc)
        return None


def _save_token(creds: Credentials) -> None:
    with open(GMAIL_CALENDAR_TOKEN_FILE, "w") as fh:
        fh.write(creds.to_json())
    logger.info("Saved token to %s", GMAIL_CALENDAR_TOKEN_FILE)


def _run_local_flow() -> Credentials:
    """Open a browser on localhost and exchange the auth code for tokens."""
    flow = InstalledAppFlow.from_client_secrets_file(
        GMAIL_CALENDAR_CREDENTIALS_FILE, SCOPES
    )
    return flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )


def load_credentials() -> Credentials:
    """Return valid user credentials, refreshing or re-authorizing as needed.

    On a headless VPS the refresh path is the only one that should ever run;
    the interactive re-auth branch will raise if no browser is available.
    """
    creds = _load_token()
    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception as exc:
            logger.warning("Refresh failed (%s); need fresh authorization", exc)

    logger.info("No valid token — starting local OAuth flow.")
    creds = _run_local_flow()
    _save_token(creds)
    return creds


@lru_cache(maxsize=1)
def get_gmail_service() -> Any:
    return build("gmail", "v1", credentials=load_credentials(), cache_discovery=False)


@lru_cache(maxsize=1)
def get_calendar_service() -> Any:
    return build(
        "calendar", "v3", credentials=load_credentials(), cache_discovery=False
    )


def _bootstrap_cli() -> int:
    """Entry point for ``python -m gmail_calendar.auth``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    creds = load_credentials()
    # Sanity-check both APIs are reachable.
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = gmail.users().getProfile(userId="me").execute()
    print(f"[ok] Gmail authorized for: {profile.get('emailAddress')}")
    cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
    cals = cal.calendarList().list().execute()
    summaries = [c.get("summary") or c.get("id") for c in cals.get("items", [])]
    print(f"[ok] Calendar authorized — visible calendars: {summaries}")
    print(f"[ok] Token written to: {GMAIL_CALENDAR_TOKEN_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_bootstrap_cli())
