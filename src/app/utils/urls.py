from __future__ import annotations

from urllib.parse import urlparse


def is_valid_absolute_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        p = urlparse(url)
        return bool(p.scheme in {"http", "https"} and p.netloc)
    except Exception:
        return False

