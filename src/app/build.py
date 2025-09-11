from __future__ import annotations

import datetime as dt
import logging
import os
from typing import List, Dict, Any
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .models import DailyCache, BuildRun, User
from .clients import wikimedia
from .clients.llm import (
    llm_selection_or_fallback,
    llm_attribution_or_fallback,
    llm_summarize_one_or_fallback,
)
from .clients.yoto import upsert_content, upload_audio_and_get_transcode
from .clients.elevenlabs import synthesize_text as el_synthesize
from .utils.tokens import ensure_yoto_access_token
from .config import settings
from .utils.audio_store import path_for_mp3

logger = logging.getLogger("today_in_history")


async def ensure_daily_cache(session: AsyncSession, date: dt.date, language: str, age_bucket: str | None = None) -> DailyCache:
    q = await session.execute(
        select(DailyCache).where(
            DailyCache.date == date,
            DailyCache.language == language,
            DailyCache.age_bucket == age_bucket,
        )
    )
    dc = q.scalars().first()
    if not dc:
        dc = DailyCache(date=date, language=language, age_bucket=age_bucket)
        session.add(dc)
        await session.commit()
        await session.refresh(dc)
    return dc


async def build_for_user(session: AsyncSession, user: User, date: dt.date, *, reset: bool = False) -> Dict:
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
        dc = await ensure_daily_cache(session, date, user.preferred_language, user.age_bucket)
        if reset:
            # Start fresh for today's audio refs
            dc.audio_refs_json = []
            await session.commit()
        dc.feed_hash = feed_hash
        await session.commit()

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
        await session.commit()

        # 4. Initialize summaries accumulator and persist incrementally
        summaries_acc: List[Dict[str, Any]] = []
        dc.summaries_json = {"date": date.isoformat(), "language": user.preferred_language, "summaries": summaries_acc}
        await session.commit()

        # 5. Attribution
        attrib_obj = llm_attribution_or_fallback(date=date.isoformat(), language=user.preferred_language)
        attrib = attrib_obj.get("attribution", "Sources for today")
        dc.attribution_script = attrib
        await session.commit()

        # 6–7. Generate audio, upload to Yoto for transcoding, store SHA refs incrementally
        def _intro_text(d: dt.date, lang: str) -> str:
            import calendar

            weekday = calendar.day_name[d.weekday()]
            month = calendar.month_name[d.month]
            return (
                f"Welcome to the Today in History playlist! Today is {weekday}, {month} {d.day}. "
                "Let's talk about a couple of cool things that have happened on this date throughout history. Enjoy!"
            )

        # Reuse intro/outro across caches for the same date
        async def _find_shared_track(date_: dt.date, lang_: str, title_: str) -> Dict | None:
            q = await session.execute(
                select(DailyCache).where(DailyCache.date == date_, DailyCache.language == lang_)
            )
            for row in q.scalars().all():
                refs = row.audio_refs_json if isinstance(row.audio_refs_json, list) else []
                for t in refs:
                    if t.get("title") == title_ and t.get("sha256"):
                        return t
            return None

        def _ensure_audio_refs(dc_: DailyCache) -> List[Dict[str, Any]]:
            if isinstance(dc_.audio_refs_json, list):
                return dc_.audio_refs_json  # type: ignore[return-value]
            elif dc_.audio_refs_json:
                return [dc_.audio_refs_json]  # type: ignore[list-item]
            return []

        audio_refs: List[Dict[str, Any]] = _ensure_audio_refs(dc)

        # Concurrency controls
        max_concurrent = int(os.getenv("PIPELINE_CONCURRENCY", "3"))
        sem = asyncio.Semaphore(max_concurrent)
        db_lock = asyncio.Lock()

        # Intro (static per day)
        intro_title = f"Welcome for {date.strftime('%B %d')}"
        intro_track = await _find_shared_track(date, user.preferred_language, intro_title)
        if not intro_track:
            if settings.offline_mode:
                intro_track = {
                    "key": "01",
                    "title": intro_title,
                    "sha256": f"offline-intro-{date.isoformat()}",
                    "duration": 10,
                    "fileSize": 1024,
                    "channels": 2,
                    "format": "mp3",
                    "type": "audio",
                }
            else:
                if not settings.elevenlabs_api_key:
                    raise RuntimeError("ELEVENLABS_API_KEY is required for audio generation")
                text = _intro_text(date, user.preferred_language)
                save_path, _ = path_for_mp3(date, intro_title, 1, age_bucket=user.age_bucket, language=user.preferred_language)
                await el_synthesize(text, save_path=save_path)
                if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
                    raise RuntimeError("Failed to synthesize intro audio")
                with open(save_path, "rb") as f:
                    audio_bytes = f.read()
                trans = await upload_audio_and_get_transcode(user.yoto_access_token or "", audio_bytes, content_type="audio/mpeg", filename=os.path.basename(save_path))
                info = trans.get("transcodedInfo") or {}
                intro_track = {
                    "key": "01",
                    "title": intro_title,
                    "sha256": trans.get("transcodedSha256"),
                    "duration": info.get("duration"),
                    "fileSize": info.get("fileSize"),
                    "channels": info.get("channels"),
                    "format": info.get("format"),
                    "type": "audio",
                }
        if not audio_refs or audio_refs[0].get("title") != intro_title or not audio_refs[0].get("sha256"):
            if audio_refs and audio_refs[0].get("title") == intro_title:
                audio_refs[0] = intro_track  # type: ignore[index]
            else:
                audio_refs = [intro_track] + [t for t in audio_refs if t.get("title") != intro_title]
            dc.audio_refs_json = audio_refs
            await session.commit()

        # Stories pipeline: summarize -> tts -> transcode per item concurrently
        async def process_story(idx: int, item: Dict[str, Any]) -> None:
            key = f"{idx:02d}"
            # Summarize (potentially blocking); run in thread
            summary: Dict[str, Any] = await asyncio.to_thread(
                llm_summarize_one_or_fallback,
                item,
                date=date.isoformat(),
                language=user.preferred_language,
                age_min=user.age_min,
                age_max=user.age_max,
            )
            # Persist summary incrementally
            async with db_lock:
                summaries_acc.append(summary)
                # Keep deterministic order by re-sorting on insertion order by key when present
                dc.summaries_json = {"date": date.isoformat(), "language": user.preferred_language, "summaries": summaries_acc}
                await session.commit()

            # Skip TTS/upload if we already have this track with a SHA
            async with db_lock:
                existing = next((t for t in audio_refs if t.get("key") == key and t.get("sha256")), None)
            if existing:
                return

            title = summary.get("title", "Story")
            script = (summary.get("script") or "").strip()
            if settings.offline_mode:
                new_track = {
                    "key": key,
                    "title": title,
                    "sha256": f"offline-story-{idx}-{date.isoformat()}",
                    "duration": summary.get("reading_time_s") or 30,
                    "fileSize": 10000,
                    "channels": 2,
                    "format": "mp3",
                    "type": "audio",
                }
            else:
                if not settings.elevenlabs_api_key:
                    raise RuntimeError("ELEVENLABS_API_KEY is required for audio generation")
                save_path, _ = path_for_mp3(date, title, idx, age_bucket=user.age_bucket, language=user.preferred_language)
                await el_synthesize(script, save_path=save_path)
                if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
                    raise RuntimeError("Failed to synthesize story audio")
                with open(save_path, "rb") as f:
                    audio_bytes = f.read()
                trans = await upload_audio_and_get_transcode(user.yoto_access_token or "", audio_bytes, content_type="audio/mpeg", filename=os.path.basename(save_path))
                info = trans.get("transcodedInfo") or {}
                new_track = {
                    "key": key,
                    "title": title,
                    "sha256": trans.get("transcodedSha256"),
                    "duration": info.get("duration"),
                    "fileSize": info.get("fileSize"),
                    "channels": info.get("channels"),
                    "format": info.get("format"),
                    "type": "audio",
                }
            # Persist new track
            async with db_lock:
                # Replace any placeholder for this key
                audio_refs[:] = [t for t in audio_refs if t.get("key") != key]
                audio_refs.append(new_track)
                # Keep tracks ordered by key
                def _key_ord(t: Dict[str, Any]) -> int:
                    try:
                        return int((t.get("key") or "00"))
                    except Exception:
                        return 0
                audio_refs.sort(key=_key_ord)
                dc.audio_refs_json = audio_refs
                await session.commit()

        async def with_sem(coro):
            async with sem:
                return await coro

        story_tasks = [
            asyncio.create_task(with_sem(process_story(idx, item)))
            for idx, item in enumerate(selection, start=2)
        ]
        await asyncio.gather(*story_tasks)

        # Outro (static per day)
        outro_title = "Sources for today"
        outro_track = await _find_shared_track(date, user.preferred_language, outro_title)
        if not outro_track:
            if settings.offline_mode:
                outro_track = {
                    "key": f"{len(audio_refs)+1:02d}",
                    "title": outro_title,
                    "sha256": f"offline-outro-{date.isoformat()}",
                    "duration": 12,
                    "fileSize": 2000,
                    "channels": 2,
                    "format": "mp3",
                    "type": "audio",
                }
            else:
                text = "Thanks for listening! " + attrib
                if not settings.elevenlabs_api_key:
                    raise RuntimeError("ELEVENLABS_API_KEY is required for audio generation")
                save_path, _ = path_for_mp3(date, outro_title, len(audio_refs)+1, age_bucket=user.age_bucket, language=user.preferred_language)
                await el_synthesize(text, save_path=save_path)
                if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
                    raise RuntimeError("Failed to synthesize outro audio")
                with open(save_path, "rb") as f:
                    audio_bytes = f.read()
                trans = await upload_audio_and_get_transcode(user.yoto_access_token or "", audio_bytes, content_type="audio/mpeg", filename=os.path.basename(save_path))
                info = trans.get("transcodedInfo") or {}
                outro_track = {
                    "key": f"{len(audio_refs)+1:02d}",
                    "title": outro_title,
                    "sha256": trans.get("transcodedSha256"),
                    "duration": info.get("duration"),
                    "fileSize": info.get("fileSize"),
                    "channels": info.get("channels"),
                    "format": info.get("format"),
                    "type": "audio",
                }
        if not audio_refs or audio_refs[-1].get("title") != outro_title or not audio_refs[-1].get("sha256"):
            audio_refs = [t for t in audio_refs if t.get("title") != outro_title]
            audio_refs.append(outro_track)
            dc.audio_refs_json = audio_refs
            await session.commit()

        # Build tracks for today's chapter from SHAs
        tracks: List[Dict] = []
        for t in audio_refs:
            tracks.append({
                "key": t.get("key"),
                "title": t.get("title"),
                "trackUrl": f"yoto:#{t.get('sha256')}",
                "duration": t.get("duration"),
                "fileSize": t.get("fileSize"),
                "channels": t.get("channels"),
                "format": t.get("format"),
                "type": "audio",
                "overlayLabel": t.get("overlayLabel") or t.get("key"),
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
            refs = cache.audio_refs_json if isinstance(cache.audio_refs_json, list) else []
            for a in refs:
                sha = a.get("sha256")
                title = a.get("title")
                if not sha or not title:
                    continue
                trs.append({
                    "key": a.get("key"),
                    "title": title,
                    "trackUrl": f"yoto:#{sha}",
                    "duration": a.get("duration"),
                    "fileSize": a.get("fileSize"),
                    "channels": a.get("channels"),
                    "format": a.get("format"),
                    "type": "audio",
                    "overlayLabel": a.get("overlayLabel") or a.get("key"),
                    "display": {"icon16x16": settings.yoto_icon_16x16},
                })
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
                select(DailyCache).where(
                    DailyCache.date == d,
                    DailyCache.language == user.preferred_language,
                    DailyCache.age_bucket == user.age_bucket,
                )
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
        if result.get("cardId") and result.get("cardId") != user.card_id:
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
