from __future__ import annotations

import json
from pathlib import Path
from typing import List
import logging

from jsonschema import validate as jsonschema_validate
from openai import OpenAI
import openai as _openai_module

from ..config import settings

logger = logging.getLogger("today_in_history")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_prompt(tpl: str, **kwargs) -> str:
    return (
        tpl.replace("{{age_min}}", str(kwargs.get("age_min", "")))
        .replace("{{age_max}}", str(kwargs.get("age_max", "")))
        .replace("{{language}}", kwargs.get("language", ""))
        .replace("{{date}}", kwargs.get("date", ""))
    )


def _client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


def _ensure_responses_available(client: OpenAI) -> None:
    # Enforce using the Responses API rather than silently falling back
    if not hasattr(client, "responses"):
        version = getattr(_openai_module, "__version__", "unknown")
        raise RuntimeError(
            f"OpenAI SDK does not expose 'responses' API (version={version}). Ensure openai>=1.42 is installed and the container was rebuilt."
        )


def select_with_llm(feed_items: List[dict], *, date: str, language: str, age_min: int, age_max: int) -> dict:
    tpl = _load_text(PROMPTS_DIR / "selection_prompt.txt")
    schema = _load_json(PROMPTS_DIR / "selection_json_schema.json")
    prompt = _format_prompt(tpl, date=date, language=language, age_min=age_min, age_max=age_max)
    # Embed schema and explicitly forbid echoing input keys
    schema_text = json.dumps(schema)
    full_prompt = (
        f"{prompt}\n\nJSON_SCHEMA (strict, no extra keys):\n{schema_text}\n\n"
        "Output rules:\n- Output a single JSON object matching the schema.\n"
        "- Do NOT include 'feed_items' or any keys not present in the schema.\n"
        "- No commentary or markdown.\n\n"
        f"Context FEED_ITEMS (read-only):\n{json.dumps({'feed_items': feed_items})}\n"
    )
    resp_client = _client()
    _ensure_responses_available(resp_client)
    resp = resp_client.responses.create(
        model="gpt-4o-mini",
        input=[{"role": "user", "content": [{"type": "input_text", "text": full_prompt}]}],
        temperature=0.4,
    )
    text = getattr(resp, "output_text", "")
    try:
        data = json.loads(text)
        jsonschema_validate(data, schema)
        return data
    except Exception as e:
        logger.warning("Selection JSON validation failed: %s; raw=%.500s", e, text)
        raise


def _strip_urls(text: str) -> str:
    import re

    return re.sub(r"https?://\S+", "", text)


def summarize_with_llm(selected: List[dict], *, date: str, language: str, age_min: int, age_max: int) -> dict:
    tpl = _load_text(PROMPTS_DIR / "summarization_prompt.txt")
    schema = _load_json(PROMPTS_DIR / "summarization_json_schema.json")
    prompt = _format_prompt(tpl, date=date, language=language, age_min=age_min, age_max=age_max)
    schema_text = json.dumps(schema)
    full_prompt = (
        f"{prompt}\n\nJSON_SCHEMA (strict, no extra keys):\n{schema_text}\n\n"
        "Output rules:\n- Output a single JSON object matching the schema.\n- No extra properties. No markdown.\n\n"
        f"SELECTED JSON (read-only):\n{json.dumps({'selected': selected})}\n"
    )
    resp_client = _client()
    _ensure_responses_available(resp_client)
    resp = resp_client.responses.create(
        model="gpt-4o-mini",
        input=[{"role": "user", "content": [{"type": "input_text", "text": full_prompt}]}],
        temperature=0.5,
    )
    text = getattr(resp, "output_text", "")
    try:
        data = json.loads(text)
        jsonschema_validate(data, schema)
    except Exception as e:
        logger.warning("Summaries JSON validation failed: %s; raw=%.500s", e, text)
        raise
    # Clean URLs and compute reading_time_s if missing
    out_items = []
    for it in data.get("summaries", []):
        script = _strip_urls(it.get("script", "")).strip()
        if not it.get("reading_time_s"):
            words = len(script.split())
            it["reading_time_s"] = (words + 2) // 3
        it["script"] = script
        out_items.append(it)
    data["summaries"] = out_items
    return data


def attribution_with_llm(*, date: str, language: str) -> dict:
    tpl = _load_text(PROMPTS_DIR / "attribution_prompt.txt")
    schema = _load_json(PROMPTS_DIR / "attribution_json_schema.json")
    prompt = _format_prompt(tpl, date=date, language=language, age_min=0, age_max=0)
    schema_text = json.dumps(schema)
    full_prompt = (
        f"{prompt}\n\nJSON_SCHEMA (strict, no extra keys):\n{schema_text}\n\n"
        "Output rules:\n- Output a single JSON object matching the schema.\n- No extra properties. No markdown.\n"
    )
    resp_client = _client()
    _ensure_responses_available(resp_client)
    resp = resp_client.responses.create(
        model="gpt-4o-mini",
        input=[{"role": "user", "content": [{"type": "input_text", "text": full_prompt}]}],
        temperature=0.2,
    )
    text = getattr(resp, "output_text", "")
    try:
        data = json.loads(text)
        jsonschema_validate(data, schema)
        return data
    except Exception as e:
        logger.warning("Attribution JSON validation failed: %s; raw=%.500s", e, text)
        raise
