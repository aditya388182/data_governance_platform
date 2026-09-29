from __future__ import annotations

import os

os.environ.setdefault("RUST_LOG", "error")   # delta-rs/DataFusion log WARN noise to stderr otherwise
from pathlib import Path

import pyarrow as pa
import yaml

ROOT = Path(__file__).resolve().parents[1]


def platform(root: Path = ROOT) -> dict:
    p = Path(root) / "conf" / "platform.yml"
    return yaml.safe_load(p.read_text()) if p.exists() else {}


def uri(path: str) -> str:
    local = os.environ.get("LAKE_LOCAL_ROOT")
    for scheme in ("s3a://", "s3n://", "s3://"):
        if path.startswith(scheme):
            rest = path[len(scheme):].rstrip("/")
            return str(Path(local) / rest) if local else "s3://" + rest
    return path


def storage_options() -> dict | None:
    if os.environ.get("LAKE_LOCAL_ROOT"):
        return None
    cfg = platform().get("lake", {})
    endpoint = os.environ.get("LAKE_ENDPOINT", cfg.get("endpoint", "http://localhost:9000"))
    return {
        "AWS_ENDPOINT_URL": endpoint,
        "AWS_ACCESS_KEY_ID": os.environ.get("LAKE_ACCESS_KEY", "minioadmin"),
        "AWS_SECRET_ACCESS_KEY": os.environ.get("LAKE_SECRET_KEY", "minioadmin123"),
        "AWS_REGION": os.environ.get("LAKE_REGION", cfg.get("region", "us-east-1")),
        "AWS_ALLOW_HTTP": "true" if endpoint.startswith("http://") else "false",
        # single-writer demo: MinIO has no DynamoDB lock; each table has one writer at a time
        "AWS_S3_ALLOW_UNSAFE_RENAME": "true",
    }


def key_store_path() -> str:
    return os.environ.get("KEY_STORE_PATH", platform().get("key_store", "s3a://keystore/dek_store"))


def quarantine_path(dataset: str) -> str:
    root = os.environ.get("QUARANTINE_ROOT", platform().get("quarantine_root", "s3a://governance-lake/quarantine"))
    return f"{root.rstrip('/')}/{dataset.split('.')[-1]}"


def pushgateway_url() -> str:
    return os.environ.get("PUSHGATEWAY_URL", platform().get("pushgateway", "http://localhost:9091"))


def read_table(table, columns=None, storage_options=None, version: int | None = None) -> pa.Table:
    """Read a Delta table into Arrow through delta-rs's own engine (DataFusion QueryBuilder).

    Never DeltaTable.to_pyarrow_table / to_pyarrow_dataset: on that path pyarrow's C++
    threads call back into delta-rs's Rust filesystem, and with the pinned pyarrow 15 the
    process ABORTS AT EXIT ('terminate called without an active exception', exit 134) in
    most runs — after the work succeeded. An exit code that lies breaks `&&` chains,
    `set -e` scripts and Airflow task states (measured: 8/10 aborts vs 0/10 on this path).
    tests/runtime/test_read_path.py keeps the bad calls out of the code base."""
    from deltalake import DeltaTable, QueryBuilder
    if isinstance(table, DeltaTable):
        dt = table
    else:
        dt = DeltaTable(table, storage_options=storage_options, version=version)
    cols = ", ".join('"' + c.replace('"', '""') + '"' for c in columns) if columns else "*"
    return pa.table(QueryBuilder().register("t", dt).execute(f"select {cols} from t").read_all())
