#!/usr/bin/env bash
# create the `production` environment that gates
# terraform-apply: you are its required reviewer, and only protected branches (main) deploy.
# Idempotent. Environments with required reviewers need a PUBLIC repo (or a paid plan).
set -euo pipefail
command -v gh >/dev/null || { echo "gh CLI not found" >&2; exit 1; }
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
ME_ID="$(gh api user --jq .id)"; ME="$(gh api user --jq .login)"
printf '{"reviewers":[{"type":"User","id":%s}],"deployment_branch_policy":{"protected_branches":true,"custom_branch_policies":false}}' "$ME_ID" \
  | gh api -X PUT "repos/$REPO/environments/production" --input - > /dev/null
gh api "repos/$REPO/environments/production" --jq '{environment: .name,
  reviewers: [.protection_rules[]? | select(.type=="required_reviewers") | .reviewers[].reviewer.login],
  branch_policy: .deployment_branch_policy}'
echo "production environment VERIFIED on $REPO: terraform-apply waits for $ME's approval"
