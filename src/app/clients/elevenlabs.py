from __future__ import annotations

import logging
import random
from typing import Optional
import asyncio
from elevenlabs import ElevenLabs

from ..config import settings

logger = logging.getLogger("today_in_history")


def synthesize_text_blocking(
    text: str,
    save_path: str,
    *,
    voice_id: Optional[str] = None,
) -> None:
    """Blocking TTS synthesis to file via ElevenLabs SDK."""
    if not settings.elevenlabs_api_key:
        logger.warning("EL: API key missing; cannot synthesize directly")
        return
    vid = voice_id or random.choice(settings.elevenlabs_voice_ids)
    client = ElevenLabs(api_key=settings.elevenlabs_api_key)
    try:
        response_iter = client.text_to_speech.convert(
            voice_id=vid,
            output_format="mp3_44100_128",
            text=text,
            model_id="eleven_turbo_v2_5",
            optimize_streaming_latency=1,
        )
    except Exception as e:
        logger.error("EL: TTS error: %s", e)
        return
    try:
        with open(save_path, "wb") as f:
            for chunk in response_iter:
                if not chunk:
                    continue
                f.write(chunk)
    except Exception as e:
        logger.error("EL: failed to read audio stream: %s", e)
        return
    logger.info("EL: saved audio to %s", save_path)


async def synthesize_text(
    text: str,
    save_path: str,
    *,
    voice_id: Optional[str] = None,
) -> None:
    """Async wrapper that runs blocking synthesis in a thread."""
    await asyncio.to_thread(synthesize_text_blocking, text, save_path, voice_id=voice_id)
