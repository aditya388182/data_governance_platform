import os
from pathlib import Path

import pendulum

from airflow import DAG
from airflow.operators.bash import BashOperator

REPO = Path(os.environ.get("P4_REPO", str(Path.home() / "data_governance_platform"))).expanduser().resolve()
PY = os.environ.get("P4_PYTHON", str(REPO / ".venv" / "bin" / "python"))
TF = os.environ.get("P4_TERRAFORM", "terraform")
WORKSPACES = ["dev", "staging", "prod"]

with DAG(
    dag_id="drift_detection",
    schedule="30 2 * * *",
    start_date=pendulum.datetime(2026, 9, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    tags=["project4", "governance", "stage5"],
    doc_md=__doc__,
) as dag:
    for ws in WORKSPACES:
        BashOperator(
            task_id=f"drift__{ws}",
            bash_command=f'cd "{REPO}" && TF_BIN="{TF}" "{PY}" "{REPO}/scripts/drift_check.py" --workspaces {ws}',
            append_env=True,
        )
