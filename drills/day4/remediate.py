#!/usr/bin/env python3
import os
import sys

os.environ.setdefault("RUST_LOG", "error")  # quiet delta-rs WARN lines
from pathlib import Path

from deltalake import DeltaTable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from govlib import lake  # noqa: E402
from govlib.contracts import load_registry  # noqa: E402

d = load_registry()["payments.transactions"]
dt = DeltaTable(lake.uri(d.delta_path), storage_options=lake.storage_options())
before = dt.version()
m = dt.delete("amount_minor < 0 OR currency NOT IN ('USD','EUR','GBP','JPY','CAD','AUD','INR','CHF')")
print(f"owner remediation: deleted {m['num_deleted_rows']} row(s) from {d.delta_path} (Delta v{before} -> v{dt.version()})")
