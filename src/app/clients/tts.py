from __future__ import annotations

from typing import Dict
import httpx
import logging

from ..config import settings

logger = logging.getLogger("today_in_history")


def _truncate(text: str, max_chars: int = 900) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rsplit(" ", 1)[0] + "…"


async def synthesize_track(title: str, text: str, language: str, access_token: str | None) -> Dict:
    """
    Calls Yoto×ElevenLabs via Yoto Labs API to create hosted audio for a single segment.
    Assumes Bearer token from Yoto OAuth works for this endpoint; no ElevenLabs key needed.
    """
    if settings.offline_mode:
        return {
            "title": title,
            "trackUrl": f"https://example.invalid/audio/{hash(title + text) % 10_000_000}.mp3",
            "format": "mp3",
            "type": "stream",
        }

    base = settings.yoto_labs_base.rstrip("/")
    url = f"{base}/content"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    } if access_token else {"Accept": "application/json", "Content-Type": "application/json"}
    # Yoto Labs expects a YotoJSON-like envelope
    clipped = _truncate(text, 900)
    payload = {
        "metadata": {
            "language": language,
        },
        "content": {
            "segments": [
                {
                    "title": title,
                    "text": clipped,
                }
            ]
        },
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload, headers=headers)
        if r.status_code >= 500:
            logger.error("Yoto Labs TTS error %s: %s", r.status_code, r.text[:500])
            r.raise_for_status()
        if r.status_code >= 400:
            logger.error("Yoto Labs TTS error %s: %s", r.status_code, r.text[:500])
            r.raise_for_status()
        data = r.json()
        # Expect trackUrl in content.segments[0].trackUrl, but support common fallbacks
        track_url = (
            data.get("content", {}).get("segments", [{}])[0].get("trackUrl")
            or data.get("segments", [{}])[0].get("trackUrl")
            or data.get("trackUrl")
            or data.get("tracks", [{}])[0].get("trackUrl")
        )
        if not track_url:
            logger.error("Yoto Labs response missing trackUrl: %s", data)
            raise RuntimeError("Yoto Labs response missing trackUrl")
        return {"title": title, "trackUrl": track_url, "format": "mp3", "type": "stream"}
