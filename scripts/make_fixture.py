#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import sys
import uuid
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import avro.io  # noqa: E402
import avro.schema  # noqa: E402

from govlib.contracts import load_catalog, load_registry, protected_columns, schema_fields  # noqa: E402
from govlib.fixtures import arrow_type, content_hash, manifest_path  # noqa: E402
from govlib.keys import fingerprint, load_keys  # noqa: E402
from govlib.pii import compile_patterns, matches  # noqa: E402

GENERATOR_VERSION = "make_fixture.py/2"
FRACTION = 0.01
MIN_PER_STRATUM = 5
EPOCH0 = int(dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
DAY_MS = 86_400_000

# Exact source counts per stratum, so the rare strata are real and reproducible.
PAYMENT_CURRENCIES = {"USD": 140_000, "EUR": 24_000, "GBP": 16_000, "JPY": 10_000,
                      "CAD": 5_000, "AUD": 3_000, "INR": 1_960, "CHF": 40}
CUSTOMER_COUNTRIES = {"US": 30_000, "GB": 8_000, "DE": 5_000, "FR": 3_000,
                      "IN": 2_500, "JP": 1_000, "CA": 450, "AU": 50}
FIRST = ["ana", "ben", "chen", "dara", "eli", "fatima", "gus", "hana", "ivan", "jo", "kofi", "lena", "mo", "nia", "omar", "priya"]
LAST = ["smith", "garcia", "wong", "okafor", "muller", "rossi", "tanaka", "patel", "kim", "silva", "nguyen", "brown"]


#  synthesis
def _strata_column(rng, counts):
    col = np.concatenate([np.full(n, k, dtype=object) for k, n in counts.items()])
    rng.shuffle(col)
    return col


def synth_transactions(rng, fields):
    n = sum(PAYMENT_CURRENCIES.values())
    currency = _strata_column(rng, PAYMENT_CURRENCIES)
    cust_num = rng.integers(1, 50_001, n)
    event_type = rng.choice(["AUTHORIZED", "CAPTURED", "SETTLED", "REFUNDED", "REVERSED"], n, p=[.30, .30, .30, .07, .03])
    status = event_type.astype(object).copy()
    status[rng.random(n) < 0.03] = "PENDING"
    base = np.where(currency == "JPY", 5_000.0, 4_000.0)
    amount = np.minimum(np.round(rng.lognormal(np.log(base), 1.0)), 50_000_000).astype(np.int64)
    amount[rng.random(n) < 0.005] = 0                           # card-verification auths
    created = EPOCH0 + rng.integers(0, 90 * DAY_MS, n)
    upd = created + rng.integers(0, 3 * DAY_MS, n)
    risk = rng.beta(2, 8, n)
    cols = {
        "transaction_id": [str(uuid.UUID(bytes=rng.bytes(16), version=4)) for _ in range(n)],
        "merchant_id": [f"M-{m:05d}" for m in rng.integers(1, 5_001, n)],
        "customer_id": [f"CUST-{c:06d}" for c in cust_num],
        "amount_minor": amount.tolist(),
        "currency": currency.tolist(),
        "status": status.tolist(),
        "event_type": event_type.tolist(),
        "event_ts": (created + rng.integers(0, 5_000, n)).tolist(),
        "created_at": created.tolist(),
        "updated_at": [int(u) if keep else None for u, keep in zip(upd, rng.random(n) < 0.5)],
        "risk_score": [float(r) if keep else None for r, keep in zip(risk, rng.random(n) < 0.7)],
        "receipt_email": [f"user{c}@example.com" if keep else None for c, keep in zip(cust_num, rng.random(n) < 0.6)],
    }
    return _conform(cols, fields, n, "transactions"), "currency"


def synth_customers(rng, fields):
    n = sum(CUSTOMER_COUNTRIES.values())
    country = _strata_column(rng, CUSTOMER_COUNTRIES)
    fi = rng.integers(0, len(FIRST), n)
    la = rng.integers(0, len(LAST), n)
    created = EPOCH0 - rng.integers(0, 365 * DAY_MS, n)
    upd = created + rng.integers(0, 30 * DAY_MS, n)
    cols = {
        "customer_id": [f"CUST-{i:06d}" for i in range(1, n + 1)],
        "email": [f"{FIRST[a]}.{LAST[b]}{i}@example.com" for i, (a, b) in enumerate(zip(fi, la), 1)],
        "ssn": [f"{x:03d}-{y:02d}-{z:04d}" for x, y, z in zip(rng.integers(100, 900, n), rng.integers(10, 100, n), rng.integers(1000, 10_000, n))],
        "full_name": [f"{FIRST[a].title()} {LAST[b].title()}" for a, b in zip(fi, la)],
        "country": country.tolist(),
        "created_at": created.tolist(),
        "updated_at": [int(u) if keep else None for u, keep in zip(upd, rng.random(n) < 0.4)],
    }
    return _conform(cols, fields, n, "customers"), "country"


def _conform(cols, fields, n, table):
    """Keep exactly the contract's fields. A field the generator does not know is
    filled with null when the contract allows it, otherwise it is an error."""
    out = {}
    for f in fields:
        if f.name in cols:
            out[f.name] = cols[f.name]
        elif f.nullable:
            out[f.name] = [None] * n
        else:
            raise SystemExit(f"FAIL: {table}.{f.name} is non-nullable and make_fixture.py has no generator for it — add one")
    return out


SYNTH = {"transactions": synth_transactions, "customers": synth_customers}


#  sampling
def stratified_indices(rng, strata_values, fraction, floor):
    values = np.asarray(strata_values, dtype=object)
    chosen, report = [], {}
    for key in sorted(set(values.tolist())):
        idx = np.flatnonzero(values == key)
        n_src = len(idx)
        n_take = min(n_src, max(floor, int(fraction * n_src + 0.5)))
        take = np.sort(rng.choice(idx, size=n_take, replace=False))
        chosen.append(take)
        report[key] = {"source": n_src, "sampled": int(n_take),
                       "bernoulli_miss_probability": round((1 - fraction) ** n_src, 6)}
    return np.sort(np.concatenate(chosen)), report


def take(cols, idx):
    return {k: [v[i] for i in idx] for k, v in cols.items()}


#  bad rows
def bad_rows(template_row):
    """Five rows, each Avro-valid, each semantically wrong in exactly one way.
    (A null merchant_id would NOT qualify: merchant_id is non-nullable, so Avro
    itself rejects it — that is a structural failure, not a semantic one.)"""
    def row(**over):
        r = dict(template_row)
        r.update(over)
        return r
    return [
        ("amount_minor = -500 (negative amount)", row(transaction_id="3f1a6c2e-8b4d-4e7f-9a1b-000000000001", amount_minor=-500)),
        ("currency = 'XYZ' (not an allowed currency)", row(transaction_id="3f1a6c2e-8b4d-4e7f-9a1b-000000000002", currency="XYZ")),
        ("currency = 'US' (2-char code)", row(transaction_id="3f1a6c2e-8b4d-4e7f-9a1b-000000000003", currency="US")),
        ("transaction_id = 'txn-12345' (not a UUID4)", row(transaction_id="txn-12345")),
        ("risk_score = 1.7 (not a probability)", row(transaction_id="3f1a6c2e-8b4d-4e7f-9a1b-000000000005", risk_score=1.7)),
    ]


#  avro validation
def to_datum(row, fields):
    d = {}
    for f in fields:
        v = row.get(f.name)
        t = f.avro_type
        inner = [x for x in t if x != "null"][0] if isinstance(t, list) else t
        if isinstance(inner, dict) and inner.get("logicalType") == "timestamp-millis" and v is not None:
            v = dt.datetime.fromtimestamp(v / 1000, tz=dt.timezone.utc)
        d[f.name] = v
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="all")
    ap.add_argument("--inject-bad", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--keys", default=str(ROOT / "conf/keys.yml"))
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    root = Path(a.root)

    keys = load_keys(a.keys)
    fkey = keys["fixtures_key"]
    registry = load_registry(root=root)
    catalog = load_catalog(root=root)
    if catalog is None:
        raise SystemExit("FAIL: contracts/pii_catalog.yml missing — fixtures are never written without a PII catalog")
    patterns = compile_patterns(catalog.name_patterns)

    targets = [d for d in registry.values() if d.fixture and (a.dataset in ("all", d.name))]
    if not targets:
        raise SystemExit(f"FAIL: no dataset '{a.dataset}' with a fixture: key in contracts/registry.yml")
    if a.inject_bad and any(d.table != "transactions" for d in targets):
        targets = [d for d in targets if d.table == "transactions"]

    for ds in targets:
        fields = schema_fields(ds.schema, root=root)
        names = [f.name for f in fields]
        #  default-deny: no plaintext for anything that looks like PII but is not classified
        unclassified = [f for f in names if matches(f, patterns) and f"{ds.table}.{f}" not in catalog.columns]
        if unclassified:
            raise SystemExit(f"FAIL: {ds.table}: field(s) {unclassified} look like PII but have no entry in "
                             f"contracts/pii_catalog.yml — classify them before generating a fixture")
        rng = np.random.default_rng(a.seed)
        cols, strat_col = SYNTH[ds.table](rng, fields)
        n_source = len(cols[names[0]])
        idx, strata = stratified_indices(rng, cols[strat_col], FRACTION, MIN_PER_STRATUM)
        sample = take(cols, idx)
        rows = [dict(zip(sample.keys(), vals)) for vals in zip(*sample.values())]

        injected = []
        if a.inject_bad:
            for desc, r in bad_rows(rows[0]):
                rows.append(r)
                injected.append(desc)

        #  structural check: every row must be a valid Avro datum under the contract
        avsc = avro.schema.parse((root / ds.schema).read_text())
        invalid = [i for i, r in enumerate(rows) if not avro.io.validate(avsc, to_datum(r, fields))]
        if invalid:
            raise SystemExit(f"FAIL: {len(invalid)} row(s) are not valid Avro under {ds.schema} (first index {invalid[0]})")

        #  anonymize per catalog (+ subject key)
        anon_cols = sorted(set(protected_columns(catalog, ds.table)) | ({catalog.subject_key} & set(names)))
        anon_cols = [c for c in anon_cols if c in names]
        for r in rows:
            for c in anon_cols:
                if r[c] is not None:
                    r[c] = hmac.new(fkey, f"{c}:{r[c]}".encode(), hashlib.sha256).hexdigest()

        #  write parquet with an Arrow schema derived from the Avro schema
        arrow_fields = []
        for f in fields:
            t, nullable = arrow_type(f.avro_type)
            arrow_fields.append(pa.field(f.name, t, nullable=nullable))
        table = pa.Table.from_pylist(rows, schema=pa.schema(arrow_fields))
        out = root / ds.fixture
        out.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, out, compression="snappy")

        manifest = {
            "dataset": ds.name, "table": ds.table, "schema": ds.schema,
            "generator": GENERATOR_VERSION, "seed": a.seed,
            "source_rows": n_source, "stratify_by": strat_col, "fraction": FRACTION,
            "min_per_stratum": MIN_PER_STRATUM,
            "strata": strata,
            "sampled_rows": int(len(idx)), "injected_bad_rows": injected,
            "rows": len(rows),
            "anonymized_columns": anon_cols,
            "token_format": "hex(HMAC-SHA256(fixtures_key, '<column>:<value>'))",
            "fixtures_key_fingerprint": fingerprint(fkey),
            "avro_valid_rows": len(rows),
            "content_sha256": content_hash(out),
        }
        manifest_path(out).write_text(json.dumps(manifest, indent=2) + "\n")

        #  report
        print(f"\n=== {ds.name} -> {ds.fixture}")
        print(f"source rows {n_source:,} | stratify by {strat_col} | fraction {FRACTION} | floor {MIN_PER_STRATUM}")
        print(f"  {'stratum':8s} {'source':>8s} {'sampled':>8s}   P(absent) under Bernoulli sampleBy({FRACTION})")
        for k, v in strata.items():
            flag = "   <-- sampleBy could return ZERO rows here" if v["bernoulli_miss_probability"] >= 0.01 else ""
            print(f"  {k:8s} {v['source']:>8,} {v['sampled']:>8,}   {v['bernoulli_miss_probability']:.2%}{flag}")
        if injected:
            print(f"  + {len(injected)} injected bad rows (all Avro-valid):")
            for d in injected:
                print(f"      - {d}")
        print(f"rows written: {len(rows):,} (all {len(rows):,} validate against {ds.schema})")
        print(f"anonymized (HMAC-SHA256, key fp {fingerprint(fkey)}): {', '.join(anon_cols)}")
        print(f"content_sha256: {manifest['content_sha256'][:16]}…  size: {out.stat().st_size/1024:.1f} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
