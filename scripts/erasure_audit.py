#!/usr/bin/env python3
import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("RUST_LOG", "error")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crypto.key_store import KeyStore, SubjectErased  # noqa: E402
from govlib import lake  # noqa: E402
from govlib.keys import load_keys  # noqa: E402
from runtime import erasure, objects  # noqa: E402


def ts(ms):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ms / 1000)) if ms else "-"


def records_for(rid):
    recs = [r for r in erasure.audit_records() if r["request_id"] == rid or r["rerun_of"] == rid]
    if not recs:
        sys.exit(f"no audit record for {rid}")
    prim = next((r for r in recs if r["rerun_of"] is None), None)
    if prim is None:          # a later request that only re-verified: show it, and point to its primary
        prim = sorted(recs, key=lambda r: r["completed_ts"])[0]
    return prim, sorted((r for r in recs if r is not prim), key=lambda r: r["completed_ts"])


def cmd_list():
    recs = sorted(erasure.audit_records(), key=lambda r: r["completed_ts"])
    print(f"{'completed':23s} {'request':16s} {'kind':24s} {'rows':>5s} lake  crypto  subject_hash")
    for r in recs:
        kind = "primary" if r["rerun_of"] is None else f"rerun_of {r['rerun_of']}"
        print(f"{ts(r['completed_ts']):23s} {r['request_id']:16s} {kind:24s} {r['rows_deleted']:5d} "
              f"{str(r['verified_lake']):5s} {str(r['verified_crypto']):6s}  {r['subject_hash'][:16]}…")
    prim = [r for r in recs if r["rerun_of"] is None]
    print(f"\nerasures: {len(prim)} (reruns and repeat requests are listed, never counted); records: {len(recs)}")


def cmd_show(rid):
    p, reruns = records_for(rid)
    cfg = erasure._dt(erasure.audit_path()).metadata().configuration
    print(f"ERASURE AUDIT — request {p['request_id']}   ({erasure.audit_path()}, delta.appendOnly={cfg.get('delta.appendOnly')})")
    print(f"  subject_hash        {p['subject_hash']}   (HMAC with audit_hmac_key; the raw id is not stored)")
    print(f"  completed           {ts(p['completed_ts'])} by {p['operator']}")
    print(f"  rows_deleted        {p['rows_deleted']}")
    print(f"  {'table':34s} {'rows':>5s} {'pre-delete':>10s} {'after VACUUM':>12s}")
    for t, n, v0, v1 in zip(p["tables_touched"], p["rows_deleted_by_table"], p["pre_delete_versions"], p["vacuum_versions"]):
        print(f"  {objects.split(t)[1]:34s} {n:5d} {'v' + str(v0):>10s} {'v' + str(v1):>12s}")
    print(f"  key_destroyed_ts    {ts(p['key_destroyed_ts'])}")
    print(f"  verified_lake       {p['verified_lake']}   (0 rows now; pre-delete versions unreadable; control intact)")
    print(f"  verified_crypto     {p['verified_crypto']}   (no DEK; wrapped key zeroed; no exposure; backup intact but undecryptable)")
    print(f"  uncatalogued found  {', '.join(p['uncatalogued_tables']) or 'none'}")
    print(f"  backups covered     {', '.join(p['backups_covered']) or 'none'}   (by crypto-shredding)")
    print(f"  downstream notify   {', '.join(p['downstream_to_notify']) or 'none'}")
    print(f"  rerun_of            {p['rerun_of'] or '— (primary record)'}")
    for r in reruns:
        print(f"  └ {r['request_id']} at {ts(r['completed_ts'])}: rerun_of={r['rerun_of']}, rows_deleted={r['rows_deleted']}, "
              f"key_destroyed_ts {'unchanged' if r['key_destroyed_ts'] == p['key_destroyed_ts'] else 'CHANGED'}, "
              f"lake={r['verified_lake']} crypto={r['verified_crypto']}")
    print(f"  erasures counted for this subject: 1   (records: {1 + len(reruns)})")


def cmd_timetravel(rid, table):
    p, _ = records_for(rid)
    cands = [(t, v) for t, v, n in zip(p["tables_touched"], p["pre_delete_versions"], p["rows_deleted_by_table"]) if n > 0]
    if table:
        cands = [(t, v) for t, v in cands if objects.split(t)[1] == table]
    if not cands:
        sys.exit("no table with deleted rows matches")
    path, v = cands[0]
    print(f"Reading {path} AS OF VERSION {v} (the version before request {rid} deleted the subject).")
    print("If the erasure worked, this raises: the historical Parquet files are physically gone.\n", flush=True)
    t = lake.read_table(lake.uri(path), storage_options=lake.storage_options(), version=v)
    print(f"✗ READABLE: {t.num_rows} rows — time travel resurrected the data. The erasure did NOT work.")
    return 1


def cmd_verify(rid, subject):
    p, _ = records_for(rid)
    keys = load_keys()
    if erasure.subject_hash(keys, subject) != p["subject_hash"]:
        sys.exit(f"{subject} does not hash to the subject_hash recorded for {rid}")
    sk, ok = erasure.subject_key(), True
    print(f"re-verifying {rid} for the subject whose hash is {p['subject_hash'][:16]}… (read-only)")
    for t, v, n in zip(p["tables_touched"], p["pre_delete_versions"], p["rows_deleted_by_table"]):
        try:
            cur = erasure.count_rows(t, sk, subject)
        except Exception as e:
            cur = f"unreadable ({type(e).__name__})"
        line = f"  {objects.split(t)[1]:26s} current rows {cur}"
        ok &= cur == 0
        if n > 0:
            thrown, msg = erasure.time_travel(t, v)
            ok &= thrown
            line += f"; @v{v} {'raises' if thrown else 'READABLE'}"
        print(line + ("   ✓" if cur == 0 else "   ✗"))
    ks = KeyStore(lake.uri(lake.key_store_path()), keys["kek"], lake.storage_options())
    try:
        ks.get_dek(subject)
        print("  key: a DEK is still available   ✗")
        ok = False
    except SubjectErased:
        print("  key: destroyed (SubjectErased)   ✓")
    print("VERIFIED" if ok else "NOT VERIFIED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "show", "timetravel", "verify"])
    ap.add_argument("request_id", nargs="?")
    ap.add_argument("--table")
    ap.add_argument("--subject")
    a = ap.parse_args()
    if a.cmd == "list":
        cmd_list()
        return 0
    if not a.request_id:
        sys.exit(f"{a.cmd} needs a REQUEST_ID")
    if a.cmd == "show":
        cmd_show(a.request_id)
        return 0
    if a.cmd == "timetravel":
        return cmd_timetravel(a.request_id, a.table)
    if not a.subject:
        sys.exit("verify needs --subject")
    return cmd_verify(a.request_id, a.subject)


if __name__ == "__main__":
    sys.exit(main())
