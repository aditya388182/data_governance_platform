from __future__ import annotations

import os
from pathlib import Path

from govlib import lake


def split(uri: str) -> tuple[str, str]:
    for scheme in ("s3a://", "s3n://", "s3://"):
        if uri.startswith(scheme):
            bucket, _, key = uri[len(scheme):].partition("/")
            return bucket, key.strip("/")
    raise ValueError(f"not an s3 uri: {uri}")


def _local_root() -> str | None:
    return os.environ.get("LAKE_LOCAL_ROOT")


def _client():
    import boto3
    from botocore.config import Config
    so = lake.storage_options()
    return boto3.client("s3", endpoint_url=so["AWS_ENDPOINT_URL"], aws_access_key_id=so["AWS_ACCESS_KEY_ID"],
                        aws_secret_access_key=so["AWS_SECRET_ACCESS_KEY"], region_name=so["AWS_REGION"],
                        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}))


def list_keys(uri: str) -> list[str]:
    """Every object under `uri`, as keys relative to it, sorted."""
    bucket, prefix = split(uri)
    local = _local_root()
    if local:
        root = Path(local) / bucket / prefix
        if not root.exists():
            return []
        return sorted(str(f.relative_to(root)) for f in root.rglob("*") if f.is_file())
    pref = f"{prefix}/" if prefix else ""
    out: list[str] = []
    for page in _client().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=pref):
        out += [o["Key"][len(pref):] for o in page.get("Contents", [])]
    return sorted(out)


def read(uri: str) -> bytes:
    bucket, key = split(uri)
    local = _local_root()
    if local:
        return (Path(local) / bucket / key).read_bytes()
    return _client().get_object(Bucket=bucket, Key=key)["Body"].read()


def write(uri: str, data: bytes) -> None:
    bucket, key = split(uri)
    local = _local_root()
    if local:
        p = Path(local) / bucket / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return
    _client().put_object(Bucket=bucket, Key=key, Body=data)
