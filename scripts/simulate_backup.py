#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("RUST_LOG", "error")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import backup  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--label")
ap.add_argument("--verify", nargs="?", const="__latest__")
ap.add_argument("--list", action="store_true")
a = ap.parse_args()

if a.list:
    for lb in backup.labels():
        m = backup.manifest(lb)
        print(f"{lb}  {len(m['objects'])} objects  frozen {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(m['created_ts'] / 1000))}")
    sys.exit(0)
if a.verify:
    lb = backup.latest() if a.verify == "__latest__" else a.verify
    if not lb:
        sys.exit("no backup yet")
    good, total, bad = backup.verify(lb)
    print(f"{backup.label_uri(lb)}: {good}/{total} objects match MANIFEST.json" + ("" if not bad else f" — MISMATCH: {bad[:3]}"))
    sys.exit(0 if not bad else 1)

try:
    doc = backup.freeze(a.label)
except FileExistsError as e:
    sys.exit(f"REFUSED: {e}")
by_table: dict[str, list] = {}
for e in doc["objects"]:
    t = e["key"].split("/_delta_log/")[0] if "/_delta_log/" in e["key"] else e["key"].rsplit("/", 1)[0]
    by_table.setdefault(t, []).append(e)
total = sum(e["size"] for e in doc["objects"])
mdig = hashlib.sha256(json.dumps(doc["objects"], sort_keys=True).encode()).hexdigest()
print(f"froze {len(doc['objects'])} objects ({total / 1024:.1f} KiB) from {doc['source']} -> {backup.label_uri(doc['label'])}/")
for t, es in sorted(by_table.items()):
    print(f"  {t:26s} {len(es):3d} objects")
for e in doc["objects"]:
    if e["key"].startswith("customers/") and e["key"].endswith(".parquet"):
        print(f"  checksum  {e['key'][:60]:60s} sha256 {e['sha256'][:16]}…")
print(f"MANIFEST.json written last; manifest digest {mdig[:16]}…  (key store excluded: separate bucket)")
