#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("RUST_LOG", "error")  # quiet delta-rs WARN lines
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crypto import policy, shred  # noqa: E402
from crypto.key_store import KeyStore  # noqa: E402
from govlib import lake  # noqa: E402
from govlib.contracts import load_registry, schema_fields  # noqa: E402
from govlib.fixtures import arrow_type  # noqa: E402
from govlib.keys import load_keys  # noqa: E402
from scripts.make_fixture import (  # noqa: E402
    CUSTOMER_COUNTRIES,
    DAY_MS,
    EPOCH0,
    FIRST,
    LAST,
    PAYMENT_CURRENCIES,
    _conform,
)


def arrow_schema(fields) -> pa.Schema:
    return pa.schema([pa.field(f.name, *arrow_type(f.avro_type)) for f in fields])


def gen_customers(rng, n, fields):
    fi, la = rng.integers(0, len(FIRST), n), rng.integers(0, len(LAST), n)
    created = EPOCH0 - rng.integers(0, 365 * DAY_MS, n)
    upd = created + rng.integers(0, 30 * DAY_MS, n)
    countries = list(CUSTOMER_COUNTRIES)
    w = np.array(list(CUSTOMER_COUNTRIES.values()), dtype=float)
    cols = {
        "customer_id": [f"CUST-{i:06d}" for i in range(1, n + 1)],
        "email": [f"{FIRST[a]}.{LAST[b]}{i}@example.com" for i, (a, b) in enumerate(zip(fi, la), 1)],
        "ssn": [f"{x:03d}-{y:02d}-{z:04d}" for x, y, z in zip(rng.integers(100, 900, n), rng.integers(10, 100, n),
                                                               rng.integers(1000, 10_000, n))],
        "full_name": [f"{FIRST[a].title()} {LAST[b].title()}" for a, b in zip(fi, la)],
        "country": rng.choice(countries, n, p=w / w.sum()).tolist(),
        "created_at": created.tolist(),
        "updated_at": [int(u) if k else None for u, k in zip(upd, rng.random(n) < 0.4)],
    }
    return _conform(cols, fields, n, "customers")


def gen_transactions(rng, n, customers, fields):
    ids, emails = customers["customer_id"], dict(zip(customers["customer_id"], customers["email"]))
    cur = list(PAYMENT_CURRENCIES)
    w = np.array(list(PAYMENT_CURRENCIES.values()), dtype=float)
    currency = rng.choice(cur, n, p=w / w.sum())
    cust = rng.choice(ids, n)
    base = np.where(currency == "JPY", 5_000.0, 4_000.0)
    amount = np.minimum(np.round(rng.lognormal(np.log(base), 1.0)), 50_000_000).astype(np.int64)
    created = EPOCH0 + rng.integers(0, 90 * DAY_MS, n)
    upd = created + rng.integers(0, 3 * DAY_MS, n)
    cols = {
        "transaction_id": [str(__import__("uuid").UUID(bytes=rng.bytes(16), version=4)) for _ in range(n)],
        "merchant_id": [f"M-{m:05d}" for m in rng.integers(1, 301, n)],
        "customer_id": cust.tolist(),
        "amount_minor": amount.tolist(),
        "currency": currency.tolist(),
        "event_type": rng.choice(["AUTHORIZED", "CAPTURED", "SETTLED", "REFUNDED", "REVERSED"], n,
                                 p=[.30, .30, .30, .07, .03]).tolist(),
        "event_ts": (created + rng.integers(0, 5_000, n)).tolist(),
        "created_at": created.tolist(),
        "updated_at": [int(u) if k else None for u, k in zip(upd, rng.random(n) < 0.5)],
        "risk_score": [float(r) if k else None for r, k in zip(rng.beta(2, 8, n), rng.random(n) < 0.7)],
        "receipt_email": [emails[c] if k else None for c, k in zip(cust, rng.random(n) < 0.6)],
    }
    return _conform(cols, fields, n, "transactions")


def write(dataset, cols, fields, ks, keys) -> DeltaTable:
    table = dataset.split(".")[-1]
    df = policy.protect(table, pd.DataFrame(cols), ks, keys)
    schema = arrow_schema(fields)
    data = pa.Table.from_pydict({f.name: df[f.name].astype(object).where(df[f.name].notna(), None).tolist()
                                 for f in fields}, schema=schema)
    path = lake.uri(load_registry()[dataset].delta_path)
    write_deltalake(path, data, mode="overwrite", schema_mode="overwrite", storage_options=lake.storage_options())
    return DeltaTable(path, storage_options=lake.storage_options())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", type=int, default=50)
    ap.add_argument("--transactions", type=int, default=5_000)
    ap.add_argument("--seed", type=int, default=20260723)
    a = ap.parse_args()
    return seed(a.subjects, a.transactions, a.seed)


def seed(subjects: int = 50, n_transactions: int = 5_000, seed_value: int = 20260723) -> int:
    class a:  # noqa: N801 — keeps the body below readable
        pass
    a.subjects, a.transactions, a.seed = subjects, n_transactions, seed_value
    reg, keys = load_registry(), load_keys()
    ks = KeyStore(lake.uri(lake.key_store_path()), keys["kek"], lake.storage_options())
    rng = np.random.default_rng(a.seed)
    fc = schema_fields(reg["customers"].schema)
    ft = schema_fields(reg["payments.transactions"].schema)
    customers = gen_customers(rng, a.subjects, fc)
    txns = gen_transactions(rng, a.transactions, customers, ft)
    _, pol = policy.load_policy()
    for dataset, cols, fields in (("customers", customers, fc), ("payments.transactions", txns, ft)):
        dt = write(dataset, cols, fields, ks, keys)
        t = lake.read_table(dt)
        print(f"{dataset}: {t.num_rows:,} rows -> {reg[dataset].delta_path} (Delta version {dt.version()})")
        for col, (tr, scope) in pol.get(dataset.split(".")[-1], {}).items():
            vals = [v for v in t.column(col).to_pylist() if v is not None]
            ok = all(shred.is_encrypted(v) for v in vals) if tr == "ENCRYPT_AESGCM" else \
                all(len(v) == 64 and all(c in "0123456789abcdef" for c in v) for v in vals)
            print(f"  {col:14s} {tr:15s} {scope:8s} {len(vals):5,} values, all protected: {ok}   e.g. {vals[0][:44]}...")
            if not ok:
                print("FAIL: plaintext PII in a live table")
                return 1
    print(f"key store: {lake.key_store_path()}  ({a.subjects} subject DEKs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
