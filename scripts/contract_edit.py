#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from govlib.contracts import load_registry  # noqa: E402


def write_schema(path: Path, doc: dict) -> None:
    head = {k: v for k, v in doc.items() if k != "fields"}
    out = ["{"] + [f'  "{k}": {json.dumps(v)},' for k, v in head.items()]
    out += ['  "fields": [', ",\n".join(f"    {json.dumps(f)}" for f in doc["fields"]), "  ]", "}"]
    path.write_text("\n".join(out) + "\n")


def edit_suite(path: Path, field: str, new: str | None) -> int:
    lines = path.read_text().splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if l.startswith("expectations:")) + 1
    blocks, cur = [], []
    for l in lines[start:]:
        if l.startswith("  - expectation_type:") and cur:
            blocks.append(cur)
            cur = []
        cur.append(l)
    if cur:
        blocks.append(cur)
    ref = re.compile(r"(column(?:_A|_B)?):\s*['\"]?" + re.escape(field) + r"['\"]?(?=\s*[,}\n])")
    kept, n = [], 0
    for b in blocks:
        text = "".join(b)
        if ref.search(text):
            n += 1
            if new:
                kept.append(ref.sub(lambda m: f"{m.group(1)}: {new}", text))
            continue
        kept.append(text)
    path.write_text("".join(lines[:start]) + "".join(kept))
    return n


ap = argparse.ArgumentParser()
ap.add_argument("op", choices=["drop", "rename"])
ap.add_argument("dataset")
ap.add_argument("field")
ap.add_argument("new", nargs="?")
ap.add_argument("--with-suite", action="store_true")
a = ap.parse_args()
ds = load_registry().get(a.dataset) or sys.exit(f"unknown dataset {a.dataset}")
p = Path(ds.schema)
doc = json.loads(p.read_text())
names = [f["name"] for f in doc["fields"]]
if a.field not in names:
    sys.exit(f"{a.field} is not a field of {a.dataset}")
if a.op == "drop":
    doc["fields"] = [f for f in doc["fields"] if f["name"] != a.field]
else:
    if not a.new:
        sys.exit("rename needs a new name")
    for f in doc["fields"]:
        if f["name"] == a.field:
            f["name"] = a.new
write_schema(p, doc)
msg = f"{p}: {a.op} {a.field}" + (f" -> {a.new}" if a.op == "rename" else "")
if a.with_suite:
    n = edit_suite(Path(ds.ge_suite), a.field, a.new if a.op == "rename" else None)
    msg += f"; {ds.ge_suite}: {'renamed' if a.op == 'rename' else 'removed'} {n} expectation(s)"
print(msg)
