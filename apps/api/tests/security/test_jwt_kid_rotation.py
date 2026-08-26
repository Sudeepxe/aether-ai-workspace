"""JWT signing-key rotation via kid overlap — the literal drill S10 #109
promises (§7.4/§7.6, docs/runbooks/secrets-rotation.md's JWT section):
a token signed under the OLD key/kid keeps validating during an overlap
window (already-issued access tokens don't need to fail immediately),
then correctly stops validating once the old key is retired.
"""

from __future__ import annotations

import base64
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aether.adapters.jwt.eddsa import EdDSATokenSigner
from aether.domain.errors import InvalidAccessTokenError

pytestmark = pytest.mark.security

_OLD_KID = "dev-1"
_NEW_KID = "dev-2"
_TTL = 900


def _generate_key_b64() -> str:
    seed = Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(seed).decode()


def test_full_kid_rotation_drill() -> None:
    old_key_b64 = _generate_key_b64()
    new_key_b64 = _generate_key_b64()

    # --- Step 1: pre-rotation — only the old key is deployed. ---
    pre_rotation_signer = EdDSATokenSigner(
        signing_key_b64=old_key_b64, kid=_OLD_KID, access_ttl_seconds=_TTL
    )
    user_id, jti = uuid4(), uuid4()
    token_issued_before_rotation = pre_rotation_signer.issue_access_token(user_id=user_id, jti=jti)

    # --- Step 2: rotation begins — new key is now primary, old key kept
    # verify-only for the overlap window. ---
    overlap_signer = EdDSATokenSigner(
        signing_key_b64=new_key_b64,
        kid=_NEW_KID,
        access_ttl_seconds=_TTL,
        previous_signing_key_b64=old_key_b64,
        previous_kid=_OLD_KID,
    )

    # The already-issued token (old kid) still validates during overlap —
    # this is the entire point: a signing-key rotation must not
    # invalidate every live session the instant it happens.
    claims = overlap_signer.verify_access_token(token_issued_before_rotation)
    assert claims.sub == user_id
    assert claims.jti == jti

    # New tokens issued during overlap use the new key/kid, not the old
    # one — the "previous" key is verify-only, never a signing option.
    new_user_id, new_jti = uuid4(), uuid4()
    token_issued_during_overlap = overlap_signer.issue_access_token(
        user_id=new_user_id, jti=new_jti
    )
    assert token_issued_during_overlap.split(".")[0] != token_issued_before_rotation.split(".")[0]
    new_claims = overlap_signer.verify_access_token(token_issued_during_overlap)
    assert new_claims.sub == new_user_id

    # --- Step 3: overlap window ends — old key retired entirely. ---
    post_rotation_signer = EdDSATokenSigner(
        signing_key_b64=new_key_b64, kid=_NEW_KID, access_ttl_seconds=_TTL
    )

    # A token from the new key/kid still works (rotation completed
    # cleanly for anything issued during or after the overlap)...
    post_claims = post_rotation_signer.verify_access_token(token_issued_during_overlap)
    assert post_claims.sub == new_user_id

    # ...but the pre-rotation, old-kid token is now correctly rejected —
    # proving this actually *rotated* the key, not just added a second
    # one permanently.
    with pytest.raises(InvalidAccessTokenError):
        post_rotation_signer.verify_access_token(token_issued_before_rotation)


def test_overlap_signer_never_issues_tokens_under_the_previous_kid() -> None:
    old_key_b64 = _generate_key_b64()
    new_key_b64 = _generate_key_b64()
    overlap_signer = EdDSATokenSigner(
        signing_key_b64=new_key_b64,
        kid=_NEW_KID,
        access_ttl_seconds=_TTL,
        previous_signing_key_b64=old_key_b64,
        previous_kid=_OLD_KID,
    )

    token = overlap_signer.issue_access_token(user_id=uuid4(), jti=uuid4())

    header = jwt.get_unverified_header(token)
    assert header["kid"] == _NEW_KID
