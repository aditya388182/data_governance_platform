#!/usr/bin/env python3
import os
import sys

os.environ.setdefault("RUST_LOG", "error")  # quiet delta-rs WARN lines
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from crypto import policy  # noqa: E402
from crypto.key_store import KeyStore  # noqa: E402
from govlib import lake  # noqa: E402
from govlib.contracts import load_registry, schema_fields  # noqa: E402
from govlib.keys import load_keys  # noqa: E402
from scripts.seed_prod_tables import arrow_schema  # noqa: E402


def inject(n_negative: int = 4, n_bad_currency: int = 3, seed: int = 7) -> list[str]:
    reg, keys, so = load_registry(), load_keys(), lake.storage_options()
    d = reg["payments.transactions"]
    path = lake.uri(d.delta_path)
    dt = DeltaTable(path, storage_options=so)
    before = dt.version()
    subjects = sorted(set(lake.read_table(dt, ["customer_id"]).column(0).to_pylist()))
    rng = np.random.default_rng(seed)
    n = n_negative + n_bad_currency
    now = int(pd.Timestamp("2026-09-01", tz="UTC").timestamp() * 1000)
    rows = pd.DataFrame({
        "transaction_id": [str(uuid.UUID(bytes=rng.bytes(16), version=4)) for _ in range(n)],
        "merchant_id": [f"M-{m:05d}" for m in rng.integers(1, 301, n)],
        "customer_id": rng.choice(subjects, n).tolist(),
        "amount_minor": [-int(x) for x in rng.integers(100, 9_999, n_negative)] + rng.integers(100, 9_999, n_bad_currency).tolist(),
        "currency": ["USD"] * n_negative + ["ZZ"] * n_bad_currency,
        "event_type": ["CAPTURED"] * n, "event_ts": [now] * n, "created_at": [now] * n, "updated_at": [None] * n,
        "risk_score": [None] * n, "receipt_email": [None] * n})
    ks = KeyStore(lake.uri(lake.key_store_path()), keys["kek"], so)
    rows = policy.protect("transactions", rows, ks, keys)
    fields = schema_fields(d.schema)
    data = pa.Table.from_pydict({f.name: rows[f.name].astype(object).where(rows[f.name].notna(), None).tolist()
                                 for f in fields}, schema=arrow_schema(fields))
    write_deltalake(path, data, mode="append", storage_options=so)
    after = DeltaTable(path, storage_options=so).version()
    print(f"appended {n} bad rows to {d.delta_path} (Delta v{before} -> v{after}), bypassing every CI gate:")
    for tid, amt, cur in zip(rows.transaction_id, rows.amount_minor, rows.currency):
        print(f"  {tid}  amount_minor={amt:>6}  currency={cur}")
    return rows.transaction_id.tolist()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7,
                    help="default 7 = the rows in the Day 4 plan; use another seed to re-run the drill with NEW rows "
                         "(identical rows are already quarantined and would not be copied again)")
    inject(seed=ap.parse_args().seed)
