import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts import check_readme_assets, gate_metrics_poller, readme_red_index  # noqa: E402


def fake_api(runs_by_wf, jobs_by_run):
    def fetch(path):
        if "/jobs" in path:
            return {"jobs": jobs_by_run[int(path.split("/runs/")[1].split("/")[0])]}
        wf = path.split("/workflows/")[1].split("/")[0]
        return None if wf not in runs_by_wf else {"workflow_runs": runs_by_wf[wf]}
    return fetch


def job(name, secs, fast=False):
    steps = [{"name": "Nothing governed changed — pass", "conclusion": "success" if fast else "skipped"}]
    done = f"2026-09-01T10:{secs // 60:02d}:{secs % 60:02d}Z"
    return {"name": name, "started_at": "2026-09-01T10:00:00Z", "completed_at": done, "steps": steps}


def test_poller_counts_rejections_and_excludes_fast_path_from_latency():
    runs = {"schema_gate.yml": [{"id": 1, "status": "completed", "conclusion": "failure"},
                                {"id": 2, "status": "completed", "conclusion": "success"},
                                {"id": 3, "status": "completed", "conclusion": "success"},
                                {"id": 4, "status": "in_progress", "conclusion": None}]}
    jobs = {1: [job("schema-gate", 200)], 2: [job("schema-gate", 180)], 3: [job("schema-gate", 12, fast=True)]}
    s = gate_metrics_poller.collect(fake_api(runs, jobs), "o/r", "2026-09-01")
    assert s["schema-gate"] == {"runs": 3, "rejections": 1, "full_checks": 2, "p50": 190.0, "p90": 200.0}
    assert s["terraform-plan"]["runs"] == 0


def test_red_index_selects_red_titles_and_splices_between_markers():
    prs = [{"number": 2, "title": "contracts: rename currency -> currency_code (expected: RED)", "url": "u2", "mergedAt": None},
           {"number": 7, "title": "contracts: add receipt_email (red → classified → green)", "url": "u7", "mergedAt": "x"},
           {"number": 9, "title": "day5: erasure that actually erases (expected: all four checks GREEN)", "url": "u9", "mergedAt": "x"}]
    block, n = readme_red_index.rows(prs)
    assert n == 2 and "[#2](u2)" in block[2] and "closed, red preserved" in block[2] and "merged after the fix" in block[3]
    text = f"a\n{readme_red_index.START}\nold\n{readme_red_index.END}\nb"
    assert "old" not in readme_red_index.splice(text, block) and "[#7](u7)" in readme_red_index.splice(text, block)


def test_readme_asset_check(tmp_path):
    (tmp_path / "docs" / "screenshots").mkdir(parents=True)
    for f in check_readme_assets.REQUIRED[:-1]:
        (tmp_path / "docs" / "screenshots" / f).write_bytes(b"x")
    (tmp_path / "README.md").write_text(" ".join(f"docs/screenshots/{f}" for f in check_readme_assets.REQUIRED))
    broken, unref, missing, extra = check_readme_assets.check(tmp_path)
    assert broken == ["16_grafana_governance_health.png"] and missing == ["16_grafana_governance_health.png"] and not unref


def test_terraform_guards():
    tf = REPO / "terraform"
    backend = (tf / "backend.tf").read_text()
    assert 'dynamodb_table = "tf-locks"' in backend and "endpoints = {" in backend and "use_path_style" in backend
    mod = (tf / "modules/encrypted_bucket/main.tf").read_text()
    assert re.search(r'"protected" \{[^}]*count = var.prevent_destroy \? 1 : 0.*?prevent_destroy = true', mod, re.S)
    vars_ = (tf / "modules/encrypted_bucket/variables.tf").read_text()
    assert vars_.count("validation {") >= 3 and vars_.count("nullable    = false") >= 3
    main = (tf / "main.tf").read_text()
    assert 'ignore_changes_to = ["compatibility"]' in main and "compatibilityLevel = each.value" in main
    assert "registry.yml" in main


def test_gate_and_workflow_guards():
    gate = (REPO / "scripts/ci/terraform_gate.sh").read_text()
    loop = gate[gate.index("for ws in $WS"):]
    assert "validate" not in gate[:gate.index("for ws in $WS")].split("init -input=false")[-1]
    assert loop.index("workspace select") < loop.index("validate") < loop.index("-detailed-exitcode")
    wf = (REPO / ".github/workflows/terraform_gate.yml").read_text()
    assert "name: terraform-plan" in wf and "terraform_wrapper: false" in wf and not re.search(r"^\s+paths:", wf, re.M)
    assert "\n  terraform-plan:\n    name: terraform-plan\n" in wf
    apply = (REPO / ".github/workflows/terraform_apply.yml").read_text()
    assert "environment: production" in apply and "branches: [main]" in apply


def test_drift_dag_one_task_per_workspace():
    dag = (REPO / "airflow/dags/drift_detection.py").read_text()
    assert 'WORKSPACES = ["dev", "staging", "prod"]' in dag and "drift_check.py" in dag and "max_active_tasks=1" in dag


def test_readme_numbers_reads_your_crypto_column_and_refuses_sandbox_copies():
    import pytest

    from scripts import readme_numbers as rn
    good = "\n".join(f"| {op} | 1.00 | {2 + i}.50 |" for i, op in enumerate(rn.OPS))
    assert rn.crypto_numbers(good)[rn.OPS[0]] == (1.0, 2.5)
    with pytest.raises(SystemExit, match="equals the sandbox"):
        rn.crypto_numbers("\n".join(f"| {op} | 1.00 | 1.00 |" for op in rn.OPS))
    with pytest.raises(SystemExit, match="fill in"):
        rn.crypto_numbers("\n".join(f"| {op} | 1.00 |  |" for op in rn.OPS))
    gates = {"schema-gate": {"runs": 5, "rejections": 2, "full_checks": 3, "p50": 190.0, "p90": 245.0},
             "terraform-plan": {"runs": 0, "rejections": 0, "full_checks": 0, "p50": None, "p90": None}}
    rows = rn.table(rn.crypto_numbers(good), {"first_s": 0.8, "rerun_s": 0.1, "rows": 109, "tables": 2}, gates, 60)
    text = "\n".join(rows)
    assert "3m10s / 4m05s" in text and "schema-gate 2" in text and "terraform-plan" not in text and "0.80 s" in text


def test_every_workflow_is_valid_yaml_and_required_gates_bind():
    """A step name containing ': ' breaks YAML and GitHub silently drops the workflow (caught on Day 6)."""
    import yaml
    required = {"schema_gate.yml": "schema-gate", "ge_gate.yml": "ge-gate", "pii_gate.yml": "pii-gate",
                "impact_bot.yml": "impact-bot", "terraform_gate.yml": "terraform-plan"}
    for wf in sorted((REPO / ".github/workflows").glob("*.yml")):
        d = yaml.safe_load(wf.read_text())
        on = d.get(True) or d.get("on")
        if wf.name in required:
            gate = required[wf.name]
            assert d["jobs"][gate]["name"] == gate, wf.name
            assert "pull_request" in on and "paths" not in (on["pull_request"] or {}), wf.name
    apply = yaml.safe_load((REPO / ".github/workflows/terraform_apply.yml").read_text())
    assert apply["jobs"]["terraform-apply"]["environment"] == "production"
