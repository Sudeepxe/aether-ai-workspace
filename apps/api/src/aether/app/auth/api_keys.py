"""Pure helpers for API-key generation (§7.4) — no I/O, no ports. Mirrors
app.auth.tokens' hashing rule exactly (SHA-256 is correct for a value
that's already high-entropy cryptographically random, same reasoning
that module's docstring already gives for refresh/invitation/password-
reset tokens — §7.4 explicitly says the identical reasoning applies to
API keys)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from aether.app.auth.tokens import hash_token

_PREFIX_CHARS = 8
_SECRET_BYTES = 32  # 256 bits


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    raw_key: str
    """Shown to the caller exactly once, at creation — never recoverable
    from ``secret_hash`` afterward."""
    prefix: str
    """The plaintext, displayable identification portion — safe to show
    again in a list view, since it alone can't authenticate anything."""
    secret_hash: str


def generate_api_key(*, env: str) -> GeneratedApiKey:
    """Format ``aeth_{env}_{8-char-prefix}{43-char-urlsafe-secret}``
    (§7.4). The prefix is generated independently of the secret (not a
    truncation of it) so that knowing the prefix reveals nothing about
    the secret, and is stored in its own column for O(1) lookup without
    ever needing to scan-and-compare every row in the table."""
    prefix = secrets.token_urlsafe(6)[:_PREFIX_CHARS]
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    raw_key = f"aeth_{env}_{prefix}{secret}"
    return GeneratedApiKey(raw_key=raw_key, prefix=prefix, secret_hash=hash_token(raw_key))


def is_api_key(raw_credential: str) -> bool:
    """Distinguishes an API-key bearer credential from a JWT bearer
    credential on the same Authorization header — a JWT never starts
    with this literal prefix (base64url JWT segments don't produce
    ``aeth_``), so this is unambiguous, not a heuristic."""
    return raw_credential.startswith("aeth_")
