#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yaml  # noqa: E402

from govlib.contracts import load_registry, schema_fields  # noqa: E402

SCAN_SUFFIXES = (".py", ".sql")
COL_FUNCS = {"col", "column"}
PYSPARK_FUNCS = {
    "sum", "avg", "mean", "min", "max", "count", "countDistinct", "count_distinct", "first", "last",
    "collect_list", "collect_set", "approx_count_distinct", "stddev", "variance", "to_date",
    "to_timestamp", "from_unixtime", "lower", "upper", "trim", "coalesce", "sha2", "md5", "length",
    "abs", "round", "year", "month", "dayofmonth", "hour", "hash", "xxhash64", "asc", "desc",
    "isnull", "isnan", "struct", "array", "concat", "concat_ws", "greatest", "least", "when"}
COLUMN_METHODS = {"select", "groupBy", "groupby", "orderBy", "sort", "drop", "dropDuplicates",
                  "drop_duplicates", "cube", "rollup", "sortWithinPartitions", "repartition", "partitionBy"}
EXPR_METHODS = {"selectExpr", "expr", "filter", "where"}
SQL_WORDS = {"select", "from", "where", "and", "or", "not", "as", "case", "when", "then", "else",
             "end", "is", "null", "in", "like", "between", "distinct", "true", "false", "cast",
             "interval", "on", "join", "group", "by", "order", "asc", "desc", "limit", "having"}
IDENT = re.compile(r"(?<![\w'\"])([A-Za-z_]\w*(?:\.(?:[A-Za-z_]\w*|\*))*)(\s*\()?")
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
AS_ALIAS = re.compile(r"\bas\s+([A-Za-z_]\w*)", re.IGNORECASE)


@dataclass
class Loc:
    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass
class Usage:
    """How one consumer uses one dataset."""
    consumer: str
    dataset: str
    refs: list = field(default_factory=list)          # [(Loc, pattern)] pattern: subject|topic|delta_path|from_avro
    fields: dict = field(default_factory=dict)        # field -> [Loc]
    qualified: dict = field(default_factory=dict)     # alias-qualified field -> [Loc] (e.g. "e.status")
    wildcard: list = field(default_factory=list)      # [Loc]
    dynamic: list = field(default_factory=list)       # [Loc]

    def uses(self, name: str) -> list:
        """Locations proving use of `name`; [] if none. Wildcard/dynamic are reported separately."""
        return self.fields.get(name, [])


#  dataset matching
def _norm_path(s: str) -> str:
    return re.sub(r"^s3[an]?://", "", s.strip()).rstrip("/")


def dataset_patterns(registry) -> dict:
    out = {}
    for name, d in registry.items():
        topic = d.subject[:-len("-value")] if d.subject.endswith("-value") else d.subject
        out[name] = {"subject": d.subject, "topic": topic, "delta_path": _norm_path(d.delta_path)}
    return out


def _match_dataset(literal: str, pats: dict) -> str | None:
    pieces = [p.strip() for p in literal.split(",")]
    if pats["subject"] in pieces:
        return "subject"
    if pats["topic"] in pieces:
        return "topic"
    n = _norm_path(literal)
    if n == pats["delta_path"] or n.startswith(pats["delta_path"] + "/"):
        return "delta_path"
    return None


#  per-file extraction
@dataclass
class FileFacts:
    dataset_refs: list = field(default_factory=list)   # [(line, dataset, pattern)]
    from_avro: list = field(default_factory=list)      # [line]
    colnames: list = field(default_factory=list)       # [(line, raw column string)]
    exprs: list = field(default_factory=list)          # [(line, sql expression)]
    literals: list = field(default_factory=list)       # [(line, str)]
    wildcard: list = field(default_factory=list)       # [line]
    dynamic: list = field(default_factory=list)        # [line]
    aliases: set = field(default_factory=set)
    defined: set = field(default_factory=set)          # names the code itself creates


def _func_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_dynamic(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    if isinstance(node, ast.Call) and _func_name(node.func) in ("format", "join"):
        return True
    if isinstance(node, (ast.Name, ast.Subscript, ast.ListComp, ast.GeneratorExp)):
        return True
    if isinstance(node, ast.Starred):
        v = node.value
        return not (isinstance(v, (ast.List, ast.Tuple)) and all(isinstance(e, ast.Constant) for e in v.elts))
    return False


def _const_str(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def scan_python(src: str, pats_by_ds: dict) -> FileFacts:
    ff = FileFacts()
    tree = ast.parse(src)
    handled: set[int] = set()

    def column_args(args, line_default):
        for a in args:
            items = a.elts if isinstance(a, (ast.List, ast.Tuple)) else [a]
            if isinstance(a, ast.Starred) and isinstance(a.value, (ast.List, ast.Tuple)):
                items = a.value.elts
            for it in items:
                s = _const_str(it)
                if s is not None:
                    handled.add(id(it))
                    ff.colnames.append((it.lineno, s))
                elif _is_dynamic(it) or (isinstance(a, ast.Starred) and it is a):
                    ff.dynamic.append(getattr(it, "lineno", line_default))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _func_name(node.func)
            if name == "from_avro":
                ff.from_avro.append(node.lineno)
            if name == "alias" and node.args and _const_str(node.args[0]):
                ff.aliases.add(node.args[0].value)
                ff.defined.add(node.args[0].value)
                handled.add(id(node.args[0]))
            elif name in COL_FUNCS or (name in PYSPARK_FUNCS and isinstance(node.func, ast.Attribute)):
                for a in node.args:
                    s = _const_str(a)
                    if s is not None:
                        handled.add(id(a))
                        if s != "*":
                            ff.colnames.append((a.lineno, s))
                    elif name in COL_FUNCS and _is_dynamic(a):
                        ff.dynamic.append(node.lineno)
            elif name in COLUMN_METHODS and isinstance(node.func, ast.Attribute):
                column_args(node.args, node.lineno)
            elif name in EXPR_METHODS:
                for a in node.args:
                    s = _const_str(a)
                    if s is not None:
                        handled.add(id(a))
                        ff.exprs.append((a.lineno, s))
                    elif _is_dynamic(a):
                        ff.dynamic.append(node.lineno)
            elif name == "withColumn" and node.args:
                s = _const_str(node.args[0])
                if s:
                    ff.defined.add(s)
                    handled.add(id(node.args[0]))
            elif name == "withColumnRenamed" and len(node.args) >= 2:
                old, new = _const_str(node.args[0]), _const_str(node.args[1])
                if old:
                    ff.colnames.append((node.args[0].lineno, old))
                    handled.add(id(node.args[0]))
                if new:
                    ff.defined.add(new)
                    handled.add(id(node.args[1]))
        elif isinstance(node, ast.Subscript):
            s = _const_str(node.slice)
            if s is not None:
                handled.add(id(node.slice))
                ff.colnames.append((node.slice.lineno, s))

    for node in ast.walk(tree):
        s = _const_str(node)
        if s is None:
            continue
        for ds, pats in pats_by_ds.items():
            p = _match_dataset(s, pats)
            if p:
                ff.dataset_refs.append((node.lineno, ds, p))
        if id(node) not in handled:
            ff.literals.append((node.lineno, s))
    return ff


def scan_sql(src: str, pats_by_ds: dict) -> FileFacts:
    ff = FileFacts()
    for i, line in enumerate(src.splitlines(), 1):
        for lit in re.findall(r"'([^']*)'|`([^`]*)`", line):
            s = lit[0] or lit[1]
            for ds, pats in pats_by_ds.items():
                p = _match_dataset(s, pats)
                if p:
                    ff.dataset_refs.append((i, ds, p))
        for ds, pats in pats_by_ds.items():
            if pats["delta_path"] in line and not any(r[0] == i and r[1] == ds for r in ff.dataset_refs):
                ff.dataset_refs.append((i, ds, "delta_path"))
        ff.exprs.append((i, line))
    return ff


def expr_columns(expr: str):
    """(columns, defined aliases, wildcard?) from a SQL expression string."""
    defined = {m.group(1) for m in AS_ALIAS.finditer(expr)}
    body = QUOTED.sub(" ", expr)
    cols, wildcard = [], body.strip() in ("*",) or bool(re.fullmatch(r"\s*[A-Za-z_]\w*\.\*\s*", body))
    for m in IDENT.finditer(body):
        tok, is_call = m.group(1), bool(m.group(2))
        if is_call or tok.lower() in SQL_WORDS or tok in defined:
            continue
        if tok.endswith(".*"):
            wildcard = True
            continue
        cols.append(tok)
    return cols, defined, wildcard


#  consumer scan
def scan_consumers(root: Path, registry: dict, field_names: dict) -> dict:
    """{(consumer, dataset): Usage}. field_names: dataset -> set of names to treat as fields
    (the union of every schema version under consideration)."""
    pats = dataset_patterns(registry)
    out: dict = {}
    cdir = root / "consumers"
    if not cdir.is_dir():
        return out
    for consumer_dir in sorted(p for p in cdir.iterdir() if p.is_dir()):
        consumer = consumer_dir.name
        for f in sorted(consumer_dir.rglob("*")):
            if f.suffix not in SCAN_SUFFIXES or "__pycache__" in f.parts:
                continue
            rel = str(f.relative_to(consumer_dir))
            src = f.read_text(errors="replace")
            try:
                ff = scan_python(src, pats) if f.suffix == ".py" else scan_sql(src, pats)
            except SyntaxError as e:
                print(f"::warning title=lineage-scan::{consumer}/{rel} does not parse ({e.msg}); scanned as text")
                ff = scan_sql(src, pats)
            datasets = {ds for _, ds, _ in ff.dataset_refs}
            for ds in datasets:
                u = out.setdefault((consumer, ds), Usage(consumer, ds))
                for line, d, p in ff.dataset_refs:
                    if d == ds:
                        u.refs.append((Loc(rel, line), p))
                if any(p in ("subject", "topic") for _, d, p in ff.dataset_refs if d == ds):
                    u.refs.extend((Loc(rel, ln), "from_avro") for ln in ff.from_avro)
                known = field_names.get(ds, set())
                aliases = ff.aliases
                cand = []
                for line, s in ff.colnames:
                    s = s.strip()
                    if s == "*" or s.endswith(".*"):
                        u.wildcard.append(Loc(rel, line))
                        continue
                    cand.append((line, s))
                for line, e in ff.exprs:
                    cols, defined, wild = expr_columns(e)
                    ff.defined |= defined
                    if wild:
                        u.wildcard.append(Loc(rel, line))
                    cand.extend((line, c) for c in cols)
                for line, s in cand:
                    head, _, tail = s.rpartition(".")
                    name = tail if head else s
                    if head and head.split(".")[0] in aliases:
                        u.qualified.setdefault(name, []).append(Loc(rel, line))
                    if name in ff.defined and name not in known:
                        continue
                    u.fields.setdefault(name, []).append(Loc(rel, line))
                for line, s in ff.literals:
                    if s in known:
                        u.fields.setdefault(s, []).append(Loc(rel, line))
                u.dynamic.extend(Loc(rel, ln) for ln in ff.dynamic)
    for u in out.values():
        for k in u.fields:
            u.fields[k] = sorted({(l.path, l.line): l for l in u.fields[k]}.values(), key=lambda l: (l.path, l.line))
        u.refs = sorted(set((str(l), p) for l, p in u.refs))
        u.refs = [(Loc(s.rsplit(":", 1)[0], int(s.rsplit(":", 1)[1])), p) for s, p in u.refs]
    return out


#  lineage catalog
class LineageError(ValueError):
    pass


class Drift(tuple):
    """(level, code, message) that also carries an optional repo-relative file/line,
    so CI can annotate the consumer's code inline. Unpacks as a 3-tuple."""
    def __new__(cls, level, code, message, path=None, line=None):
        t = super().__new__(cls, (level, code, message))
        t.path, t.line = path, line
        return t


def load_lineage(root: Path) -> dict:
    """consumer -> {team, repo, reads, acks{pr(str): [ {fields, by, reason} ]}}. Raises LineageError."""
    out = {}
    d = root / "contracts" / "lineage"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.yml")):
        try:
            doc = yaml.safe_load(f.read_text()) or {}
        except yaml.YAMLError as e:
            raise LineageError(f"{f.name}: invalid YAML ({e})") from e
        for k in ("consumer", "team", "repo", "reads"):
            if not doc.get(k):
                raise LineageError(f"{f.name}: missing '{k}'")
        if doc["consumer"] != f.stem:
            raise LineageError(f"{f.name}: consumer '{doc['consumer']}' must match the file name")
        if not isinstance(doc["reads"], list):
            raise LineageError(f"{f.name}: 'reads' must be a list of dataset names")
        acks = {}
        for pr, entries in (doc.get("acks") or {}).items():
            if not str(pr).isdigit():
                raise LineageError(f"{f.name}: ack key '{pr}' must be a PR number")
            if isinstance(entries, dict):
                entries = [entries]
            for e in entries or []:
                if not isinstance(e, dict) or not e.get("fields") or not isinstance(e["fields"], list):
                    raise LineageError(f"{f.name}: ack for PR {pr} needs a non-empty 'fields' list")
            acks[str(pr)] = entries or []
        out[doc["consumer"]] = {"team": doc["team"], "repo": doc["repo"], "reads": list(doc["reads"]), "acks": acks}
    return out


def codeowners_rules(root: Path) -> set:
    p = root / "CODEOWNERS"
    if not p.exists():
        return set()
    rules = set()
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rules.add(line.split()[0].lstrip("/"))
    return rules


def drift(root: Path, registry: dict, usages: dict, lineage: dict, skip_fields: dict | None = None) -> list:
    """[(level, code, message)] with level in {warning, notice}.
    skip_fields: dataset -> fields removed by the change under review (reported by impact-bot
    itself, so they are not repeated as DANGLING)."""
    skip_fields = skip_fields or {}
    out = []
    contract_fields = {ds: {f.name for f in schema_fields(d.schema, root=root)} for ds, d in registry.items()}
    for (consumer, ds), u in sorted(usages.items()):
        first = u.refs[0] if u.refs else None
        if consumer not in lineage or ds not in lineage[consumer]["reads"]:
            where = f" via {first[1]} at consumers/{consumer}/{first[0]}" if first else ""
            out.append(Drift("warning", "UNREGISTERED CONSUMER",
                             f"consumers/{consumer} reads {ds}{where} but is not registered in contracts/lineage/ — "
                             f"register it (contracts/lineage/{consumer}.yml) before its first deploy",
                             f"consumers/{consumer}/{first[0].path}" if first else None, first[0].line if first else None))
        for fname, locs in sorted(u.qualified.items()):
            if fname not in contract_fields.get(ds, set()) and fname not in skip_fields.get(ds, set()):
                acked = [pr for pr, es in (lineage.get(consumer, {}).get("acks") or {}).items()
                         if any(fname in e["fields"] or "*" in e["fields"] for e in es)]
                note = f" (removal ACKed in #{', #'.join(acked)})" if acked else ""
                out.append(Drift("warning", "DANGLING FIELD",
                                 f"consumers/{consumer} uses '{fname}' at {locs[0]}, which is no longer in the {ds} contract{note}",
                                 f"consumers/{consumer}/{locs[0].path}", locs[0].line))
    for consumer, entry in sorted(lineage.items()):
        repo_dir = root / entry["repo"]
        if not repo_dir.is_dir():
            out.append(("warning", "MISSING CONSUMER REPO",
                        f"contracts/lineage/{consumer}.yml points to {entry['repo']}, which does not exist"))
        for ds in entry["reads"]:
            if ds not in registry:
                out.append(("warning", "UNKNOWN DATASET", f"contracts/lineage/{consumer}.yml reads '{ds}', which is not in contracts/registry.yml"))
            elif repo_dir.is_dir() and (consumer, ds) not in usages:
                out.append(("notice", "STALE LINEAGE", f"{consumer} is registered for {ds} but no reference was found in {entry['repo']}"))
        rules = codeowners_rules(root)
        if rules and f"contracts/lineage/{consumer}.yml" not in rules:
            out.append(("warning", "NO CODEOWNER",
                        f"contracts/lineage/{consumer}.yml has no dedicated CODEOWNERS rule — in an organization anyone "
                        f"could approve its ACKs"))
    return out


def main() -> int:
    registry = load_registry(root=ROOT)
    names = {ds: {f.name for f in schema_fields(d.schema, root=ROOT)} for ds, d in registry.items()}
    usages = scan_consumers(ROOT, registry, names)
    lineage = load_lineage(ROOT)
    for (consumer, ds), u in sorted(usages.items()):
        print(f"\n{consumer} -> {ds}")
        for loc, p in u.refs:
            print(f"  ref    {str(loc):34s} {p}")
        for f, locs in sorted(u.fields.items()):
            if f in names.get(ds, set()):
                print(f"  field  {f:20s} {', '.join(map(str, locs))}")
        if u.wildcard:
            print(f"  WILDCARD at {', '.join(map(str, u.wildcard))}")
        if u.dynamic:
            print(f"  DYNAMIC at {', '.join(map(str, u.dynamic))}")
    print()
    for d in drift(ROOT, registry, usages, lineage):
        loc = f" file={d.path},line={d.line}," if d.path else " "
        print(f"::{d[0]}{loc}title=lineage-scan::{d[1]}: {d[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
