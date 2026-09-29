#!/usr/bin/env python3
import os
import sys

os.environ.setdefault("RUST_LOG", "error")  # quiet delta-rs WARN lines
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crypto import policy  # noqa: E402
from crypto.key_store import KeyStore, SubjectErased  # noqa: E402
from govlib import lake  # noqa: E402
from govlib.contracts import load_registry  # noqa: E402
from govlib.keys import load_keys  # noqa: E402

sid, so, reg = sys.argv[1], lake.storage_options(), load_registry()
ks = KeyStore(lake.uri(lake.key_store_path()), load_keys()["kek"], so)
cust = lake.read_table(lake.uri(reg["customers"].delta_path), storage_options=so).to_pandas()
row = cust[cust.customer_id == sid]
if row.empty:
    sys.exit(f"{sid}: not in customers")
try:
    clear = policy.reveal("customers", row, ks).iloc[0]
except SubjectErased as e:
    print(f"{sid}: SUBJECT ERASED — {e}; the stored ciphertext is noise now")
    sys.exit(3)
raw = row.iloc[0]
print(f"{sid}\n  email      {raw.email[:38]}...  ->  {clear.email}\n  full_name  {raw.full_name[:38]}...  ->  {clear.full_name}")
print(f"  ssn        {raw.ssn[:24]}...  (HMAC token: one-way, nothing to decrypt)")
