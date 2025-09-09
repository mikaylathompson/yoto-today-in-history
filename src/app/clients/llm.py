from __future__ import annotations

from typing import List, Dict

from ..config import settings

_BANNED_KEYWORDS = {"gore", "torture", "suicide", "massacre", "sexual"}


def safe_filter(items: List[dict]) -> List[dict]:
    out = []
    for it in items:
        text = f"{it.get('title','')} {it.get('summary','')}".lower()
        if any(b in text for b in _BANNED_KEYWORDS):
            continue
        out.append(it)
    return out


def select_items(items: List[dict], min_count: int = 5, max_count: int = 10) -> List[dict]:
    # Offline deterministic selection: first N after safety filter; aim for diversity by year spacing.
    filtered = safe_filter(items)
    filtered.sort(key=lambda x: (x.get("year") or 0, x.get("title") or ""))
    step = max(1, len(filtered) // max_count) if filtered else 1
    selection = filtered[::step][:max_count]
    if len(selection) < min_count:
        selection = filtered[:min_count]
    return selection


def summarize_item(it: dict, target_min_words: int = 100) -> Dict:
    # Offline: simple template; real impl would call OpenAI.
    title = it.get("title") or "Untitled"
    year = it.get("year") or ""
    base = f"{title} ({year}). This is a kid-friendly summary adapted from Wikipedia. "
    # Pad to ~100 words
    words = base.split()
    while len(words) < target_min_words:
        words += ["It", "shares", "what", "happened", "and", "why", "it", "matters", "today."]
    text = " ".join(words[: max(target_min_words, 100)])
    reading_time_s = (len(text.split()) + 2) // 3  # ~3 wps
    return {
        "id": it.get("id"),
        "title": title,
        "script": text,
        "reading_time_s": reading_time_s,
    }


def attribution_script(language: str) -> str:
    return (
        "Thanks for listening! Today’s stories were adapted from Wikipedia "
        "(CC BY-SA). Music and narration powered by Yoto and ElevenLabs."
    )
