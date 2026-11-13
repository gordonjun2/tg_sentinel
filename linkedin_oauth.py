#!/usr/bin/env python3
"""One-shot LinkedIn OAuth for the company-page forwarder."""

import argparse
import logging
import os
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

from linkedin.client import LinkedInClient
from linkedin.tokens import bundle_from_oauth_response, save_tokens

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(_ROOT, "data", "linkedin_tokens.json")
AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
SCOPES = "r_organization_social rw_organization_admin"


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": SCOPES,
        }
    )
    return f"{AUTH_URL}?{query}"


def extract_code(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("http"):
        qs = parse_qs(urlparse(raw).query)
        code = (qs.get("code") or [None])[0]
        if not code:
            raise ValueError("Redirect URL missing code=")
        return code
    return raw


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str, session) -> dict:
    resp = session.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-orgs", action="store_true", help="after auth, list administered orgs")
    args = parser.parse_args()

    client_id = os.getenv("LINKEDIN_CLIENT_ID") or ""
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET") or ""
    redirect_uri = (
        os.getenv("LINKEDIN_REDIRECT_URI")
        or "https://www.linkedin.com/developers/tools/oauth/redirect"
    )
    if not client_id or not client_secret:
        logger.error("Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in .env")
        return 1

    state = "tg-sentinel-linkedin"
    url = build_authorize_url(client_id, redirect_uri, state)
    print("\n1) Open this URL as a LinkedIn Page admin:\n")
    print(url)
    print("\n2) Paste the full redirect URL (or just the code) here:\n")
    raw = input("> ").strip()
    code = extract_code(raw)

    session = requests.Session()
    payload = exchange_code(client_id, client_secret, redirect_uri, code, session)
    bundle = bundle_from_oauth_response(payload, now=time.time())
    if not bundle.refresh_token:
        logger.warning(
            "No refresh_token in response — LinkedIn may not have issued "
            "programmatic refresh for this app yet; you will need to re-auth when access expires"
        )
    os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)
    save_tokens(TOKEN_FILE, bundle)
    logger.info("Wrote %s", TOKEN_FILE)

    if args.list_orgs:
        li = LinkedInClient(session, bundle.access_token)
        try:
            orgs = li.list_administered_orgs()
        except Exception as exc:
            logger.error("Could not list orgs (CMA pending?): %s", exc)
            return 0
        print("\nAdministered orgs / ACLs:")
        for row in orgs:
            print(row)
        print("\nSet LINKEDIN_ORGANIZATION_ID to the numeric id from urn:li:organization:{id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
