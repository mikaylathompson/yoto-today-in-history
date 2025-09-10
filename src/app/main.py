from __future__ import annotations

import datetime as dt
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Form
import logging
import time
import httpx
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from .config import settings
from .db import get_session, Base, engine
from .models import User, BuildRun
from .models import DailyCache
from .schemas import MeResponse, SettingsUpdate, RebuildRequest, StatusItem
from .security import get_current_user
from .build import build_for_user
from .utils.pkce import generate_verifier, challenge_from_verifier
from .clients.yoto_auth import build_authorize_url, exchange_code_for_token, refresh_access_token
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
        stored_challenge = request.session.get("pkce_challenge")
        recomputed = challenge_from_verifier(verifier)
        # Proceed without verbose logging
        tok = await exchange_code_for_token(code, verifier, redirect_uri)
    except httpx.HTTPStatusError as e:
        logger.exception("Token exchange failed: %s %s", getattr(e.response, 'status_code', '?'), getattr(e.response, 'text', '')[:500])
        return RedirectResponse(url=f"/?oauth_error=token_exchange_failed", status_code=303)
    except Exception:
        logger.exception("Token exchange failed: unexpected error")
        return RedirectResponse(url=f"/?oauth_error=token_exchange_failed", status_code=303)
    # Persist or update user with tokens
    q = await session.execute(select(User).limit(1))
    user = q.scalars().first()
    if not user:
        user = User()
        session.add(user)
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
    age_min: int = Form(...),
    age_max: int = Form(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    user.preferred_language = preferred_language
    user.timezone = timezone
    user.age_min = age_min
    user.age_max = age_max
    await session.commit()
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@app.get("/rebuild")
async def rebuild_get(
    date: dt.date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    User-facing rebuild action. On success, redirect to /debug for the built date.
    On failure, redirect to /debug with an error banner. Avoids exposing raw 500s.
    """
    target_date = date or dt.datetime.now(dt.timezone.utc).date()
    try:
        await build_for_user(session, user, target_date)
        return RedirectResponse(url=f"/debug?date={target_date.isoformat()}&built=1", status_code=303)
    except Exception as e:  # noqa: BLE001
        logger.exception("Rebuild failed for user=%s date=%s: %s", user.id, target_date, e)
        # Pass a compact error code; details are in server logs
        return RedirectResponse(url=f"/debug?date={target_date.isoformat()}&error=build_failed", status_code=303)


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

        return {
            "date": d.isoformat(),
            "language": language,
            "selection": sel_obj,
            "summaries": sum_obj,
            "attribution": att_obj,
        }

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
