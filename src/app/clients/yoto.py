from __future__ import annotations

from typing import Dict, List, Optional
import httpx
from ..config import settings


async def upsert_content(
    access_token: str,
    card_id: Optional[str],
    language: str,
    age_min: int,
    age_max: int,
    chapters: List[Dict],
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

    base = settings.yoto_content_base.rstrip("/")
    url = f"{base}/content"
    headers = {"Authorization": f"Bearer {access_token}"}
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
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=body, headers=headers)
        r.raise_for_status()
        return r.json()
