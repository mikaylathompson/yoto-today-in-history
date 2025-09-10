from __future__ import annotations

import datetime as dt
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Form
import logging
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
    date = date or dt.datetime.now(dt.timezone.utc).date()
    result = await build_for_user(session, user, date)
    return result


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
            },
        )

    selection_obj = dc.selection_json or {}
    summaries_obj = dc.summaries_json or {}
    selection = selection_obj.get("selected") if isinstance(selection_obj, dict) else selection_obj
    summaries = summaries_obj.get("summaries") if isinstance(summaries_obj, dict) else summaries_obj

    return templates.TemplateResponse(
        "debug.html",
        {
            "request": request,
            "message": None,
            "selection": selection or [],
            "summaries": summaries or [],
            "date": dc.date.isoformat(),
            "language": dc.language,
        },
    )
