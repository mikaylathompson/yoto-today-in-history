from __future__ import annotations

from typing import Dict
import httpx

from ..config import settings


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
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    payload = {"language": language, "segments": [{"title": title, "text": text}]}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        # Assumption: API returns array of segments with trackUrl; support common shapes
        track_url = (
            data.get("segments", [{}])[0].get("trackUrl")
            or data.get("trackUrl")
            or data.get("tracks", [{}])[0].get("trackUrl")
        )
        if not track_url:
            raise RuntimeError("Yoto Labs response missing trackUrl")
        return {"title": title, "trackUrl": track_url, "format": "mp3", "type": "stream"}
