#!/usr/bin/env bash
# scripts/setup_branch_protection.sh <required-check> [<required-check> ...]
#
# Makes `main` protected with the given REQUIRED status checks. Idempotent:
# re-run it whenever a gate is added (the full list replaces the old one).
#   Day 1:  scripts/setup_branch_protection.sh schema-gate
#   Day 6:  scripts/setup_branch_protection.sh schema-gate ge-gate pii-gate impact-bot terraform-plan
#
# What it sets, and why (Day 1 corrections to the original plan):
#   * required_status_checks.strict=true + the given contexts. The names must
#     match workflow JOB names byte-for-byte or protection silently binds nothing.
#   * enforce_admins=true: you (repo admin) cannot bypass the gate either.
#   * required_approving_review_count=0 — NOT 1. On a solo account you cannot
#     approve your own PR, so count=1 + enforce_admins=true means nothing can
#     ever merge (PR A would be stuck). PRs are still REQUIRED (no direct pushes
#     to main); the binding constraint is the required checks. A real org adds
#     2 approvals + "Require review from Code Owners" (documented in the README).
#   * restrictions=null is sent explicitly: the API requires the key.
#   * allow_force_pushes=false, allow_deletions=false.
#   * The body is sent as typed JSON via --input. The original plan's `gh api -f`
#     call sends "true"/"1" as strings and omits `restrictions` — it fails
#     validation against GitHub's published OpenAPI schema on 4 counts.
#
# Branch protection on a PRIVATE repo needs GitHub Pro (free with the GitHub
# Student Developer Pack). On GitHub Free, make the repo public.
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
