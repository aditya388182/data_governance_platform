#!/usr/bin/env python3
import os
import sys

os.environ.setdefault("RUST_LOG", "error")  # quiet delta-rs WARN lines
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse  # noqa: E402

from crypto import policy  # noqa: E402
from crypto.key_store import KeyStore, SubjectErased  # noqa: E402
from govlib import lake  # noqa: E402
from govlib.contracts import load_registry  # noqa: E402
from govlib.keys import load_keys  # noqa: E402
from runtime import backup  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("subject")
ap.add_argument("--from-backup", nargs="?", const="latest", metavar="LABEL",
                help="read the frozen backup copy instead of the live table (default label: the latest)")
a = ap.parse_args()
sid, so, reg = a.subject, lake.storage_options(), load_registry()
ks = KeyStore(lake.uri(lake.key_store_path()), load_keys()["kek"], so)
path = reg["customers"].delta_path
if a.from_backup:
    label = backup.latest() if a.from_backup == "latest" else a.from_backup
    if not label:
        sys.exit("no backup yet: python scripts/simulate_backup.py")
    path = backup.table_uri(label, "customers")
    print(f"(reading the frozen backup {path})")
cust = lake.read_table(lake.uri(path), storage_options=so).to_pandas()
row = cust[cust.customer_id == sid]
if row.empty:
    st = ks.status(sid)
    if st and st["destroyed_ts"]:
        print(f"{sid}: SUBJECT ERASED — no row in {path} and the key was destroyed at {st['destroyed_ts']}")
        sys.exit(3)
    sys.exit(f"{sid}: not in {path}")
try:
    clear = policy.reveal("customers", row, ks).iloc[0]
except SubjectErased as e:
    print(f"{sid}: SUBJECT ERASED — {e}; the stored ciphertext is noise now")
    sys.exit(3)
raw = row.iloc[0]
print(f"{sid}\n  email      {raw.email[:38]}...  ->  {clear.email}\n  full_name  {raw.full_name[:38]}...  ->  {clear.full_name}")
print(f"  ssn        {raw.ssn[:24]}...  (HMAC token: one-way, nothing to decrypt)")
