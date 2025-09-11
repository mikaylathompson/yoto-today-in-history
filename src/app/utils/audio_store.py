from __future__ import annotations

import os
import re
import datetime as dt
from typing import Tuple

from ..config import settings


_SAFE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _safe_name(text: str) -> str:
    return _SAFE_RE.sub("-", text).strip("-") or "audio"


def ensure_audio_dir() -> str:
    path = os.path.abspath(settings.audio_dir)
    os.makedirs(path, exist_ok=True)
    return path


def path_for_mp3(date: dt.date, title: str, index: int, age_bucket: str | None = None, language: str | None = None) -> Tuple[str, str]:
    """
    Construct target filesystem path and public URL for an MP3 without writing it.
    Filename format: {date}_{lang}_{bucket}_{index}_{slug}.mp3
    """
    base = ensure_audio_dir()
    slug = _safe_name(title)[:20]
    lang = (language or "en").lower()
    bucket = (age_bucket or "5-8").replace("/", "-")
    fname = f"{date.isoformat()}_{lang}_{bucket}_{index:02d}_{slug}.mp3"
    fpath = os.path.join(base, fname)
    relative_url = f"audio/{fname}"
    return fpath, relative_url


def delete_older_than(hours: int) -> int:
    base = ensure_audio_dir()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    removed = 0
    for name in os.listdir(base):
        if not name.endswith(".mp3"):
            continue
        path = os.path.join(base, name)
        try:
            st = os.stat(path)
            mtime = dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc)
            if mtime < cutoff:
                os.remove(path)
                removed += 1
        except Exception:
            # best-effort cleanup
            continue
    return removed


