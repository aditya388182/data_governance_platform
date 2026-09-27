# Day 2 drills — reproduce every red

| PR | Branch | What changes | Expected checks | Proves | Screenshot |
|---|---|---|---|---|---|
| bad fixture values | `bad-fixture-values` | fixture regenerated with `--inject-bad` (5 Avro-valid, semantically wrong rows) | schema-gate ✓ (no-op) · **ge-gate ✗** · pii-gate ✓ (no-op) | valid shape, invalid value | `05_ge_gate_red.png` |
| receipt_email (red) | `add-receipt-email` | `pr_receipt_email.avsc` → schema; catalog untouched | schema-gate ✓ · ge-gate ✓ (stale-fixture warning) · **pii-gate ✗** | unclassified PII is default-deny | `06_pii_gate_red.png` |
| receipt_email (fixed, same PR) | `add-receipt-email` | + catalog entry + regenerated fixture | all three ✓ | three orthogonal authorities on one diff | `06b_gates_composing.png` |

```bash
# bad fixture values (from an up-to-date main)
git switch -c bad-fixture-values
python scripts/make_fixture.py --dataset payments.transactions --inject-bad
git commit -am "fixtures: producer sample with 5 Avro-valid but wrong rows (expected: ge-gate RED)"

# receipt_email — red commit
git switch main && git pull --ff-only && git switch -c add-receipt-email
cp drills/day2/pr_receipt_email.avsc contracts/schemas/payments_transactions.avsc
git commit -am "contracts: add nullable receipt_email (expected: pii-gate RED)"

# receipt_email — fix commit (same branch)
perl -0pi -e 's/(  transactions\.merchant_id:[^\n]*\n)/$1  transactions.receipt_email: {class: DIRECT_IDENTIFIER, treatment: ENCRYPT_AESGCM}\n/' contracts/pii_catalog.yml
python scripts/make_fixture.py --dataset payments.transactions
git commit -am "pii: classify receipt_email DIRECT/ENCRYPT_AESGCM; regenerate fixture"
```
