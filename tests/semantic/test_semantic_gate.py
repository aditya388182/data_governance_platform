import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
TEST_KEYS = {"GOV_KEK": "11" * 32, "GOV_FIXTURES_KEY": "22" * 32, "GOV_AUDIT_HMAC_KEY": "33" * 32}
DATA = Path(__file__).resolve().parent / "data"      # frozen test universe (see data/README.md)
CODE = ("ge", "govlib", "scripts", ".github")          # code under test comes from the live repo
PAY = "fixtures/payments_transactions_sample.parquet"


def run(cmd, cwd, env_extra=None, timeout=300):
    env = dict(os.environ, PYTHON=sys.executable, PYTHONWARNINGS="ignore", GE_USAGE_STATS="FALSE",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", **TEST_KEYS)
    env.pop("GITHUB_STEP_SUMMARY", None)
    env.update(env_extra or {})
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def make(root, *args):
    return run([sys.executable, "scripts/make_fixture.py", "--keys", "/nonexistent", *args], cwd=root)


def gate(root):
    return run([sys.executable, "scripts/ci/ge_gate.py"], cwd=root)


@pytest.fixture(scope="module")
def pristine(tmp_path_factory):
    """Repo copy with freshly generated fixtures (generated once per module)."""
    root = tmp_path_factory.mktemp("pristine")
    for rel in CODE:
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(DATA / "contracts", root / "contracts")               # frozen contracts
    shutil.rmtree(root / "ge/suites")
    shutil.copytree(DATA / "suites", root / "ge/suites")                  # frozen suites
    r = make(root)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


@pytest.fixture()
def repo(pristine, tmp_path):
    root = tmp_path / "r"
    shutil.copytree(pristine, root)
    run(["git", "init", "-q", "-b", "main"], cwd=root)
    run(["git", "add", "-A"], cwd=root)
    run(["git", "commit", "-qm", "base"], cwd=root)
    return root


#  fixture generator
def test_fixture_is_stratified_with_floor(pristine):
    m = json.loads((pristine / "fixtures/payments_transactions_sample.manifest.json").read_text())
    assert m["rows"] == 2005 and m["sampled_rows"] == 2005
    assert all(v["sampled"] >= 5 for v in m["strata"].values())
    assert m["strata"]["CHF"] == {"source": 40, "sampled": 5, "bernoulli_miss_probability": 0.668972}
    c = json.loads((pristine / "fixtures/customers_sample.manifest.json").read_text())
    assert c["rows"] == 505 and c["strata"]["AU"]["sampled"] == 5


def test_fixture_is_deterministic(pristine, tmp_path):
    root = tmp_path / "again"
    shutil.copytree(pristine, root)
    before = json.loads((root / "fixtures/payments_transactions_sample.manifest.json").read_text())["content_sha256"]
    assert make(root).returncode == 0
    after = json.loads((root / "fixtures/payments_transactions_sample.manifest.json").read_text())["content_sha256"]
    assert before == after


def test_fixture_pii_is_tokenized(pristine):
    t = pq.read_table(pristine / "fixtures/customers_sample.parquet").to_pydict()
    for col in ("customer_id", "email", "ssn", "full_name"):
        assert all(len(v) == 64 and all(ch in "0123456789abcdef" for ch in v) for v in t[col]), col
    assert not any("@" in v for v in t["email"])
    p = pq.read_table(pristine / PAY).to_pydict()
    assert not any(v.startswith(("CUST-", "M-")) for v in p["customer_id"] + p["merchant_id"])


def test_generator_refuses_unclassified_pii(repo):
    shutil.copy(DATA / "pr_receipt_email.avsc", repo / "contracts/schemas/payments_transactions.avsc")
    r = make(repo, "--dataset", "payments.transactions")
    assert r.returncode != 0 and "look like PII but have no entry" in (r.stdout + r.stderr)


#  the gate
def test_clean_fixtures_pass(repo):
    r = gate(repo)
    assert r.returncode == 0, r.stdout
    assert "ge-gate: PASS" in r.stdout


def test_valid_shape_invalid_value_fails(repo):
    r = make(repo, "--dataset", "payments.transactions", "--inject-bad")
    assert r.returncode == 0 and "all 2,010 validate against" in r.stdout   # every row is Avro-valid
    g = gate(repo)
    assert g.returncode == 1
    failed = [l for l in g.stdout.splitlines() if l.startswith("::error")]
    assert len(failed) == 5, g.stdout
    for needle in ("match_regex(transaction_id)", "between(amount_minor)", "lengths_to_equal(currency)",
                   "in_set(currency) failed — 2 unexpected", "between(risk_score)"):
        assert any(needle in l for l in failed), needle


def test_suite_referencing_renamed_column_fails(repo):
    shutil.copy(DATA / "pr_b_rename_currency.avsc", repo / "contracts/schemas/payments_transactions.avsc")
    g = gate(repo)
    assert g.returncode == 1 and "references column(s) ['currency']" in g.stdout


def test_plaintext_pii_in_fixture_fails(repo):
    t = pq.read_table(repo / PAY)
    ids = t.column("customer_id").to_pylist()
    ids[0] = "CUST-000123"                        # one plaintext subject id
    t = t.set_column(t.column_names.index("customer_id"), pa.field("customer_id", pa.string(), nullable=False), pa.array(ids))
    pq.write_table(t, repo / PAY)
    g = gate(repo)
    assert g.returncode == 1 and "PII LEAK" in g.stdout and "CUST-000123" not in g.stdout


def test_hand_edited_fixture_fails_manifest(repo):
    t = pq.read_table(repo / PAY)
    amt = t.column("amount_minor").to_pylist()
    amt[0] += 1
    t = t.set_column(t.column_names.index("amount_minor"), pa.field("amount_minor", pa.int64(), nullable=False), pa.array(amt, pa.int64()))
    pq.write_table(t, repo / PAY)
    g = gate(repo)
    assert g.returncode == 1 and "does not match its manifest hash" in g.stdout


def test_empty_suite_fails(repo):
    (repo / "ge/suites/customers.yml").write_text("expectation_suite_name: customers\nexpectations: []\n")
    g = gate(repo)
    assert g.returncode == 1 and "an empty suite is not a contract" in g.stdout


def test_stale_fixture_warns_but_passes(repo):
    shutil.copy(DATA / "pr_receipt_email.avsc", repo / "contracts/schemas/payments_transactions.avsc")
    g = gate(repo)
    assert g.returncode == 0 and "fixture is stale" in g.stdout


#  detect
def test_detect(repo):
    sh = str(repo / "scripts/ci/ge_gate.sh")
    run(["git", "checkout", "-qb", "x"], cwd=repo)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs/n.md").write_text("n\n")
    assert run([sh, "detect", "main"], cwd=repo).stdout.strip() == "run=false"
    make(repo, "--dataset", "payments.transactions", "--inject-bad")
    assert run([sh, "detect", "main"], cwd=repo).stdout.strip() == "run=true"
    bad = run([sh, "detect", "no-such-ref"], cwd=repo)
    assert bad.returncode != 0 and "run=false" not in bad.stdout


#  GE runner unit
def test_runner_maps_results_to_suite_order():
    import pandas as pd
    from ge.run_suite import validate
    doc = {"expectation_suite_name": "t", "expectations": [
        {"expectation_type": "expect_column_values_to_be_between", "kwargs": {"column": "a", "min_value": 0}},
        {"expectation_type": "expect_column_values_to_be_in_set", "kwargs": {"column": "b", "value_set": ["x"]}},
        {"expectation_type": "expect_column_values_to_not_be_null", "kwargs": {"column": "nope"}},
    ]}
    res = validate(pd.DataFrame({"a": [1, -2], "b": ["x", "y"]}), doc)
    assert [r.index for r in res] == [0, 1, 2]
    assert (res[0].success, res[0].unexpected_count, res[0].sample) == (False, 1, [-2])
    assert (res[1].success, res[1].sample) == (False, ["y"])
    assert res[2].success is False                      # missing column is a failure, never a pass
