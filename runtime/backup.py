from __future__ import annotations

import hashlib
import json
import time

from govlib import lake
from runtime import objects

SOURCE_BUCKET = "governance-lake"


def root() -> str:
    return lake.platform().get("backups_root", "s3a://backups").rstrip("/")


def label_uri(label: str) -> str:
    return f"{root()}/{SOURCE_BUCKET}/{label}"


def table_uri(label: str, table: str) -> str:
    return f"{label_uri(label)}/{table}"


def labels() -> list[str]:
    keys = objects.list_keys(f"{root()}/{SOURCE_BUCKET}")
    return sorted({k.split("/")[0] for k in keys if k.endswith("/MANIFEST.json") and k.count("/") == 1})


def latest() -> str | None:
    ls = labels()
    return ls[-1] if ls else None


def manifest(label: str) -> dict:
    return json.loads(objects.read(f"{label_uri(label)}/MANIFEST.json"))


def freeze(label: str | None = None, source: str | None = None) -> dict:
    label = label or time.strftime("%Y-%m-%d", time.gmtime())
    source = source or f"s3a://{SOURCE_BUCKET}"
    if objects.list_keys(label_uri(label)):
        raise FileExistsError(f"backup {label} already exists — backups are never overwritten; use --label")
    entries = []
    for key in objects.list_keys(source):
        data = objects.read(f"{source}/{key}")
        objects.write(f"{label_uri(label)}/{key}", data)
        entries.append({"key": key, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    doc = {"label": label, "source": source, "created_ts": int(time.time() * 1000), "objects": entries,
           "note": "frozen before any erasure; never rewritten; key store excluded (separate bucket)"}
    objects.write(f"{label_uri(label)}/MANIFEST.json", json.dumps(doc, indent=1).encode())
    return doc


def verify(label: str) -> tuple[int, int, list[str]]:
    """Re-hash every object in the manifest. Returns (matching, total, problems)."""
    doc = manifest(label)
    bad = []
    for e in doc["objects"]:
        try:
            h = hashlib.sha256(objects.read(f"{label_uri(label)}/{e['key']}")).hexdigest()
        except Exception as ex:  # missing object
            bad.append(f"{e['key']}: {type(ex).__name__}")
            continue
        if h != e["sha256"]:
            bad.append(f"{e['key']}: sha256 differs")
    return len(doc["objects"]) - len(bad), len(doc["objects"]), bad


def tables(label: str) -> list[str]:
    """Delta tables inside a backup, as paths relative to the label (e.g. customers, quarantine/transactions)."""
    keys = objects.list_keys(label_uri(label))
    return sorted({k.split("/_delta_log/")[0] for k in keys if "/_delta_log/" in k})
