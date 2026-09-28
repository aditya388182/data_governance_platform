#!/usr/bin/env bash
# consumer impact analysis (required check `impact-bot`).
#   check: extracts main-side files from the MERGE-BASE (for the field diff) and the
#   lineage catalog from ACK_REF (default origin/main — the only place ACKs count),
#   then runs scripts/ci/impact_bot.py. A missing ACK_REF fails closed.
set -euo pipefail
CMD="${1:?usage: impact_gate.sh detect|check [BASE_REF]}"; BASE="${2:-origin/main}"
cd "$(git rev-parse --show-toplevel)"
RE='^(contracts/schemas/|contracts/registry\.yml$|contracts/lineage/|consumers/|CODEOWNERS$|govlib/|scripts/lineage_scan\.py$|scripts/ci/impact_|scripts/ci/gate_detect\.sh$|tests/impact/|\.github/workflows/impact_bot\.yml$|requirements-ci\.txt$)'
case "$CMD" in
  detect) scripts/ci/gate_detect.sh "$RE" "$BASE" ;;
  check)
    ACK_REF="${ACK_REF:-origin/main}"
    MB="$(git merge-base "$BASE" HEAD 2>/dev/null)" || { echo "::error::cannot compute merge-base with '$BASE' — FAILING CLOSED"; exit 2; }
    git rev-parse --verify --quiet "$ACK_REF^{commit}" >/dev/null || { echo "::error::cannot read ACKs from '$ACK_REF' — FAILING CLOSED"; exit 2; }
    WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
    mkdir -p "$WORK/base" "$WORK/acks"
    for f in $(git ls-tree -r --name-only "$MB" -- contracts/); do
      mkdir -p "$WORK/base/$(dirname "$f")"; git show "$MB:$f" > "$WORK/base/$f"
    done
    for f in $(git ls-tree -r --name-only "$ACK_REF" -- contracts/lineage/); do
      mkdir -p "$WORK/acks/$(dirname "$f")"; git show "$ACK_REF:$f" > "$WORK/acks/$f"
    done
    # Output goes OUTSIDE the repo (never committable): $RUNNER_TEMP in CI, the temp dir locally.
    OUT="${IMPACT_OUT:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/impact-bot}"
    echo "field diff vs merge-base ${MB:0:12}; ACKs read from $ACK_REF ($(git rev-parse --short "$ACK_REF")); PR #${PR_NUMBER:-0}; output -> $OUT"
    "${PYTHON:-python3}" scripts/ci/impact_bot.py --base-root "$WORK/base" --acks-root "$WORK/acks" \
        --pr "${PR_NUMBER:-0}" --out "$OUT" ;;
  *) echo "usage: impact_gate.sh detect|check [BASE_REF]" >&2; exit 64 ;;
esac
