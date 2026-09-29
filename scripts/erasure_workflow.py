#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("RUST_LOG", "error")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from govlib import lake  # noqa: E402
from runtime import erasure, metrics  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--request-id", required=True)
    ap.add_argument("--control", help="a subject that must stay readable (default: the first other customer)")
    ap.add_argument("--backup", help="backup label for the crypto proof (default: the latest)")
    ap.add_argument("--operator")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    try:
        res = erasure.run(a.subject, a.request_id, a.control, a.dry_run, a.backup, a.operator)
    except Exception as e:
        print(f"\nERASURE ERROR: {type(e).__name__}: {e}\nFAILING CLOSED — nothing was recorded as complete. "
              "Fix the cause and rerun the same command: every step is idempotent.")
        return 2
    if a.dry_run:
        return 0
    rec = res["record"]
    print("\n== Requester notification ==\n  " + erasure.notification(rec).replace("\n", "\n  "))
    if not a.no_push:
        done = sum(1 for r in erasure.audit_records() if r["rerun_of"] is None and r["verified_lake"] and r["verified_crypto"])
        try:
            metrics.push(lake.pushgateway_url(), "erasure", "audit_erasure", {"erasure_completions_total": done})
            print(f"\n  pushed erasure_completions_total={done} (reruns are not counted)")
        except Exception as e:
            print(f"\n  WARN: metrics push failed ({type(e).__name__}); the erasure itself is complete and audited")
    ok = rec["verified_lake"] and rec["verified_crypto"]
    print(f"\nRESULT: {'ERASED AND VERIFIED' if ok else 'VERIFICATION FAILED'} — "
          f"verified_lake={rec['verified_lake']} verified_crypto={rec['verified_crypto']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
