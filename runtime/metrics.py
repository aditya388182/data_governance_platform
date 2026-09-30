from __future__ import annotations

import urllib.parse
import urllib.request

HELP = {
    "ge_checkpoint_pass": "1 if every expectation of the dataset's suite passed on the live table, else 0",
    "quarantine_rows_total": "rows in the dataset's quarantine table (append-only, cumulative)",
    "quarantine_rows_last_run": "rows newly quarantined by the latest checkpoint run",
    "ge_checkpoint_failed_expectations": "failed expectations in the latest run",
    "ge_checkpoint_source_version": "Delta version of the source table the latest run read",
    "ge_checkpoint_last_run_timestamp_seconds": "when the latest checkpoint run finished",
    "erasure_completions_total": "erasure requests completed and verified (primary records; reruns excluded)",
    "terraform_drift": "1 if terraform plan -detailed-exitcode returned 2 (infrastructure differs from code)",
    "terraform_drift_attribute": "1 per attribute changed outside Terraform (from the plan's resource_drift)",
    "terraform_drift_check_ok": "1 if the drift check's plan ran (exit 0 or 2); 0 if it errored",
    "terraform_pending_changes": "resource changes the plan would make in this workspace",
    "terraform_drift_last_run_timestamp_seconds": "when the latest drift check finished",
    "schema_gate_rejections_total": "PR runs of the gate that concluded failure, in the polling window",
    "gate_runs_total": "PR runs of the gate, in the polling window",
    "gate_latency_seconds": "duration of the gate job on runs that performed the full check",
}


def exposition(values: dict[str, float]) -> str:
    lines = []
    for name, v in values.items():
        lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} gauge", f"{name} {v}"]
    return "\n".join(lines) + "\n"


def push(url: str, job: str, dataset: str, values: dict[str, float], timeout: float = 5.0) -> None:
    """PUT replaces this (job, dataset) group. Raises on any failure (callers fail closed)."""
    target = f"{url.rstrip('/')}/metrics/job/{urllib.parse.quote(job, safe='')}/dataset/{urllib.parse.quote(dataset, safe='')}"
    req = urllib.request.Request(target, data=exposition(values).encode(), method="PUT",
                                 headers={"Content-Type": "text/plain; version=0.0.4"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status not in (200, 202):
            raise RuntimeError(f"pushgateway answered HTTP {r.status}")


def _label(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def push_series(url: str, job: str, grouping: dict, series: list, timeout: float = 5.0) -> None:
    """Day 6: PUT a group of labelled gauges. `series` = [(name, {label: value}, number)].
    Replacing the whole group is the point: a clean run removes the previous run's series."""
    path = f"/metrics/job/{urllib.parse.quote(job, safe='')}" + "".join(
        f"/{urllib.parse.quote(k, safe='')}/{urllib.parse.quote(str(v), safe='')}" for k, v in grouping.items())
    lines, typed = [], set()
    for name, labels, value in series:
        if name not in typed:
            lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} gauge"]
            typed.add(name)
        lbl = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
        lines.append(f"{name}{{{lbl}}} {value}" if lbl else f"{name} {value}")
    req = urllib.request.Request(url.rstrip("/") + path, data=("\n".join(lines) + "\n").encode(), method="PUT",
                                 headers={"Content-Type": "text/plain; version=0.0.4"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status not in (200, 202):
            raise RuntimeError(f"pushgateway answered HTTP {r.status}")
