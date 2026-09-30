#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
GATES = {"schema_gate.yml": "schema-gate", "ge_gate.yml": "ge-gate", "pii_gate.yml": "pii-gate",
         "impact_bot.yml": "impact-bot", "terraform_gate.yml": "terraform-plan"}
FAST_PATH = re.compile(r"^Nothing\b")
MAX_JOB_LOOKUPS = 40


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def github_fetcher(token):
    def fetch(path):
        req = urllib.request.Request("https://api.github.com" + path, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
    return fetch


def quantile(xs, q):
    """p50 = the median; other quantiles = nearest rank."""
    if not xs:
        return None
    if q == 0.5:
        return float(statistics.median(xs))
    xs = sorted(xs)
    return float(xs[max(0, math.ceil(q * len(xs)) - 1)])


def collect(fetch, repo, since):
    out = {}
    for wf, gate in GATES.items():
        runs, page = [], 1
        while page <= 5:
            r = fetch(f"/repos/{repo}/actions/workflows/{wf}/runs?event=pull_request&per_page=100&page={page}&created=%3E%3D{since}")
            if r is None:
                break
            runs += r.get("workflow_runs", [])
            if len(r.get("workflow_runs", [])) < 100:
                break
            page += 1
        done = [x for x in runs if x.get("status") == "completed" and x.get("conclusion") in ("success", "failure")]
        durations = []
        for x in done[:MAX_JOB_LOOKUPS]:
            jobs = (fetch(f"/repos/{repo}/actions/runs/{x['id']}/jobs") or {}).get("jobs", [])
            j = next((j for j in jobs if j.get("name") == gate), None)
            if not j or not j.get("started_at") or not j.get("completed_at"):
                continue
            if any(FAST_PATH.search(s.get("name", "")) and s.get("conclusion") == "success" for s in j.get("steps", [])):
                continue
            durations.append((_ts(j["completed_at"]) - _ts(j["started_at"])).total_seconds())
        out[gate] = {"runs": len(done), "rejections": sum(x["conclusion"] == "failure" for x in done),
                     "full_checks": len(durations), "p50": quantile(durations, 0.5), "p90": quantile(durations, 0.9)}
    return out


def fmt(s):
    return "-" if s is None else f"{int(s // 60)}m{int(s % 60):02d}s"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    token = os.environ.get("GITHUB_TOKEN") or subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    repo = os.environ.get("GITHUB_REPOSITORY") or subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], capture_output=True, text=True).stdout.strip()
    if not token or not repo:
        sys.exit("need GITHUB_TOKEN (or `gh auth login`) and a repo (GITHUB_REPOSITORY or run inside the clone)")
    since = (datetime.now(timezone.utc) - timedelta(days=a.days)).strftime("%Y-%m-%d")
    stats = collect(github_fetcher(token), repo, since)
    print(f"gate outcomes on pull requests, {repo}, since {since}")
    print(f"  {'gate':15s} {'runs':>5s} {'rejections':>10s} {'full checks':>11s} {'p50':>7s} {'p90':>7s}")
    for g, s in stats.items():
        print(f"  {g:15s} {s['runs']:5d} {s['rejections']:10d} {s['full_checks']:11d} {fmt(s['p50']):>7s} {fmt(s['p90']):>7s}")
    if not a.no_push:
        from govlib import lake
        from runtime import metrics
        series = []
        for g, s in stats.items():
            series += [("schema_gate_rejections_total", {"gate": g}, s["rejections"]), ("gate_runs_total", {"gate": g}, s["runs"])]
            series += [("gate_latency_seconds", {"gate": g, "quantile": q}, s[k])
                       for q, k in (("0.5", "p50"), ("0.9", "p90")) if s[k] is not None]
        url = os.environ.get("PUSHGATEWAY_URL") or lake.pushgateway_url()
        metrics.push_series(url, "gate_metrics", {"window": f"{a.days}d"}, series)
        print(f"pushed schema_gate_rejections_total / gate_runs_total / gate_latency_seconds to {url} at {time.strftime('%H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
