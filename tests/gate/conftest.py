import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from mock_registry import MockRegistry  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def registry():
    with MockRegistry() as reg:
        yield reg


def run(cmd, cwd=REPO, env_extra=None, timeout=120):
    env = dict(os.environ)
    env.update({
        "PYTHON": sys.executable,
        "GIT_AUTHOR_NAME": "gate-test", "GIT_AUTHOR_EMAIL": "gate@test.local",
        "GIT_COMMITTER_NAME": "gate-test", "GIT_COMMITTER_EMAIL": "gate@test.local",
        "REGISTRY_WAIT_INTERVAL": "1",
    })
    env.pop("GITHUB_STEP_SUMMARY", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


@pytest.fixture()
def gate_repo(tmp_path):
    """A throwaway git repo holding a copy of the real contracts + gate scripts,
    with one bootstrap commit on main (schema version 1)."""
    root = tmp_path / "repo"
    root.mkdir()
    for rel in ("contracts/registry.yml", "contracts/schemas", "scripts/ci", "infra/ci",
                ".github/workflows/schema_gate.yml", "drills/day1"):
        src = REPO / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    (root / "README.md").write_text("test repo\n")

    def git(*args):
        r = run(["git", *args], cwd=root)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "bootstrap: v1 contracts")
    root_git = git
    return root, root_git
