from __future__ import annotations

import datetime as dt
import logging
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

logger = logging.getLogger("today_in_history")


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
    logger.info("Build start user=%s date=%s lang=%s", user.id, date, user.preferred_language)
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
        logger.info("Fetched feed: %s items (filtered)", len(normalized))
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
        logger.info("Selection chosen: %s items", len(selection))
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
        logger.info("Summaries generated: %s items", len(summaries))
        dc.summaries_json = summaries_obj

        # 5. Attribution
        attrib_obj = llm_attribution_or_fallback(date=date.isoformat(), language=user.preferred_language)
        attrib = attrib_obj.get("attribution", "Sources for today")
        dc.attribution_script = attrib

        # 6–7. Build chapter with intro + stories + attribution using ElevenLabs inline text
        tracks: List[Dict] = []
        # Intro track first
        def _intro_text(d: dt.date, lang: str, count: int) -> str:
            import calendar

            weekday = calendar.day_name[d.weekday()]
            month = calendar.month_name[d.month]
            # Day of year
            start = dt.date(d.year, 1, 1)
            doy = (d - start).days + 1
            days_in_year = 366 if calendar.isleap(d.year) else 365
            remaining = days_in_year - doy
            return (
                f"It's {weekday}, {month} {d.day}. Today is day {doy} of {d.year} "
                f"({remaining} days remaining). Welcome to Today in History!"
            )

        intro_text = _intro_text(date, user.preferred_language, len(summaries))
        tracks.append({
            "key": "01",
            "type": "elevenlabs",
            "format": "mp3",
            "title": f"Welcome for {date.strftime('%B %d')}",
            "trackUrl": f"text:{intro_text}",
            "display": {"icon16x16": settings.yoto_icon_16x16},
        })

        # Story tracks
        for idx, s in enumerate(summaries, start=2):
            script = s.get("script", "").strip()
            tracks.append({
                "key": f"{idx:02d}",
                "type": "elevenlabs",
                "format": "mp3",
                "title": s.get("title", "Story"),
                "trackUrl": f"text:{script}",
                "display": {"icon16x16": settings.yoto_icon_16x16},
            })
        # Attribution final track
        tracks.append({
            "key": f"{len(tracks)+1:02d}",
            "type": "elevenlabs",
            "format": "mp3",
            "title": "Sources for today",
            "trackUrl": f"text:{attrib}",
            "display": {"icon16x16": settings.yoto_icon_16x16},
        })

        chapter_today = {
            "key": date.isoformat(),
            "title": date.strftime("%B %d"),
            "tracks": tracks,
            "overlayLabel": str(date.day),
            "display": {"icon16x16": settings.yoto_icon_16x16},
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
            return {
                "key": cache.date.isoformat(),
                "title": cache.date.strftime("%B %d"),
                "tracks": trs,
                "overlayLabel": str(cache.date.day),
                "display": {"icon16x16": settings.yoto_icon_16x16},
            }

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
            use_labs=True,
        )
        if not user.card_id and result.get("cardId"):
            user.card_id = result["cardId"]
            await session.commit()

        build.status = "success"
        await session.commit()
        logger.info("Build success user=%s date=%s card=%s chapters=%s", user.id, date, user.card_id, len(chapters))
        return {"build_id": str(build.id), "status": build.status, "chapters": chapters}
    except Exception as e:  # noqa: BLE001
        build.status = "failed"
        build.error = str(e)
        await session.commit()
        logger.exception("Build failed user=%s date=%s: %s", user.id, date, e)
        raise
