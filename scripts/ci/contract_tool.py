#!/usr/bin/env python3
"""Contract tooling for the schema gate (Project 4, Stage 0).

  lint  [--registry contracts/registry.yml]
        Validates the contract registry. Fails (exit 1) on:
          - missing required keys, unknown/NONE compatibility modes
          - schema files that are missing, not JSON, not an Avro record,
            or have duplicate field names
          - duplicate subjects / schema paths / delta_paths
          - any *.avsc under contracts/schemas/ that no dataset references
            (an ungoverned schema is default-deny, same idea as the PII gate)

  plan  --base-registry FILE --changed-files FILE [--registry contracts/registry.yml]
        Decides WHAT the gate checks and UNDER WHICH MODE. Prints TSV rows:
          dataset  subject  schema_path  history_path  mode  reason
        - mode comes from the BASE branch (the contract in force); a PR that
          edits `compatibility:` gets ::warning:: and is judged by the old mode
        - a dataset is selected if its schema file changed, or (self-test) if
          registry.yml or any gate infrastructure changed
        - removing a governed dataset from the registry is refused (exit 1)

Stdout carries data only; human-readable notes and ::warning:: / ::error::
annotations go to stderr.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ALLOWED_MODES = {
    "BACKWARD", "BACKWARD_TRANSITIVE",
    "FORWARD", "FORWARD_TRANSITIVE",
    "FULL", "FULL_TRANSITIVE",
}
REQUIRED_KEYS = ("schema", "subject", "owner", "sla", "compatibility", "ge_suite", "delta_path")
SCHEMA_DIR = "contracts/schemas"
# A change to any of these re-runs the gate against EVERY dataset (self-test).
GATE_INFRA_PREFIXES = ("scripts/ci/", "infra/ci/", "tests/gate/")
GATE_INFRA_FILES = (".github/workflows/schema_gate.yml", "contracts/registry.yml", "requirements-ci.txt")


def err(msg: str) -> None:
    print(f"::error title=contract-lint::{msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"::warning title=schema-gate::{msg}", file=sys.stderr)


def load_registry(path: str) -> dict:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return {}
    doc = yaml.safe_load(p.read_text()) or {}
    if not isinstance(doc, dict):
        raise SystemExit(f"{path}: top level must be a mapping")
    ds = doc.get("datasets") or {}
    if not isinstance(ds, dict):
        raise SystemExit(f"{path}: 'datasets' must be a mapping")
    return ds


def lint(registry_path: str) -> int:
    datasets = load_registry(registry_path)
    problems = 0
    if not datasets:
        err(f"{registry_path}: no datasets declared")
        return 1
    seen = {"subject": {}, "schema": {}, "delta_path": {}}
    for name, ds in datasets.items():
        if not isinstance(ds, dict):
            err(f"{name}: entry must be a mapping"); problems += 1; continue
        missing = [k for k in REQUIRED_KEYS if not str(ds.get(k, "")).strip()]
        if missing:
            err(f"{name}: missing required key(s): {', '.join(missing)}"); problems += 1; continue
        mode = ds["compatibility"]
        if mode == "NONE":
            err(f"{name}: compatibility NONE is not allowed for a governed dataset"); problems += 1
        elif mode not in ALLOWED_MODES:
            err(f"{name}: unknown compatibility mode '{mode}' (allowed: {', '.join(sorted(ALLOWED_MODES))})"); problems += 1
        subj = ds["subject"]
        if not (subj.endswith("-value") or subj.endswith("-key")):
            err(f"{name}: subject '{subj}' must follow TopicNameStrategy (<topic>-value / <topic>-key)"); problems += 1
        if not str(ds["delta_path"]).startswith("s3a://"):
            err(f"{name}: delta_path must be an s3a:// URI"); problems += 1
        for key in ("subject", "schema", "delta_path"):
            val = ds[key]
            if val in seen[key]:
                err(f"{name}: {key} '{val}' already used by {seen[key][val]}"); problems += 1
            seen[key][val] = name
        sp = Path(ds["schema"])
        if not sp.exists():
            err(f"{name}: schema file {sp} does not exist"); problems += 1; continue
        try:
            sch = json.loads(sp.read_text())
        except json.JSONDecodeError as e:
            err(f"{name}: {sp} is not valid JSON ({e})"); problems += 1; continue
        if not isinstance(sch, dict) or sch.get("type") != "record" or not sch.get("name"):
            err(f"{name}: {sp} must be an Avro record with a name"); problems += 1; continue
        fields = sch.get("fields") or []
        names = [f.get("name") for f in fields if isinstance(f, dict)]
        if not names:
            err(f"{name}: {sp} has no fields"); problems += 1
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            err(f"{name}: {sp} has duplicate field names: {', '.join(dupes)}"); problems += 1
    referenced = {str(Path(ds["schema"])) for ds in datasets.values() if isinstance(ds, dict) and ds.get("schema")}
    for avsc in sorted(Path(SCHEMA_DIR).glob("*.avsc")):
        if str(avsc) not in referenced:
            err(f"{avsc} is not referenced by any dataset in {registry_path} — ungoverned schemas are not allowed")
            problems += 1
    if problems:
        print(f"contract lint: {problems} problem(s)", file=sys.stderr)
        return 1
    print(f"contract lint: OK ({len(datasets)} dataset(s))", file=sys.stderr)
    return 0


def plan(registry_path: str, base_registry_path: str, changed_files_path: str) -> int:
    pr = load_registry(registry_path)
    base = load_registry(base_registry_path)
    changed = [l.strip() for l in Path(changed_files_path).read_text().splitlines() if l.strip()]
    infra_changed = any(c.startswith(GATE_INFRA_PREFIXES) or c in GATE_INFRA_FILES for c in changed)

    removed = sorted(set(base) - set(pr))
    if removed:
        for name in removed:
            err(f"dataset '{name}' was removed from the registry — retiring a governed dataset "
                f"requires a deprecation process with consumer sign-off, not a schema-gate PR")
        return 1

    rows = []
    for name, ds in pr.items():
        b = base.get(name)
        if b:
            mode = b["compatibility"]
            if ds["compatibility"] != mode:
                warn(f"{name}: compatibility change {mode} -> {ds['compatibility']} detected. "
                     f"This PR is judged under the mode in force on main ({mode}); "
                     f"the new mode applies only after this change merges on its own.")
            if ds["subject"] != b["subject"]:
                warn(f"{name}: subject rename {b['subject']} -> {ds['subject']} — producers and consumers must migrate")
            history_path = b["schema"]
        else:
            mode = ds["compatibility"]
            history_path = ds["schema"]
        if ds["schema"] in changed or history_path in changed:
            reason = "schema changed"
        elif infra_changed:
            reason = "self-test (registry.yml or gate infrastructure changed)"
        else:
            continue
        rows.append((name, ds["subject"], ds["schema"], history_path, mode, reason))

    for r in rows:
        print("\t".join(r))
    print(f"gate plan: {len(rows)} dataset(s) selected", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("lint")
    l.add_argument("--registry", default="contracts/registry.yml")
    p = sub.add_parser("plan")
    p.add_argument("--registry", default="contracts/registry.yml")
    p.add_argument("--base-registry", required=True)
    p.add_argument("--changed-files", required=True)
    a = ap.parse_args()
    if a.cmd == "lint":
        return lint(a.registry)
    return plan(a.registry, a.base_registry, a.changed_files)


if __name__ == "__main__":
    sys.exit(main())
