from __future__ import annotations

import hashlib
import datetime as dt
from typing import Any, Dict, List
import httpx

from ..config import settings


def normalize_item(raw: dict) -> dict:
    return {
        "id": raw.get("pageid") or raw.get("id") or hashlib.sha1(str(raw).encode()).hexdigest(),
        "kind": raw.get("type") or raw.get("kind") or "event",
        "title": raw.get("text") or raw.get("title") or "Untitled",
        "year": raw.get("year") or None,
        "summary": raw.get("extract") or raw.get("summary") or "",
        "page_url": raw.get("content_urls", {}).get("desktop", {}).get("page") or raw.get("pages", [{}])[0].get("content_urls", {}).get("desktop", {}).get("page"),
    }


def normalize_feed(feed: dict) -> List[dict]:
    items: List[dict] = []
    for key in ["events", "births", "deaths", "holidays"]:
        for raw in feed.get(key, []) or []:
            items.append(normalize_item(raw))
    return items


async def fetch_on_this_day(lang: str, date: dt.date) -> dict:
    if settings.offline_mode:
        # Minimal offline sample
        return {
            "events": [
                {"id": "e1", "type": "event", "title": "Sample Event", "year": 1969, "extract": "The moon landing.", "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Apollo_11"}}},
                {"id": "e2", "type": "event", "title": "Another Event", "year": 1990, "extract": "A kid-friendly milestone.", "content_urls": {"desktop": {"page": "https://example.com"}}},
            ]
        }
    mm = f"{date.month:02d}"
    dd = f"{date.day:02d}"
    url = f"https://api.wikimedia.org/feed/v1/wikipedia/{lang}/onthisday/all/{mm}/{dd}"
    headers = {"Api-User-Agent": settings.api_user_agent}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()


def feed_hash(feed: dict) -> str:
    normalized = normalize_feed(feed)
    m = hashlib.sha256()
    for item in normalized:
        m.update((item.get("id") or "").encode())
        m.update((item.get("title") or "").encode())
        m.update((item.get("summary") or "").encode())
    return m.hexdigest()
