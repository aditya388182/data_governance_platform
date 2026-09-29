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
