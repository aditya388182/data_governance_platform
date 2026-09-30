#!/usr/bin/env python3
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REQUIRED = ["00_five_required_checks.png", "01_schema_gate_green.png", "02_schema_gate_red.png", "03_fail_closed_red.png",
            "04_compat_matrix.png", "05_ge_gate_red.png", "06_pii_gate_red.png", "07_impact_bot_comment.png",
            "07b_impact_bot_acked.png", "08_lineage_drift_warn.png", "09_quarantine_rows.png", "10_quarantine_alert.png",
            "11_timetravel_fail.png", "12_crypto_shred_undecryptable.png", "13_audit_records.png",
            "14_tf_plan_workspaces.png", "14b_tf_state_lock.png", "15_drift_alert.png", "16_grafana_governance_health.png"]


def check(repo=REPO):
    text = (repo / "README.md").read_text()
    refs = set(re.findall(r"docs/screenshots/([\w.-]+\.png)", text))
    files = {p.name for p in (repo / "docs" / "screenshots").glob("*.png")}
    return sorted(refs - files), sorted(set(REQUIRED) - refs), sorted(set(REQUIRED) - files), sorted(files - refs)


if __name__ == "__main__":
    broken, unreferenced_required, missing_required, extra = check()
    for x in broken:
        print(f"BROKEN LINK: README references docs/screenshots/{x}, which does not exist")
    for x in missing_required:
        print(f"MISSING: docs/screenshots/{x}")
    for x in unreferenced_required:
        print(f"NOT IN README: {x}")
    for x in extra:
        print(f"note: docs/screenshots/{x} exists but README does not show it")
    ok = not (broken or missing_required or unreferenced_required)
    print(f"{len(REQUIRED)} required screenshots: {'ALL PRESENT AND REFERENCED' if ok else 'INCOMPLETE'}")
    sys.exit(0 if ok else 1)
