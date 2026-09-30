#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TFDIR = REPO / "terraform"
TF = os.environ.get("TF_BIN", "terraform")
NOISE = ("tags_all", "api_data", "api_response")


def leaf_diff(before, after, prefix=""):
    """[(path, before, after)] for every leaf that differs; JSON-encoded strings are opened up."""
    if isinstance(before, str) and isinstance(after, str) and before[:1] in "{[" and after[:1] in "{[":
        try:
            before, after = json.loads(before), json.loads(after)
        except ValueError:
            pass
    if isinstance(before, dict) and isinstance(after, dict):
        out = []
        for k in sorted(set(before) | set(after)):
            out += leaf_diff(before.get(k), after.get(k), f"{prefix}.{k}" if prefix else k)
        return out
    return [] if before == after else [(prefix, before, after)]


def check(ws: str) -> dict:
    env = {**os.environ, "TF_WORKSPACE": ws, "TF_IN_AUTOMATION": "1"}
    with tempfile.TemporaryDirectory() as d:
        pf = f"{d}/{ws}.tfplan"
        p = subprocess.run([TF, "plan", "-detailed-exitcode", "-input=false", "-no-color", "-lock-timeout=120s",
                            f"-out={pf}"], cwd=TFDIR, env=env, capture_output=True, text=True)
        res = {"workspace": ws, "exit": p.returncode, "drift": [], "pending": [], "log": p.stdout + p.stderr}
        if p.returncode != 2:
            return res
        s = subprocess.run([TF, "show", "-json", pf], cwd=TFDIR, env=env, capture_output=True, text=True)
        if s.returncode != 0:
            res["exit"] = 1
            res["log"] += s.stderr
            return res
        plan = json.loads(s.stdout)
    # A real out-of-band change is one the plan would REVERT: present in resource_drift
    # (live differs from the last-applied state) AND in resource_changes (apply changes it
    # back). Refresh-time normalisations (computed attributes filled in after apply) show up
    # in resource_drift only, and are not drift.
    reverts = {}
    for rc in plan.get("resource_changes", []):
        if rc["change"]["actions"] not in (["no-op"], ["read"]):
            ch = rc["change"]
            reverts[rc["address"]] = {p: a for p, b, a in leaf_diff(ch.get("before") or {}, ch.get("after") or {})}
    for rd in plan.get("resource_drift", []):
        ch, addr = rd["change"], rd["address"]
        if "delete" in ch["actions"] and addr in reverts:
            res["drift"].append({"resource": addr, "attribute": "(deleted outside Terraform)", "code": "present", "live": None})
            continue
        for path, b, a in leaf_diff(ch.get("before") or {}, ch.get("after") or {}):
            if not path.startswith(NOISE) and path in reverts.get(addr, {}):
                res["drift"].append({"resource": addr, "attribute": path, "code": reverts[addr][path], "live": a})
    res["pending"] = [(rc["address"], "/".join(rc["change"]["actions"])) for rc in plan.get("resource_changes", [])
                      if rc["change"]["actions"] not in (["no-op"], ["read"])]
    return res


def push(results: list) -> None:
    from govlib import lake
    from runtime import metrics
    url = os.environ.get("PUSHGATEWAY_URL") or lake.pushgateway_url()
    now = time.time()
    for r in results:
        series = [("terraform_drift", {}, 1 if r["exit"] == 2 else 0),
                  ("terraform_drift_check_ok", {}, 1 if r["exit"] in (0, 2) else 0),
                  ("terraform_pending_changes", {}, len(r["pending"])),
                  ("terraform_drift_last_run_timestamp_seconds", {}, now)]
        series += [("terraform_drift_attribute", {"resource": d["resource"], "attribute": d["attribute"]}, 1) for d in r["drift"]]
        metrics.push_series(url, "terraform_drift", {"workspace": r["workspace"]}, series)
    print(f"pushed terraform_drift* for {len(results)} workspace(s) to {url}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspaces", nargs="+", default=["dev", "staging", "prod"])
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    print(f"DRIFT CHECK {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} — terraform plan -detailed-exitcode per workspace")
    results = [check(ws) for ws in a.workspaces]
    for r in results:
        if r["exit"] == 0:
            print(f"  {r['workspace']:8s} exit 0  clean")
        elif r["exit"] == 2:
            kind = "DRIFT: changed outside Terraform" if r["drift"] else "UNAPPLIED: code differs from live, no out-of-band change"
            print(f"  {r['workspace']:8s} exit 2  {kind}")
            for d in r["drift"]:
                print(f"             {d['resource']}  {d['attribute']}: code {json.dumps(d['code'])} -> live {json.dumps(d['live'])}")
            for addr, act in r["pending"]:
                print(f"             plan would {act}: {addr}")
        else:
            tail = [ln for ln in r["log"].strip().splitlines() if ln.strip()][-3:]
            print(f"  {r['workspace']:8s} exit 1  PLAN FAILED — FAILING CLOSED (not reported as clean)")
            for ln in tail:
                print(f"             {ln[:150]}")
    if not a.no_push:
        try:
            push(results)
        except Exception as e:
            print(f"ERROR: metrics push failed ({type(e).__name__}: {e}) — the alert path is down: FAILING CLOSED")
            return 1
    if any(r["drift"] for r in results):
        print("Remediate: re-apply the code (terraform apply reverts it), or adopt the change in code via a PR. Never edit state.")
    worst = 1 if any(r["exit"] not in (0, 2) for r in results) else 2 if any(r["exit"] == 2 for r in results) else 0
    print(f"exit {worst}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
