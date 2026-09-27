#!/usr/bin/env bash
# scripts/ci/pii_gate.sh detect|check [BASE_REF] — the compliance gate (required check `pii-gate`).
# `check` extracts main's registry, catalog and schemas (from the merge-base) so
# the gate can apply main's detection patterns and compare classifications.
set -euo pipefail
CMD="${1:?usage: pii_gate.sh detect|check [BASE_REF]}"; BASE="${2:-origin/main}"
cd "$(git rev-parse --show-toplevel)"
RE='^(contracts/schemas/|contracts/registry\.yml$|contracts/pii_catalog\.yml$|govlib/|scripts/ci/pii_gate\.|scripts/ci/gate_detect\.sh$|tests/pii/|\.github/workflows/pii_gate\.yml$|requirements-ci\.txt$)'
case "$CMD" in
  detect) scripts/ci/gate_detect.sh "$RE" "$BASE" ;;
  check)
    MB="$(git merge-base "$BASE" HEAD 2>/dev/null)" || { echo "::error::cannot compute merge-base with '$BASE' — FAILING CLOSED"; exit 1; }
    WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
    for f in $(git ls-tree -r --name-only "$MB" -- contracts/); do
      mkdir -p "$WORK/base/$(dirname "$f")"
      git show "$MB:$f" > "$WORK/base/$f"
    done
    "${PYTHON:-python3}" scripts/ci/pii_gate.py --base-root "$WORK/base" ;;
  *) echo "usage: pii_gate.sh detect|check [BASE_REF]" >&2; exit 64 ;;
esac
