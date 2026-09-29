from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc:v1:"


class NonceReuse(RuntimeError):
    pass


class NonceGuard:
    """Records (key id, nonce) pairs and raises on a repeat."""

    def __init__(self) -> None:
        self._seen: set[tuple[bytes, bytes]] = set()
        self._lock = threading.Lock()      # check-then-add must be atomic across threads 

    def check(self, key: bytes, nonce: bytes) -> None:
        kid = hashlib.sha256(b"kid:" + key).digest()[:8]
        with self._lock:
            if (kid, nonce) in self._seen:
                raise NonceReuse("AES-GCM nonce reused under the same key — refusing to encrypt")
            self._seen.add((kid, nonce))


_GUARD = NonceGuard()


def tokenize(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def aad(subject_id: str, field: str) -> bytes:
    return f"{subject_id}|{field}".encode("utf-8")


def encrypt(dek: bytes, subject_id: str, field: str, value: str, guard: NonceGuard | None = None,
            nonce: bytes | None = None) -> str:
    nonce = nonce if nonce is not None else os.urandom(12)
    if len(nonce) != 12:
        raise ValueError("AES-GCM nonce must be 96 bits")
    (guard or _GUARD).check(dek, nonce)
    ct = AESGCM(dek).encrypt(nonce, value.encode("utf-8"), aad(subject_id, field))
    return PREFIX + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def decrypt(dek: bytes, subject_id: str, field: str, blob: str) -> str:
    """Raises cryptography.exceptions.InvalidTag on a wrong key, subject or field."""
    if not is_encrypted(blob):
        raise ValueError("not an enc:v1 value")
    n64, c64 = blob[len(PREFIX):].split(":", 1)
    return AESGCM(dek).decrypt(base64.b64decode(n64), base64.b64decode(c64), aad(subject_id, field)).decode("utf-8")
