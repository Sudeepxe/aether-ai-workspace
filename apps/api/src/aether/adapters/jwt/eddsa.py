"""EdDSA (Ed25519) access-token signer/verifier (ADR-7.2).

Security posture, enforced structurally, not by convention:
- Algorithm is pinned to EdDSA — `algorithms=["EdDSA"]` is passed fixed to
  `jwt.decode`, never derived from the token's own header, so a token
  cannot select its own verification algorithm (alg-confusion attacks).
- The key set is static and in-process (one dev key today; ADR-7.2's
  "overlapping validity windows" rotation model extends this dict without
  changing the verify path). `kid` is resolved only against this dict —
  never fetched from a JWKS URL supplied by the token.

Claims deliberately exclude tenant_id/role/scopes: Sprint 1 has no
workspace-context-selection endpoint yet (no route lets a client say
"act as me in workspace X"), so there is nothing real to put in those
claims. They're added when that selection flow lands, not omitted by
oversight.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from aether.domain.errors import InvalidAccessTokenError
from aether.ports.security import AccessTokenClaims

_ALGORITHM = "EdDSA"


class EdDSATokenSigner:
    def __init__(self, *, signing_key_b64: str, kid: str, access_ttl_seconds: int) -> None:
        seed = base64.b64decode(signing_key_b64)
        self._private_key = Ed25519PrivateKey.from_private_bytes(seed)
        self._kid = kid
        self._access_ttl = timedelta(seconds=access_ttl_seconds)
        # The static in-process key set (ADR-7.2). One key today; rotation
        # adds entries here with overlapping validity, verify path unchanged.
        self._verify_keys: dict[str, Ed25519PublicKey] = {
            kid: self._private_key.public_key(),
        }

    def issue_access_token(self, *, user_id: UUID, jti: UUID) -> str:
        now = datetime.now(UTC)
        exp = now + self._access_ttl
        claims = {
            "sub": str(user_id),
            "jti": str(jti),
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        return jwt.encode(
            claims, self._private_key, algorithm=_ALGORITHM, headers={"kid": self._kid}
        )

    def verify_access_token(self, token: str) -> AccessTokenClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.exceptions.DecodeError as exc:
            raise InvalidAccessTokenError("malformed token") from exc

        kid = header.get("kid")
        public_key = self._verify_keys.get(kid) if isinstance(kid, str) else None
        if public_key is None:
            raise InvalidAccessTokenError("unknown key id")

        try:
            claims = jwt.decode(token, public_key, algorithms=[_ALGORITHM])
        except jwt.exceptions.InvalidTokenError as exc:
            raise InvalidAccessTokenError(str(exc)) from exc

        try:
            return AccessTokenClaims(
                sub=UUID(claims["sub"]),
                jti=UUID(claims["jti"]),
                issued_at=datetime.fromtimestamp(claims["iat"], tz=UTC),
                expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
            )
        except (KeyError, ValueError) as exc:
            raise InvalidAccessTokenError("malformed claims") from exc
