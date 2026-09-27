from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Field:
    name: str
    nullable: bool      # union containing "null"
    has_default: bool
    avro_type: object


@dataclass(frozen=True)
class Dataset:
    name: str           # registry key, e.g. "payments.transactions"
    table: str          # last segment of delta_path, e.g. "transactions" (catalog key prefix)
    schema: str
    subject: str
    owner: str
    compatibility: str
    ge_suite: str
    delta_path: str
    fixture: str | None


def load_registry(path: str | Path = "contracts/registry.yml", root: str | Path = ".") -> dict[str, Dataset]:
    p = Path(root) / path
    if not p.exists() or p.stat().st_size == 0:
        return {}
    doc = yaml.safe_load(p.read_text()) or {}
    out: dict[str, Dataset] = {}
    for name, d in (doc.get("datasets") or {}).items():
        out[name] = Dataset(
            name=name,
            table=str(d["delta_path"]).rstrip("/").rsplit("/", 1)[-1],
            schema=d["schema"], subject=d["subject"], owner=d["owner"],
            compatibility=d["compatibility"], ge_suite=d["ge_suite"],
            delta_path=d["delta_path"], fixture=d.get("fixture"),
        )
    return out


def schema_fields(schema_path: str | Path, root: str | Path = ".") -> list[Field]:
    p = Path(root) / schema_path
    if not p.exists():
        return []
    doc = json.loads(p.read_text())
    fields = []
    for f in doc.get("fields", []):
        t = f["type"]
        nullable = isinstance(t, list) and "null" in t
        fields.append(Field(f["name"], nullable, "default" in f, t))
    return fields


def load_schema(schema_path: str | Path, root: str | Path = ".") -> dict:
    return json.loads((Path(root) / schema_path).read_text())


# ---------------------------------------------------------------- PII catalog
VALID_CLASSES = {"DIRECT_IDENTIFIER", "QUASI_IDENTIFIER", "NOT_PII"}
VALID_TREATMENTS = {"TOKENIZE_HMAC", "ENCRYPT_AESGCM", "NONE"}
PROTECTED_CLASSES = {"DIRECT_IDENTIFIER", "QUASI_IDENTIFIER"}
CLASS_RANK = {"NOT_PII": 0, "QUASI_IDENTIFIER": 1, "DIRECT_IDENTIFIER": 2}
TREATMENT_RANK = {"NONE": 0, "TOKENIZE_HMAC": 1, "ENCRYPT_AESGCM": 1}


@dataclass(frozen=True)
class Catalog:
    subject_key: str
    columns: dict[str, dict]          # "table.column" -> {class, treatment, reason?}
    name_patterns: list[str]


def load_catalog(path: str | Path = "contracts/pii_catalog.yml", root: str | Path = ".") -> Catalog | None:
    p = Path(root) / path
    if not p.exists() or p.stat().st_size == 0:
        return None
    doc = yaml.safe_load(p.read_text()) or {}
    return Catalog(
        subject_key=doc.get("subject_key", ""),
        columns={str(k): dict(v or {}) for k, v in (doc.get("columns") or {}).items()},
        name_patterns=list((doc.get("detection") or {}).get("name_patterns") or []),
    )


def protected_columns(catalog: Catalog | None, table: str) -> dict[str, dict]:
    """column -> entry for DIRECT/QUASI identifiers of one table."""
    if catalog is None:
        return {}
    out = {}
    for key, entry in catalog.columns.items():
        t, _, col = key.partition(".")
        if t == table and entry.get("class") in PROTECTED_CLASSES:
            out[col] = entry
    return out
