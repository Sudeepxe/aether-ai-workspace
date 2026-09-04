#!/usr/bin/env bash
# Regression check for env-export.sh / `make secrets-env` (S12): a real
# `eval "$(make secrets-env)"` failure showed the previous
# implementation (1) leaked non-AETHER_* keys (e.g. a documentation
# placeholder field) into the shell, and (2) never shell-quoted values,
# so anything containing a space broke `eval` outright.
#
# Runs entirely against a scratch SOPS+age bundle in a temp directory;
# never touches infra/secrets/dev.enc.yaml or .sops.yaml, and never
# prints a real secret (every value below is a synthetic test fixture,
# the same non-credential posture tests/**'s fixture literals already
# use elsewhere in this repo — not a real credential to protect).
set -euo pipefail

command -v sops >/dev/null || { echo "sops not found — see infra/secrets/README.md" >&2; exit 1; }
command -v age-keygen >/dev/null || { echo "age-keygen not found" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_EXPORT="$SCRIPT_DIR/env-export.sh"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

age-keygen -o key.txt 2>/dev/null
PUB="$(grep 'public key:' key.txt | awk '{print $NF}')"

# A representative bundle: a non-AETHER_* documentation field (the real
# shape dev.enc.yaml itself has — "this bundle is a ... demo, not a
# real secret" is exactly a value containing spaces on a non-AETHER_*
# key), a simple AETHER_* value, one with spaces, and one with an
# embedded single quote — the three failure modes this fix targets.
cat > plain.yaml <<'EOF'
placeholder_note: this bundle is a scratch test fixture, not a real secret
AETHER_TEST_SIMPLE: simplevalue
AETHER_TEST_WITH_SPACES: value with spaces
AETHER_TEST_WITH_QUOTE: value with a ' quote
NOT_AN_AETHER_VAR: should never be exported
EOF
sops --encrypt --age "$PUB" plain.yaml > bundle.enc.yaml

echo "== Step 1: only AETHER_* keys are exported =="
export SOPS_AGE_KEY_FILE="$WORKDIR/key.txt"
OUTPUT="$("$ENV_EXPORT" bundle.enc.yaml)"

if echo "$OUTPUT" | grep -q 'placeholder_note'; then
  echo "FAIL: non-AETHER_* key 'placeholder_note' leaked into the output" >&2
  exit 1
fi
if echo "$OUTPUT" | grep -q 'NOT_AN_AETHER_VAR'; then
  echo "FAIL: non-AETHER_* key 'NOT_AN_AETHER_VAR' leaked into the output" >&2
  exit 1
fi
EXPORT_COUNT="$(echo "$OUTPUT" | grep -c '^export AETHER_')"
if [ "$EXPORT_COUNT" -ne 3 ]; then
  echo "FAIL: expected exactly 3 'export AETHER_*' lines, got $EXPORT_COUNT" >&2
  exit 1
fi
echo "  ok — exactly the 3 AETHER_* keys, nothing else"

echo "== Step 2: output is genuinely eval-safe and round-trips values with spaces/quotes =="
# The actual regression: the old implementation didn't just look wrong,
# it broke eval outright (a bareword mid-value was parsed as a second
# shell command). Proving this fix works means actually eval-ing the
# output in a subshell and checking the *resulting* variable values,
# not just eyeballing the printed lines.
(
  eval "$OUTPUT"
  [ "$AETHER_TEST_SIMPLE" = "simplevalue" ] || { echo "FAIL: AETHER_TEST_SIMPLE mismatch" >&2; exit 1; }
  [ "$AETHER_TEST_WITH_SPACES" = "value with spaces" ] || { echo "FAIL: AETHER_TEST_WITH_SPACES mismatch" >&2; exit 1; }
  [ "$AETHER_TEST_WITH_QUOTE" = "value with a ' quote" ] || { echo "FAIL: AETHER_TEST_WITH_QUOTE mismatch" >&2; exit 1; }
  [ -z "${placeholder_note:-}" ] || { echo "FAIL: placeholder_note somehow got set" >&2; exit 1; }
  [ -z "${NOT_AN_AETHER_VAR:-}" ] || { echo "FAIL: NOT_AN_AETHER_VAR somehow got set" >&2; exit 1; }
)
echo "  ok — eval succeeded, every value round-tripped exactly, no stray var leaked"

echo "PASS: secrets-env exports only AETHER_* keys, correctly shell-quoted"
