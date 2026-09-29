#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("RUST_LOG", "error")  # quiet delta-rs WARN lines
import time
import uuid
from pathlib import Path

os.environ.setdefault("GE_USAGE_STATS", "FALSE")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deltalake import DeltaTable  # noqa: E402

from ge.run_suite import load_suite_doc, validate  # noqa: E402
from govlib import lake  # noqa: E402
from govlib.contracts import load_registry  # noqa: E402
from runtime import metrics, quarantine  # noqa: E402


def check_dataset(name: str, d) -> dict:
    so = lake.storage_options()
    src = lake.uri(d.delta_path)
    run_ts, cid = int(time.time() * 1000), str(uuid.uuid4())
    dt = DeltaTable(src, storage_options=so)
    version = dt.version()
    table = lake.read_table(dt)
    df = table.to_pandas()                      # default RangeIndex: GE positions == row numbers
    suite = load_suite_doc(d.ge_suite)
    results = validate(df, suite, result_format="COMPLETE")
    offenders = quarantine.offending_rows(results)
    qpath = lake.uri(lake.quarantine_path(name))
    if offenders:
        meta = {"dataset": name, "suite": suite["expectation_suite_name"], "run_ts": run_ts, "checkpoint_id": cid,
                "source_version": version, "owner": d.owner}
        new, total = quarantine.append_new(qpath, quarantine.build(table, offenders, meta), so)
    else:
        new, total = 0, len(quarantine.existing_fingerprints(qpath, so))
    after = DeltaTable(src, storage_options=so).version()
    return {"dataset": name, "owner": d.owner, "version": version, "rows": table.num_rows, "checkpoint_id": cid,
            "passed": all(r.success for r in results), "failed": [r for r in results if not r.success],
            "table_level": quarantine.table_level_failures(results), "offending": len(offenders),
            "new": new, "total": total, "source_untouched": after == version, "after": after,
            "quarantine": lake.quarantine_path(name)}


def run(datasets=None, push_url: str | None = None) -> tuple[int, list[dict]]:
    reg = load_registry()
    names = datasets or list(reg)
    code, out = 0, []
    for name in names:
        if name not in reg:
            print(f"CHECKPOINT ERROR {name}: not in contracts/registry.yml — FAILING CLOSED")
            code = 2
            continue
        try:
            s = check_dataset(name, reg[name])
        except Exception as e:  # unreadable table, broken suite, storage error
            print(f"CHECKPOINT ERROR {name}: {type(e).__name__}: {str(e)[:200]} — FAILING CLOSED (reported as pass=0)")
            s, code = {"dataset": name, "error": str(e), "passed": False, "new": 0, "total": 0, "failed": [],
                       "version": -1}, 2
        out.append(s)
        if "error" not in s:
            verdict = "PASS" if s["passed"] else "FAIL"
            print(f"{name}: {verdict} — {s['rows']:,} rows @ Delta v{s['version']}, owner {s['owner']}, checkpoint {s['checkpoint_id'][:8]}")
            for r in s["failed"]:
                print(f"  ✗ {quarantine.label(r)}: {r.unexpected_count if r.unexpected_count is not None else 'table-level'} unexpected")
            if s["offending"]:
                print(f"  quarantined {s['new']} new row(s) of {s['offending']} offending -> {s['quarantine']} (total {s['total']})")
            print(f"  source untouched: {s['source_untouched']} (version before {s['version']}, after {s['after']})")
        if push_url:
            vals = {"ge_checkpoint_pass": int(bool(s["passed"])), "quarantine_rows_total": s["total"],
                    "quarantine_rows_last_run": s["new"], "ge_checkpoint_failed_expectations": len(s["failed"]),
                    "ge_checkpoint_source_version": s["version"], "ge_checkpoint_last_run_timestamp_seconds": int(time.time())}
            try:
                metrics.push(push_url, "ge_checkpoint", name, vals)
                print(f"  pushed metrics -> {push_url} (ge_checkpoint_pass={vals['ge_checkpoint_pass']}, "
                      f"quarantine_rows_total={vals['quarantine_rows_total']}, quarantine_rows_last_run={vals['quarantine_rows_last_run']})")
            except Exception as e:
                print(f"PUSH ERROR {name}: {type(e).__name__}: {e} — FAILING CLOSED (the alert path is down)")
                code = 2
    return code, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", action="append", help="repeatable; default: every dataset in the registry")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--pushgateway", default=None)
    a = ap.parse_args()
    code, _ = run(a.dataset, None if a.no_push else (a.pushgateway or lake.pushgateway_url()))
    return code


if __name__ == "__main__":
    sys.exit(main())
