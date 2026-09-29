import os
import sys
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import write_deltalake

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from crypto.key_store import SCHEMA, KeyStore, SubjectErased  # noqa: E402
from govlib.lake import read_table  # noqa: E402

KEK = os.urandom(32)


@pytest.fixture()
def ks(tmp_path):
    return KeyStore(str(tmp_path / "keystore"), KEK)


def log_text(path: Path) -> str:
    return "".join(p.read_text() for p in sorted((path / "_delta_log").glob("*.json")))


def test_get_or_create_is_stable_and_batched(ks):
    a = ks.get_or_create_deks(["s1", "s2", "s1"])
    assert set(a) == {"s1", "s2"} and len(a["s1"]) == 32 and a["s1"] != a["s2"]
    assert ks.get_or_create_dek("s1") == a["s1"] and ks.get_dek("s2") == a["s2"]


def test_destroy_is_idempotent_and_preserves_original_ts(ks):
    ks.get_or_create_dek("s1")
    t1 = ks.destroy_key("s1")
    v = ks._table().version()
    assert ks.destroy_key("s1") == t1 and ks._table().version() == v        # rerun: no write at all
    assert ks.status("s1")["destroyed_ts"] == t1


def test_post_destroy_never_mints_a_new_key(ks):
    ks.get_or_create_deks(["s1", "s2"])
    ks.destroy_key("s1")
    with pytest.raises(SubjectErased):
        ks.get_or_create_dek("s1")
    with pytest.raises(SubjectErased):
        ks.get_or_create_deks(["s2", "s1"])            # a batch containing an erased subject fails whole
    with pytest.raises(SubjectErased):
        ks.get_dek("s1")
    assert ks.get_dek("s2")                             # others unaffected


def test_destroying_an_unknown_subject_writes_a_tombstone(ks):
    ks.destroy_key("never-seen")
    with pytest.raises(SubjectErased):
        ks.get_or_create_dek("never-seen")


def test_f3_time_travel_exposes_key_until_vacuum(ks):
    ks.get_or_create_deks(["s1", "s2"])
    ks.destroy_key("s1", vacuum=False)
    assert ks.history_exposure("s1"), "control: without VACUUM the old wrapped DEK is still readable"
    ks.vacuum()
    assert ks.history_exposure("s1") == []


def test_destroy_key_vacuums_by_default(ks):
    ks.get_or_create_deks(["s1", "s2"])
    ks.get_or_create_dek("s3")
    ks.destroy_key("s1")
    assert ks.history_exposure("s1") == []
    assert ks.get_dek("s2") and ks.get_dek("s3")


def test_wrapped_dek_never_appears_in_delta_log(ks, tmp_path):
    ks.get_or_create_dek("solo")                        # a one-row file: min = max = the value
    ks.get_or_create_deks([f"s{i}" for i in range(5)])
    wrapped = [r["wrapped_dek"] for r in read_table(ks._table()).to_pylist()]
    text = log_text(Path(ks.uri))
    assert not any(w in text for w in wrapped)
    # control: the same row WITHOUT the statistics setting leaks the wrapped key into the log
    raw = tmp_path / "no_config"
    write_deltalake(str(raw), pa.Table.from_pylist([{"subject_id": "solo", "created_ts": 1, "destroyed_ts": None,
                                                      "wrapped_dek": wrapped[0]}], schema=SCHEMA))
    assert wrapped[0] in log_text(raw)


def test_wrong_kek_cannot_unwrap(ks):
    ks.get_or_create_dek("s1")
    with pytest.raises(Exception):
        KeyStore(ks.uri, os.urandom(32)).get_dek("s1")
