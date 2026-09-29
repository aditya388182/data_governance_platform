#!/usr/bin/env python3
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("RUST_LOG", "error")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pyarrow as pa  # noqa: E402
from deltalake import DeltaTable, write_deltalake  # noqa: E402

from govlib.lake import read_table  # noqa: E402

d = tempfile.mkdtemp(prefix="erasure-lab-")
try:
    write_deltalake(d, pa.table({"customer_id": ["LAB-1", "LAB-2", "LAB-3"], "email": ["enc:a", "enc:b", "enc:c"]}))
    print(f"1. throwaway table {d}: 3 subjects @ v0")
    m = DeltaTable(d).delete("customer_id = 'LAB-2'")
    print(f"2. DELETE LAB-2 -> {m['num_deleted_rows']} row deleted, table now v{DeltaTable(d).version()}")
    old = read_table(d, version=0).to_pylist()
    print(f"3. TRAP: time travel to v0 still returns LAB-2: {[r['customer_id'] for r in old]}  <- DELETE is not erasure")
    try:
        DeltaTable(d).vacuum(retention_hours=0, dry_run=False)
    except Exception as e:
        print(f"4. VACUUM RETAIN 0 refused by the retention guard: {' '.join(str(e).split())[:95]}…")
    removed = DeltaTable(d).vacuum(retention_hours=0, enforce_retention_duration=False, dry_run=False)
    print(f"5. guard overridden (demo only: production keeps 7 days for running readers) -> {len(removed)} file removed")
    try:
        read_table(d, version=0)
        print("6. ✗ time travel still works")
    except Exception as e:
        print(f"6. time travel to v0 now RAISES -> {type(e).__name__}: {' '.join(str(e).split())[:90]}…")
    print(f"7. control: current rows {[r['customer_id'] for r in read_table(d).to_pylist()]} — surgical, not a wipe")
    logs = "".join(p.read_text() for p in sorted(Path(d, '_delta_log').glob('*.json')))
    print(f"8. residue: the _delta_log still names LAB-2 {logs.count('LAB-2')} time(s) (the DELETE predicate) -> F13")
finally:
    shutil.rmtree(d, ignore_errors=True)
print("lab table removed")
