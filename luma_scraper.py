#!/usr/bin/env python3
"""Scrape Luma event start date from a public event URL."""

import json
import sys
from typing import Optional
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def extract_event_slug(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def find_start_at(obj, depth=0) -> Optional[str]:
    if depth > 15:
        return None
    if isinstance(obj, dict):
        for key in ("start_at", "startDate", "start_time", "startTime"):
            if key in obj and isinstance(obj[key], str) and obj[key]:
                return obj[key]
        for v in obj.values():
            result = find_start_at(v, depth + 1)
            if result:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = find_start_at(item, depth + 1)
            if result:
                return result
    return None


SGT = timezone(timedelta(hours=8))

def format_datetime(raw: str) -> str:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(SGT)
        return dt.strftime("%A, %d %B %Y at %I:%M %p SGT")
    except Exception:
        return raw


def get_event_start_dt(url: str) -> Optional[datetime]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag or not tag.string:
            return None
        data = json.loads(tag.string)
        raw = find_start_at(data)
        if not raw:
            return None
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(SGT)
    except Exception:
        return None


def scrape_luma_event(url: str) -> None:
    print(f"Fetching {url} ...")
    resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        print("Could not find __NEXT_DATA__ in page.")
        return

    data = json.loads(tag.string)
    start_at = find_start_at(data)
    if start_at:
        print(f"Start date: {format_datetime(start_at)}")
        print(f"Raw:        {start_at}")
    else:
        print("start_at not found in __NEXT_DATA__.")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://luma.com/ycg6hw61"
    scrape_luma_event(url)
