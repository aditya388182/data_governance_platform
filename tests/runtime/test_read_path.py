import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BAD = re.compile(r"\.to_pyarrow_(table|dataset)\(")


def test_no_abort_prone_read_path_in_code():
    offenders = []
    for d in ("crypto", "runtime", "scripts", "drills", "airflow", "govlib", "tests"):
        for f in (REPO / d).rglob("*.py"):
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if BAD.search(line) and "noqa: read-path" not in line and f.name != "test_read_path.py":
                    offenders.append(f"{f.relative_to(REPO)}:{i}")
    assert offenders == [], f"use govlib.lake.read_table instead: {offenders}"
