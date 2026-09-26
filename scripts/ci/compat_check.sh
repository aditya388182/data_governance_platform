#!/usr/bin/env bash
# scripts/ci/compat_check.sh SUBJECT SCHEMA_FILE MODE [HISTORY_DIR]
#
# Judges ONE proposed schema against a hermetic Schema Registry.
#
#   1. Probe the registry (fail-closed).
#   2. Replay HISTORY_DIR/*.avsc (oldest -> newest, i.e. every version of the
#      schema file on main) into a throwaway subject with compatibility NONE.
#      Without this replay an empty CI registry holds ONE version and
#      BACKWARD_TRANSITIVE silently collapses to BACKWARD.
#   3. Set the subject to the contract's declared MODE and read it back
#      (trust but verify: a mode that did not apply is an infra failure).
#   4. Register the proposed schema. The registry applies MODE across ALL
#      prior versions for *_TRANSITIVE modes. 200 = compatible,
#      409 = breaking, 422 = invalid schema, anything else = FAIL CLOSED.
#   5. On 409, print a per-version diagnosis (which historical version breaks).
#   6. Delete the throwaway subject (best-effort; never changes the verdict).
#
# Exit codes: 0 compatible | 1 breaking or invalid | 2 registry unavailable/error (FAILING CLOSED)
# Subjects are namespaced with GATE_SUBJECT_PREFIX (default gate.<pid>.<epoch>.)
# so a local run can never touch a real subject.
set -euo pipefail

SUBJECT_IN="${1:?usage: compat_check.sh SUBJECT SCHEMA_FILE MODE [HISTORY_DIR]}"
SCHEMA_FILE="${2:?schema file required}"
MODE="${3:?compatibility mode required}"
HISTORY_DIR="${4:-}"
REG="${REG:-http://localhost:18081}"
PREFIX="${GATE_SUBJECT_PREFIX:-gate.$$.$(date +%s).}"
SUBJECT="${PREFIX}${SUBJECT_IN}"
CT='Content-Type: application/vnd.schemaregistry.v1+json'
HERE="$(cd "$(dirname "$0")" && pwd)"

TMP="$(mktemp -d)"
# shellcheck disable=SC2329  # invoked via trap
cleanup() {
  curl -s -o /dev/null --max-time 5 -X DELETE "$REG/subjects/$SUBJECT" 2>/dev/null || true
  curl -s -o /dev/null --max-time 5 -X DELETE "$REG/subjects/$SUBJECT?permanent=true" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

fail_closed() { echo "::error title=schema-gate::$1 — FAILING CLOSED"; exit 2; }

# http METHOD URL [BODY_FILE] -> sets HTTP_CODE, response body in $TMP/body
http() {
  local method="$1" url="$2" data="${3:-}"
  if [ -n "$data" ]; then
    HTTP_CODE=$(curl -sS -o "$TMP/body" -w '%{http_code}' --max-time 30 -X "$method" \
      -H "$CT" --data-binary "@$data" "$url" 2>"$TMP/curl_err") || HTTP_CODE="000"
  else
    HTTP_CODE=$(curl -sS -o "$TMP/body" -w '%{http_code}' --max-time 30 -X "$method" \
      -H "$CT" "$url" 2>"$TMP/curl_err") || HTTP_CODE="000"
  fi
}

payload() {  # schema file -> {"schema": "<file contents as string>"}
  jq -n --rawfile s "$1" '{schema: $s}' > "$2"
}

# ---- 0. the proposed file must at least be JSON --------------------------------
if ! jq -e . "$SCHEMA_FILE" > /dev/null 2>&1; then
  echo "::error title=schema-gate::INVALID SCHEMA — $SCHEMA_FILE is not valid JSON"
  exit 1
fi

# ---- 1. probe ------------------------------------------------------------------
"$HERE/wait_fail_closed.sh" "$REG" || exit 2

# ---- 2. replay history under NONE ---------------------------------------------
N_HIST=0
if [ -n "$HISTORY_DIR" ] && [ -d "$HISTORY_DIR" ]; then
  printf '{"compatibility":"NONE"}' > "$TMP/none.json"
  http PUT "$REG/config/$SUBJECT" "$TMP/none.json"
  [ "$HTTP_CODE" = "200" ] || fail_closed "could not set NONE for history replay (HTTP $HTTP_CODE: $(cat "$TMP/body" 2>/dev/null))"
  for f in "$HISTORY_DIR"/*.avsc; do
    [ -e "$f" ] || continue
    payload "$f" "$TMP/hist.json"
    http POST "$REG/subjects/$SUBJECT/versions" "$TMP/hist.json"
    if [ "$HTTP_CODE" != "200" ]; then
      fail_closed "history replay failed on $(basename "$f") (HTTP $HTTP_CODE: $(cat "$TMP/body" 2>/dev/null))"
    fi
    N_HIST=$(( N_HIST + 1 ))
  done
fi

# ---- 3. apply and VERIFY the declared mode -------------------------------------
printf '{"compatibility":"%s"}' "$MODE" > "$TMP/mode.json"
http PUT "$REG/config/$SUBJECT" "$TMP/mode.json"
[ "$HTTP_CODE" = "200" ] || fail_closed "could not set compatibility $MODE (HTTP $HTTP_CODE: $(cat "$TMP/body" 2>/dev/null))"
http GET "$REG/config/$SUBJECT"
applied=$(jq -r '.compatibilityLevel // .compatibility // empty' "$TMP/body" 2>/dev/null || true)
[ "$HTTP_CODE" = "200" ] && [ "$applied" = "$MODE" ] \
  || fail_closed "registry reports compatibility '${applied:-?}' after setting '$MODE'"

# ---- 4. the verdict ------------------------------------------------------------
payload "$SCHEMA_FILE" "$TMP/proposed.json"
http POST "$REG/subjects/$SUBJECT/versions" "$TMP/proposed.json"
case "$HTTP_CODE" in
  200)
    echo "COMPATIBLE: $SUBJECT_IN ($SCHEMA_FILE) under $MODE against $N_HIST prior version(s) replayed from main"
    exit 0 ;;
  409)
    echo "::error title=schema-gate::BREAKING SCHEMA CHANGE — $SUBJECT_IN is incompatible under $MODE (checked against $N_HIST prior version(s)); merge blocked"
    echo "registry says: $(jq -r '.message // .' "$TMP/body" 2>/dev/null || cat "$TMP/body")"
    # ---- 5. per-version diagnosis (diagnostic only) ----
    http GET "$REG/subjects/$SUBJECT/versions"
    if [ "$HTTP_CODE" = "200" ]; then
      for v in $(jq -r '.[]' "$TMP/body" 2>/dev/null); do
        http POST "$REG/compatibility/subjects/$SUBJECT/versions/$v?verbose=true" "$TMP/proposed.json"
        if [ "$HTTP_CODE" = "200" ]; then
          if [ "$(jq -r '.is_compatible' "$TMP/body")" = "true" ]; then
            echo "  vs version $v (replayed from main): compatible"
          else
            echo "  vs version $v (replayed from main): INCOMPATIBLE — $(jq -r '(.messages // []) | join("; ")' "$TMP/body" | cut -c1-400)"
          fi
        fi
      done
    fi
    exit 1 ;;
  422)
    echo "::error title=schema-gate::INVALID SCHEMA — registry rejected $SCHEMA_FILE: $(jq -r '.message // .' "$TMP/body" 2>/dev/null)"
    exit 1 ;;
  *)
    fail_closed "unexpected registry response HTTP $HTTP_CODE ($(cat "$TMP/curl_err" "$TMP/body" 2>/dev/null | tr '\n' ' ' | cut -c1-300))" ;;
esac
