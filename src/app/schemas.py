from __future__ import annotations

import datetime as dt
from pydantic import BaseModel, Field
from typing import Optional, List


class MeResponse(BaseModel):
    preferred_language: str
    timezone: str
    age_min: int
    age_max: int
    card_id: Optional[str]


class SettingsUpdate(BaseModel):
    preferred_language: Optional[str] = Field(None)
    timezone: Optional[str] = Field(None)
    age_min: Optional[int] = Field(None, ge=3)
    age_max: Optional[int] = Field(None, ge=3)


class RebuildRequest(BaseModel):
    date: Optional[dt.date] = None


class StatusItem(BaseModel):
    id: str
    user_id: str
    date: dt.date
    status: str
    error: Optional[str]
