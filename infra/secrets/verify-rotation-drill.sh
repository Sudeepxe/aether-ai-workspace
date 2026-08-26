#!/usr/bin/env bash
# Verifies the SOPS/age recipient-key rotation procedure actually rotates
# (not just adds) a recipient — S10 #109, docs/runbooks/secrets-rotation.md.
#
# Runs entirely against scratch files in a temp directory; never touches
# infra/secrets/dev.enc.yaml or .sops.yaml. This proves the *mechanism*
# real rotations rely on, without requiring (or risking) the real
# repo-committed secrets bundle or its actual recipient's private key,
# which this script — like any CI run — has no access to.
set -euo pipefail

command -v sops >/dev/null || { echo "sops not found — see infra/secrets/README.md" >&2; exit 1; }
command -v age-keygen >/dev/null || { echo "age-keygen not found" >&2; exit 1; }

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

age-keygen -o key-old.txt 2>/dev/null
age-keygen -o key-new.txt 2>/dev/null
OLD_PUB="$(grep 'public key:' key-old.txt | awk '{print $NF}')"
NEW_PUB="$(grep 'public key:' key-new.txt | awk '{print $NF}')"

echo "placeholder_secret: rotate-me-123" > plain.yaml
# No .sops.yaml yet: an explicit --age on the CLI needs no config-based
# rule matching, and once a .sops.yaml exists sops's config
# auto-discovery insists on matching creation_rules against the input
# path even when the recipient was already given explicitly — so it's
# introduced only from here on, once updatekeys actually needs it.
sops --encrypt --age "$OLD_PUB" plain.yaml > secrets.enc.yaml

echo "== Step 1: baseline — only the old key decrypts =="
SOPS_AGE_KEY_FILE=key-old.txt sops --decrypt secrets.enc.yaml >/dev/null
if SOPS_AGE_KEY_FILE=key-new.txt sops --decrypt secrets.enc.yaml >/dev/null 2>&1; then
  echo "FAIL: new key decrypted before being added as a recipient" >&2
  exit 1
fi
echo "  ok"

echo "== Step 2: add the new recipient in .sops.yaml, sync with updatekeys =="
cat > .sops.yaml <<EOF
creation_rules:
  - path_regex: secrets\.enc\.yaml\$
    age: "$OLD_PUB,$NEW_PUB"
EOF
SOPS_AGE_KEY_FILE=key-old.txt sops updatekeys --yes secrets.enc.yaml >/dev/null
SOPS_AGE_KEY_FILE=key-old.txt sops --decrypt secrets.enc.yaml >/dev/null
SOPS_AGE_KEY_FILE=key-new.txt sops --decrypt secrets.enc.yaml >/dev/null
echo "  ok — both keys decrypt during the overlap window"

echo "== Step 3: remove the old recipient, sync again =="
cat > .sops.yaml <<EOF
creation_rules:
  - path_regex: secrets\.enc\.yaml\$
    age: "$NEW_PUB"
EOF
SOPS_AGE_KEY_FILE=key-new.txt sops updatekeys --yes secrets.enc.yaml >/dev/null
SOPS_AGE_KEY_FILE=key-new.txt sops --decrypt secrets.enc.yaml >/dev/null
if SOPS_AGE_KEY_FILE=key-old.txt sops --decrypt secrets.enc.yaml >/dev/null 2>&1; then
  echo "FAIL: old key still decrypts after being removed as a recipient — this rotated nothing" >&2
  exit 1
fi
echo "  ok — old key no longer decrypts; rotation genuinely completed"

echo "PASS: SOPS/age recipient rotation drill"
