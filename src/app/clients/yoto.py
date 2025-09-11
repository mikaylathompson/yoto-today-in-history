from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import logging
import asyncio
import httpx
from ..config import settings

logger = logging.getLogger("today_in_history")


async def request_upload_url(access_token: str) -> Tuple[str, str]:
    """Request a temporary URL and uploadId for audio upload."""
    base = settings.yoto_content_base.rstrip("/")
    url = f"{base}/media/transcode/audio/uploadUrl"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    logger.info("Yoto: requesting upload URL")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
    upload = data.get("upload") or {}
    upload_url = upload.get("uploadUrl")
    upload_id = upload.get("uploadId")
    if not upload_url or not upload_id:
        raise RuntimeError("Failed to get Yoto upload URL")
    logger.info("Yoto: received upload URL (uploadId=%s)", upload_id)
    return upload_url, upload_id


async def put_audio_to_upload_url(upload_url: str, audio_bytes: bytes, content_type: str, filename: str) -> None:
    """PUT the audio bytes to the signed upload URL."""
    headers = {
        "Content-Type": content_type,
    }
    # httpx requires content=bytes for PUT to raw URL
    size = len(audio_bytes or b"")
    logger.info("Yoto: uploading audio to signed URL filename=%s size=%d content_type=%s", filename, size, content_type)
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.put(upload_url, content=audio_bytes, headers=headers)
        logger.info("Yoto: upload complete status=%s", r.status_code)
        r.raise_for_status()


async def poll_transcoded(access_token: str, upload_id: str, *, max_attempts: int = 30, delay_ms: int = 500) -> Dict:
    """Poll until the uploaded audio has been transcoded, returning the transcode object."""
    base = settings.yoto_content_base.rstrip("/")
    url = f"{base}/media/upload/{upload_id}/transcoded?loudnorm=false"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    logger.info("Yoto: polling for transcode (uploadId=%s)", upload_id)
    async with httpx.AsyncClient(timeout=20) as client:
        for attempt in range(max_attempts):
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json() or {}
                transcode = data.get("transcode") or {}
                if transcode.get("transcodedSha256"):
                    info = transcode.get("transcodedInfo") or {}
                    logger.info(
                        "Yoto: transcode ready uploadId=%s sha=%s duration=%s fileSize=%s",
                        upload_id,
                        transcode.get("transcodedSha256"),
                        info.get("duration"),
                        info.get("fileSize"),
                    )
                    return transcode
            if attempt % 5 == 0:
                logger.debug("Yoto: transcode polling attempt=%d/%d (uploadId=%s)", attempt + 1, max_attempts, upload_id)
            await asyncio.sleep(delay_ms / 1000.0)
    raise TimeoutError("Yoto transcoding timed out")


async def upload_audio_and_get_transcode(access_token: str, audio_bytes: bytes, *, content_type: str = "audio/mpeg", filename: str = "audio.mp3") -> Dict:
    """Convenience: request URL, upload audio, poll for transcode; return the transcode object."""
    logger.info("Yoto: upload+transcode start filename=%s size=%d", filename, len(audio_bytes or b""))
    upload_url, upload_id = await request_upload_url(access_token)
    await put_audio_to_upload_url(upload_url, audio_bytes, content_type, filename)
    transcode = await poll_transcoded(access_token, upload_id)
    logger.info("Yoto: upload+transcode complete sha=%s", transcode.get("transcodedSha256"))
    return transcode


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
    """Create or update the Yoto content card with the provided chapters.

    Note: We always post to the main API per the documented flow. The body references
    transcoded audio via `yoto:#<sha>` trackUrl values.
    """
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
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {
        **({"cardId": card_id} if card_id else {}),
        "title": "Today in History",
        "content": {"chapters": chapters},
    }
    async with httpx.AsyncClient(timeout=90) as client:
        last_err = None
        for attempt in range(1, 6):
            logger.info("Yoto: upserting content chapters=%d attempt=%d card=%s", len(chapters), attempt, bool(card_id))
            r = await client.post(url, json=body, headers=headers)
            if r.status_code >= 500:
                last_err = r
                logger.error("Yoto content upsert %s: %s (attempt %s)", r.status_code, r.text[:500], attempt)
                await asyncio.sleep(0.8 * attempt)
                continue
            r.raise_for_status()
            out = r.json()
            logger.info("Yoto: upsert success status=%s cardId=%s", r.status_code, (out.get("cardId") if isinstance(out, dict) else None))
            return out
        assert last_err is not None
        last_err.raise_for_status()
