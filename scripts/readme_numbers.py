#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
README = REPO / "README.md"
START, END = "<!-- NUMBERS:START -->", "<!-- NUMBERS:END -->"
OPS = ["HMAC-SHA256 tokenize", "AES-GCM encrypt (random nonce + AAD)", "AES-GCM decrypt"]


def crypto_numbers(text):
    """{operation: (sandbox, yours)} from docs/crypto.md; yours must be filled in and differ from the sandbox row."""
    out = {}
    for op in OPS:
        m = re.search(rf"^\| {re.escape(op)} \| *([\d.]+) *\| *([^|]*?) *\|", text, re.M)
        if not m:
            raise SystemExit(f"docs/crypto.md: no row for '{op}'")
        try:
            yours = float(m.group(2))
        except ValueError:
            raise SystemExit(f"docs/crypto.md: fill in your '{op}' number first (python scripts/bench_crypto.py)")
        out[op] = (float(m.group(1)), yours)
    if all(s == y for s, y in out.values()):
        raise SystemExit("docs/crypto.md: the (yours) column equals the sandbox column. Run scripts/bench_crypto.py and paste YOUR numbers")
    return out


def fmt(s):
    return "n/a" if s is None else f"{int(s // 60)}m{int(s % 60):02d}s"


def table(crypto, erasure, gates, days):
    rows = ["| Measurement | Value | Source |", "|---|---:|---|"]
    for op, (_, yours) in crypto.items():
        rows.append(f"| {op} | {yours:.2f} µs / field | `scripts/bench_crypto.py`, 100k fields ([crypto.md](docs/crypto.md)) |")
    rows.append(f"| Erasure, full workflow: first run | {erasure['first_s']:.2f} s | `scripts/bench_erasure.py`: {erasure['rows']} rows, "
                f"{erasure['tables']} tables, VACUUM, key destroyed, both proofs, audit row |")
    rows.append(f"| Erasure, idempotent rerun | {erasure['rerun_s']:.2f} s | same run, 0 rows, proofs re-verified |")
    for g, s in gates.items():
        if s["full_checks"]:
            rows.append(f"| `{g}` latency, full check (p50 / p90) | {fmt(s['p50'])} / {fmt(s['p90'])} | "
                        f"Actions API, {s['full_checks']} full runs, last {days} days (fast-path runs excluded) |")
    rej = " · ".join(f"{g} {s['rejections']}" for g, s in gates.items() if s["runs"])
    if rej:
        rows.append(f"| PRs rejected by each gate | {rej} | Actions API, last {days} days: the deliberate reds and any real ones |")
    rows.append("")
    rows.append(f"_Generated {time.strftime('%Y-%m-%d')} by `python scripts/readme_numbers.py` on the author's machine._")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args()
    crypto = crypto_numbers((REPO / "docs" / "crypto.md").read_text())
    b = subprocess.run([sys.executable, str(REPO / "scripts" / "bench_erasure.py"), "--json"], capture_output=True, text=True)
    if b.returncode != 0:
        raise SystemExit(f"bench_erasure failed:\n{b.stdout}{b.stderr}")
    erasure = json.loads(b.stdout.strip().splitlines()[-1])
    from scripts import gate_metrics_poller as p
    token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    repo = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                          capture_output=True, text=True).stdout.strip()
    if not token or not repo:
        raise SystemExit("gh is not logged in (gh auth login) or this is not the repo clone")
    since = (datetime.now(timezone.utc) - timedelta(days=a.days)).strftime("%Y-%m-%d")
    gates = p.collect(p.github_fetcher(token), repo, since)
    block = table(crypto, erasure, gates, a.days)
    if a.dry_run:
        print("\n".join(block))
        return 0
    text = README.read_text()
    if START not in text or END not in text:
        raise SystemExit(f"README.md lacks the {START} / {END} markers")
    i, j = text.index(START) + len(START), text.index(END)
    README.write_text(text[:i] + "\n" + "\n".join(block) + "\n" + text[j:])
    print(f"README.md: measured-numbers table rewritten ({len(block) - 4} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
