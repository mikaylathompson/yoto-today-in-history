from __future__ import annotations

import base64
import os
import hashlib


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate_verifier(length: int = 64) -> str:
    return _b64url(os.urandom(length))


def challenge_from_verifier(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return _b64url(digest)

