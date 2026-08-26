# Runbook: Secrets rotation drill

- **Verifying automation:**
  [`infra/secrets/verify-rotation-drill.sh`](../../infra/secrets/verify-rotation-drill.sh)
  (`make secrets-rotation-drill`, run in CI's `security` job on every PR)
  proves the SOPS/age procedure below actually rotates a recipient, not
  just adds one.
  [`apps/api/tests/security/test_jwt_kid_rotation.py`](../../apps/api/tests/security/test_jwt_kid_rotation.py)
  (runs in CI's `security` job on every PR) proves the JWT kid-overlap
  procedure below end to end.

## When to run this

- Scheduled hygiene (recommended: yearly, or per your org's policy).
- A device holding a private key (age identity, or a machine with a
  provider API key configured) is lost, decommissioned, or potentially
  compromised.
- A team member with SOPS decrypt access leaves.
- After any suspected secret exposure (accidental commit later
  `git`-scrubbed, a leaked CI log, etc.) — rotation is the actual fix;
  scrubbing history alone doesn't invalidate a secret an attacker
  already copied.

This repo has exactly three secret classes (§7.4/§7.5); each gets its
own procedure below.

---

## 1. SOPS/age recipient key

The age keypair that gates who can decrypt `infra/secrets/*.enc.yaml`.
Rotating it means: generate a new keypair, add it as a *second*
recipient (overlap window — both old and new decrypt), verify the new
key actually works, then remove the old recipient and verify it
genuinely no longer works. Adding a recipient without ever removing the
old one isn't a rotation, it's just growing the trust set forever — the
verification step below exists specifically to catch that mistake.

```bash
# 1. Generate the new keypair (whoever is taking over/renewing access).
age-keygen -o new-key.txt
# Public key: age1...   (copy this)

# 2. Add the new recipient in .sops.yaml — keep the old one too, this is
#    the overlap window.
#    creation_rules:
#      - path_regex: infra/secrets/.*\.enc\.yaml$
#        age: "<old-public-key>,<new-public-key>"

# 3. Sync every already-encrypted file to the updated recipient list —
#    needs a currently-valid decrypt identity (the old key still works
#    here, since it hasn't been removed yet).
SOPS_AGE_KEY_FILE=<old-key-path> sops updatekeys --yes infra/secrets/dev.enc.yaml

# 4. Verify the NEW key can now decrypt (this is the point of the
#    overlap window — confirm it works before you burn the old bridge).
SOPS_AGE_KEY_FILE=new-key.txt sops --decrypt infra/secrets/dev.enc.yaml

# 5. Once every holder of the old key has confirmed their new key
#    works, remove the old recipient from .sops.yaml:
#    creation_rules:
#      - path_regex: infra/secrets/.*\.enc\.yaml$
#        age: "<new-public-key>"

# 6. Sync again — this time using the NEW key as the decrypt identity
#    (the old one is being removed, not used to authorize its own removal).
SOPS_AGE_KEY_FILE=new-key.txt sops updatekeys --yes infra/secrets/dev.enc.yaml

# 7. Verify rotation actually completed: the OLD key must now fail.
SOPS_AGE_KEY_FILE=<old-key-path> sops --decrypt infra/secrets/dev.enc.yaml
# Expected: "Recovery failed because no master key was able to decrypt the file."
```

Commit the updated `.sops.yaml` and the re-encrypted `*.enc.yaml` files
together — an updated recipient list with stale ciphertext (or vice
versa) leaves the repo in a broken, half-rotated state.

## 2. Provider API key (OpenAI / Anthropic / Resend)

The envelope-encryption-in-PG rotation path ADR-7.5 anticipates:

```bash
# 1. Generate the new key in the provider's own dashboard. Do not
#    revoke the old one yet — that's step 4, after the new one is
#    confirmed live.

# 2. Update the value in the SOPS bundle (edits happen through the
#    encrypted file directly — sops decrypts to your editor, you never
#    see plaintext hit disk).
make secrets-edit
# change the relevant key's value, save, sops re-encrypts on exit.

# 3. Redeploy so the running process picks up the new value (§4's
#    expand-contract deploy model — a config-only change still goes
#    through the same rolling-replace path as a code deploy).

# 4. Verify the NEW key is what's actually in use — a real provider
#    call is the only real proof:
curl -s -X POST http://localhost:8000/v1/workspaces/<ws>/threads/<t>/messages \
  -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' \
  -d '{"content": "test", "client_message_id": "'"$(uuidgen)"'"}'
#    Check the response's `model` field / the LLM_PROVIDER_TTFT metric's
#    provider label in Grafana — both reflect whichever provider/key
#    actually answered (GeneratorPort.primary_model, §3.2.4).

# 5. Once confirmed, revoke the OLD key in the provider's dashboard.
#    Confirm no reference to it remains: grep the SOPS bundle (already
#    overwritten in step 2) and any deploy-time secret-manager entries.
```

**Honest gap:** step 4's real-provider-call verification needs an
actual configured `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` — this dev/CI
environment has none (the whole project's generation path honestly
falls back to `EchoGenerator` without one, see
`http/composition.py`'s `_build_generator`), so this step has not been
exercised against a real provider in this repo's automation. It's a
real, documented, undischarged gap — not silently skipped or faked —
and the honest reason the North Star eval score carries the same
caveat elsewhere in this repo.

## 3. JWT signing key (kid overlap)

Unlike the other two classes, this one has dedicated application code
(`EdDSATokenSigner`, §7.2) rather than being purely an operational
procedure — the overlap mechanism is real, tested code, not just a
sequence of commands.

```bash
# 1. Generate a new Ed25519 signing key + a new kid value.
python3 -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import base64
seed = Ed25519PrivateKey.generate().private_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PrivateFormat.Raw,
    encryption_algorithm=serialization.NoEncryption(),
)
print(base64.b64encode(seed).decode())
"
# pick a new kid, e.g. "dev-2" if the current one is "dev-1"

# 2. In the SOPS bundle: set AETHER_JWT_SIGNING_KEY/AETHER_JWT_KID to
#    the NEW pair, and AETHER_JWT_PREVIOUS_SIGNING_KEY/
#    AETHER_JWT_PREVIOUS_KID to the value the CURRENT pair had —
#    this is the overlap window. EdDSATokenSigner adds the previous key
#    to its verify-only key set; it is never used to issue tokens
#    (see the adapter's own docstring for the structural guarantee).
make secrets-edit

# 3. Redeploy. Already-issued access tokens (signed under the old kid,
#    up to jwt_access_ttl_seconds old — 15 min by default) keep
#    validating; new tokens are issued under the new kid.

# 4. Once the old access-token TTL window has fully elapsed (so no
#    live token signed under the old kid can possibly still exist),
#    remove AETHER_JWT_PREVIOUS_SIGNING_KEY/AETHER_JWT_PREVIOUS_KID
#    from the bundle entirely and redeploy again — this retires the
#    old key. A token signed under the old kid now fails verification
#    (unknown key id), proving this genuinely rotated the signing key
#    rather than just adding a second permanent one.
```

**If this rotation is itself the response to a suspected signing-key
compromise** (not routine hygiene), pair it with a global refresh-token
revocation — rotating the *access*-token signing key alone doesn't
invalidate outstanding refresh tokens, and a compromised signing key
means an attacker could have minted forged access tokens against the
old key throughout its exposure window. See
[`refresh-reuse-detected.md`](refresh-reuse-detected.md) for the
`revoke_family`/`revoke_all_for_user` mechanism this calls for at
scale.

## Verification (how you know it's fixed)

- SOPS/age: `make secrets-rotation-drill` passes (also runs in CI on
  every PR — a real regression in the rotation procedure itself would
  be caught before it ever reached a real rotation).
- JWT kid: `pytest tests/security/test_jwt_kid_rotation.py` passes
  (also runs in CI's `security` job on every PR).
- Provider key: the real-call check in step 4 of that section returns
  a successful, grounded (or correctly-refused) response, and the
  provider's own dashboard shows request volume against the new key,
  not the old one.
