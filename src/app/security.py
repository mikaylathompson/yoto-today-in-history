from __future__ import annotations

import uuid
from fastapi import HTTPException, status, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import User


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Load the current logged-in user from session. 401 if missing."""
    uid = request.session.get("user_id") if hasattr(request, "session") else None
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        user_id = uuid.UUID(uid)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user
