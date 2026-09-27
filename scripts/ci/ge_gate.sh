#!/usr/bin/env bash
# scripts/ci/ge_gate.sh detect|check [BASE_REF] — the semantic gate (required check `ge-gate`).
# Relevance: contracts, suites, fixtures, the shared libraries, or the gate itself.
set -euo pipefail
CMD="${1:?usage: ge_gate.sh detect|check [BASE_REF]}"; BASE="${2:-origin/main}"
cd "$(git rev-parse --show-toplevel)"
RE='^(contracts/schemas/|contracts/registry\.yml$|contracts/pii_catalog\.yml$|ge/|fixtures/|govlib/|scripts/ci/ge_gate\.|scripts/ci/gate_detect\.sh$|tests/semantic/|\.github/workflows/ge_gate\.yml$|requirements-ci-semantic\.txt$)'
case "$CMD" in
  detect) scripts/ci/gate_detect.sh "$RE" "$BASE" ;;
  check)  "${PYTHON:-python3}" scripts/ci/ge_gate.py ;;
  *) echo "usage: ge_gate.sh detect|check [BASE_REF]" >&2; exit 64 ;;
esac
