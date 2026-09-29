from __future__ import annotations

from pathlib import Path

import yaml

from crypto import shred
from govlib.keys import require_key

ROOT = Path(__file__).resolve().parents[1]
SCOPES = {"SUBJECT", "PLATFORM"}


class PolicyError(ValueError):
    pass


def load_policy(root: Path = ROOT) -> tuple[str, dict]:
    doc = yaml.safe_load((Path(root) / "contracts" / "pii_catalog.yml").read_text())
    out: dict[str, dict[str, tuple[str, str]]] = {}
    for key, e in (doc.get("columns") or {}).items():
        table, col = key.split(".", 1)
        tr, cls, scope = e.get("treatment"), e.get("class"), e.get("key_scope", "SUBJECT")
        if tr not in ("TOKENIZE_HMAC", "ENCRYPT_AESGCM"):
            continue
        if scope not in SCOPES:
            raise PolicyError(f"{key}: key_scope '{scope}' is not one of {sorted(SCOPES)}")
        if scope == "PLATFORM" and not (tr == "TOKENIZE_HMAC" and cls == "QUASI_IDENTIFIER"):
            raise PolicyError(f"{key}: PLATFORM scope is only allowed for TOKENIZE_HMAC on a QUASI_IDENTIFIER "
                              f"(a platform-keyed {cls} would survive the subject's erasure)")
        out.setdefault(table, {})[col] = (tr, scope)
    return doc["subject_key"], out


def protect(table: str, df, key_store, keys: dict, root: Path = ROOT, guard=None):
    """Return a copy of `df` with every catalogued column of `table` tokenized/encrypted."""
    subject_key, pol = load_policy(root)
    cols = pol.get(table, {})
    if not cols:
        return df.copy()
    if subject_key not in df.columns:
        raise PolicyError(f"{table}: subject key '{subject_key}' missing — cannot choose keys")
    out = df.copy()
    deks = key_store.get_or_create_deks(out[subject_key].dropna().unique().tolist())
    token_key = require_key(keys, "token_key") if any(s == "PLATFORM" for _, s in cols.values()) else None
    guard = guard or shred.NonceGuard()
    for col, (tr, scope) in cols.items():
        if col not in out.columns:
            continue
        vals = []
        for sid, v in zip(out[subject_key], out[col]):
            if v is None or (isinstance(v, float) and v != v):
                vals.append(None)
            elif tr == "TOKENIZE_HMAC":
                vals.append(shred.tokenize(token_key if scope == "PLATFORM" else deks[sid], str(v)))
            else:
                vals.append(shred.encrypt(deks[sid], sid, f"{table}.{col}", str(v), guard=guard))
        out[col] = vals
    return out


def reveal(table: str, df, key_store, root: Path = ROOT):
    """Authorized read path: decrypt every ENCRYPT_AESGCM column (tokens stay tokens)."""
    subject_key, pol = load_policy(root)
    out = df.copy()
    deks: dict[str, bytes] = {}
    for col, (tr, _) in pol.get(table, {}).items():
        if tr != "ENCRYPT_AESGCM" or col not in out.columns:
            continue
        vals = []
        for sid, v in zip(out[subject_key], out[col]):
            if not shred.is_encrypted(v):
                vals.append(v)
                continue
            if sid not in deks:
                deks[sid] = key_store.get_dek(sid)
            vals.append(shred.decrypt(deks[sid], sid, f"{table}.{col}", v))
        out[col] = vals
    return out
