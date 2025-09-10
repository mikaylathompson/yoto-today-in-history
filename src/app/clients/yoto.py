from __future__ import annotations

from typing import Dict, List, Optional
import json
import logging
import httpx
from ..config import settings

logger = logging.getLogger("today_in_history")


async def upsert_content(
    access_token: str,
    card_id: Optional[str],
    language: str,
    age_min: int,
    age_max: int,
    chapters: List[Dict],
    *,
    use_labs: bool = False,
) -> Dict:
    """Create or update the Yoto content card with the provided chapters."""
    if settings.offline_mode:
        return {
            "cardId": card_id or "mock-card-1234",
            "languages": [language],
            "age_min": age_min,
            "age_max": age_max,
            "chapters": chapters,
        }

    base = (settings.yoto_labs_base if use_labs else settings.yoto_content_base).rstrip("/")
    url = f"{base}/content"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "Accept": "application/json"}
    body = {
        **({"cardId": card_id} if card_id else {}),
        "title": "Today in History",
        "metadata": {
            "description": "Kid-friendly history stories updated daily. Sources: Wikipedia (CC BY-SA). Summaries adapted.",
            "languages": [language],
            "minAge": age_min,
            "maxAge": age_max,
        },
        "content": {"chapters": chapters},
    }
    # Log payload (truncated) for Labs debugging
    try:
        preview = body.copy()
        chs = preview.get("content", {}).get("chapters", [])
        for ch in chs:
            for t in ch.get("tracks", [])
:
                if isinstance(t.get("trackUrl"), str) and t["trackUrl"].startswith("text:"):
                    txt = t["trackUrl"][5:]
                    t["trackUrl"] = "text:" + (txt[:120] + ("…" if len(txt) > 120 else ""))
        logger.info("Upserting via %s with %s chapters; sample=%s", "labs" if use_labs else "api", len(chs), json.dumps(preview)[:500])
    except Exception:
        pass
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=body, headers=headers)
        r.raise_for_status()
        return r.json()
