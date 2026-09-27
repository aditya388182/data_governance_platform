from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

KEY_NAMES = ("kek", "fixtures_key", "audit_hmac_key")
ENV = {"kek": "GOV_KEK", "fixtures_key": "GOV_FIXTURES_KEY", "audit_hmac_key": "GOV_AUDIT_HMAC_KEY"}


class KeyError_(RuntimeError):
    pass


def load_keys(path: str | Path = "conf/keys.yml") -> dict[str, bytes]:
    p = Path(path)
    doc = yaml.safe_load(p.read_text()) if p.exists() else {}
    doc = doc or {}
    keys: dict[str, bytes] = {}
    for name in KEY_NAMES:
        raw = os.environ.get(ENV[name]) or doc.get(name)
        if not raw:
            raise KeyError_(f"key '{name}' missing: run `python scripts/init_keys.py` (or set {ENV[name]})")
        try:
            b = bytes.fromhex(str(raw))
        except ValueError as e:
            raise KeyError_(f"key '{name}' is not hex") from e
        if len(b) != 32:
            raise KeyError_(f"key '{name}' must be 32 bytes (64 hex chars), got {len(b)}")
        keys[name] = b
    if len({keys[n] for n in KEY_NAMES}) != len(KEY_NAMES):
        raise KeyError_("kek, fixtures_key and audit_hmac_key must be distinct")
    return keys


def fingerprint(key: bytes) -> str:
    """Non-secret identifier of a key (safe to commit in a manifest)."""
    return hashlib.sha256(b"fingerprint:" + key).hexdigest()[:12]
