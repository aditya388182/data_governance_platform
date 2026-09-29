#!/usr/bin/env python3
import argparse
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("RUST_LOG", "error")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crypto.key_store import KeyStore, SubjectErased  # noqa: E402
from govlib import lake  # noqa: E402
from govlib.keys import load_keys  # noqa: E402
from govlib.lake import read_table  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("cmd", choices=["count", "status", "demo", "destroy"])
ap.add_argument("subject", nargs="?")
ap.add_argument("--yes", action="store_true")
a = ap.parse_args()
ks = KeyStore(lake.uri(lake.key_store_path()), load_keys()["kek"], lake.storage_options())

if a.cmd == "count":
    rows = read_table(ks.uri, ["subject_id", "destroyed_ts"], lake.storage_options()).to_pylist()
    dead = sum(r["destroyed_ts"] is not None for r in rows)
    print(f"{lake.key_store_path()}: {len(rows) - dead} live DEK(s), {dead} destroyed, Delta v{ks._table().version()}")
elif a.cmd == "status":
    print(ks.status(a.subject) or f"{a.subject}: no key")
elif a.cmd == "destroy":
    if not (a.subject and a.yes):
        sys.exit("destroy needs SUBJECT and --yes (this is irreversible)")
    ts = ks.destroy_key(a.subject)
    print(f"{a.subject}: key destroyed at {ts}; time-travel exposure after VACUUM: {ks.history_exposure(a.subject)}")
else:
    s = f"DEMO-{uuid.uuid4().hex[:8]}"
    k1 = ks.get_or_create_dek(s)
    print(f"1. {s}: DEK created; stable on re-request: {ks.get_or_create_dek(s) == k1}")
    ts = ks.destroy_key(s, vacuum=False)
    print(f"2. destroyed at {ts} WITHOUT vacuum -> versions still exposing the wrapped DEK: {ks.history_exposure(s)}  (F3 is real)")
    print(f"3. VACUUM removed {len(ks.vacuum())} file(s) -> exposure now: {ks.history_exposure(s)}")
    print(f"4. destroy again -> {ks.destroy_key(s)} (original timestamp kept: {ks.destroy_key(s) == ts})")
    try:
        ks.get_or_create_dek(s)
        print("5. FAIL: a new DEK was minted for an erased subject")
        sys.exit(1)
    except SubjectErased as e:
        print(f"5. get_or_create_dek refused: SubjectErased({str(e)[:60]}...)")
