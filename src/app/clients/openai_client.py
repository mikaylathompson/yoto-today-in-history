from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import validate as jsonschema_validate
from openai import OpenAI

from ..config import settings


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _format_prompt(tpl: str, **kwargs) -> str:
    return tpl.replace("{{age_min}}", str(kwargs.get("age_min", ""))).replace(
        "{{age_max}}", str(kwargs.get("age_max", ""))
    ).replace("{{language}}", kwargs.get("language", "")).replace("{{date}}", kwargs.get("date", ""))


def _client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key)


def _extract_text_from_responses(resp) -> str:
    # Responses API helpers
    try:
        return resp.output_text  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        parts = resp.output[0].content  # type: ignore[attr-defined]
        for p in parts:
            if p.get("type") == "output_text":
                return p.get("text", "")
    except Exception:
        pass
    return ""


def _extract_text_from_chat(resp) -> str:
    try:
        return resp.choices[0].message.content  # type: ignore[attr-defined]
    except Exception:
        return ""


def _call_json(client: OpenAI, messages: list[dict], schema: dict | None, temperature: float, model: str) -> str:
    # Prefer Responses API if available in this SDK; otherwise fall back to Chat Completions
    if hasattr(client, "responses"):
        resp = client.responses.create(
            model=model,
            input=messages,
            response_format=(
                {"type": "json_schema", "json_schema": {"name": "payload", "schema": schema, "strict": True}}
                if schema
                else {"type": "json_object"}
            ),
            temperature=temperature,
        )
        return _extract_text_from_responses(resp)
    # Chat Completions fallback
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    return _extract_text_from_chat(resp)


def select_with_llm(feed_items: List[dict], *, date: str, language: str, age_min: int, age_max: int) -> dict:
    tpl = _load_text(PROMPTS_DIR / "selection_prompt.txt")
    schema = _load_json(PROMPTS_DIR / "selection_json_schema.json")
    prompt = _format_prompt(tpl, date=date, language=language, age_min=age_min, age_max=age_max)
    client = _client()
    messages = [
        {"role": "system", "content": "You are a helpful assistant that outputs strict JSON only."},
        {
            "role": "user",
            "content": f"{prompt}\n\nFEED_ITEMS JSON:\n" + json.dumps({"feed_items": feed_items}),
        },
    ]
    text = _call_json(client, messages, schema=schema, temperature=0.4, model="gpt-4o-mini")
    data = json.loads(text)
    jsonschema_validate(data, schema)
    return data


def _strip_urls(text: str) -> str:
    import re

    return re.sub(r"https?://\S+", "", text)


def summarize_with_llm(selected: List[dict], *, date: str, language: str, age_min: int, age_max: int) -> dict:
    tpl = _load_text(PROMPTS_DIR / "summarization_prompt.txt")
    schema = _load_json(PROMPTS_DIR / "summarization_json_schema.json")
    prompt = _format_prompt(tpl, date=date, language=language, age_min=age_min, age_max=age_max)
    client = _client()
    messages = [
        {"role": "system", "content": "You are a helpful assistant that outputs strict JSON only."},
        {"role": "user", "content": f"{prompt}\n\nSELECTED JSON:\n" + json.dumps({"selected": selected})},
    ]
    text = _call_json(client, messages, schema=schema, temperature=0.5, model="gpt-4o-mini")
    data = json.loads(text)
    jsonschema_validate(data, schema)
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
    client = _client()
    messages = [
        {"role": "system", "content": "You are a helpful assistant that outputs strict JSON only."},
        {"role": "user", "content": prompt},
    ]
    text = _call_json(client, messages, schema=schema, temperature=0.2, model="gpt-4o-mini")
    data = json.loads(text)
    jsonschema_validate(data, schema)
    return data
