"""Pure helpers shared by the auth use cases — no I/O, no ports."""

from __future__ import annotations

import hashlib


def hash_token(raw_token: str) -> str:
    """SHA-256 is the right tool here, not Argon2: the input is already a
    high-entropy cryptographically random value (secrets.token_urlsafe),
    not a low-entropy human password — there is nothing for a slow, memory-
    hard hash to defend against that a fast hash doesn't already (Blueprint
    §7.4's identical reasoning for API keys applies equally to refresh,
    invitation, and password-reset tokens — every single-use random-token
    flow in this codebase shares this one hashing rule)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def hash_refresh_token(raw_token: str) -> str:
    return hash_token(raw_token)
