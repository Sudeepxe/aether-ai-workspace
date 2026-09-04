#!/usr/bin/env bash
# Decrypts a SOPS+age secrets bundle and emits shell-quoted
# `export AETHER_*=value` lines, safe for `eval "$(...)"` (ADR-7.5).
#
# Replaces a previous inline Makefile one-liner
# (`sops -d --output-type dotenv | sed 's/^/export /'`) that had two
# real bugs, found from a genuine `eval "$(make secrets-env)"` failure:
# it emitted *every* top-level key verbatim, including non-`AETHER_*`
# documentation/placeholder fields that were never meant to reach the
# shell; and it never quoted values at all, so any value containing a
# space (or any other shell metacharacter) broke `eval` outright — only
# the first word attached to the `export`, the rest got parsed as a
# second, usually-invalid shell command.
#
# Fix: decrypt to JSON (an unambiguous, structured format) instead of
# dotenv-then-regex, filter to keys that actually look like
# `AETHER_*` env vars, and let `jq`'s `@sh` filter do the shell-quoting
# — the one job sed/grep can't do reliably for arbitrary values.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <path-to-sops-encrypted-bundle.yaml>" >&2
  exit 64
fi
BUNDLE="$1"

command -v sops >/dev/null || { echo "sops not found — see infra/secrets/README.md" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }

sops -d --input-type yaml --output-type json "$BUNDLE" | jq -r '
  to_entries[]
  | select(.key | test("^AETHER_[A-Za-z0-9_]*$"))
  | "export " + .key + "=" + (.value | tostring | @sh)
'
