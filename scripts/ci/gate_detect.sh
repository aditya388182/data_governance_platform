#!/usr/bin/env bash
# scripts/ci/gate_detect.sh <RELEVANT_REGEX> [BASE_REF]
# Shared relevance detector for ge-gate and pii-gate (same contract as
# `schema_gate.sh detect`): prints run=true|false; a detection ERROR exits
# non-zero so the job fails instead of quietly skipping the gate.
set -euo pipefail
RE="${1:?usage: gate_detect.sh <regex> [BASE_REF]}"
BASE="${2:-origin/main}"
cd "$(git rev-parse --show-toplevel)"
MB="$(git merge-base "$BASE" HEAD 2>/dev/null)" || {
  echo "::error::cannot compute merge-base between '$BASE' and HEAD — FAILING CLOSED" >&2; exit 1; }
FILES="$({ git diff --name-only "$MB"; git ls-files --others --exclude-standard; } | sort -u)"
echo "changed files vs $BASE (merge-base ${MB:0:12}):" >&2
if [ -n "$FILES" ]; then printf '%s\n' "$FILES" | sed 's/^/  /' >&2; else echo "  (none)" >&2; fi
if printf '%s\n' "$FILES" | grep -Eq "$RE"; then echo "run=true"; else echo "run=false"; fi
