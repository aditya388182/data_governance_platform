#!/usr/bin/env bash
set -euo pipefail

[ "$#" -ge 1 ] || { echo "usage: $0 <required-check> [...]" >&2; exit 64; }
command -v gh >/dev/null || { echo "gh CLI not found" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }

BRANCH="${BRANCH:-main}"
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

checks_json=$(printf '%s\n' "$@" | jq -R . | jq -sc 'map({context: .})')
contexts_json=$(printf '%s\n' "$@" | jq -R . | jq -sc '.')

common='{
  enforce_admins: true,
  required_pull_request_reviews: {required_approving_review_count: 0, dismiss_stale_reviews: true, require_code_owner_reviews: false},
  restrictions: null,
  required_linear_history: false,
  allow_force_pushes: false,
  allow_deletions: false,
  required_conversation_resolution: false
}'
jq -n --argjson c "$checks_json"   "$common + {required_status_checks: {strict: true, checks: \$c}}"   > "$TMP/body_checks.json"
jq -n --argjson c "$contexts_json" "$common + {required_status_checks: {strict: true, contexts: \$c}}" > "$TMP/body_contexts.json"

put() { gh api -X PUT "repos/$REPO/branches/$BRANCH/protection" --input "$1" > /dev/null 2> "$TMP/err"; }

echo "Protecting $REPO:$BRANCH with required checks: $*"
# Primary: the 'contexts' form — the only form that validates against GitHub's
# published OpenAPI schema for this endpoint (checked Sept 2026). Fallback: 'checks'.
if ! put "$TMP/body_contexts.json"; then
  if grep -qiE 'upgrade to github pro|make this repository public' "$TMP/err"; then
    echo "ERROR: branch protection is not available for this private repo on your plan." >&2
    echo "  Fix A: gh repo edit $REPO --visibility public --accept-visibility-change-consequences" >&2
    echo "  Fix B: activate GitHub Pro via the Student Developer Pack, then re-run this script." >&2
    exit 1
  fi
  echo "note: 'contexts' form rejected ($(tr '\n' ' ' < "$TMP/err")); retrying with the 'checks' form"
  put "$TMP/body_checks.json" || { cat "$TMP/err" >&2; exit 1; }
fi

# ---- verify: read it back and assert every property we care about -------------
gh api "repos/$REPO/branches/$BRANCH/protection" > "$TMP/got.json"
jq '{required_checks: .required_status_checks.contexts,
     strict: .required_status_checks.strict,
     enforce_admins: .enforce_admins.enabled,
     approvals_required: .required_pull_request_reviews.required_approving_review_count,
     force_pushes_allowed: .allow_force_pushes.enabled,
     deletions_allowed: .allow_deletions.enabled}' "$TMP/got.json"

ok=1
for c in "$@"; do
  jq -e --arg c "$c" '.required_status_checks.contexts | index($c) != null' "$TMP/got.json" > /dev/null \
    || { echo "VERIFY FAILED: '$c' is not a required check" >&2; ok=0; }
done
jq -e '.required_status_checks.strict == true'        "$TMP/got.json" > /dev/null || { echo "VERIFY FAILED: strict" >&2; ok=0; }
jq -e '.enforce_admins.enabled == true'                "$TMP/got.json" > /dev/null || { echo "VERIFY FAILED: enforce_admins" >&2; ok=0; }
jq -e '.allow_force_pushes.enabled == false'           "$TMP/got.json" > /dev/null || { echo "VERIFY FAILED: force pushes allowed" >&2; ok=0; }
[ "$ok" = 1 ] && echo "branch protection VERIFIED on $REPO:$BRANCH" || exit 1
