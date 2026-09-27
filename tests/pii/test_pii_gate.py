import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DATA = Path(__file__).resolve().parent / "data"      # frozen test universe (see data/README.md)
GATE = [sys.executable, "scripts/ci/pii_gate.py"]


def run(cmd, cwd, **kw):
    env = dict(os.environ, PYTHON=sys.executable, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    env.pop("GITHUB_STEP_SUMMARY", None)
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=120, **kw)


@pytest.fixture()
def repo(tmp_path):
    """A repo copy; `base/` holds a snapshot of main's contracts/ for --base-root."""
    root = tmp_path / "r"
    for rel in ("govlib", "scripts", ".github"):                       # code under test: live
        shutil.copytree(REPO / rel, root / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(DATA / "contracts", root / "contracts")             # contracts: frozen
    shutil.copytree(DATA / "contracts", tmp_path / "base" / "contracts")
    return root


def gate(root, base=None):
    base = base or (root.parent / "base")
    return run(GATE + ["--base-root", str(base)], cwd=root)


def catalog(root):
    return root / "contracts/pii_catalog.yml"


def add_entry(root, line):
    p = catalog(root)
    p.write_text(p.read_text().replace("\ndetection:", f"  {line}\n\ndetection:", 1))


def add_field(root, name, table_schema="contracts/schemas/payments_transactions.avsc"):
    p = root / table_schema
    s = p.read_text()
    new = f'    {{"name": "{name}", "type": ["null", "string"], "default": null}}'
    p.write_text(re.sub(r"\n  \]\n\}\n$", f",\n{new}\n  ]\n}}\n", s))


def test_clean_passes(repo):
    r = gate(repo)
    assert r.returncode == 0, r.stdout


def test_unclassified_new_field_fails(repo):
    shutil.copy(DATA / "pr_receipt_email.avsc", repo / "contracts/schemas/payments_transactions.avsc")
    r = gate(repo)
    assert r.returncode == 1
    assert "UNCLASSIFIED PII field 'receipt_email' in transactions (new in this PR" in r.stdout


def test_classifying_it_passes(repo):
    shutil.copy(DATA / "pr_receipt_email.avsc", repo / "contracts/schemas/payments_transactions.avsc")
    add_entry(repo, "transactions.receipt_email: {class: DIRECT_IDENTIFIER, treatment: ENCRYPT_AESGCM}")
    assert gate(repo).returncode == 0


def test_not_pii_requires_reason(repo):
    add_field(repo, "merchant_name")
    assert "UNCLASSIFIED PII field 'merchant_name'" in gate(repo).stdout       # name pattern is conservative
    add_entry(repo, "transactions.merchant_name: {class: NOT_PII, treatment: NONE}")
    r = gate(repo)
    assert r.returncode == 1 and "needs a written reason" in r.stdout
    p = catalog(repo)
    p.write_text(p.read_text().replace("transactions.merchant_name: {class: NOT_PII, treatment: NONE}",
                                       'transactions.merchant_name: {class: NOT_PII, treatment: NONE, reason: "business name, not a person"}'))
    assert gate(repo).returncode == 0


def test_protected_class_needs_protective_treatment(repo):
    p = catalog(repo)
    p.write_text(p.read_text().replace("customers.ssn:            {class: DIRECT_IDENTIFIER, treatment: TOKENIZE_HMAC}",
                                       "customers.ssn:            {class: DIRECT_IDENTIFIER, treatment: NONE}"))
    r = gate(repo)
    assert r.returncode == 1 and "requires treatment TOKENIZE_HMAC or ENCRYPT_AESGCM" in r.stdout


def test_unknown_table_fails(repo):
    add_entry(repo, "orders.email: {class: DIRECT_IDENTIFIER, treatment: ENCRYPT_AESGCM}")
    r = gate(repo)
    assert r.returncode == 1 and "has table 'orders'" in r.stdout


def test_deleting_entry_of_existing_column_fails(repo):
    p = catalog(repo)
    p.write_text("\n".join(l for l in p.read_text().splitlines() if "transactions.merchant_id" not in l) + "\n")
    r = gate(repo)
    assert r.returncode == 1 and "was deleted from the catalog while the column still exists" in r.stdout


def test_downgrade_warns_but_passes(repo):
    p = catalog(repo)
    p.write_text(p.read_text().replace("customers.full_name:      {class: DIRECT_IDENTIFIER",
                                       "customers.full_name:      {class: QUASI_IDENTIFIER"))
    r = gate(repo)
    assert r.returncode == 0 and "classification DOWNGRADE 'customers.full_name'" in r.stdout


def test_pr_cannot_weaken_detection_and_add_pii_in_one_diff(repo):
    p = catalog(repo)
    p.write_text(p.read_text().replace("    - 'e_?mail'\n", ""))            # PR removes the email pattern…
    shutil.copy(DATA / "pr_receipt_email.avsc", repo / "contracts/schemas/payments_transactions.avsc")
    r = gate(repo)                                                          # …but main's patterns still apply
    assert r.returncode == 1 and "UNCLASSIFIED PII field 'receipt_email'" in r.stdout
    assert "detection.name_patterns changed in this PR" in r.stdout


def test_bootstrap_without_base_catalog_uses_pr_patterns(repo, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert gate(repo, base=empty).returncode == 0


@pytest.mark.parametrize("field,flagged", [("zip_code", True), ("ip_address", True), ("client_ip", True),
                                           ("receipt_email", True), ("phone_number", True), ("date_of_birth", True),
                                           ("shipping_address", True), ("tip_amount", False), ("zipper_color", False),
                                           ("equipment_id", False), ("risk_score", False), ("currency", False)])
def test_detection_patterns(field, flagged):
    sys.path.insert(0, str(REPO))
    from govlib.contracts import load_catalog
    from govlib.pii import compile_patterns, matches
    pats = compile_patterns(load_catalog(root=DATA).name_patterns)
    assert bool(matches(field, pats)) is flagged


def test_subject_key_required_where_pii_lives(repo):
    p = catalog(repo)
    p.write_text(p.read_text().replace("subject_key: customer_id", "subject_key: account_id"))
    r = gate(repo)
    assert r.returncode == 1 and "has no subject key column 'account_id'" in r.stdout


def test_pii_gate_sh_end_to_end_with_git(repo):
    run(["git", "init", "-q", "-b", "main"], cwd=repo)
    run(["git", "add", "-A"], cwd=repo)
    run(["git", "commit", "-qm", "base"], cwd=repo)
    run(["git", "checkout", "-qb", "add-receipt-email"], cwd=repo)
    sh = str(repo / "scripts/ci/pii_gate.sh")
    (repo / "notes.md").write_text("x\n")
    assert run([sh, "detect", "main"], cwd=repo).stdout.strip() == "run=false"
    shutil.copy(DATA / "pr_receipt_email.avsc", repo / "contracts/schemas/payments_transactions.avsc")
    assert run([sh, "detect", "main"], cwd=repo).stdout.strip() == "run=true"
    red = run([sh, "check", "main"], cwd=repo)
    assert red.returncode == 1 and "UNCLASSIFIED PII field 'receipt_email'" in red.stdout
    add_entry(repo, "transactions.receipt_email: {class: DIRECT_IDENTIFIER, treatment: ENCRYPT_AESGCM}")
    assert run([sh, "check", "main"], cwd=repo).returncode == 0
