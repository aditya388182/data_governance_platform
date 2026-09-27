import shutil
import sys
from pathlib import Path

from conftest import DATA, REPO, run

CC = str(REPO / "scripts/ci/compat_check.sh")
WAIT = str(REPO / "scripts/ci/wait_fail_closed.sh")
# Frozen test universe — never the live contracts (see tests/gate/data/README.md)
S = DATA / "payments_v1.avsc"
A = DATA / "pr_a.avsc"
B = DATA / "pr_b.avsc"
C = DATA / "pr_c.avsc"


def hist(tmp_path, *files):
    d = tmp_path / "hist"
    d.mkdir()
    for i, f in enumerate(files, 1):
        shutil.copy(f, d / f"{i:04d}.avsc")
    return str(d)


#  wait_fail_closed
def test_wait_ready(registry):
    r = run([WAIT, registry.url])
    assert r.returncode == 0 and "registry ready" in r.stdout


def test_wait_fails_closed_when_registry_down():
    r = run([WAIT, "http://127.0.0.1:9"], env_extra={"REGISTRY_WAIT_TIMEOUT": "2"})
    assert r.returncode == 1
    assert "FAILING CLOSED" in r.stdout and "::error" in r.stdout


#  compat_check verdicts
def test_nullable_add_is_compatible(registry, tmp_path):
    r = run([CC, "payments.public.transactions-value", str(A), "BACKWARD_TRANSITIVE", hist(tmp_path, S)],
            env_extra={"REG": registry.url})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "COMPATIBLE" in r.stdout and "1 prior version" in r.stdout


def test_rename_is_breaking(registry, tmp_path):
    r = run([CC, "payments.public.transactions-value", str(B), "BACKWARD_TRANSITIVE", hist(tmp_path, S, A)],
            env_extra={"REG": registry.url})
    assert r.returncode == 1
    assert "BREAKING SCHEMA CHANGE" in r.stdout


def test_transitivity_needs_history(registry, tmp_path):
    h = hist(tmp_path, S, A)
    plain = run([CC, "t-value", str(C), "BACKWARD", h], env_extra={"REG": registry.url})
    trans = run([CC, "t-value", str(C), "BACKWARD_TRANSITIVE", h], env_extra={"REG": registry.url})
    assert plain.returncode == 0, plain.stdout
    assert trans.returncode == 1, trans.stdout
    assert "vs version 1 (replayed from main): INCOMPATIBLE" in trans.stdout
    assert "vs version 2 (replayed from main): compatible" in trans.stdout


def test_without_history_transitive_collapses_to_backward(registry, tmp_path):
    """The original plan's bug, pinned: only the latest version registered => PR C passes."""
    r = run([CC, "t-value", str(C), "BACKWARD_TRANSITIVE", hist(tmp_path, A)], env_extra={"REG": registry.url})
    assert r.returncode == 0


def test_invalid_json_fails(registry, tmp_path):
    bad = tmp_path / "bad.avsc"
    bad.write_text("{not json")
    r = run([CC, "t-value", str(bad), "BACKWARD"], env_extra={"REG": registry.url})
    assert r.returncode == 1 and "INVALID SCHEMA" in r.stdout


def test_invalid_avro_fails(registry, tmp_path):
    bad = tmp_path / "bad.avsc"
    bad.write_text('{"type":"record","name":"X","fields":[{"name":"a","type":"no_such_type"}]}')
    r = run([CC, "t-value", str(bad), "BACKWARD"], env_extra={"REG": registry.url})
    assert r.returncode == 1 and "INVALID SCHEMA" in r.stdout


#  fail-closed paths
def test_registry_5xx_fails_closed(registry, tmp_path):
    registry.state.faults = {"register_status": 500}
    r = run([CC, "t-value", str(A), "BACKWARD_TRANSITIVE", hist(tmp_path, S)], env_extra={"REG": registry.url})
    assert r.returncode == 2 and "FAILING CLOSED" in r.stdout


def test_mode_not_applied_fails_closed(registry, tmp_path):
    registry.state.faults = {"config_echo": "NONE"}
    r = run([CC, "t-value", str(A), "BACKWARD_TRANSITIVE", hist(tmp_path, S)], env_extra={"REG": registry.url})
    assert r.returncode == 2 and "FAILING CLOSED" in r.stdout


def test_registry_down_fails_closed(tmp_path):
    r = run([CC, "t-value", str(A), "BACKWARD_TRANSITIVE"],
            env_extra={"REG": "http://127.0.0.1:9", "REGISTRY_WAIT_TIMEOUT": "2"})
    assert r.returncode == 2 and "FAILING CLOSED" in r.stdout


def test_throwaway_subjects_are_deleted(registry, tmp_path):
    run([CC, "t-value", str(A), "BACKWARD_TRANSITIVE", hist(tmp_path, S)], env_extra={"REG": registry.url})
    assert registry.live_subjects() == []


#  orchestrator (real git history)
def _cp(src, root, rel="contracts/schemas/payments_transactions.avsc"):
    shutil.copy(src, root / rel)


def test_day1_pr_sequence(registry, gate_repo):
    root, git = gate_repo
    env = {"REG": registry.url}
    gate = str(root / "scripts/ci/schema_gate.sh")

    # PR A (green)
    git("checkout", "-q", "-b", "add-risk-score")
    _cp(A, root)
    assert run([gate, "detect", "main"], cwd=root).stdout.strip() == "run=true"
    r = run([gate, "check", "main"], cwd=root, env_extra=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "history v1" in r.stdout
    git("commit", "-qam", "PR A: add nullable risk_score")
    git("checkout", "-q", "main")
    git("merge", "-q", "--squash", "add-risk-score")
    git("commit", "-qm", "Add risk_score (#1)")

    # PR B (red: rename)
    git("checkout", "-q", "-b", "rename-currency")
    _cp(B, root)
    r = run([gate, "check", "main"], cwd=root, env_extra=env)
    assert r.returncode == 1 and "BREAKING SCHEMA CHANGE" in r.stdout
    git("checkout", "-q", "--", ".")
    git("checkout", "-q", "main")

    # PR C (red: transitivity, only visible because history v1+v2 is replayed)
    git("checkout", "-q", "-b", "drop-risk-default")
    _cp(C, root)
    r = run([gate, "check", "main"], cwd=root, env_extra=env)
    assert r.returncode == 1, r.stdout
    assert "history v2" in r.stdout
    assert "vs version 1 (replayed from main): INCOMPATIBLE" in r.stdout
    git("checkout", "-q", "--", ".")
    git("checkout", "-q", "main")
    assert registry.live_subjects() == []


def test_docs_only_change_skips_registry(gate_repo):
    root, git = gate_repo
    git("checkout", "-q", "-b", "docs")
    (root / "docs").mkdir()
    (root / "docs/daily_log.md").write_text("notes\n")
    r = run([str(root / "scripts/ci/schema_gate.sh"), "detect", "main"], cwd=root)
    assert r.returncode == 0 and r.stdout.strip() == "run=false"


def test_gate_infra_change_self_tests_all_datasets(registry, gate_repo):
    root, git = gate_repo
    git("checkout", "-q", "-b", "infra")
    p = root / "infra/ci/registry-compose.yml"
    p.write_text(p.read_text() + "\n# touched\n")
    gate = str(root / "scripts/ci/schema_gate.sh")
    assert run([gate, "detect", "main"], cwd=root).stdout.strip() == "run=true"
    r = run([gate, "check", "main"], cwd=root, env_extra={"REG": registry.url})
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("self-test") >= 2   # both datasets


def test_detect_fails_closed_on_bad_base(gate_repo):
    root, _ = gate_repo
    r = run([str(root / "scripts/ci/schema_gate.sh"), "detect", "no-such-ref"], cwd=root)
    assert r.returncode != 0 and "FAILING CLOSED" in r.stderr
    assert "run=false" not in r.stdout


def test_pr_cannot_weaken_mode_and_break_in_same_diff(registry, gate_repo):
    root, git = gate_repo
    git("checkout", "-q", "-b", "setup")
    _cp(A, root)
    git("commit", "-qam", "v2")
    git("checkout", "-q", "main")
    git("merge", "-q", "setup")
    git("checkout", "-q", "-b", "sneaky")
    reg = root / "contracts/registry.yml"
    reg.write_text(reg.read_text().replace("compatibility: BACKWARD_TRANSITIVE", "compatibility: BACKWARD", 1))
    _cp(C, root)   # passes BACKWARD, fails BACKWARD_TRANSITIVE
    r = run([str(root / "scripts/ci/schema_gate.sh"), "check", "main"], cwd=root, env_extra={"REG": registry.url})
    assert r.returncode == 1, r.stdout
    assert "compatibility change BACKWARD_TRANSITIVE -> BACKWARD" in r.stderr


def test_ungoverned_schema_file_is_refused(registry, gate_repo):
    root, git = gate_repo
    git("checkout", "-q", "-b", "stray")
    (root / "contracts/schemas/stray.avsc").write_text('{"type":"record","name":"S","fields":[{"name":"a","type":"string"}]}')
    r = run([str(root / "scripts/ci/schema_gate.sh"), "check", "main"], cwd=root, env_extra={"REG": registry.url})
    assert r.returncode != 0 and "not referenced by any dataset" in r.stderr


def test_dataset_removal_is_refused(registry, gate_repo):
    root, git = gate_repo
    git("checkout", "-q", "-b", "remove")
    reg = root / "contracts/registry.yml"
    text = reg.read_text()
    reg.write_text(text[: text.index("  customers:")])
    (root / "contracts/schemas/customers.avsc").unlink()
    r = run([str(root / "scripts/ci/schema_gate.sh"), "check", "main"], cwd=root, env_extra={"REG": registry.url})
    assert r.returncode != 0 and "was removed from the registry" in r.stderr


def test_lint_rejects_mode_none(gate_repo):
    root, git = gate_repo
    reg = root / "contracts/registry.yml"
    reg.write_text(reg.read_text().replace("compatibility: BACKWARD_TRANSITIVE", "compatibility: NONE", 1))
    r = run([sys.executable, "scripts/ci/contract_tool.py", "lint"], cwd=root)
    assert r.returncode == 1 and "NONE is not allowed" in r.stderr


#  the drill's oracle
def test_drill_oracle_matches_reference(registry, tmp_path):
    out = tmp_path / "matrix.md"
    r = run([sys.executable, str(REPO / "scripts/compat_matrix_drill.py"), "--registry", registry.url,
             "--out", str(out), "--timeout", "5"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "36/36 verdicts match the oracle" in r.stdout
    assert registry.live_subjects() == []
    assert "Oracle mismatches in this run: **0**" in out.read_text()
