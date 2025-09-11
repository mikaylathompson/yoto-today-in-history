from __future__ import annotations

import datetime as dt
import uuid
from sqlalchemy import (
    String,
    DateTime,
    Integer,
    Date,
    JSON,
    Text,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    yoto_sub: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    yoto_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    yoto_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    yoto_token_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String, default="UTC")
    preferred_language: Mapped[str] = mapped_column(String, default="en")
    age_bucket: Mapped[str] = mapped_column(String, default="5-8")
    age_min: Mapped[int] = mapped_column(Integer, default=5)
    age_max: Mapped[int] = mapped_column(Integer, default=10)
    card_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    builds: Mapped[list["BuildRun"]] = relationship(back_populates="user")


class DailyCache(Base):
    __tablename__ = "daily_cache"
    __table_args__ = (UniqueConstraint("date", "language", "age_bucket", name="uq_daily_date_lang_bucket"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    language: Mapped[str] = mapped_column(String, index=True)
    age_bucket: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    feed_hash: Mapped[str | None] = mapped_column(String, index=True)
    selection_json: Mapped[dict | None] = mapped_column(JSON)
    summaries_json: Mapped[dict | None] = mapped_column(JSON)
    audio_refs_json: Mapped[dict | None] = mapped_column(JSON)
    attribution_script: Mapped[str | None] = mapped_column(Text)


class BuildRun(Base):
    __tablename__ = "build_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String, default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    user: Mapped[User] = relationship(back_populates="builds")
