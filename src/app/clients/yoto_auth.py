from __future__ import annotations

import time
from typing import Optional, Tuple
import httpx

from ..config import settings


def build_authorize_url(client_id: str, redirect_uri: str, state: str, code_challenge: str, scope: str = "openid offline_access") -> str:
    base = settings.yoto_oauth_base.rstrip("/")
    # Assumption: authorize endpoint path as below; adjust if docs differ
    qp = httpx.QueryParams(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return f"{base}/oauth/authorize?{qp}"


async def exchange_code_for_token(code: str, code_verifier: str) -> dict:
    base = settings.yoto_oauth_base.rstrip("/")
    url = f"{base}/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.yoto_redirect_uri,
        "client_id": settings.yoto_client_id,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, data=data)
        r.raise_for_status()
        tok = r.json()
        # Expected fields: access_token, refresh_token, expires_in
        tok["obtained_at"] = int(time.time())
        return tok


async def refresh_access_token(refresh_token: str) -> dict:
    base = settings.yoto_oauth_base.rstrip("/")
    url = f"{base}/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.yoto_client_id,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, data=data)
        r.raise_for_status()
        tok = r.json()
        tok["obtained_at"] = int(time.time())
        return tok
