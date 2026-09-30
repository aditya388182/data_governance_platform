#!/usr/bin/env bash
# scripts/ci/wait_fail_closed.sh [REGISTRY_URL]
#
# Readiness probe for the hermetic Schema Registry. Returns 0 once
# GET <REG>/subjects answers with a JSON array. On timeout it prints
#   ::error title=schema-gate::Registry failed to start — FAILING CLOSED ...
# and exits 1.
#
# FAIL-CLOSED IS A PROPERTY, NOT A PHRASE: there is deliberately no fallback
# of any kind here — no cached verdict, no "warn and continue". A cached
# "compatible" can be stale, and a stale "compatible" is exactly the 3am
# incident this project exists to prevent. Registry unavailable == red build.
#
# Env: REGISTRY_WAIT_TIMEOUT (seconds, default 150), REGISTRY_WAIT_INTERVAL (default 3)
# Portable: bash 3.2+ (macOS default) and bash 5 (Ubuntu / GitHub runners).
set -euo pipefail

REG="${1:-${REG:-http://localhost:18081}}"
TIMEOUT="${REGISTRY_WAIT_TIMEOUT:-150}"
INTERVAL="${REGISTRY_WAIT_INTERVAL:-3}"

start=$(date +%s)
deadline=$(( start + TIMEOUT ))
attempt=0
while true; do
  attempt=$(( attempt + 1 ))
  body=""
  if body=$(curl -sS --max-time 5 "$REG/subjects" 2>/dev/null); then
    case "$body" in
      \[*)
        echo "registry ready at $REG after $(( $(date +%s) - start ))s (attempt $attempt)"
        exit 0 ;;
    esac
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "::error title=schema-gate::Registry failed to start — FAILING CLOSED (no valid answer from $REG/subjects within ${TIMEOUT}s; no cached verdict is ever used)"
    exit 1
  fi
  sleep "$INTERVAL"
done
