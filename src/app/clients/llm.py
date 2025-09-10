from __future__ import annotations

from typing import List, Dict

from ..config import settings
from .openai_client import (
    select_with_llm as _select_with_llm,
    summarize_with_llm as _summarize_with_llm,
    attribution_with_llm as _attribution_with_llm,
)

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


# High-level LLM wrappers (prefer OpenAI when available and not offline)
def llm_selection_or_fallback(feed_items: List[dict], *, date: str, language: str, age_min: int, age_max: int) -> dict:
    if not settings.offline_mode and settings.openai_api_key:
        try:
            return _select_with_llm(feed_items, date=date, language=language, age_min=age_min, age_max=age_max)
        except Exception:
            pass
    # Fallback
    sel = select_items(feed_items)
    return {
        "date": date,
        "language": language,
        "age_band": {"min": age_min, "max": age_max},
        "selected": sel,
    }


def llm_summaries_or_fallback(selected: List[dict], *, date: str, language: str, age_min: int, age_max: int) -> dict:
    if not settings.offline_mode and settings.openai_api_key:
        try:
            return _summarize_with_llm(selected, date=date, language=language, age_min=age_min, age_max=age_max)
        except Exception:
            pass
    # Fallback one-by-one
    summaries = [summarize_item(it) for it in selected]
    return {"date": date, "language": language, "summaries": summaries}


def llm_attribution_or_fallback(*, date: str, language: str) -> dict:
    if not settings.offline_mode and settings.openai_api_key:
        try:
            return _attribution_with_llm(date=date, language=language)
        except Exception:
            pass
    return {"date": date, "language": language, "attribution": attribution_script(language)}
