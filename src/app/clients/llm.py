from __future__ import annotations

from typing import List, Dict

import logging
from ..config import settings
from .openai_client import (
    select_with_llm as _select_with_llm,
    summarize_with_llm as _summarize_with_llm,
    attribution_with_llm as _attribution_with_llm,
)

logger = logging.getLogger("today_in_history")

_BANNED_KEYWORDS = {"gore", "torture", "suicide", "massacre", "sexual"}


def safe_filter(items: List[dict]) -> List[dict]:
    out = []
    for it in items:
        # Exclude deaths
        kind = (it.get('kind') or '').lower()
        if kind in {"death", "deaths"}:
            continue
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


def _format_year(year):
    try:
        y = int(year)
    except Exception:
        return None
    if y < 0:
        return f"{abs(y)} BCE"
    return str(y)


def summarize_item(it: dict, target_min_words: int = 120) -> Dict:
    # Improved offline fallback: use the feed summary if available; clean and shape for kids.
    import re

    title = it.get("title") or "Untitled"
    yr = _format_year(it.get("year"))
    hook = f"Let's talk about {title}" + (f" ({yr})" if yr else "") + ". "
    body = (it.get("summary") or "").strip()
    # Strip URLs and excessive whitespace
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"\s+", " ", body)
    if not body:
        body = "This story from history is often remembered because it changed how people lived and learned."
    closing = " This reminds us to stay curious, be kind, and try new ideas."
    script = (hook + body).strip()
    # Trim or extend to a reasonable length
    words = script.split()
    if len(words) < target_min_words:
        script = script + " " + " ".join(["It", "was", "important", "and", "people", "still", "learn", "from", "it", "today."] * 5)
    # Final clean
    script = (script + closing).strip()
    reading_time_s = (len(script.split()) + 2) // 3
    return {"id": it.get("id"), "title": title, "script": script, "reading_time_s": reading_time_s}


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
        except Exception as e:
            logger.warning("LLM selection fallback: error from OpenAI: %s", str(e)[:300])
    else:
        reason = []
        if settings.offline_mode:
            reason.append("offline_mode=true")
        if not settings.openai_api_key:
            reason.append("OPENAI_API_KEY missing")
        logger.warning("LLM selection fallback: %s", ", ".join(reason) or "unknown reason")
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
        except Exception as e:
            logger.warning("LLM summaries fallback: error from OpenAI: %s", str(e)[:300])
    else:
        reason = []
        if settings.offline_mode:
            reason.append("offline_mode=true")
        if not settings.openai_api_key:
            reason.append("OPENAI_API_KEY missing")
        logger.warning("LLM summaries fallback: %s", ", ".join(reason) or "unknown reason")
    # Fallback one-by-one
    summaries = [summarize_item(it) for it in selected]
    return {"date": date, "language": language, "summaries": summaries}


def llm_attribution_or_fallback(*, date: str, language: str) -> dict:
    if not settings.offline_mode and settings.openai_api_key:
        try:
            return _attribution_with_llm(date=date, language=language)
        except Exception as e:
            logger.warning("LLM attribution fallback: error from OpenAI: %s", str(e)[:300])
    else:
        reason = []
        if settings.offline_mode:
            reason.append("offline_mode=true")
        if not settings.openai_api_key:
            reason.append("OPENAI_API_KEY missing")
        logger.warning("LLM attribution fallback: %s", ", ".join(reason) or "unknown reason")
    return {"date": date, "language": language, "attribution": attribution_script(language)}
