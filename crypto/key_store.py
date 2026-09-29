from __future__ import annotations

import os
import time

import pyarrow as pa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import TableNotFoundError

from govlib.lake import read_table

SCHEMA = pa.schema([("subject_id", pa.string(), False), ("created_ts", pa.int64(), False),
                    ("destroyed_ts", pa.int64(), True), ("wrapped_dek", pa.string(), False)])
TABLE_CONFIG = {"delta.dataSkippingNumIndexedCols": "3"}
ZEROS = "0" * 120        # same width as a wrapped DEK: 12-byte nonce + 32-byte key + 16-byte tag, hex


class SubjectErased(RuntimeError):
    """The subject's key was destroyed: its data is unreadable and must not be re-collected."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


class KeyStore:
    def __init__(self, uri: str, kek: bytes, storage_options: dict | None = None):
        if len(kek) != 32:
            raise ValueError("KEK must be 32 bytes")
        self.uri, self._kek, self._so = uri, kek, storage_options

    #  wrapping
    def _wrap(self, subject_id: str, dek: bytes) -> str:
        nonce = os.urandom(12)
        return (nonce + AESGCM(self._kek).encrypt(nonce, dek, b"keystore|" + subject_id.encode())).hex()

    def _unwrap(self, subject_id: str, wrapped: str) -> bytes:
        raw = bytes.fromhex(wrapped)
        return AESGCM(self._kek).decrypt(raw[:12], raw[12:], b"keystore|" + subject_id.encode())

    #  table access
    def _table(self, version: int | None = None) -> DeltaTable | None:
        try:
            dt = DeltaTable(self.uri, storage_options=self._so)
        except TableNotFoundError:
            return None
        if version is not None:
            dt.load_as_version(version)
        return dt

    def _rows(self) -> dict[str, dict]:
        dt = self._table()
        if dt is None:
            return {}
        return {r["subject_id"]: r for r in read_table(dt).to_pylist()}

    def _append(self, rows: list[dict]) -> None:
        write_deltalake(self.uri, pa.Table.from_pylist(rows, schema=SCHEMA), mode="append",
                        configuration=TABLE_CONFIG, storage_options=self._so)

    #  public API
    def get_or_create_deks(self, subject_ids) -> dict[str, bytes]:
        subject_ids = list(dict.fromkeys(subject_ids))
        rows = self._rows()
        erased = sorted(s for s in subject_ids if s in rows and rows[s]["destroyed_ts"] is not None)
        if erased:
            raise SubjectErased(f"key destroyed for {len(erased)} subject(s), e.g. {erased[0]} — refusing to mint a new DEK")
        out, new_rows = {}, []
        for s in subject_ids:
            if s in rows:
                out[s] = self._unwrap(s, rows[s]["wrapped_dek"])
            else:
                dek = os.urandom(32)
                out[s] = dek
                new_rows.append({"subject_id": s, "created_ts": _now_ms(), "destroyed_ts": None,
                                 "wrapped_dek": self._wrap(s, dek)})
        if new_rows:
            self._append(new_rows)
        return out

    def get_or_create_dek(self, subject_id: str) -> bytes:
        return self.get_or_create_deks([subject_id])[subject_id]

    def get_dek(self, subject_id: str) -> bytes:
        """Read path: never creates. KeyError if unknown, SubjectErased if destroyed."""
        row = self._rows().get(subject_id)
        if row is None:
            raise KeyError(subject_id)
        if row["destroyed_ts"] is not None:
            raise SubjectErased(f"key for {subject_id} was destroyed at {row['destroyed_ts']}")
        return self._unwrap(subject_id, row["wrapped_dek"])

    def status(self, subject_id: str) -> dict | None:
        row = self._rows().get(subject_id)
        return None if row is None else {k: v for k, v in row.items() if k != "wrapped_dek"}

    def destroy_key(self, subject_id: str, vacuum: bool = True) -> int:
        """Idempotent. Returns the (original) destroyed_ts."""
        row = self._rows().get(subject_id)
        if row is not None and row["destroyed_ts"] is not None:
            return row["destroyed_ts"]
        ts = _now_ms()
        if row is None:
            self._append([{"subject_id": subject_id, "created_ts": ts, "destroyed_ts": ts, "wrapped_dek": ZEROS}])
        else:
            dt = self._table()
            dt.update(new_values={"wrapped_dek": ZEROS, "destroyed_ts": ts}, predicate=f"subject_id = {_q(subject_id)}")
        if vacuum:
            self.vacuum()
        return ts

    def vacuum(self) -> list[str]:
        dt = self._table()
        return [] if dt is None else dt.vacuum(retention_hours=0, enforce_retention_duration=False, dry_run=False)

    def history_exposure(self, subject_id: str) -> list[int]:
        """Table versions from which the subject's REAL wrapped DEK can still be read
        (time travel). Must be [] after destroy_key(). Unreadable versions count as safe."""
        dt = self._table()
        if dt is None:
            return []
        exposed = []
        for v in range(dt.version() + 1):
            try:
                t = read_table(self._table(v)).to_pylist()
            except Exception:              # files vacuumed away -> version unreadable -> safe
                continue
            if any(r["subject_id"] == subject_id and r["wrapped_dek"] != ZEROS for r in t):
                exposed.append(v)
        return exposed
