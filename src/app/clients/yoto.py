from __future__ import annotations

from typing import Dict, List, Optional
import json
import logging
import asyncio
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
            "description": "Kid-friendly history stories updated daily. Sources: Wikipedia (CC BY-SA). Summaries adapted."
            # These aren't included in the example, so I'm stripping them out for now.
            # "languages": [language],
            # "minAge": age_min,
            # "maxAge": age_max,
        },
        "content": {"chapters": chapters},
    }
    logger.info(f"Sending to: {url}")
    logger.info(f"With headers: {headers}")
    logger.info("Full body:")
    print(body) # Keep this print statement
    async with httpx.AsyncClient(timeout=90) as client:
        last_err = None
        for attempt in range(1, 6):
            logger.info(f"POST attempt {attempt}")
            r = await client.post(url, json=body, headers=headers)
            logger.info("Full response:")
            print(r)
            if r.status_code >= 500:
                last_err = r
                logger.error("Labs upsert %s: %s (attempt %s)", r.status_code, r.text[:500], attempt)
                await asyncio.sleep(0.8 * attempt)
                continue
            r.raise_for_status()
            return r.json()
        assert last_err is not None
        last_err.raise_for_status()
