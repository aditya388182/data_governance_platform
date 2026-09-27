from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


def manifest_path(fixture: str | Path) -> Path:
    p = Path(fixture)
    return p.with_name(p.name.replace(".parquet", ".manifest.json"))


def content_hash(path: str | Path) -> str:
    rows = pq.read_table(path).to_pylist()
    blob = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def arrow_type(avro_type) -> tuple[pa.DataType, bool]:
    """Avro field type -> (arrow type, nullable). Fixtures mirror the structural contract."""
    nullable = False
    t = avro_type
    if isinstance(t, list):
        nullable = "null" in t
        rest = [x for x in t if x != "null"]
        if len(rest) != 1:
            raise ValueError(f"unsupported union {avro_type}")
        t = rest[0]
    if isinstance(t, dict):
        if t.get("type") == "enum":
            return pa.string(), nullable
        if t.get("type") in ("long", "int") and t.get("logicalType") in ("timestamp-millis", "timestamp-micros"):
            return pa.int64(), nullable
        t = t.get("type")
    mapping = {"string": pa.string(), "long": pa.int64(), "int": pa.int32(),
               "double": pa.float64(), "float": pa.float32(), "boolean": pa.bool_()}
    if t not in mapping:
        raise ValueError(f"unsupported Avro type {avro_type!r} for fixtures")
    return mapping[t], nullable
