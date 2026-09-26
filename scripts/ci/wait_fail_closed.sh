#!/usr/bin/env bash
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
