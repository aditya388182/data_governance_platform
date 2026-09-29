#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("RUST_LOG", "error")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deltalake.exceptions import TableNotFoundError  # noqa: E402

from govlib import lake  # noqa: E402
from govlib.contracts import load_registry  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--dataset", default="payments.transactions")
a = ap.parse_args()
d, so = load_registry()[a.dataset], lake.storage_options()
try:
    q = lake.read_table(lake.uri(lake.quarantine_path(a.dataset)), storage_options=so).to_pylist()
except TableNotFoundError:
    print(f"{lake.quarantine_path(a.dataset)}: no quarantine table yet (nothing ever failed)")
    sys.exit(0)
key = "transaction_id" if "transaction_id" in q[0] else "customer_id"
print(f"{lake.quarantine_path(a.dataset)} — {len(q)} row(s)   owner: {q[0]['owner']}   suite: {q[0]['suite']}")
print(f"{key:38s} {'amount':>7s} {'cur':4s} {'src v':>5s} {'checkpoint':10s} expectation(s) failed")
for r in sorted(q, key=lambda r: (r["run_ts"], r[key])):
    print(f"{r[key]:38s} {str(r.get('amount_minor', '')):>7s} {str(r.get('currency', '')):4s} {r['source_version']:>5d} "
          f"{r['checkpoint_id'][:8]:10s} {'; '.join(e.split('(')[0].replace('expect_column_', '') + '(' + e.split('(')[1] for e in r['expectation'])}")
src = set(lake.read_table(lake.uri(d.delta_path), [key], so).column(0).to_pylist())
print(f"read-only proof: {sum(r[key] in src for r in q)}/{len(q)} quarantined rows are still in the live source {d.delta_path}")
