#!/usr/bin/env bash
# detect|check [BASE_REF] — the IaC gate (required check `terraform-plan`).
#   detect: run=true when terraform/ or anything it reads/runs on changed. A mode change in
#           contracts/registry.yml IS an infrastructure change: Terraform applies it to the registry.
#   check:  fmt -check -> module tests (offline, mocked provider) -> LocalStack backend
#           bootstrap -> init -> per workspace: validate + plan -detailed-exitcode.
#           Plan exit 0 = no changes, 2 = changes (the point of an infra PR: success here),
#           1 = error -> FAIL CLOSED. Writes $TF_GATE_OUT/comment.md for the PR comment.
# validate runs INSIDE each workspace: in `default` the module's tag validation rejects
# environment = "default", so a validate-before-select gate would be red on every PR.
set -euo pipefail
CMD="${1:?usage: terraform_gate.sh detect|check [BASE_REF]}"; BASE="${2:-origin/main}"
RE='^(terraform/|contracts/registry\.yml$|scripts/ci/terraform_gate\.sh$|scripts/tf_localstack\.py$|scripts/ci/gate_detect\.sh$|infra/docker-compose\.yml$|\.github/workflows/terraform_(gate|apply)\.yml$)'
case "$CMD" in
  detect) scripts/ci/gate_detect.sh "$RE" "$BASE" ;;
  check)
    TF="${TF_BIN:-terraform}"; PY="${PYTHON:-python}"; WS="${TF_WORKSPACES:-dev staging prod}"
    OUT="${TF_GATE_OUT:-$(mktemp -d)}"; mkdir -p "$OUT"
    cd terraform
    echo "== fmt -check"; "$TF" fmt -check -recursive -diff
    echo "== module tests (offline: mocked AWS provider)"
    (cd modules/encrypted_bucket && "$TF" init -backend=false -input=false -no-color >/dev/null && "$TF" test -no-color)
    echo "== backend (LocalStack S3 + DynamoDB lock table)"
    "$PY" ../scripts/tf_localstack.py bootstrap || { echo "::error::LocalStack backend unavailable — FAILING CLOSED"; exit 1; }
    "$TF" init -input=false -no-color
    { echo "<!-- terraform-plan -->"; echo "### terraform-plan"; echo; echo "| workspace | plan |"; echo "|---|---|"; } > "$OUT/comment.md"
    : > "$OUT/details.md"; failed=0
    for ws in $WS; do
      "$TF" workspace select -or-create "$ws" >/dev/null
      "$TF" validate -no-color
      set +e
      "$TF" plan -detailed-exitcode -input=false -no-color -lock-timeout=120s > "$OUT/$ws.plan.txt" 2>&1; rc=$?
      set -e
      case $rc in
        0) res="no changes" ;;
        2) res="$(grep -E '^Plan:' "$OUT/$ws.plan.txt" || echo 'changes')" ;;
        *) res="**PLAN FAILED (exit $rc)** — failing closed"; failed=1 ;;
      esac
      echo "== $ws: exit $rc — $res"
      [ "$rc" = 0 ] || [ "$rc" = 2 ] || tail -n 25 "$OUT/$ws.plan.txt"
      echo "| \`$ws\` | $res |" >> "$OUT/comment.md"
      { echo; echo "<details><summary><code>$ws</code> plan</summary>"; echo; echo '```'
        sed -n '/will perform the following actions\|No changes\|Error/,$p' "$OUT/$ws.plan.txt" | head -c 18000
        echo '```'; echo '</details>'; } >> "$OUT/details.md"
    done
    cat "$OUT/details.md" >> "$OUT/comment.md"
    printf '\n_Planned against a fresh, hermetic LocalStack + Kafka + Schema Registry started from infra/docker-compose.yml. Apply runs only on main, behind the `production` environment approval._\n' >> "$OUT/comment.md"
    [ "$failed" = 0 ] || { echo "::error::a workspace plan failed — FAILING CLOSED"; exit 1; }
    echo "terraform-plan: all workspaces planned"
    ;;
  *) echo "usage: terraform_gate.sh detect|check [BASE_REF]" >&2; exit 64 ;;
esac
