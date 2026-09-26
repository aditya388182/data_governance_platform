#!/usr/bin/env bash
# scripts/gate_registry.sh up|down|status — the hermetic gate registry (:18081),
# the same compose file CI uses. `up` blocks until ready (fail-closed probe).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
F=infra/ci/registry-compose.yml
case "${1:-status}" in
  up)     docker compose -f "$F" up -d && scripts/ci/wait_fail_closed.sh http://localhost:18081 ;;
  down)   docker compose -f "$F" down -v ;;
  status) docker compose -f "$F" ps; curl -s --max-time 3 http://localhost:18081/subjects && echo ;;
  *) echo "usage: $0 up|down|status" >&2; exit 64 ;;
esac
