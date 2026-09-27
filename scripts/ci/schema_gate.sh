#!/usr/bin/env bash
set -euo pipefail

CMD="${1:?usage: schema_gate.sh detect|check [BASE_REF]}"
BASE="${2:-origin/main}"
REG="${REG:-http://localhost:18081}"; export REG
PY="${PYTHON:-python3}"

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

RELEVANT_RE='^(contracts/schemas/|contracts/registry\.yml$|scripts/ci/|infra/ci/|tests/gate/|\.github/workflows/schema_gate\.yml$|requirements-ci\.txt$)'

merge_base() {
  git merge-base "$BASE" HEAD 2>/dev/null || {
    echo "::error title=schema-gate::cannot compute merge-base between '$BASE' and HEAD (shallow clone or bad ref?) — FAILING CLOSED" >&2
    exit 1
  }
}

changed_files() {  # merge-base vs working tree, plus untracked files
  local mb="$1"
  { git diff --name-only "$mb"; git ls-files --others --exclude-standard; } | sort -u
}

case "$CMD" in
  detect)
    MB="$(merge_base)"
    FILES="$(changed_files "$MB")"   # captured first: no `producer | grep -q` + pipefail SIGPIPE trap
    echo "changed files vs $BASE (merge-base ${MB:0:12}):" >&2
    if [ -n "$FILES" ]; then printf '%s\n' "$FILES" | sed 's/^/  /' >&2; else echo "  (none)" >&2; fi
    if printf '%s\n' "$FILES" | grep -Eq "$RELEVANT_RE"; then echo "run=true"; else echo "run=false"; fi
    ;;

  check)
    MB="$(merge_base)"
    WORK="$(mktemp -d)"
    trap 'rm -rf "$WORK"' EXIT
    changed_files "$MB" > "$WORK/changed.txt"

    if git cat-file -e "$MB:contracts/registry.yml" 2>/dev/null; then
      git show "$MB:contracts/registry.yml" > "$WORK/base_registry.yml"
    else
      : > "$WORK/base_registry.yml"   # first PR introducing the registry
    fi

    "$PY" scripts/ci/contract_tool.py lint
    "$PY" scripts/ci/contract_tool.py plan --base-registry "$WORK/base_registry.yml" \
          --changed-files "$WORK/changed.txt" > "$WORK/plan.tsv"

    if [ ! -s "$WORK/plan.tsv" ]; then
      echo "schema-gate: no governed dataset affected — PASS"
      exit 0
    fi

    SUMMARY="$WORK/summary.md"
    printf '### schema-gate\n\n| dataset | subject | mode | versions replayed | reason | verdict |\n|---|---|---|---|---|---|\n' > "$SUMMARY"
    overall=0
    while IFS="$(printf '\t')" read -r name subject schema hist_path mode reason; do
      echo ""
      echo "=== $name ($subject) — mode $mode — $reason"
      hdir="$WORK/hist/$name"; mkdir -p "$hdir"
      n=0
      for sha in $(git log --reverse --format=%H "$MB" -- "$hist_path"); do
        if git cat-file -e "$sha:$hist_path" 2>/dev/null; then
          n=$(( n + 1 ))
          git show "$sha:$hist_path" > "$hdir/$(printf '%04d' "$n").avsc"
          echo "  history v$n <- ${sha:0:12} $(git log -1 --format=%s "$sha" | cut -c1-60)"
        fi
      done
      [ "$n" -gt 0 ] || echo "  no history on main: NEW SUBJECT (registration validates the schema only)"
      set +e
      scripts/ci/compat_check.sh "$subject" "$schema" "$mode" "$hdir"
      rc=$?
      set -e
      case "$rc" in
        0) verdict="✅ compatible" ;;
        1) verdict="❌ breaking/invalid"; overall=1 ;;
        *) verdict="⛔ registry unavailable (fail-closed)"; overall=2 ;;
      esac
      # shellcheck disable=SC2016  # backticks are markdown, not command substitution
      printf '| %s | `%s` | %s | %s | %s | %s |\n' "$name" "$subject" "$mode" "$n" "$reason" "$verdict" >> "$SUMMARY"
      [ "$rc" -ge 2 ] && break   # registry is down: stop, don't pretend to check the rest
    done < "$WORK/plan.tsv"

    echo ""
    cat "$SUMMARY"
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat "$SUMMARY" >> "$GITHUB_STEP_SUMMARY"; fi
    case "$overall" in
      0) echo "schema-gate: PASS" ;;
      1) echo "schema-gate: FAIL — breaking or invalid schema change" ;;
      *) echo "schema-gate: FAIL — registry unavailable, FAILING CLOSED" ;;
    esac
    exit "$overall"
    ;;

  *)
    echo "usage: schema_gate.sh detect|check [BASE_REF]" >&2; exit 64 ;;
esac
