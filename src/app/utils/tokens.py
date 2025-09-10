from __future__ import annotations

from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User
from ..clients.yoto_auth import refresh_access_token


async def ensure_yoto_access_token(session: AsyncSession, user: User) -> str | None:
    now = datetime.now(timezone.utc)
    exp = user.yoto_token_expires_at
    # SQLite stores naive datetimes; coerce to UTC-aware for safe comparison
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if user.yoto_access_token and exp and exp > now + timedelta(seconds=60):
        return user.yoto_access_token
    if user.yoto_refresh_token:
        tok = await refresh_access_token(user.yoto_refresh_token)
        user.yoto_access_token = tok.get("access_token")
        # Replace refresh token (single-use)
        if tok.get("refresh_token"):
            user.yoto_refresh_token = tok.get("refresh_token")
        expires_in = tok.get("expires_in") or 3600
        user.yoto_token_expires_at = now + timedelta(seconds=int(expires_in))
        await session.commit()
        return user.yoto_access_token
    return user.yoto_access_token
