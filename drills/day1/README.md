# Day 1 drills — reproduce every red (and the green)

Each payload is a complete replacement for `contracts/schemas/payments_transactions.avsc`.
Every diff is ONE line against its base, so the PR shows exactly one cause.

| Payload | Branch | Base | Verdict | What it proves | Screenshot |
|---|---|---|---|---|---|
| `pr_a_add_risk_score.avsc` | `add-risk-score` | v1 | GREEN — merged | nullable field with default is BACKWARD_TRANSITIVE-safe | `01_schema_gate_green.png` |
| `pr_b_rename_currency.avsc` | `rename-currency` | v2 (after A) | RED — closed, branch kept for Day 3 | rename = remove + add-without-default; merge button disabled | `02_schema_gate_red.png` |
| `pr_c_transitive_drop_default.avsc` | `drop-risk-default` | v2 (after A) | RED — closed | compatible with v2, incompatible with v1: only caught because the gate replays history | `02b_transitive_red.png` |
| *(edit to `infra/ci/registry-compose.yml`)* | `test-fail-closed` | main | RED — closed | registry unavailable ⇒ FAILING CLOSED, never a warning | `03_fail_closed_red.png` |
| *(docs only)* | `day1-wrapup` | main | GREEN in seconds — merged | required check still reports on PRs that touch nothing governed | `01b_noop_pass.png` |

## Commands (from repo root, on an up-to-date main)

```bash
# PR A
git switch -c add-risk-score
cp drills/day1/pr_a_add_risk_score.avsc contracts/schemas/payments_transactions.avsc
git commit -am "contracts: add nullable risk_score to payments.transactions"

# PR B (after A is merged and main is pulled)
git switch -c rename-currency
cp drills/day1/pr_b_rename_currency.avsc contracts/schemas/payments_transactions.avsc
git commit -am "contracts: rename currency -> currency_code (expected: BLOCKED)"

# PR C (after A is merged and main is pulled)
git switch -c drop-risk-default
cp drills/day1/pr_c_transitive_drop_default.avsc contracts/schemas/payments_transactions.avsc
git commit -am "contracts: drop risk_score default (expected: BLOCKED by transitivity)"

# PR D — sabotage the hermetic registry's Kafka address (portable in-place edit)
git switch -c test-fail-closed
perl -pi -e 's#PLAINTEXT://gate-kafka:9092#PLAINTEXT://kafka-does-not-exist:9092# if /KAFKASTORE_BOOTSTRAP_SERVERS/' infra/ci/registry-compose.yml
git commit -am "test: sabotage gate registry (expected: FAILING CLOSED)"
```
