import http.server
import os
import sys
import threading
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("GE_USAGE_STATS", "FALSE")

from govlib.lake import read_table  # noqa: E402
from runtime import quarantine  # noqa: E402


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    root = tmp_path_factory.mktemp("lake")
    mp = pytest.MonkeyPatch()
    mp.chdir(REPO)
    mp.setenv("LAKE_LOCAL_ROOT", str(root))
    for n in ("GOV_KEK", "GOV_FIXTURES_KEY", "GOV_AUDIT_HMAC_KEY", "GOV_TOKEN_KEY"):
        mp.setenv(n, os.urandom(32).hex())
    from scripts.seed_prod_tables import seed
    assert seed(10, 400, 1) == 0
    yield root
    mp.undo()


class Stub(http.server.BaseHTTPRequestHandler):
    got: list = []

    def do_PUT(self):
        Stub.got.append((self.path, self.rfile.read(int(self.headers["Content-Length"])).decode()))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture()
def gateway():
    Stub.got = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def txn_table(root):
    return DeltaTable(str(root / "governance-lake/transactions"))


def test_clean_tables_pass_and_nothing_is_quarantined(lake, gateway):
    from scripts.ge_checkpoint import run
    code, out = run(None, gateway)
    assert code == 0 and all(s["passed"] and s["new"] == 0 for s in out)
    assert not (lake / "governance-lake/quarantine").exists()
    paths = sorted(p for p, _ in Stub.got)
    assert paths == ["/metrics/job/ge_checkpoint/dataset/customers", "/metrics/job/ge_checkpoint/dataset/payments.transactions"]
    assert all("ge_checkpoint_pass 1" in body and "quarantine_rows_total 0" in body for _, body in Stub.got)


def test_seeded_violation_quarantined_with_evidence_source_untouched(lake, gateway):
    from drills.day4.inject_bad_rows import inject
    from scripts.ge_checkpoint import run
    bad_ids = inject()
    before = txn_table(lake)
    v, n = before.version(), read_table(before).num_rows
    code, out = run(["payments.transactions"], gateway)
    s = out[0]
    assert code == 0 and not s["passed"] and s["offending"] == 7 and s["new"] == 7 and s["total"] == 7
    after = txn_table(lake)
    assert after.version() == v and read_table(after).num_rows == n          # read-only: nothing changed
    assert set(bad_ids) <= set(read_table(after, ["transaction_id"]).column(0).to_pylist())
    q = read_table(str(lake / "governance-lake/quarantine/transactions")).to_pylist()
    assert sorted(r["transaction_id"] for r in q) == sorted(bad_ids)                 # routing: exactly the bad rows
    for r in q:
        assert all(r[c] not in (None, [], "") for c in quarantine.EVIDENCE_NAMES)
        assert r["owner"] == "payments-platform-team" and r["source_version"] == v and r["suite"] == "payments_transactions"
    exp = sorted(tuple(r["expectation"]) for r in q)
    assert exp.count(("expect_column_values_to_be_between(amount_minor)",)) == 4
    assert exp.count(("expect_column_value_lengths_to_equal(currency)", "expect_column_values_to_be_in_set(currency)")) == 3
    body = dict(Stub.got)["/metrics/job/ge_checkpoint/dataset/payments.transactions"]
    for line in ("ge_checkpoint_pass 0", "quarantine_rows_total 7", "quarantine_rows_last_run 7",
                 "ge_checkpoint_failed_expectations 3", f"ge_checkpoint_source_version {v}"):
        assert line in body


def test_rerun_is_idempotent_and_still_failing(lake, gateway):
    from scripts.ge_checkpoint import run
    code, out = run(["payments.transactions"], gateway)
    assert code == 0 and not out[0]["passed"] and out[0]["new"] == 0 and out[0]["total"] == 7
    assert "quarantine_rows_last_run 0" in Stub.got[0][1] and "ge_checkpoint_pass 0" in Stub.got[0][1]


def test_owner_remediation_resolves_and_evidence_is_kept(lake, gateway):
    import runpy

    from scripts.ge_checkpoint import run
    runpy.run_path(str(REPO / "drills/day4/remediate.py"))
    code, out = run(["payments.transactions"], gateway)
    assert code == 0 and out[0]["passed"] and out[0]["new"] == 0 and out[0]["total"] == 7
    assert "ge_checkpoint_pass 1" in Stub.got[0][1] and "quarantine_rows_total 7" in Stub.got[0][1]


def test_unreachable_pushgateway_fails_closed(lake, capsys):
    from scripts.ge_checkpoint import run
    code, _ = run(["customers"], "http://127.0.0.1:9")
    assert code == 2 and "FAILING CLOSED" in capsys.readouterr().out


def test_unreadable_table_fails_closed_and_reports_pass_0(lake, gateway, monkeypatch, tmp_path):
    from scripts.ge_checkpoint import run
    monkeypatch.setenv("LAKE_LOCAL_ROOT", str(tmp_path / "empty"))
    code, out = run(["customers"], gateway)
    assert code == 2 and out[0]["passed"] is False and "ge_checkpoint_pass 0" in Stub.got[0][1]


def test_unknown_dataset_fails_closed():
    from scripts.ge_checkpoint import run
    assert run(["no.such.dataset"], None)[0] == 2


def test_build_rejects_evidence_column_collision():
    t = pa.table({"suite": ["x"], "a": [1]})
    with pytest.raises(ValueError, match="collide"):
        quarantine.build(t, {0: ["e(a)"]}, {"dataset": "d", "suite": "s", "run_ts": 1, "checkpoint_id": "c",
                                            "source_version": 0, "owner": "o"})


def test_table_level_failures_name_no_rows():
    class R:
        def __init__(self, success, idx, t="expect_table_row_count_to_be_between", target="", error=None):
            self.success, self.unexpected_index_list, self.expectation_type, self.target, self.error = success, idx, t, target, error
    res = [R(False, None), R(False, [2, 5], "expect_column_values_to_not_be_null", "a"), R(True, None)]
    assert quarantine.offending_rows(res) == {2: ["expect_column_values_to_not_be_null(a)"], 5: ["expect_column_values_to_not_be_null(a)"]}
    assert quarantine.table_level_failures(res) == ["expect_table_row_count_to_be_between"]
