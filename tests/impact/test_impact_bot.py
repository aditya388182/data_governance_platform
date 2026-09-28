import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data"


def run(cmd, cwd, **env):
    e = dict(os.environ, PYTHON=sys.executable, PYTHONWARNINGS="ignore", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", **env)
    e.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, timeout=120)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "r"
    for rel in ("govlib", "scripts", ".github"):
        shutil.copytree(REPO / rel, r / rel, ignore=shutil.ignore_patterns("__pycache__"))
    for item in ("contracts", "consumers"):
        shutil.copytree(DATA / item, r / item)
    shutil.copy(DATA / "CODEOWNERS", r / "CODEOWNERS")
    shutil.copytree(DATA / "marketing-etl", r / "drills/marketing-etl")
    (r / ".gitignore").write_text("__pycache__/\n")
    run(["git", "init", "-q", "-b", "main"], r)
    run(["git", "add", "-A"], r)
    run(["git", "commit", "-qm", "base"], r)
    run(["git", "checkout", "-qb", "pr"], r)
    return r


def check(r, pr="11", ack_ref="main", base="main"):
    out = r.parent / "impact-out"
    res = run([str(r / "scripts/ci/impact_gate.sh"), "check", base], r, PR_NUMBER=pr, ACK_REF=ack_ref, IMPACT_OUT=str(out))
    v = json.loads((out / "verdict.json").read_text()) if (out / "verdict.json").exists() else None
    c = (out / "comment.md").read_text() if (out / "comment.md").exists() else ""
    assert not (r / "impact").exists(), "impact-bot wrote output into the repo"
    return res, v, c


def edit(r, *args):
    res = run([sys.executable, "scripts/contract_edit.py", *args], r)
    assert res.returncode == 0, res.stderr


def commit(r, msg="x"):
    run(["git", "add", "-A"], r)
    run(["git", "commit", "-qm", msg], r)


def ack_on_main(r, consumer, pr, fields):
    run(["git", "stash", "-q", "--include-untracked"], r)
    run(["git", "checkout", "-q", "main"], r)
    res = run([sys.executable, "scripts/ack.py", consumer, "--pr", pr, "--fields", fields, "--reason", "test"], r)
    assert res.returncode == 0, res.stderr
    commit(r, "ack")
    run(["git", "checkout", "-q", "pr"], r)
    run(["git", "stash", "pop", "-q"], r)


def test_no_schema_change_passes_without_comment(repo):
    res, v, _ = check(repo)
    assert res.returncode == 0 and v["comment"] is False and v["label"] is False


def test_drop_used_field_blocks_and_names_names(repo):
    edit(repo, "drop", "payments.transactions", "status")
    res, v, c = check(repo)
    assert res.returncode == 1 and v["blocked"] and v["label"]
    assert v["blocking"] == [{"consumer": "fraud-service", "team": "@fraud-team", "dataset": "payments.transactions",
                              "fields": ["status"], "at": "consumers/fraud-service/jobs/score_txns.py:26"}]
    assert "::error file=consumers/fraud-service/jobs/score_txns.py,line=26,title=impact-bot::BLOCKED" in res.stdout
    assert "| fraud-service | `@fraud-team` |" in c and "**YES** — `status` at `jobs/score_txns.py:26`, `jobs/score_txns.py:27`" in c
    assert re.search(r"\| finance-recon \|[^\n]*\| no \|", c)
    assert v["review_teams"] == ["@fraud-team"]


def test_no_bare_mentions_in_comment(repo):
    edit(repo, "drop", "payments.transactions", "status")
    _, _, c = check(repo)
    outside_code = re.sub(r"`[^`]*`", "", re.sub(r"```.*?```", "", c, flags=re.S))
    assert "@" not in outside_code          # team handles only inside code spans: no pings to strangers


def test_ack_in_pr_branch_is_ignored(repo):
    edit(repo, "drop", "payments.transactions", "status")
    run([sys.executable, "scripts/ack.py", "fraud-service", "--pr", "11", "--fields", "status", "--reason", "self"], repo)
    res, v, _ = check(repo)
    assert res.returncode == 1 and "ACKs count only from main" in res.stdout


def test_ack_on_main_unblocks(repo):
    edit(repo, "drop", "payments.transactions", "status")
    ack_on_main(repo, "fraud-service", "11", "status")
    res, v, c = check(repo)
    assert res.returncode == 0 and v["label"] is True and "✅ ACKed on main for #11" in c


@pytest.mark.parametrize("pr,fields", [("12", "status"), ("11", "currency")])
def test_ack_is_scoped_to_pr_and_fields(repo, pr, fields):
    edit(repo, "drop", "payments.transactions", "status")
    ack_on_main(repo, "fraud-service", pr, fields)
    res, _, _ = check(repo, pr="11")
    assert res.returncode == 1


def test_deleting_lineage_in_pr_does_not_unregister(repo):
    edit(repo, "drop", "payments.transactions", "status")
    (repo / "contracts/lineage/fraud-service.yml").unlink()
    res, v, _ = check(repo)
    assert res.returncode == 1 and v["blocking"][0]["consumer"] == "fraud-service"


def test_registration_in_flight_still_protects(repo):
    run(["git", "checkout", "-q", "main"], repo)
    (repo / "contracts/lineage/fraud-service.yml").unlink()
    commit(repo, "main without fraud-service registration")
    run(["git", "checkout", "-q", "pr"], repo)
    run(["git", "merge", "-q", "main"], repo)
    shutil.copy(DATA / "contracts/lineage/fraud-service.yml", repo / "contracts/lineage/fraud-service.yml")
    edit(repo, "drop", "payments.transactions", "status")
    res, v, _ = check(repo)
    assert res.returncode == 1 and v["blocking"][0]["consumer"] == "fraud-service"


def test_rename_blocks_every_user_of_old_name(repo):
    edit(repo, "rename", "payments.transactions", "currency", "currency_code")
    res, v, c = check(repo)
    assert res.returncode == 1
    assert {b["consumer"] for b in v["blocking"]} == {"fraud-service", "finance-recon"}
    assert "removes `currency` (renamed to `currency_code`?)" in c


def test_wildcard_consumer_is_affected_by_any_drop(repo):
    p = repo / "consumers/finance-recon/jobs/recon_read.py"
    p.write_text(p.read_text().replace('.groupBy("currency")', '.select("*").groupBy("currency")'))
    commit(repo)
    run(["git", "checkout", "-q", "main"], repo); run(["git", "merge", "-q", "pr"], repo); run(["git", "checkout", "-q", "pr"], repo)
    edit(repo, "drop", "payments.transactions", "risk_score")
    res, v, c = check(repo)
    assert res.returncode == 1 and v["blocking"][0]["consumer"] == "finance-recon" and "wildcard column use" in c


def test_dynamic_consumer_is_affected_fail_closed(repo):
    p = repo / "consumers/finance-recon/jobs/recon_read.py"
    p.write_text(p.read_text() + '\nEXTRA = ["risk_score"]\n\ndef extra(df):\n    return df.select(*[c for c in EXTRA])\n')
    edit(repo, "drop", "payments.transactions", "risk_score")
    res, v, _ = check(repo)
    assert res.returncode == 1 and v["blocking"][0]["consumer"] == "finance-recon"


def test_unregistered_consumer_warns_not_blocks(repo):
    shutil.copytree(repo / "drills/marketing-etl", repo / "consumers/marketing-etl")
    res, v, _ = check(repo)
    assert res.returncode == 0 and "UNREGISTERED CONSUMER: consumers/marketing-etl reads payments.transactions via delta_path" in res.stdout
    assert "::warning file=consumers/marketing-etl/jobs/segment_spend.py,line=7,title=impact-bot::UNREGISTERED CONSUMER" in res.stdout


def test_unregistered_user_of_removed_field_warns(repo):
    shutil.copytree(repo / "drills/marketing-etl", repo / "consumers/marketing-etl")
    p = repo / "consumers/marketing-etl/jobs/segment_spend.py"
    p.write_text(p.read_text().replace('"customer_id", "amount_minor", "currency",', '"customer_id", "amount_minor", "currency", "risk_score",'))
    edit(repo, "drop", "payments.transactions", "risk_score")
    res, v, _ = check(repo)
    assert res.returncode == 0 and "UNREGISTERED CONSUMER USES REMOVED FIELD" in res.stdout


def test_dataset_removal_counts_every_field(repo):
    reg = repo / "contracts/registry.yml"
    reg.write_text(reg.read_text().split("  customers:")[0].replace("  payments.transactions:", "  payments.txn_archive:", 1))
    res, v, c = check(repo)
    assert res.returncode == 1 and "removes the **whole dataset**" in c


def test_invalid_lineage_fails_closed(repo):
    (repo / "contracts/lineage/fraud-service.yml").write_text("consumer: [unclosed\n")
    res, v, _ = check(repo)
    assert res.returncode == 1 and v["errors"]


def test_missing_ack_ref_fails_closed(repo):
    res, _, _ = check(repo, ack_ref="origin/does-not-exist")
    assert res.returncode == 2 and "FAILING CLOSED" in res.stdout


def test_detect(repo):
    sh = str(repo / "scripts/ci/impact_gate.sh")
    (repo / "notes.md").write_text("x\n")
    assert run([sh, "detect", "main"], repo).stdout.strip() == "run=false"
    (repo / "consumers/fraud-service/README.md").write_text("changed\n")
    assert run([sh, "detect", "main"], repo).stdout.strip() == "run=true"
    bad = run([sh, "detect", "no-such-ref"], repo)
    assert bad.returncode != 0 and "run=false" not in bad.stdout


def test_default_output_is_outside_the_repo(repo, tmp_path):
    res = run([str(repo / "scripts/ci/impact_gate.sh"), "check", "main"], repo, PR_NUMBER="1", ACK_REF="main",
              TMPDIR=str(tmp_path), RUNNER_TEMP="")
    assert res.returncode == 0 and (tmp_path / "impact-bot/verdict.json").exists()
    assert run(["git", "status", "--porcelain"], repo).stdout == ""


def test_github_script_is_valid_javascript(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    import yaml
    wf = yaml.safe_load((REPO / ".github/workflows/impact_bot.yml").read_text())
    script = next(s["with"]["script"] for s in wf["jobs"]["impact-bot"]["steps"] if "github-script" in s.get("uses", ""))
    f = tmp_path / "s.js"
    f.write_text("async function main(github, context, core) {\n" + script + "\n}\n")
    assert subprocess.run([node, "--check", str(f)], capture_output=True).returncode == 0
