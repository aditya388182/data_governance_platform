from __future__ import annotations

import hashlib
import json

import pyarrow as pa
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError

from govlib.lake import read_table

EVIDENCE = [("dataset", pa.string()), ("suite", pa.string()), ("expectation", pa.list_(pa.string())),
            ("run_ts", pa.int64()), ("checkpoint_id", pa.string()), ("source_version", pa.int64()),
            ("owner", pa.string()), ("row_fingerprint", pa.string())]
EVIDENCE_NAMES = [n for n, _ in EVIDENCE]


def label(r) -> str:
    return f"{r.expectation_type}({r.target})" if r.target else r.expectation_type


def offending_rows(results) -> dict[int, list[str]]:
    rows: dict[int, list[str]] = {}
    for r in results:
        if not r.success and r.unexpected_index_list:
            for i in r.unexpected_index_list:
                rows.setdefault(i, []).append(label(r))
    return rows


def table_level_failures(results) -> list[str]:
    """Failures that name no rows (row count, schema, execution errors): alert, nothing to copy."""
    return [label(r) + (f" [error: {r.error}]" if r.error else "") for r in results
            if not r.success and not r.unexpected_index_list]


def fingerprint(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def build(source: pa.Table, offenders: dict[int, list[str]], meta: dict) -> pa.Table:
    clash = set(EVIDENCE_NAMES) & set(source.column_names)
    if clash:
        raise ValueError(f"source has column(s) {sorted(clash)} that collide with quarantine evidence columns")
    idx = sorted(offenders)
    rows = source.take(pa.array(idx, type=pa.int64()))
    n = len(idx)
    ev = {"dataset": [meta["dataset"]] * n, "suite": [meta["suite"]] * n, "expectation": [offenders[i] for i in idx],
          "run_ts": [meta["run_ts"]] * n, "checkpoint_id": [meta["checkpoint_id"]] * n,
          "source_version": [meta["source_version"]] * n, "owner": [meta["owner"]] * n,
          "row_fingerprint": [fingerprint(r) for r in rows.to_pylist()]}
    for name, typ in EVIDENCE:
        rows = rows.append_column(pa.field(name, typ), pa.array(ev[name], type=typ))
    return rows


def existing_fingerprints(uri: str, storage_options=None) -> set[str]:
    try:
        dt = DeltaTable(uri, storage_options=storage_options)
    except TableNotFoundError:
        return set()
    return set(read_table(dt, ["row_fingerprint"]).column(0).to_pylist())


def append_new(uri: str, qtable: pa.Table, storage_options=None) -> tuple[int, int]:
    """Append rows not yet quarantined. Returns (new_rows, total_rows_in_quarantine)."""
    seen = existing_fingerprints(uri, storage_options)
    keep = [i for i, f in enumerate(qtable.column("row_fingerprint").to_pylist()) if f not in seen]
    if keep:
        write_deltalake(uri, qtable.take(pa.array(keep, type=pa.int64())), mode="append", schema_mode="merge",
                        storage_options=storage_options)
    return len(keep), len(seen) + len(keep)
