from __future__ import annotations

import datetime as dt
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Form
from fastapi import BackgroundTasks
import logging
import time
import httpx
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from .utils.audio_store import ensure_audio_dir

from .config import settings
from .db import get_session, Base, engine, SessionLocal
from uuid import UUID
from .models import User, BuildRun
from .models import DailyCache
from .schemas import MeResponse, StatusItem
from .security import get_current_user
from .build import build_for_user, ensure_daily_cache
from .utils.pkce import generate_verifier, challenge_from_verifier
from .clients.yoto_auth import build_authorize_url, exchange_code_for_token
from .utils.urls import is_valid_absolute_url
from .clients import wikimedia as wm_client
from .clients.openai_client import (
    select_with_llm as oi_select,
    summarize_with_llm as oi_summarize,
    attribution_with_llm as oi_attribution,
)

app = FastAPI(title="Today in History API")
logger = logging.getLogger("today_in_history")
# Harden session cookies in production
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=(settings.env == "production"),
    same_site="lax",
)
templates = Jinja2Templates(directory="templates")
ensure_audio_dir()
app.mount("/audio", StaticFiles(directory=settings.audio_dir, html=False), name="audio")


@app.on_event("startup")
async def on_startup() -> None:
    # Configure logging for our app namespace to ensure INFO logs are visible under uvicorn
    try:
        level = getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO)
    except Exception:
        level = logging.INFO
    logger.setLevel(level)
    # Attach a stream handler if none present
    if not logger.handlers:
        import sys

        h = logging.StreamHandler(sys.stdout)
        h.setLevel(level)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        h.setFormatter(fmt)
        logger.addHandler(h)
        logger.propagate = False

    logger.info(
        "Startup: env=%s offline_mode=%s openai_key=%s openai_version=%s",
        settings.env,
        settings.offline_mode,
        "set" if settings.openai_api_key else "missing",
        __import__('openai').__version__ if settings.openai_api_key else 'n/a',
    )

    # Create tables if not exist (for SQLite demo). In production use Alembic.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Optimize SQLite to reduce locks
        try:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
            await conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
            await conn.exec_driver_sql("PRAGMA temp_store=MEMORY;")
        except Exception:
            pass


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session)):
    # If logged in, load user for template context
    user = None
    uid = request.session.get("user_id") if hasattr(request, "session") else None
    if uid:
        from uuid import UUID

        try:
            user = await session.get(User, UUID(uid))
        except Exception:
            user = None
    installed = request.query_params.get("installed") == "1"
    return templates.TemplateResponse("index.html", {"request": request, "user": user, "installed": installed})


# Client-side OAuth test page was temporary and has been removed.


@app.get("/install")
async def install(request: Request):
    if settings.offline_mode:
        # Dev shortcut: simulate OAuth success
        return RedirectResponse(url=f"{settings.app_base_url}/oauth/callback?code=demo&state=demo")
    verifier = generate_verifier()
    challenge = challenge_from_verifier(verifier)
    state = generate_verifier(24)
    request.session["pkce_verifier"] = verifier
    request.session["oauth_state"] = state
    request.session["pkce_challenge"] = challenge
    # Prefer configured redirect if valid; otherwise derive from request
    redirect_uri = (
        settings.yoto_redirect_uri
        if is_valid_absolute_url(settings.yoto_redirect_uri)
        else str(request.url_for("oauth_callback"))
    )
    url = build_authorize_url(
        settings.yoto_client_id or "",
        redirect_uri,
        state,
        challenge,
    )
    # Persist values we must reuse on callback
    request.session["redirect_uri"] = redirect_uri
    return RedirectResponse(url=url)


@app.get("/oauth/callback")
async def oauth_callback(request: Request, code: str, state: str, session: AsyncSession = Depends(get_session)):
    if settings.offline_mode:
        # Create or use a demo user
        q = await session.execute(select(User).limit(1))
        user = q.scalars().first()
        if not user:
            user = User()
            session.add(user)
            await session.commit()
            await session.refresh(user)
        request.session["user_id"] = str(user.id)
        return RedirectResponse("/?installed=1")

    if state != request.session.get("oauth_state"):
        raise HTTPException(status_code=400, detail="Invalid state")
    verifier = request.session.get("pkce_verifier")
    if not verifier:
        raise HTTPException(status_code=400, detail="Missing PKCE verifier")
    # Attempt token exchange; capture detailed error if present
    redirect_uri = request.session.get("redirect_uri") or (
        settings.yoto_redirect_uri if is_valid_absolute_url(settings.yoto_redirect_uri) else str(request.url_for("oauth_callback"))
    )
    try:
        # Optional PKCE sanity check (silent)
        _stored_challenge = request.session.get("pkce_challenge")
        _recomputed = challenge_from_verifier(verifier)
        # Proceed without verbose logging
        tok = await exchange_code_for_token(code, verifier, redirect_uri)
    except httpx.HTTPStatusError as e:
        logger.exception("Token exchange failed: %s %s", getattr(e.response, 'status_code', '?'), getattr(e.response, 'text', '')[:500])
        return RedirectResponse(url="/?oauth_error=token_exchange_failed", status_code=303)
    except Exception:
        logger.exception("Token exchange failed: unexpected error")
        return RedirectResponse(url="/?oauth_error=token_exchange_failed", status_code=303)
    # Persist or update user with tokens
    q = await session.execute(select(User).limit(1))
    user = q.scalars().first()
    is_new = False
    if not user:
        user = User()
        session.add(user)
        is_new = True
    user.yoto_access_token = tok.get("access_token")
    user.yoto_refresh_token = tok.get("refresh_token")
    expires_in = tok.get("expires_in") or 3600
    from datetime import datetime, timezone, timedelta

    user.yoto_token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    await session.commit()
    await session.refresh(user)
    # Clear PKCE verifier from session
    if "pkce_verifier" in request.session:
        request.session.pop("pkce_verifier", None)
    if "oauth_state" in request.session:
        request.session.pop("oauth_state", None)
    request.session["user_id"] = str(user.id)
    if is_new:
        logger.info("<EVENT> [NEW USER] user_id=%s lang=%s tz=%s", str(user.id), user.preferred_language, user.timezone)
        return RedirectResponse("/settings?first=1")
    return RedirectResponse("/?installed=1")


@app.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)):
    return MeResponse(
        preferred_language=user.preferred_language,
        timezone=user.timezone,
        age_min=user.age_min,
        age_max=user.age_max,
        card_id=user.card_id,
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: User = Depends(get_current_user)):
    saved = request.query_params.get("saved") == "1"
    return templates.TemplateResponse("settings.html", {"request": request, "user": user, "saved": saved})


@app.post("/settings")
async def update_settings(
    preferred_language: str = Form(...),
    timezone: str = Form(...),
    age_bucket: str = Form(default="5-8"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    user.preferred_language = preferred_language
    user.timezone = timezone
    # Update age bucket and keep min/max for compatibility
    if age_bucket not in ("2-4", "5-8", "9-12"):
        age_bucket = "5-8"
    user.age_bucket = age_bucket
    if age_bucket == "2-4":
        user.age_min, user.age_max = 2, 4
    elif age_bucket == "9-12":
        user.age_min, user.age_max = 9, 12
    else:
        user.age_min, user.age_max = 5, 8
    await session.commit()
    return RedirectResponse(url="/settings?saved=1", status_code=303)


async def _run_build_background(user_id: str, target_date: dt.date):
    async with SessionLocal() as s:  # type: AsyncSession
        try:
            uid = UUID(user_id)
        except Exception:
            logger.error("Background build: invalid user id %s", user_id)
            return
        u = await s.get(User, uid)
        if not u:
            logger.error("Background build: user %s not found", user_id)
            return
        try:
            await build_for_user(s, u, target_date)
        except Exception as e:  # noqa: BLE001
            logger.exception("Background build failed user=%s date=%s: %s", user_id, target_date, e)


@app.get("/rebuild")
async def rebuild_get(
    background: BackgroundTasks,
    date: dt.date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    Kick off a rebuild asynchronously and redirect to a progress page.
    """
    target_date = date or dt.datetime.now(dt.timezone.utc).date()
    background.add_task(_run_build_background, str(user.id), target_date)
    return RedirectResponse(url=f"/rebuilding?date={target_date.isoformat()}", status_code=303)


@app.get("/build_status")
async def build_status(
    date: dt.date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    target_date = date or dt.datetime.now(dt.timezone.utc).date()
    q = await session.execute(
        select(BuildRun).where(BuildRun.user_id == user.id, BuildRun.date == target_date).order_by(desc(BuildRun.created_at)).limit(1)
    )
    b = q.scalars().first()
    if not b:
        return {"status": "none"}
    return {"status": b.status, "created_at": b.created_at.isoformat(), "id": str(b.id)}


@app.post("/rebuild")
async def rebuild(
    date: dt.date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    date = date or dt.datetime.now(dt.timezone.utc).date()
    result = await build_for_user(session, user, date)
    return result


@app.get("/status", response_model=list[StatusItem])
async def status(session: AsyncSession = Depends(get_session)):
    q = await session.execute(select(BuildRun).order_by(BuildRun.created_at.desc()).limit(25))
    items = []
    for b in q.scalars().all():
        items.append(
            StatusItem(
                id=str(b.id), user_id=str(b.user_id), date=b.date, status=b.status, error=b.error
            )
        )
    return items


if settings.env == "debug":
    @app.get("/llm-test")
    async def llm_test(
        date: str | None = Query(default=None, description="YYYY-MM-DD"),
        language: str = Query(default="en"),
        age_min: int = Query(default=5),
        age_max: int = Query(default=10),
    ):
        """
        Run the LLM pipeline (selection, summaries, attribution) without Yoto auth or TTS.
        Enabled only when ENV=debug. Requires OPENAI_API_KEY and OFFLINE_MODE=false for real results.
        """
        t0 = time.perf_counter()
        # Parse date
        if date:
            try:
                d = dt.date.fromisoformat(date)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format; expected YYYY-MM-DD")
        else:
            d = dt.datetime.now(dt.timezone.utc).date()
        logger.info("LLM-TEST start date=%s lang=%s ages=%s-%s", d, language, age_min, age_max)
        # Fetch and normalize feed
        try:
            feed = await wm_client.fetch_on_this_day(language, d)
            items = wm_client.normalize_feed(feed)
        except Exception as e:
            logger.exception("LLM-TEST fetch/normalize error: %s", e)
            raise HTTPException(status_code=502, detail=f"fetch error: {e}")
        if not items:
            raise HTTPException(status_code=502, detail="Empty feed from Wikimedia")
        logger.info("LLM-TEST feed items=%s", len(items))

        # Run LLM stages directly (no fallbacks)
        try:
            logger.info("LLM-TEST selection start")
            sel_obj = oi_select(items, date=d.isoformat(), language=language, age_min=age_min, age_max=age_max)
        except Exception as e:
            logger.exception("LLM-TEST selection error: %s", e)
            raise HTTPException(status_code=500, detail=f"selection error: {e}")
        selected = sel_obj.get("selected", [])
        logger.info("LLM-TEST selection ok items=%s", len(selected))
        try:
            logger.info("LLM-TEST summaries start items=%s", len(selected))
            sum_obj = oi_summarize(selected, date=d.isoformat(), language=language, age_min=age_min, age_max=age_max)
        except Exception as e:
            logger.exception("LLM-TEST summaries error: %s", e)
            raise HTTPException(status_code=500, detail=f"summaries error: {e}")
        try:
            logger.info("LLM-TEST attribution start")
            att_obj = oi_attribution(date=d.isoformat(), language=language)
        except Exception as e:
            logger.exception("LLM-TEST attribution error: %s", e)
            raise HTTPException(status_code=500, detail=f"attribution error: {e}")

        elapsed = time.perf_counter() - t0
        logger.info(
            "LLM-TEST done date=%s sel=%s summaries=%s attr_len=%s elapsed=%.2fs",
            d,
            len(selected),
            len(sum_obj.get("summaries", []) if isinstance(sum_obj, dict) else []),
            len(att_obj.get("attribution", "")) if isinstance(att_obj, dict) else 0,
            elapsed,
        )

        # Persist into DailyCache for this date/language
        try:
            async with SessionLocal() as s2:  # type: ignore[name-defined]
                dc = await ensure_daily_cache(s2, d, language)
                dc.feed_hash = wm_client.feed_hash(feed)
                dc.selection_json = sel_obj
                dc.summaries_json = sum_obj
                dc.attribution_script = att_obj.get("attribution")
                await s2.commit()
        except Exception as e:
            logger.warning("LLM-TEST cache persist failed: %s", e)

        return {
            "date": d.isoformat(),
            "language": language,
            "selection": sel_obj,
            "summaries": sum_obj,
            "attribution": att_obj,
        }

    @app.get("/tts-test", response_class=HTMLResponse)
    async def tts_test(
        request: Request,
        date: str | None = Query(default=None),
        language: str | None = Query(default=None),
        session: AsyncSession = Depends(get_session),
    ):
        """
        Generate TTS for the latest summaries via ElevenLabs and play them in-browser.
        Collates tracks into a simple playlist (sequential playback).
        Requires ENV=debug and ELEVENLABS_API_KEY.
        """
        from .clients.elevenlabs import synthesize_text as el_synthesize

        # Resolve target date and language from latest cache if not given
        target_date: dt.date | None = None
        if date:
            try:
                target_date = dt.date.fromisoformat(date)
            except ValueError:
                target_date = None
        lang = language or "en"

        dc: DailyCache | None = None
        if target_date:
            q = await session.execute(
                select(DailyCache).where(DailyCache.date == target_date, DailyCache.language == lang)
            )
            dc = q.scalars().first()
        else:
            q = await session.execute(
                select(DailyCache).where(DailyCache.language == lang).order_by(desc(DailyCache.date)).limit(1)
            )
            dc = q.scalars().first()
        logger.info("TTS-TEST start date_param=%s lang_param=%s cache_found=%s", date, language, bool(dc))
        summaries: list[dict] | None = None
        used_date = None
        used_lang = lang
        if dc:
            summaries_obj = dc.summaries_json or {}
            summaries = summaries_obj.get("summaries") if isinstance(summaries_obj, dict) else summaries_obj
            used_date = dc.date.isoformat()
            used_lang = dc.language
        else:
            # On-the-fly generation without DB/user auth
            try:
                feed = await wm_client.fetch_on_this_day(lang, target_date or dt.datetime.now(dt.timezone.utc).date())
                items = wm_client.normalize_feed(feed)
                from .clients.llm import llm_selection_or_fallback, llm_summaries_or_fallback

                sel_obj = llm_selection_or_fallback(items, date=(target_date or dt.datetime.now(dt.timezone.utc).date()).isoformat(), language=lang, age_min=5, age_max=8)
                sel = sel_obj.get("selected", [])
                sum_obj = llm_summaries_or_fallback(sel, date=(target_date or dt.datetime.now(dt.timezone.utc).date()).isoformat(), language=lang, age_min=5, age_max=8)
                summaries = sum_obj.get("summaries", [])
                used_date = (target_date or dt.datetime.now(dt.timezone.utc).date()).isoformat()
                logger.info("TTS-TEST generated on-the-fly summaries=%s", len(summaries))
            except Exception as e:
                logger.exception("TTS-TEST on-the-fly generation failed: %s", e)
                return templates.TemplateResponse(
                    "tts_test.html",
                    {"request": request, "message": f"Failed to generate summaries: {e}", "date": date, "language": lang, "has_key": bool(settings.elevenlabs_api_key)},
                )
        mp3_files: list[dict] = []
        if not settings.elevenlabs_api_key:
            return templates.TemplateResponse(
                "tts_test.html",
                {"request": request, "message": "ELEVENLABS_API_KEY is missing.", "date": used_date, "language": used_lang, "has_key": False, "mp3_files": []},
            )
        # Generate audio files sequentially
        total = 0
        ok = 0
        for idx, s in enumerate(summaries or [], start=1):
            total += 1
            script = (s.get("script") or "").strip()
            title = s.get("title") or "Story"
            try:
                # Prefer streaming directly to a file path to avoid memory issues and SDK quirks
                from .utils.audio_store import path_for_mp3
                save_path, relative_url = path_for_mp3(
                    date=dt.date.fromisoformat(used_date) if used_date else dt.datetime.now(dt.timezone.utc).date(),
                    title=title,
                    index=idx,
                    age_bucket=None,
                    language=used_lang,
                )
                await el_synthesize(script, save_path=save_path)

                mp3_files.append({"title": title, "url": relative_url})
                ok += 1
            except Exception as e:
                logger.warning("TTS-TEST ElevenLabs failed for '%s': %s", title, e)

        logger.info("TTS-TEST done ok=%s/%s", ok, total)
        return templates.TemplateResponse(
            "tts_test.html",
            {
                "request": request,
                "message": None if mp3_files else "No audio files returned.",
                "mp3_files": mp3_files,
                "date": used_date,
                "language": used_lang,
                "has_key": True,
                },
        )

@app.get("/debug", response_class=HTMLResponse)
async def debug_page(
    request: Request,
    date: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    # Determine target date
    target_date: dt.date | None = None
    if date:
        try:
            target_date = dt.date.fromisoformat(date)
        except ValueError:
            target_date = None

    dc: DailyCache | None = None
    if target_date:
        q = await session.execute(
            select(DailyCache).where(
                DailyCache.date == target_date, DailyCache.language == user.preferred_language
            )
        )
        dc = q.scalars().first()
    else:
        q = await session.execute(
            select(DailyCache)
            .where(DailyCache.language == user.preferred_language)
            .order_by(desc(DailyCache.date))
            .limit(1)
        )
        dc = q.scalars().first()

    if not dc:
        return templates.TemplateResponse(
            "debug.html",
            {
                "request": request,
                "message": "No cached build found yet. Trigger a rebuild first.",
                "selection": None,
                "summaries": None,
                "date": date,
                "language": user.preferred_language,
                "generated_at": None,
                "status": None,
                "error": request.query_params.get("error"),
                "built": request.query_params.get("built") == "1",
            },
        )

    selection_obj = dc.selection_json or {}
    summaries_obj = dc.summaries_json or {}
    selection = selection_obj.get("selected") if isinstance(selection_obj, dict) else selection_obj
    summaries = summaries_obj.get("summaries") if isinstance(summaries_obj, dict) else summaries_obj

    # Find latest build run for this user/date for generated_at/status
    qbr = await session.execute(
        select(BuildRun)
        .where(BuildRun.user_id == user.id, BuildRun.date == dc.date)
        .order_by(desc(BuildRun.created_at))
        .limit(1)
    )
    br = qbr.scalars().first()

    return templates.TemplateResponse(
        "debug.html",
        {
            "request": request,
            "message": None,
            "selection": selection or [],
            "summaries": summaries or [],
            "date": dc.date.isoformat(),
            "language": dc.language,
            "generated_at": br.created_at.isoformat() if br else None,
            "status": br.status if br else None,
            "error": request.query_params.get("error"),
            "built": request.query_params.get("built") == "1",
        },
    )
@app.get("/rebuilding", response_class=HTMLResponse)
async def rebuilding_page(request: Request, date: str | None = Query(default=None)):
    return templates.TemplateResponse("rebuilding.html", {"request": request, "date": date})
