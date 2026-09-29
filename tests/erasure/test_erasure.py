import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("GE_USAGE_STATS", "FALSE")

from govlib.lake import read_table  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("lake5")
    mp = pytest.MonkeyPatch()
    mp.chdir(REPO)
    mp.setenv("LAKE_LOCAL_ROOT", str(root))
    for n in ("GOV_KEK", "GOV_FIXTURES_KEY", "GOV_AUDIT_HMAC_KEY", "GOV_TOKEN_KEY"):
        mp.setenv(n, os.urandom(32).hex())
    import runpy

    from drills.day4.inject_bad_rows import inject
    from runtime import backup
    from scripts.ge_checkpoint import run as checkpoint
    from scripts.seed_prod_tables import seed
    assert seed(12, 500, 3) == 0
    inject()
    assert checkpoint(None, None)[0] == 0
    runpy.run_path(str(REPO / "drills/day4/remediate.py"))
    backup.freeze("2026-01-01")
    q = read_table(str(root / "governance-lake/quarantine/transactions"), ["customer_id"]).column(0).to_pylist()
    subject = sorted(q)[0]
    control = next(f"CUST-{i:06d}" for i in range(1, 13) if f"CUST-{i:06d}" not in q)
    yield {"root": root, "subject": subject, "control": control}
    mp.undo()


def py(*args):
    return subprocess.run([sys.executable, *args], cwd=REPO, capture_output=True, text=True, timeout=180)


def test_lab_shows_the_traps_and_cleans_up():
    r = py("drills/day5/lake_lab.py")
    assert r.returncode == 0 and "still returns LAB-2" in r.stdout and "now RAISES" in r.stdout and "residue" in r.stdout


def test_backup_is_frozen_verified_and_never_overwritten(world):
    from runtime import backup
    good, total, bad = backup.verify("2026-01-01")
    assert good == total > 0 and not bad
    assert "keystore" not in " ".join(e["key"] for e in backup.manifest("2026-01-01")["objects"])
    with pytest.raises(FileExistsError):
        backup.freeze("2026-01-01")


def test_backup_verify_detects_tampering(tmp_path, monkeypatch):
    from runtime import backup, objects
    monkeypatch.setenv("LAKE_LOCAL_ROOT", str(tmp_path))
    objects.write("s3a://governance-lake/t/a.bin", b"one")
    objects.write("s3a://governance-lake/t/b.bin", b"two")
    backup.freeze("x")
    objects.write(backup.label_uri("x") + "/t/b.bin", b"TAMPERED")
    good, total, bad = backup.verify("x")
    assert (good, total) == (1, 2) and "t/b.bin" in bad[0]


def test_decrypt_from_backup_works_before(world):
    r = py("scripts/decrypt_view.py", world["subject"], "--from-backup")
    assert r.returncode == 0 and "@example.com" in r.stdout


def test_dry_run_changes_nothing_and_intake_finds_the_uncatalogued_copy(world):
    from runtime import erasure
    before = {p: erasure._dt(p).version() for p in erasure.sweep("customer_id")}
    res = erasure.run(world["subject"], "REQ-DRY", dry_run=True, say=lambda *a: None)
    after = {p: erasure._dt(p).version() for p in erasure.sweep("customer_id")}
    assert before == after and res["rows"] > 0
    src = {h.name: h.source for h in res["intake"]["holdings"]}
    assert src == {"customers": "catalog", "transactions": "catalog", "quarantine/transactions": "sweep"}
    assert res["intake"]["backups"] and any("fraud-service" in d for d in res["intake"]["downstream"])
    assert erasure.audit_records() == []


def test_erasure_end_to_end(world):
    from runtime import erasure
    s, c = world["subject"], world["control"]
    res = erasure.run(s, "GDPR-TEST-1", control=c, say=lambda *a: None)
    rec = res["record"]
    assert res["verified_lake"] and res["verified_crypto"] and res["before"].endswith("@example.com")
    assert rec["rows_deleted"] == sum(rec["rows_deleted_by_table"]) > 2 and rec["rerun_of"] is None
    assert rec["uncatalogued_tables"] == ["s3a://governance-lake/quarantine/transactions"]
    for p in rec["tables_touched"]:
        assert erasure.count_rows(p, "customer_id", s) == 0
    for p, v, n in zip(rec["tables_touched"], rec["pre_delete_versions"], rec["rows_deleted_by_table"]):
        assert n > 0 and erasure.time_travel(p, v)[0]
    assert erasure.count_rows("s3a://governance-lake/customers", "customer_id", c) == 1


def test_audit_never_stores_the_raw_subject(world):
    files = [f for f in (world["root"] / "governance-lake/audit/erasure").rglob("*") if f.is_file()]
    assert files and not any(world["subject"].encode() in f.read_bytes() for f in files)


def test_decrypt_after_is_refused_everywhere(world):
    for args in (("--from-backup",), ()):
        r = py("scripts/decrypt_view.py", world["subject"], *args)
        assert r.returncode == 3 and "SUBJECT ERASED" in r.stdout and "@example.com" not in r.stdout
    assert py("scripts/decrypt_view.py", world["control"]).returncode == 0


def test_rerun_is_idempotent_and_visible(world):
    from runtime import erasure
    first = [r for r in erasure.audit_records() if r["request_id"] == "GDPR-TEST-1"][0]
    res = erasure.run(world["subject"], "GDPR-TEST-1", control=world["control"], say=lambda *a: None)
    rec = res["record"]
    assert rec["rows_deleted"] == 0 and rec["rerun_of"] == "GDPR-TEST-1"
    assert rec["key_destroyed_ts"] == first["key_destroyed_ts"] and rec["verified_lake"] and rec["verified_crypto"]


def test_a_second_request_for_the_same_subject_links_to_the_first(world):
    from runtime import erasure
    rec = erasure.run(world["subject"], "GDPR-TEST-2", say=lambda *a: None)["record"]
    assert rec["rerun_of"] == "GDPR-TEST-1"
    recs = erasure.audit_records()
    assert len(recs) == 3 and sum(r["rerun_of"] is None for r in recs) == 1


def test_audit_table_is_append_only(world):
    from deltalake.exceptions import CommitFailedError

    from runtime import erasure
    with pytest.raises(CommitFailedError):
        erasure._dt(erasure.audit_path()).delete("request_id = 'GDPR-TEST-1'")
    assert len(erasure.audit_records()) == 3


def test_regulator_verify_needs_the_matching_subject(world):
    from scripts import erasure_audit
    assert erasure_audit.cmd_verify("GDPR-TEST-1", world["subject"]) == 0
    with pytest.raises(SystemExit):
        erasure_audit.cmd_verify("GDPR-TEST-1", world["control"])


def test_timetravel_probe_raises(world):
    r = py("scripts/erasure_audit.py", "timetravel", "GDPR-TEST-1", "--table", "customers")
    assert r.returncode != 0 and "not found" in r.stderr and "READABLE" not in r.stdout
