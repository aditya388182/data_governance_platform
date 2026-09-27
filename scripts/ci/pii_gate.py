#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from govlib.contracts import (CLASS_RANK, PROTECTED_CLASSES, TREATMENT_RANK, VALID_CLASSES,  # noqa: E402
                              VALID_TREATMENTS, load_catalog, load_registry, schema_fields)
from govlib.pii import compile_patterns, matches  # noqa: E402

errors: list[str] = []
rows: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)
    print(f"::error title=pii-gate::{msg}")


def warn(msg: str) -> None:
    print(f"::warning title=pii-gate::{msg}")


def md(pattern: str) -> str:
    return pattern.replace("|", "\\|")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-root", required=True)
    a = ap.parse_args()
    root, base_root = Path("."), Path(a.base_root)

    registry = load_registry(root=root)
    catalog = load_catalog(root=root)
    base_registry = load_registry(root=base_root)
    base_catalog = load_catalog(root=base_root)
    if catalog is None:
        err("contracts/pii_catalog.yml is missing — every governed repo needs a PII catalog")
        return finish()

    #  which patterns apply: main's (the contract in force)
    if base_catalog and base_catalog.name_patterns:
        patterns = base_catalog.name_patterns
        if catalog.name_patterns != base_catalog.name_patterns:
            warn("detection.name_patterns changed in this PR. This PR is judged with the patterns on main; "
                 "the new patterns apply after this change merges on its own (privacy-owner review).")
    else:
        patterns = catalog.name_patterns
    if not patterns:
        err("no detection.name_patterns defined — detection cannot be empty")
        return finish()
    compiled = compile_patterns(patterns)

    tables = {d.table: d for d in registry.values()}
    fields_by_table = {t: [f.name for f in schema_fields(d.schema, root=root)] for t, d in tables.items()}
    base_tables = {d.table: d for d in base_registry.values()}
    base_fields = {t: {f.name for f in schema_fields(d.schema, root=base_root)} for t, d in base_tables.items()}

    #  2. catalog lint
    if not catalog.subject_key:
        err("subject_key is not set — erasure cannot locate a person's rows without it")
    for key, e in catalog.columns.items():
        table, dot, col = key.partition(".")
        if not dot or not col:
            err(f"catalog key '{key}' must be '<table>.<column>'")
            continue
        if table not in tables:
            err(f"catalog entry '{key}': no dataset in contracts/registry.yml has table '{table}' (tables: {sorted(tables)})")
            continue
        cls, tr = e.get("class"), e.get("treatment")
        if cls not in VALID_CLASSES:
            err(f"'{key}': class '{cls}' is not one of {sorted(VALID_CLASSES)}")
        if tr not in VALID_TREATMENTS:
            err(f"'{key}': treatment '{tr}' is not one of {sorted(VALID_TREATMENTS)}")
        if cls in PROTECTED_CLASSES and tr not in ("TOKENIZE_HMAC", "ENCRYPT_AESGCM"):
            err(f"'{key}': {cls} requires treatment TOKENIZE_HMAC or ENCRYPT_AESGCM (got {tr})")
        if cls == "NOT_PII":
            if tr != "NONE":
                err(f"'{key}': NOT_PII must have treatment NONE")
            if not str(e.get("reason", "")).strip():
                err(f"'{key}': NOT_PII needs a written reason — declassification is a reviewed decision")
        if col not in fields_by_table.get(table, []):
            warn(f"catalog entry '{key}' refers to a column that is not in the {table} schema (stale entry?)")
    for table, flds in fields_by_table.items():
        has_protected = any(k.startswith(f"{table}.") and v.get("class") in PROTECTED_CLASSES for k, v in catalog.columns.items())
        if has_protected and catalog.subject_key and catalog.subject_key not in flds:
            err(f"table '{table}' holds protected columns but has no subject key column '{catalog.subject_key}' — erasure could not find its rows")

    #  1. detection
    for table, flds in fields_by_table.items():
        for f in flds:
            hits = matches(f, compiled)
            key = f"{table}.{f}"
            entry = catalog.columns.get(key)
            new = f not in base_fields.get(table, set())
            if hits and entry is None:
                err(f"UNCLASSIFIED PII field '{f}' in {table} ({'new in this PR' if new else 'already on main'}; "
                    f"matched /{hits[0]}/) — add '{key}' to contracts/pii_catalog.yml with a class and treatment")
                rows.append(f"| `{key}` | — | — | detected `{md(hits[0])}` | ❌ unclassified |")
            elif entry is not None:
                src = f"detected `{md(hits[0])}`" if hits else "catalogued"
                rows.append(f"| `{key}` | {entry.get('class')} | {entry.get('treatment')} | {src} | ✅ |")

    #  3. changes vs main
    if base_catalog:
        for key, be in base_catalog.columns.items():
            table, _, col = key.partition(".")
            now = catalog.columns.get(key)
            still_exists = col in fields_by_table.get(table, [])
            if now is None:
                if still_exists and be.get("class") in PROTECTED_CLASSES:
                    err(f"'{key}' ({be.get('class')}) was deleted from the catalog while the column still exists. "
                        f"Declassify explicitly (class NOT_PII + reason) so the decision is reviewed")
                continue
            if CLASS_RANK.get(now.get("class"), 0) < CLASS_RANK.get(be.get("class"), 0) or \
               TREATMENT_RANK.get(now.get("treatment"), 0) < TREATMENT_RANK.get(be.get("treatment"), 0):
                warn(f"classification DOWNGRADE '{key}': {be.get('class')}/{be.get('treatment')} -> "
                     f"{now.get('class')}/{now.get('treatment')} — requires privacy-owner review")
    return finish()


def finish() -> int:
    report = "### pii-gate\n\n| column | class | treatment | source | verdict |\n|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    print("\n" + report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(report)
    print("pii-gate: PASS" if not errors else f"pii-gate: FAIL — {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
