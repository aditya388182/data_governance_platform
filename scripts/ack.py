#!/usr/bin/env python3
"""Record a consumer team's ACK for field removals in one PR.

  python scripts/ack.py fraud-service --pr 11 --fields status --reason "status is informational for us"

Edits contracts/lineage/<consumer>.yml (the consumer-owned file). Open the change
as its own PR: impact-bot honours ACKs only once they are on main.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

import yaml

ap = argparse.ArgumentParser()
ap.add_argument("consumer")
ap.add_argument("--pr", required=True, type=int)
ap.add_argument("--fields", required=True, help="comma-separated field names")
ap.add_argument("--reason", required=True)
ap.add_argument("--by", default=None, help="defaults to the consumer's team")
a = ap.parse_args()
p = Path("contracts/lineage") / f"{a.consumer}.yml"
if not p.exists():
    sys.exit(f"{p} not found — the consumer must be registered before it can ACK")
text = p.read_text()
doc = yaml.safe_load(text) or {}
acks = {str(k): (v if isinstance(v, list) else [v]) for k, v in (doc.get("acks") or {}).items()}
entry = {"fields": [f.strip() for f in a.fields.split(",") if f.strip()], "by": a.by or doc["team"],
         "reason": a.reason, "date": dt.date.today().isoformat()}
acks.setdefault(str(a.pr), []).append(entry)
lines = text.splitlines()
idx = next(i for i, l in enumerate(lines) if l.startswith("acks:"))
block = yaml.safe_dump({"acks": acks}, sort_keys=False, default_flow_style=False, allow_unicode=True).rstrip("\n")
p.write_text("\n".join(lines[:idx] + [block]) + "\n")
print(f"{p}: {doc['team']} ACKs {entry['fields']} for PR #{a.pr}")
