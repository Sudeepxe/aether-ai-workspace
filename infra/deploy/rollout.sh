#!/usr/bin/env bash
# Real health-gated rollout + auto-rollback (D10-2/ADR-10.2, S11 #120).
#
# D10-2's real production mechanism is CI-push over SSH: pull new
# digests, `docker compose up -d` with rolling replace, post-deploy
# smoke, auto-rollback to previous digests on failure. No real VPS/SSH
# credentials exist in this environment (an honest, named S11 gap — see
# docs/architecture/prr.md) — this script exercises the exact same
# health-gate-then-rollback DECISION LOGIC against whatever Docker host
# it's run on (a local machine or a CI runner today; a real VPS's
# Docker daemon tomorrow, unchanged). "Deploy" here is `docker run`
# against a single named container, not full `docker compose` rolling
# replace (no second api replica exists in the dev/CI topology to roll
# through) — the mechanism under test is the health-gate-and-rollback
# decision, not the multi-replica choreography around it.
#
# /readyz deliberately always returns HTTP 200 — degraded state lives
# in the JSON body, decoupled from container liveness (see its own
# docstring in http/app.py: "readiness... a stronger claim", separate
# from healthz's "never checks dependencies"). A deploy-smoke check
# that only looks at curl's exit code would treat a fully-degraded
# instance as healthy — this script parses the body, the real
# post-deploy-smoke responsibility the blueprint assigns to the deploy
# pipeline, not to the container's own liveness probe.
set -euo pipefail

usage() {
  echo "Usage: $0 <network> <new-image> <new-env-file> <previous-image> <previous-env-file>" >&2
  exit 64
}

[ $# -eq 5 ] || usage

NETWORK="$1"
NEW_IMAGE="$2"
NEW_ENV_FILE="$3"
PREVIOUS_IMAGE="$4"
PREVIOUS_ENV_FILE="$5"

CONTAINER_NAME="aether-rollout-api"
PORT="${ROLLOUT_PORT:-8000}"
READY_TIMEOUT_SECONDS="${ROLLOUT_READY_TIMEOUT:-30}"

log() { echo "[rollout] $*"; }

deploy_container() {
  local image="$1" env_file="$2"
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker run -d --name "$CONTAINER_NAME" --network "$NETWORK" \
    --env-file "$env_file" -p "${PORT}:8000" "$image" >/dev/null
}

# Polls /readyz and parses the JSON body's "status" field — a bare
# curl -f would pass on any 200, including a degraded one (see header
# comment). Falls back to treating a connection-refused/non-200
# response as "not ready yet" (the process may still be starting).
wait_for_ready() {
  local deadline=$((SECONDS + READY_TIMEOUT_SECONDS))
  while [ "$SECONDS" -lt "$deadline" ]; do
    body="$(curl -sf "http://localhost:${PORT}/readyz" 2>/dev/null || true)"
    if [ -n "$body" ]; then
      status="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('status',''))" "$body" 2>/dev/null || true)"
      if [ "$status" = "ok" ]; then
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}

log "deploying new image: $NEW_IMAGE"
deploy_container "$NEW_IMAGE" "$NEW_ENV_FILE"

if wait_for_ready; then
  log "DEPLOY OK — $NEW_IMAGE is ready and serving"
  exit 0
fi

log "new deploy failed to become ready within ${READY_TIMEOUT_SECONDS}s — rolling back"
docker logs "$CONTAINER_NAME" 2>&1 | tail -n 20 || true

log "rolling back to previous image: $PREVIOUS_IMAGE"
deploy_container "$PREVIOUS_IMAGE" "$PREVIOUS_ENV_FILE"

if wait_for_ready; then
  log "ROLLBACK OK — reverted to $PREVIOUS_IMAGE, which is ready and serving"
  exit 1
fi

log "ROLLBACK FAILED — $PREVIOUS_IMAGE also did not become ready — manual intervention required"
docker logs "$CONTAINER_NAME" 2>&1 | tail -n 20 || true
exit 2
