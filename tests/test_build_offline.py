import datetime as dt
import pytest

from src.app.db import Base, engine, SessionLocal
from src.app.models import User
from src.app.build import build_for_user


@pytest.mark.asyncio
async def test_build_for_user_offline(tmp_path):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:  # type: AsyncSession
        user = User(preferred_language="en")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        out = await build_for_user(session, user, dt.date(2025, 1, 1))
        assert out["status"] == "success"
        assert out["chapters"][0]["tracks"][-1]["title"] == "Sources for today"
