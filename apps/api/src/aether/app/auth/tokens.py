"""Pure helpers shared by the auth use cases — no I/O, no ports."""

from __future__ import annotations

import hashlib


def hash_refresh_token(raw_token: str) -> str:
    """SHA-256 is the right tool here, not Argon2: the input is already a
    256-bit cryptographically random value (secrets.token_urlsafe(32)),
    not a low-entropy human password — there is nothing for a slow, memory-
    hard hash to defend against that a fast hash doesn't already (Blueprint
    §7.4's identical reasoning for API keys applies equally to refresh
    tokens)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
