from __future__ import annotations

import datetime as dt
from typing import List, Dict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .models import DailyCache, BuildRun, User
from .clients import wikimedia
from .clients.llm import (
    llm_selection_or_fallback,
    llm_summaries_or_fallback,
    llm_attribution_or_fallback,
)
from .clients.tts import synthesize_track
from .clients.yoto import upsert_content
from .utils.tokens import ensure_yoto_access_token


async def ensure_daily_cache(session: AsyncSession, date: dt.date, language: str) -> DailyCache:
    q = await session.execute(
        select(DailyCache).where(DailyCache.date == date, DailyCache.language == language)
    )
    dc = q.scalars().first()
    if not dc:
        dc = DailyCache(date=date, language=language)
        session.add(dc)
        await session.commit()
        await session.refresh(dc)
    return dc


async def build_for_user(session: AsyncSession, user: User, date: dt.date) -> Dict:
    build = BuildRun(user_id=user.id, date=date, status="running")
    session.add(build)
    await session.commit()
    await session.refresh(build)

    try:
        # Ensure token
        await ensure_yoto_access_token(session, user)

        # 1. Fetch feed
        feed = await wikimedia.fetch_on_this_day(user.preferred_language, date)
        normalized = wikimedia.normalize_feed(feed)
        feed_hash = wikimedia.feed_hash(feed)

        # 2. Cache
        dc = await ensure_daily_cache(session, date, user.preferred_language)
        dc.feed_hash = feed_hash

        # 3. Selection (LLM preferred)
        selection_obj = llm_selection_or_fallback(
            normalized,
            date=date.isoformat(),
            language=user.preferred_language,
            age_min=user.age_min,
            age_max=user.age_max,
        )
        selection = selection_obj.get("selected", [])
        dc.selection_json = selection_obj

        # 4. Summaries
        summaries_obj = llm_summaries_or_fallback(
            selection,
            date=date.isoformat(),
            language=user.preferred_language,
            age_min=user.age_min,
            age_max=user.age_max,
        )
        summaries = summaries_obj.get("summaries", [])
        dc.summaries_json = summaries_obj

        # 5. Attribution
        attrib_obj = llm_attribution_or_fallback(date=date.isoformat(), language=user.preferred_language)
        attrib = attrib_obj.get("attribution", "Sources for today")
        dc.attribution_script = attrib

        # 6. TTS
        audio_refs = []
        for s in summaries:
            audio = await synthesize_track(
                s.get("title", "Story"), s["script"], user.preferred_language, user.yoto_access_token
            )
            audio_refs.append({"id": s["id"], "title": s["title"], "track_url": audio["trackUrl"]})
        # Attribution track
        attrib_audio = await synthesize_track(
            "Sources for today", attrib, user.preferred_language, user.yoto_access_token
        )
        audio_refs.append({"id": "attribution", "title": "Sources for today", "track_url": attrib_audio["trackUrl"]})
        dc.audio_refs_json = audio_refs
        await session.commit()

        # 7. Assemble chapter
        tracks: List[Dict] = []
        for i, a in enumerate(audio_refs[:-1], start=1):
            tracks.append(
                {
                    "key": f"{i:02d}",
                    "type": "stream",
                    "format": "mp3",
                    "title": a["title"],
                    "trackUrl": a["track_url"],
                }
            )
        # Attribution as final track 10/11
        tracks.append(
            {
                "key": f"{len(tracks)+1:02d}",
                "type": "stream",
                "format": "mp3",
                "title": "Sources for today",
                "trackUrl": audio_refs[-1]["track_url"],
            }
        )

        chapter_today = {
            "key": date.isoformat(),
            "title": date.strftime("%B %d"),
            "tracks": tracks,
        }

        # Helper to build a chapter from cached audio refs
        def chapter_from_cache(cache: DailyCache) -> Dict:
            trs: List[Dict] = []
            if not cache.audio_refs_json:
                return {"key": cache.date.isoformat(), "title": cache.date.strftime("%B %d"), "tracks": trs}
            refs = cache.audio_refs_json
            for i, a in enumerate(refs[:-1], start=1):
                trs.append({"key": f"{i:02d}", "type": "stream", "format": "mp3", "title": a["title"], "trackUrl": a["track_url"]})
            trs.append({"key": f"{len(trs)+1:02d}", "type": "stream", "format": "mp3", "title": "Sources for today", "trackUrl": refs[-1]["track_url"]})
            return {"key": cache.date.isoformat(), "title": cache.date.strftime("%B %d"), "tracks": trs}

        # 8–9. Maintain 7-day window and upsert content
        # Collect today + previous up to 6 days if available
        chapters = [chapter_today]
        for back in range(1, 7):
            d = date - dt.timedelta(days=back)
            qdc = await session.execute(
                select(DailyCache).where(DailyCache.date == d, DailyCache.language == user.preferred_language)
            )
            prev = qdc.scalars().first()
            if prev and prev.audio_refs_json:
                chapters.append(chapter_from_cache(prev))
        # Keep newest first
        chapters.sort(key=lambda c: c["key"], reverse=True)
        chapters = chapters[:7]
        result = await upsert_content(
            user.yoto_access_token or "",
            user.card_id,
            user.preferred_language,
            user.age_min,
            user.age_max,
            chapters,
        )
        if not user.card_id and result.get("cardId"):
            user.card_id = result["cardId"]
            await session.commit()

        build.status = "success"
        await session.commit()
        return {"build_id": str(build.id), "status": build.status, "chapters": chapters}
    except Exception as e:  # noqa: BLE001
        build.status = "failed"
        build.error = str(e)
        await session.commit()
        raise
