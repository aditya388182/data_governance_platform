import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data"
sys.path.insert(0, str(REPO))
from govlib.contracts import load_registry, schema_fields  # noqa: E402
from scripts.lineage_scan import LineageError, drift, load_lineage, scan_consumers  # noqa: E402

DS = "payments.transactions"


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "r"
    shutil.copytree(DATA, r)
    return r


def scan(root):
    reg = load_registry(root=root)
    names = {ds: {f.name for f in schema_fields(d.schema, root=root)} for ds, d in reg.items()}
    return reg, scan_consumers(root, reg, names)


def add_consumer(root, name, code, fname="jobs/job.py"):
    p = root / "consumers" / name / fname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code)


def fields_of(u, known):
    return {f: [l.line for l in locs] for f, locs in u.fields.items() if f in known}


def test_fraud_service_fields_and_lines(root):
    reg, us = scan(root)
    u = us[("fraud-service", DS)]
    known = {f.name for f in schema_fields(reg[DS].schema, root=root)}
    assert fields_of(u, known) == {"transaction_id": [25], "merchant_id": [25], "customer_id": [25], "currency": [26],
                                   "amount_minor": [26], "status": [26, 27], "event_ts": [26]}
    assert [(str(l), p) for l, p in u.refs] == [("jobs/score_txns.py:12", "subject"), ("jobs/score_txns.py:13", "topic"),
                                               ("jobs/score_txns.py:24", "from_avro")]
    assert not u.wildcard and not u.dynamic
    assert "status" in u.qualified                      # e.status: qualified by the from_avro alias


def test_finance_recon_fields_count_star_is_not_wildcard(root):
    reg, us = scan(root)
    u = us[("finance-recon", DS)]
    known = {f.name for f in schema_fields(reg[DS].schema, root=root)}
    assert fields_of(u, known) == {"event_ts": [10], "currency": [11], "amount_minor": [12]}
    assert [(str(l), p) for l, p in u.refs] == [("jobs/recon_read.py:5", "delta_path")]
    assert not u.wildcard                                # F.count("*") is not a projection


@pytest.mark.parametrize("code,flag", [
    ('df = spark.read.format("delta").load("s3a://governance-lake/transactions")\nout = df.select("*")\n', "wildcard"),
    ('from pyspark.sql.avro.functions import from_avro\nt = "payments.public.transactions"\n'
     'e = raw.select(from_avro(raw.value, s).alias("e")).select("e.*")\n', "wildcard"),
    ('df = spark.read.format("delta").load("s3://governance-lake/transactions/")\ncols = ["a"]\nout = df.select(*[f"{c}" for c in cols])\n', "dynamic"),
    ('from pyspark.sql.functions import col\ndf = spark.read.format("delta").load("s3a://governance-lake/transactions")\nout = df.select(col(f"e_{x}"))\n', "dynamic"),
    ('df = spark.read.format("delta").load("s3a://governance-lake/transactions")\nCOLS = ["currency"]\nout = df.select(COLS)\n', "dynamic"),
])
def test_wildcard_and_dynamic_are_flagged(root, code, flag):
    add_consumer(root, "x", code)
    _, us = scan(root)
    u = us[("x", DS)]
    assert getattr(u, flag), (flag, u)


def test_select_expr_aliases_and_withcolumn_are_not_fields(root):
    add_consumer(root, "x", 'df = spark.read.format("delta").load("s3a://governance-lake/transactions/date=2026-09-01")\n'
                            'out = (df.withColumn("amount_major", df["amount_minor"] / 100)\n'
                            '         .selectExpr("amount_minor / 100 AS amount_usd", "upper(currency) AS ccy")\n'
                            '         .select("amount_usd", "ccy", "amount_major"))\n')
    _, us = scan(root)
    u = us[("x", DS)]
    assert {"amount_minor", "currency"} <= set(u.fields)
    assert not {"amount_usd", "ccy", "amount_major"} & set(u.fields)


def test_subject_in_subscribe_list_and_sql_files(root):
    add_consumer(root, "k", 'q = spark.readStream.option("subscribe", "other.topic, payments.public.transactions").load()\n')
    add_consumer(root, "s", "SELECT customer_id, sum(amount_minor) AS s\nFROM delta.`s3a://governance-lake/transactions`\nGROUP BY customer_id\n",
                 fname="sql/q.sql")
    _, us = scan(root)
    assert ("k", DS) in us and ("s", DS) in us
    assert {"customer_id", "amount_minor"} <= set(us[("s", DS)].fields)


def test_drift_unregistered_stale_dangling_codeowner(root):
    shutil.copytree(root / "marketing-etl", root / "consumers" / "marketing-etl")
    (root / "consumers/finance-recon/jobs/recon_read.py").write_text("# migrated away\n")
    reg, us = scan(root)
    lin = load_lineage(root)
    out = drift(root, reg, us, lin)
    codes = {(c, m.split()[0]) for _, c, m in out}
    assert ("UNREGISTERED CONSUMER", "consumers/marketing-etl") in codes
    assert any(c == "STALE LINEAGE" and "finance-recon" in m for _, c, m in out)
    # drop status from the contract: fraud-service's e.status is now dangling
    p = root / "contracts/schemas/payments_transactions.avsc"
    p.write_text("\n".join(l for l in p.read_text().splitlines() if '"name": "status"' not in l) + "\n")
    reg, us = scan(root)
    out = drift(root, reg, us, lin)
    assert any(c == "DANGLING FIELD" and "'status'" in m for _, c, m in out)
    assert not any(c == "DANGLING FIELD" for _, c, m in drift(root, reg, us, lin, skip_fields={DS: {"status"}}))
    (root / "CODEOWNERS").write_text("/contracts/ @someone\n")
    assert any(c == "NO CODEOWNER" for _, c, m in drift(root, reg, us, lin))


@pytest.mark.parametrize("mutate,msg", [
    (lambda t: t.replace("team:", "teem:"), "missing 'team'"),
    (lambda t: t.replace("consumer: fraud-service", "consumer: fraud"), "must match the file name"),
    (lambda t: t.replace("acks: {}", "acks:\n  next: [{fields: [status]}]"), "must be a PR number"),
    (lambda t: t.replace("acks: {}", "acks:\n  '11': [{by: x}]"), "non-empty 'fields'"),
])
def test_invalid_lineage_fails(root, mutate, msg):
    p = root / "contracts/lineage/fraud-service.yml"
    p.write_text(mutate(p.read_text()))
    with pytest.raises(LineageError, match=msg):
        load_lineage(root)
