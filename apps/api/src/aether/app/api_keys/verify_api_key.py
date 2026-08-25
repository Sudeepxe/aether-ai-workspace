"""VerifyApiKey use case — the read path behind every API-key-
authenticated request (§7.4). Pool-bound (no tenant context exists yet
when a caller presents only a bearer string — see the api_keys
migration's docstring), used by http/deps.py's auth dependency exactly
where JWT verification currently sits.

One error for every failure mode (unknown prefix, hash mismatch,
revoked, expired) — InvalidApiKeyError, the same enumeration-safety
posture as AuthenticationFailedError: a caller probing a leaked/guessed
key must not be able to distinguish "wrong secret" from "revoked" from
"expired" from "never existed"."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from uuid import UUID

from aether.app.auth.tokens import hash_token
from aether.domain.entities import ApiKeyScope
from aether.domain.errors import InvalidApiKeyError
from aether.observability.metrics import API_KEY_AUTH_TOTAL
from aether.ports.repositories import ApiKeyRepositoryPort
from aether.ports.security import ClockPort

_PREFIX_LEN = 8
_KEY_PREFIX_MARKER = "aeth_"


@dataclass(frozen=True, slots=True)
class ApiKeyPrincipal:
    api_key_id: UUID
    workspace_id: UUID
    scopes: frozenset[ApiKeyScope]


def _extract_prefix(raw_key: str) -> str:
    """``aeth_{env}_{8-char-prefix}{secret}`` — the prefix is always the
    8 characters right after the second underscore-delimited segment.
    Raises ValueError (mapped to InvalidApiKeyError by the caller) on
    anything too short or malformed to contain one, rather than crashing
    on a slice of a too-short string."""
    parts = raw_key.split("_", 2)
    if len(parts) != 3 or parts[0] != "aeth" or len(parts[2]) < _PREFIX_LEN:
        raise ValueError("malformed API key")
    return parts[2][:_PREFIX_LEN]


class VerifyApiKey:
    def __init__(self, *, api_keys: ApiKeyRepositoryPort, clock: ClockPort) -> None:
        self._api_keys = api_keys
        self._clock = clock

    async def execute(self, raw_key: str) -> ApiKeyPrincipal:
        try:
            prefix = _extract_prefix(raw_key)
        except ValueError as exc:
            API_KEY_AUTH_TOTAL.labels(outcome="invalid").inc()
            raise InvalidApiKeyError("malformed API key") from exc

        api_key = await self._api_keys.get_by_prefix(prefix)
        if api_key is None:
            API_KEY_AUTH_TOTAL.labels(outcome="invalid").inc()
            raise InvalidApiKeyError("unknown API key")

        # Constant-time compare: both sides are already fixed-length
        # SHA-256 hex digests, but this is the credential-comparison
        # path — the same discipline as every other secret-hash check
        # in this codebase, not optional here.
        if not hmac.compare_digest(api_key.secret_hash, hash_token(raw_key)):
            API_KEY_AUTH_TOTAL.labels(outcome="invalid").inc()
            raise InvalidApiKeyError("invalid API key")

        now = self._clock.now()
        if api_key.revoked_at is not None or (
            api_key.expires_at is not None and api_key.expires_at <= now
        ):
            API_KEY_AUTH_TOTAL.labels(outcome="invalid").inc()
            raise InvalidApiKeyError("API key no longer valid")

        await self._api_keys.touch_last_used(api_key.id, used_at=now)
        API_KEY_AUTH_TOTAL.labels(outcome="success").inc()

        return ApiKeyPrincipal(
            api_key_id=api_key.id, workspace_id=api_key.workspace_id, scopes=api_key.scopes
        )
