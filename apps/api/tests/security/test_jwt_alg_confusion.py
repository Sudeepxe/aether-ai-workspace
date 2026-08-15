"""JWT algorithm-confusion and tampering resistance (ADR-7.2, Blueprint
§7.5 "JWT alg/kid games"). EdDSATokenSigner's whole security argument is
structural — algorithm and kid are both resolved server-side, never from
the token — so this proves that structure actually holds at runtime,
not just that the code reads that way.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aether.adapters.jwt.eddsa import EdDSATokenSigner
from aether.domain.errors import InvalidAccessTokenError

pytestmark = pytest.mark.security

_KID = "dev-1"
_TTL = 900


def _signer() -> EdDSATokenSigner:
    seed = Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return EdDSATokenSigner(
        signing_key_b64=base64.b64encode(seed).decode(), kid=_KID, access_ttl_seconds=_TTL
    )


def test_a_genuine_token_round_trips() -> None:
    signer = _signer()
    user_id, jti = uuid4(), uuid4()
    token = signer.issue_access_token(user_id=user_id, jti=jti)

    claims = signer.verify_access_token(token)

    assert claims.sub == user_id
    assert claims.jti == jti


def test_tampered_signature_is_rejected() -> None:
    signer = _signer()
    token = signer.issue_access_token(user_id=uuid4(), jti=uuid4())
    header_b64, payload_b64, sig_b64 = token.split(".")
    flipped = bytearray(base64.urlsafe_b64decode(sig_b64 + "=="))
    flipped[0] ^= 0xFF
    tampered = f"{header_b64}.{payload_b64}.{base64.urlsafe_b64encode(bytes(flipped)).decode().rstrip('=')}"

    with pytest.raises(InvalidAccessTokenError):
        signer.verify_access_token(tampered)


def test_alg_none_token_is_rejected() -> None:
    """The classic alg-confusion attack: a token that claims alg=none and
    carries no signature at all. verify_access_token pins
    algorithms=["EdDSA"] to jwt.decode rather than trusting the token's
    own header, so this must never verify regardless of its claims."""
    signer = _signer()
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "jti": str(uuid4()),
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(seconds=_TTL)).timestamp()),
        },
        key="",  # PyJWT's alg=none convention — no key material to sign with
        algorithm="none",
        headers={"kid": _KID},
    )

    with pytest.raises(InvalidAccessTokenError):
        signer.verify_access_token(forged)


def test_token_signed_with_a_different_algorithm_is_rejected() -> None:
    """A token forged with HS256, using the signer's own Ed25519 *public*
    key bytes as an HMAC secret — the RS256/HS256-style confusion attack
    adapted to an EdDSA signer. Only exploitable if the verifier ever
    lets the token pick its algorithm; pinning algorithms=["EdDSA"]
    should reject this outright, before signature verification even
    matters."""
    signer = _signer()
    public_key = signer._verify_keys[_KID]
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "jti": str(uuid4()),
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(seconds=_TTL)).timestamp()),
        },
        key=public_bytes,
        algorithm="HS256",
        headers={"kid": _KID},
    )

    with pytest.raises(InvalidAccessTokenError):
        signer.verify_access_token(forged)


def test_unknown_kid_is_rejected() -> None:
    """A token correctly signed by the signer's own private key, but
    declaring a kid the verifier never registered — kid resolution must
    fail closed, not fall back to "the only key we have"."""
    signer = _signer()
    seed = signer._private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "jti": str(uuid4()),
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(seconds=_TTL)).timestamp()),
        },
        key=Ed25519PrivateKey.from_private_bytes(seed),
        algorithm="EdDSA",
        headers={"kid": "attacker-supplied-kid"},
    )

    with pytest.raises(InvalidAccessTokenError):
        signer.verify_access_token(forged)


def test_expired_token_is_rejected() -> None:
    signer = _signer()
    seed = signer._private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "jti": str(uuid4()),
            "iat": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
        },
        key=Ed25519PrivateKey.from_private_bytes(seed),
        algorithm="EdDSA",
        headers={"kid": _KID},
    )

    with pytest.raises(InvalidAccessTokenError):
        signer.verify_access_token(expired)
