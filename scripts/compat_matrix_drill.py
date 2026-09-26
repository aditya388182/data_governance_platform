#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import uuid

import requests

CT = {"Content-Type": "application/vnd.schemaregistry.v1+json"}
MODES = ("BACKWARD_TRANSITIVE", "BACKWARD", "FORWARD")


def rec(fields):
    return {"type": "record", "name": "Txn", "namespace": "drill", "fields": fields}


def F(name, typ, **kw):
    d = {"name": name, "type": typ}
    d.update(kw)
    return d


BASE = [F("id", "string"), F("currency", "string")]
E3 = {"type": "enum", "name": "EventType", "symbols": ["AUTHORIZED", "CAPTURED", "REFUNDED"]}
E4 = dict(E3, symbols=E3["symbols"] + ["CHARGEBACK"])
E2 = dict(E3, symbols=["AUTHORIZED", "CAPTURED"])
E3D = dict(E3, symbols=E3["symbols"] + ["UNKNOWN"], default="UNKNOWN")
E4D = dict(E3D, symbols=E3D["symbols"] + ["CHARGEBACK"])

OK, BRK = True, False
# (key, label, history (oldest..newest), proposed, oracle{mode: verdict}, why)
CASES = [
    ("add_nullable_default", "Add nullable field with default",
     [rec(BASE)], rec(BASE + [F("risk_score", ["null", "double"], default=None)]),
     {"BACKWARD_TRANSITIVE": OK, "BACKWARD": OK, "FORWARD": OK},
     "New reader fills the default for old records; old readers skip the unknown field. Safe both ways."),
    ("add_field_no_default", "Add field WITHOUT default",
     [rec(BASE)], rec(BASE + [F("channel", "string")]),
     {"BACKWARD_TRANSITIVE": BRK, "BACKWARD": BRK, "FORWARD": OK},
     "The new reader needs `channel` and old records don't have it — nothing to fill it with. This is the real reason a rename fails."),
    ("remove_field_with_default", "Remove field that had a default",
     [rec(BASE + [F("status", "string", default="PENDING")])], rec(BASE),
     {"BACKWARD_TRANSITIVE": OK, "BACKWARD": OK, "FORWARD": OK},
     "The new reader ignores the extra field in old data. Old readers fill the default for new data, so FORWARD holds too."),
    ("remove_field_no_default", "Remove field WITHOUT default",
     [rec(BASE + [F("merchant_id", "string")])], rec(BASE),
     {"BACKWARD_TRANSITIVE": OK, "BACKWARD": OK, "FORWARD": BRK},
     "BACKWARD passes: a reader simply skips fields it doesn't declare. It is FORWARD that breaks — old consumers still require merchant_id. (The original plan had this row inverted.)"),
    ("rename_no_alias", "Rename field (no alias)",
     [rec(BASE)], rec([F("id", "string"), F("currency_code", "string")]),
     {"BACKWARD_TRANSITIVE": BRK, "BACKWARD": BRK, "FORWARD": BRK},
     "A rename is remove `currency` + add `currency_code` with no default: the new reader can't fill currency_code from old data, and old readers lose currency."),
    ("rename_with_alias", "Rename field WITH alias",
     [rec(BASE)], rec([F("id", "string"), F("currency_code", "string", aliases=["currency"])]),
     {"BACKWARD_TRANSITIVE": OK, "BACKWARD": OK, "FORWARD": BRK},
     "The reader's alias maps old `currency` onto `currency_code`, so the registry is green — but un-upgraded consumers still break. Registry-green is not consumer-safe."),
    ("widen_int_long", "Widen int -> long",
     [rec([F("id", "string"), F("amt", "int")])], rec([F("id", "string"), F("amt", "long")]),
     {"BACKWARD_TRANSITIVE": OK, "BACKWARD": OK, "FORWARD": BRK},
     "Avro promotes int to long when reading. An old int reader cannot read long data."),
    ("narrow_long_int", "Narrow long -> int",
     [rec([F("id", "string"), F("amt", "long")])], rec([F("id", "string"), F("amt", "int")]),
     {"BACKWARD_TRANSITIVE": BRK, "BACKWARD": BRK, "FORWARD": OK},
     "No long-to-int promotion exists (it would truncate)."),
    ("add_enum_symbol", "Add enum symbol",
     [rec([F("id", "string"), F("event_type", E3)])], rec([F("id", "string"), F("event_type", E4)]),
     {"BACKWARD_TRANSITIVE": OK, "BACKWARD": OK, "FORWARD": BRK},
     "The counterintuitive row, stated correctly: BACKWARD passes (the new reader knows every old symbol). The break is FORWARD: an old consumer meets CHARGEBACK and fails. BACKWARD assumes consumers upgrade first, so a producer-first deploy crashes consumers while the registry is green. (The original plan marked this ❌ under BACKWARD — wrong.)"),
    ("remove_enum_symbol", "Remove enum symbol",
     [rec([F("id", "string"), F("event_type", E3)])], rec([F("id", "string"), F("event_type", E2)]),
     {"BACKWARD_TRANSITIVE": BRK, "BACKWARD": BRK, "FORWARD": OK},
     "Old data may contain REFUNDED, which the new reader no longer declares."),
    ("add_enum_symbol_reader_default", "Add enum symbol when the enum has a default",
     [rec([F("id", "string"), F("event_type", E3D)])], rec([F("id", "string"), F("event_type", E4D)]),
     {"BACKWARD_TRANSITIVE": OK, "BACKWARD": OK, "FORWARD": OK},
     "The fix for the enum trap: with an enum default (Avro >= 1.9), old readers map unknown symbols to UNKNOWN, so symbol additions become forward-safe."),
    ("transitive_drop_default", "Drop a default added in v2 (3-version history)",
     [rec([F("id", "string")]), rec([F("id", "string"), F("region", "string", default="US")])],
     rec([F("id", "string"), F("region", "string")]),
     {"BACKWARD_TRANSITIVE": BRK, "BACKWARD": OK, "FORWARD": OK},
     "Compatible with v2 (v2 data has region) but not with v1 (v1 data lacks region and there is no default any more). BACKWARD passes; BACKWARD_TRANSITIVE fails. This is why the contract declares _TRANSITIVE — and why the gate must replay history."),
]

def wait_fail_closed(reg: str, timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{reg}/subjects", timeout=5)
            if r.status_code == 200 and isinstance(r.json(), list):
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    print(f"::error::Registry at {reg} failed to start — FAILING CLOSED (drill aborted, nothing recorded)")
    sys.exit(2)


def put_mode(reg: str, subject: str, mode: str) -> None:
    r = requests.put(f"{reg}/config/{subject}", headers=CT, data=json.dumps({"compatibility": mode}), timeout=15)
    if r.status_code != 200:
        sys.exit(f"FAIL CLOSED: could not set {mode} on {subject}: HTTP {r.status_code} {r.text}")
    g = requests.get(f"{reg}/config/{subject}", timeout=15)
    if g.status_code != 200 or g.json().get("compatibilityLevel") != mode:
        sys.exit(f"FAIL CLOSED: {subject} reports {g.text} after setting {mode}")


def register(reg: str, subject: str, schema: dict) -> int:
    r = requests.post(f"{reg}/subjects/{subject}/versions", headers=CT,
                      data=json.dumps({"schema": json.dumps(schema)}), timeout=15)
    return r.status_code


def delete_subject(reg: str, subject: str) -> None:
    for q in ("", "?permanent=true"):
        try:
            requests.delete(f"{reg}/subjects/{subject}{q}", timeout=10)
        except requests.RequestException:
            pass


def run(reg: str) -> tuple[list[dict], int]:
    run_id = uuid.uuid4().hex[:8]
    results, mismatches = [], 0
    for key, label, hist, proposed, oracle, why in CASES:
        row = {"key": key, "label": label, "why": why, "verdicts": {}, "mismatch": []}
        for mode in MODES:
            subject = f"drill.{run_id}.{key}.{mode.lower()}-value"
            try:
                put_mode(reg, subject, "NONE")
                for h in hist:
                    code = register(reg, subject, h)
                    if code != 200:
                        sys.exit(f"FAIL CLOSED: history replay for {subject} returned HTTP {code}")
                put_mode(reg, subject, mode)
                code = register(reg, subject, proposed)
                if code not in (200, 409):
                    sys.exit(f"FAIL CLOSED: {subject} returned HTTP {code} (expected 200 or 409)")
                actual = code == 200
                row["verdicts"][mode] = actual
                if actual != oracle[mode]:
                    row["mismatch"].append(mode)
                    mismatches += 1
            finally:
                delete_subject(reg, subject)
        results.append(row)
    return results, mismatches


def mark(v: bool) -> str:
    return "✅" if v else "❌"


def render(results: list[dict], reg: str, mismatches: int) -> str:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Avro compatibility matrix — recorded from the live registry",
        "",
        f"Generated by `scripts/compat_matrix_drill.py` against `{reg}` on {now}.",
        "Every ✅/❌ below is the registry's own HTTP verdict (200 = accepted, 409 = rejected), not typed by hand.",
        "",
        "Columns: **BACKWARD_TRANSITIVE** is the contract mode (new reader vs *every* old version). "
        "**BACKWARD** is new reader vs the latest version only. **FORWARD** is what an *un-upgraded consumer* "
        "experiences when the producer deploys first.",
        "",
        "| Change | BACKWARD_TRANSITIVE (contract) | BACKWARD | FORWARD (old consumers) | Why |",
        "|---|:---:|:---:|:---:|---|",
    ]
    for r in results:
        cells = []
        for m in MODES:
            c = mark(r["verdicts"][m])
            if m in r["mismatch"]:
                c += " ⚠ oracle mismatch"
            cells.append(c)
        lines.append(f"| {r['label']} | {cells[0]} | {cells[1]} | {cells[2]} | {r['why']} |")
    lines += [
        "",
        "## What this table corrects in the original plan",
        "",
        "- **Removing a field without a default is BACKWARD-compatible.** A reader skips fields it does not declare. "
        "The failure is FORWARD (old consumers still require the field).",
        "- **Adding an enum symbol is BACKWARD-compatible.** The failure is FORWARD. The real trap: BACKWARD assumes "
        "consumers upgrade before producers. Deploy the producer first and old consumers crash on the new symbol while "
        "the registry shows green. Fix: give enums a `default`, or deploy consumers first — and that ordering is exactly "
        "what the Day 3 impact bot makes visible.",
        "- **Transitivity is only real if the registry holds history.** An empty CI registry with one registered "
        "version makes BACKWARD_TRANSITIVE identical to BACKWARD. The gate therefore replays every version of the "
        "schema file from main before judging a PR (see `scripts/ci/schema_gate.sh`). PR C on Day 1 proves it in CI.",
        "",
        f"Oracle mismatches in this run: **{mismatches}**.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=os.environ.get("REG", "http://localhost:18081"))
    ap.add_argument("--out", default="contracts/compat_matrix.md")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("REGISTRY_WAIT_TIMEOUT", "150")))
    a = ap.parse_args()
    wait_fail_closed(a.registry, a.timeout)
    results, mismatches = run(a.registry)
    md = render(results, a.registry, mismatches)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(md)
    w = max(len(r["label"]) for r in results)
    print(f"{'Change':<{w}}  B_TRANS  BACKWARD  FORWARD")
    for r in results:
        v = r["verdicts"]
        flag = "   <-- ORACLE MISMATCH: " + ",".join(r["mismatch"]) if r["mismatch"] else ""
        print(f"{r['label']:<{w}}  {mark(v['BACKWARD_TRANSITIVE']):^7}  {mark(v['BACKWARD']):^8}  {mark(v['FORWARD']):^7}{flag}")
    total = len(results) * len(MODES)
    print(f"\n{total - mismatches}/{total} verdicts match the oracle. Written: {a.out}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
