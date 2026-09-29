from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

KEY_NAMES = ("kek", "fixtures_key", "audit_hmac_key")
# (F4): platform tokenization key for quasi-identifiers that must stay joinable
# across subjects (merchant_id). Optional here so 1st and 2nd tooling keeps working;
# code that needs it calls require_key(). `python scripts/init_keys.py` adds it.
OPTIONAL_KEY_NAMES = ("token_key",)
ENV = {"kek": "GOV_KEK", "fixtures_key": "GOV_FIXTURES_KEY", "audit_hmac_key": "GOV_AUDIT_HMAC_KEY",
       "token_key": "GOV_TOKEN_KEY"}


class KeyError_(RuntimeError):
    pass


def load_keys(path: str | Path = "conf/keys.yml") -> dict[str, bytes]:
    p = Path(path)
    doc = yaml.safe_load(p.read_text()) if p.exists() else {}
    doc = doc or {}
    keys: dict[str, bytes] = {}
    for name in KEY_NAMES + OPTIONAL_KEY_NAMES:
        raw = os.environ.get(ENV[name]) or doc.get(name)
        if not raw:
            if name in OPTIONAL_KEY_NAMES:
                continue
            raise KeyError_(f"key '{name}' missing: run `python scripts/init_keys.py` (or set {ENV[name]})")
        try:
            b = bytes.fromhex(str(raw))
        except ValueError as e:
            raise KeyError_(f"key '{name}' is not hex") from e
        if len(b) != 32:
            raise KeyError_(f"key '{name}' must be 32 bytes (64 hex chars), got {len(b)}")
        keys[name] = b
    if len(set(keys.values())) != len(keys):
        raise KeyError_(f"keys must be distinct ({', '.join(keys)})")
    return keys


def require_key(keys: dict, name: str) -> bytes:
    if name not in keys:
        raise KeyError_(f"key '{name}' missing: run `python scripts/init_keys.py` — it adds missing keys "
                        f"and never touches existing ones (or set {ENV[name]})")
    return keys[name]


def fingerprint(key: bytes) -> str:
    """Non-secret identifier of a key (safe to commit in a manifest)."""
    return hashlib.sha256(b"fingerprint:" + key).hexdigest()[:12]
