#!/usr/bin/env python3
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def measure() -> dict:
    os.environ.setdefault("RUST_LOG", "error")
    root = tempfile.mkdtemp(prefix="erasure-bench-")
    os.environ["LAKE_LOCAL_ROOT"] = root
    for n in ("GOV_KEK", "GOV_FIXTURES_KEY", "GOV_AUDIT_HMAC_KEY", "GOV_TOKEN_KEY"):
        os.environ[n] = os.urandom(32).hex()
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    from runtime import backup, erasure
    from scripts.seed_prod_tables import seed
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            seed()
            backup.freeze("bench")
        t0 = time.perf_counter()
        res = erasure.run("CUST-000011", "BENCH-1", control="CUST-000007", say=lambda *a: None)
        t1 = time.perf_counter()
        rerun = erasure.run("CUST-000011", "BENCH-1", control="CUST-000007", say=lambda *a: None)
        t2 = time.perf_counter()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    rec = res["record"]
    return {"first_s": round(t1 - t0, 2), "rerun_s": round(t2 - t1, 2), "rows": rec["rows_deleted"],
            "tables": sum(n > 0 for n in rec["rows_deleted_by_table"]),
            "verified": bool(res["verified_lake"] and res["verified_crypto"] and rerun["verified_lake"] and rerun["verified_crypto"])}


if __name__ == "__main__":
    m = measure()
    if "--json" in sys.argv:
        print(json.dumps(m))
    else:
        print("erasure wall-clock (50 customers / 5,000 transactions, local disk, throwaway keys):")
        print(f"  first run {m['first_s']:5.2f} s   ({m['rows']} rows from {m['tables']} tables, "
              "VACUUM, key destroyed, both proofs, audit)")
        print(f"  rerun     {m['rerun_s']:5.2f} s   (0 rows, proofs re-verified)")
        print(f"  verified: {m['verified']}   (MinIO adds network round-trips; key destruction itself is milliseconds)")
    sys.exit(0 if m["verified"] else 1)
