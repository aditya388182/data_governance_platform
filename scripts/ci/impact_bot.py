#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from govlib.contracts import load_registry, schema_fields  # noqa: E402
from scripts.lineage_scan import LineageError, drift, load_lineage, scan_consumers  # noqa: E402

MARK = "<!-- impact-bot -->"


def annotate(level: str, msg: str, path: str | None = None, line: int | None = None) -> None:
    loc = f" file={path},line={line}," if path else " "
    print(f"::{level}{loc}title=impact-bot::{msg}")


def field_diff(base_root: Path, head_root: Path, base_reg: dict, head_reg: dict) -> dict:
    out = {}
    for ds, bd in base_reg.items():
        bf = {f.name: f for f in schema_fields(bd.schema, root=base_root)}
        if ds not in head_reg:
            out[ds] = {"removed": sorted(bf), "renamed": {}, "changed": [], "dataset_removed": True, "subject": bd.subject}
            continue
        hf = {f.name: f for f in schema_fields(head_reg[ds].schema, root=head_root)}
        removed = [n for n in bf if n not in hf]
        added = [n for n in hf if n not in bf]
        renamed = {}
        for r in removed:
            same = [a for a in added if json.dumps(hf[a].avro_type, sort_keys=True) == json.dumps(bf[r].avro_type, sort_keys=True)
                    and a not in renamed.values()]
            if len(same) == 1:
                renamed[r] = same[0]
        changed = [n for n in bf if n in hf and (json.dumps(bf[n].avro_type, sort_keys=True) != json.dumps(hf[n].avro_type, sort_keys=True))]
        if removed or changed:
            out[ds] = {"removed": removed, "renamed": renamed, "changed": changed, "dataset_removed": False,
                       "subject": head_reg[ds].subject}
    return out


def acked_fields(acks_lineage: dict, consumer: str, pr: str) -> set:
    entries = (acks_lineage.get(consumer) or {}).get("acks", {}).get(pr, [])
    return {f for e in entries for f in e["fields"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-root", required=True)
    ap.add_argument("--acks-root", required=True)
    ap.add_argument("--pr", default=os.environ.get("PR_NUMBER", "0"))
    ap.add_argument("--out", required=True, help="output dir for verdict.json + comment.md (outside the repo)")
    a = ap.parse_args()
    head, base, acks_root, pr = Path("."), Path(a.base_root), Path(a.acks_root), str(a.pr)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    head_reg, base_reg = load_registry(root=head), load_registry(root=base)
    errors: list[str] = []
    try:
        lineage = load_lineage(head)
    except LineageError as e:
        errors.append(f"invalid lineage catalog on this branch: {e}")
        lineage = {}
    try:
        main_lineage = load_lineage(acks_root)
    except LineageError as e:
        errors.append(f"invalid lineage catalog on main (ACK source): {e}")
        main_lineage = {}

    diff = field_diff(base, head, base_reg, head_reg)
    names = {}
    for ds in set(base_reg) | set(head_reg):
        s = set()
        if ds in base_reg:
            s |= {f.name for f in schema_fields(base_reg[ds].schema, root=base)}
        if ds in head_reg:
            s |= {f.name for f in schema_fields(head_reg[ds].schema, root=head)}
        names[ds] = s
    scan_reg = dict(base_reg)
    scan_reg.update(head_reg)
    usages = scan_consumers(head, scan_reg, names)

    # ACK changes inside this PR never count (they must merge to main first)
    for c, entry in lineage.items():
        if entry.get("acks", {}) != (main_lineage.get(c) or {}).get("acks", {}):
            annotate("notice", f"this PR changes ACKs in contracts/lineage/{c}.yml. ACKs count only from main, "
                               f"so they take effect for the PR they name once this change merges")

    rows, blocking, unregistered_hits = [], [], []
    for ds, d in sorted(diff.items()):
        removed = d["removed"]
        for (consumer, uds), u in sorted(usages.items()):
            if uds != ds:
                continue
            reg = lineage.get(consumer) or main_lineage.get(consumer)
            # Registered on main OR in this PR (a registration in flight still protects the
            # consumer); deleting a lineage file in the PR cannot unregister it (main counts).
            # ACKs, however, count only from main.
            registered = any(consumer in L and ds in L[consumer]["reads"] for L in (main_lineage, lineage))
            team = reg["team"] if reg else "(unregistered)"
            hits = {f: u.uses(f) for f in removed if u.uses(f)}
            blanket = [("wildcard", u.wildcard), ("dynamic", u.dynamic)]
            blanket = [(k, v) for k, v in blanket if v]
            affected = sorted(set(hits) | (set(removed) if blanket else set()))
            acked = acked_fields(main_lineage, consumer, pr)
            missing = [f for f in affected if f not in acked and "*" not in acked]
            status = "—"
            first_use = next((l for f in sorted(hits) for l in hits[f]), None)
            if affected:
                if not registered:
                    status = "unregistered — cannot ACK"
                    unregistered_hits.append((consumer, ds, affected))
                elif missing:
                    status = "missing"
                    blocking.append({"consumer": consumer, "team": team, "dataset": ds, "fields": missing,
                                     "at": f"consumers/{consumer}/{first_use.path}:{first_use.line}" if first_use else None})
                else:
                    status = f"✅ ACKed on main for #{pr}"
            refs = ", ".join(f"`{loc}` ({p})" for loc, p in u.refs[:3])
            if affected:
                why = "; ".join(f"`{f}` at {', '.join(f'`{l}`' for l in locs[:3])}" for f, locs in sorted(hits.items()))
                for k, locs in blanket:
                    why += ("; " if why else "") + f"{k} column use at {', '.join(f'`{l}`' for l in locs[:2])} (treated as using every field)"
                uses = f"**YES** — {why}"
            else:
                uses = "no"
            rows.append((ds, consumer, team, refs, uses, status))

    warnings = drift(head, head_reg, usages, lineage, skip_fields={ds: set(d["removed"]) for ds, d in diff.items()})
    for consumer, ds, fields in unregistered_hits:
        warnings.append(("warning", "UNREGISTERED CONSUMER USES REMOVED FIELD",
                         f"consumers/{consumer} uses {', '.join(fields)} of {ds} but is not registered, so it cannot ACK"))
    for e in errors:
        annotate("error", e)
    for w in warnings:
        annotate(w[0], f"{w[1]}: {w[2]}", getattr(w, "path", None), getattr(w, "line", None))
    for b in blocking:
        path, _, line = (b.get("at") or "::").rpartition(":")
        annotate("error", f"BLOCKED: {b['consumer']} ({b['team']}) uses removed field(s) {', '.join(b['fields'])} of "
                          f"{b['dataset']} and has not ACKed PR #{pr} on main (contracts/lineage/{b['consumer']}.yml)",
                 path or None, int(line) if line else None)

    #  comment
    breaking = any(d["removed"] for d in diff.values())
    lines = [MARK]
    if not diff:
        lines += ["###  Impact analysis", "", "No field removals or type changes in the current diff."]
    else:
        lines += [f"### {'⛔' if blocking else '⚠️'} Impact analysis — PR #{pr}", ""]
        for ds, d in sorted(diff.items()):
            what = []
            if d["dataset_removed"]:
                what.append("removes the **whole dataset**")
            for r in d["removed"]:
                what.append(f"removes `{r}`" + (f" (renamed to `{d['renamed'][r]}`?)" if r in d["renamed"] else ""))
            what += [f"changes the type of `{c}`" for c in d["changed"]]
            lines += [f"**{ds}** (`{d['subject']}`) — this PR {', '.join(what)}.", "",
                      "| Consumer | Team | References | Uses removed field | ACK (read from main) |",
                      "|---|---|---|---|---|"]
            lines += [f"| {c} | `{t}` | {r} | {u} | {s} |" for (rds, c, t, r, u, s) in rows if rds == ds]
            if not [1 for row in rows if row[0] == ds]:
                lines.append("| _no consumer references this dataset_ | | | | |")
            lines.append("")
        if blocking:
            teams = sorted({b["team"] for b in blocking})
            lines += [f"**Merge blocked** until {', '.join(f'`{t}`' for t in teams)} ACK the removal for PR #{pr} on main.", "",
                      "<details><summary>How a consumer team ACKs</summary>", "",
                      "```bash",
                      *[f"python scripts/ack.py {b['consumer']} --pr {pr} --fields {','.join(b['fields'])} --reason \"<why this is safe for us>\""
                        for b in blocking],
                      "```", "",
                      "Open that change as its own PR and merge it. The ACK counts only once it is on main. "
                      "Then update this PR's branch (`gh pr update-branch`) so impact-bot re-runs.", "</details>"]
        elif breaking:
            lines += ["Every affected registered consumer has ACKed this PR on main."]
    shown = [w for w in warnings if w[0] == "warning"]
    if shown and diff:
        lines += ["", "**Lineage drift**", ""] + [f"- ⚠️ **{code}**: {msg}" for _, code, msg in shown]
    lines += ["", "<sub>impact-bot · field diff vs merge-base · consumers scanned with `scripts/lineage_scan.py` · "
                  "ACKs read from `main` only</sub>"]
    (out_dir / "comment.md").write_text("\n".join(lines) + "\n")
    verdict = {"pr": pr, "blocked": bool(blocking or errors), "blocking": blocking, "errors": errors,
               "diff": diff, "comment": bool(diff), "label": breaking,
               "review_teams": sorted({b["team"] for b in blocking}),
               "warnings": [{"level": l, "code": c, "message": m} for l, c, m in warnings]}
    (out_dir / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n")

    summary = ["### impact-bot", ""]
    summary += [f"- **{ds}**: removed {d['removed'] or '—'}, changed {d['changed'] or '—'}" for ds, d in sorted(diff.items())] or ["- no field removals or type changes"]
    summary += [f"- ⚠️ {c}: {m}" for l, c, m in warnings if l == "warning"]
    summary += [f"- ⛔ blocked: {b['consumer']} needs an ACK for {', '.join(b['fields'])}" for b in blocking]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("\n".join(summary) + "\n")
    print("\n".join(summary))
    print("impact-bot: BLOCKED" if verdict["blocked"] else "impact-bot: PASS")
    return 1 if verdict["blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
