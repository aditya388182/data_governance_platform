import os
from pathlib import Path

import pendulum
import yaml

from airflow import DAG
from airflow.operators.bash import BashOperator

REPO = Path(os.environ.get("P4_REPO", str(Path.home() / "data_governance_platform"))).expanduser().resolve()
PY = os.environ.get("P4_PYTHON", str(REPO / ".venv" / "bin" / "python"))
DATASETS = list((yaml.safe_load((REPO / "contracts" / "registry.yml").read_text()) or {}).get("datasets") or {})
if not DATASETS:
    raise RuntimeError(f"no datasets in {REPO}/contracts/registry.yml — refusing to schedule an empty checkpoint")

with DAG(
    dag_id="ge_checkpoint_daily",
    schedule="0 6 * * *",
    start_date=pendulum.datetime(2026, 9, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["project4", "governance", "stage3"],
    doc_md=__doc__,
) as dag:
    for name in DATASETS:
        BashOperator(
            task_id="checkpoint__" + name.replace(".", "_"),
            bash_command=f'cd "{REPO}" && "{PY}" "{REPO}/scripts/ge_checkpoint.py" --dataset "{name}"',
            append_env=True,
        )
