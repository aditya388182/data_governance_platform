from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml

os.environ.setdefault("GE_USAGE_STATS", "FALSE")
warnings.filterwarnings("ignore")

COLUMN_KWARGS = ("column", "column_A", "column_B")
COLUMN_LIST_KWARGS = ("column_list",)


@dataclass
class ExpectationResult:
    index: int
    expectation_type: str
    target: str
    success: bool
    unexpected_count: int | None
    sample: list = field(default_factory=list)
    error: str | None = None


def load_suite_doc(path: str | Path) -> dict:
    doc = yaml.safe_load(Path(path).read_text()) or {}
    if not doc.get("expectation_suite_name"):
        raise ValueError(f"{path}: missing expectation_suite_name")
    if not doc.get("expectations"):
        raise ValueError(f"{path}: suite has no expectations — an empty suite is not a contract")
    return doc


def suite_columns(doc: dict) -> set[str]:
    cols: set[str] = set()
    for e in doc["expectations"]:
        kw = e.get("kwargs") or {}
        cols |= {kw[k] for k in COLUMN_KWARGS if k in kw}
        for k in COLUMN_LIST_KWARGS:
            cols |= set(kw.get(k) or [])
    return cols


def _target(kw: dict) -> str:
    if "column_A" in kw:
        return f"{kw['column_A']} vs {kw['column_B']}"
    return kw.get("column", "(table)")


def _context():
    from great_expectations.data_context import EphemeralDataContext
    from great_expectations.data_context.types.base import (
        DataContextConfig, InMemoryStoreBackendDefaults, ProgressBarsConfig)
    cfg = DataContextConfig(
        store_backend_defaults=InMemoryStoreBackendDefaults(),
        anonymous_usage_statistics={"enabled": False},
        progress_bars=ProgressBarsConfig(globally=False, profilers=False, metric_calculations=False),
    )
    return EphemeralDataContext(project_config=cfg)


def validate(df, suite_doc: dict, engine: str = "pandas") -> list[ExpectationResult]:
    from great_expectations.core import ExpectationConfiguration, ExpectationSuite
    ctx = _context()
    configs = [ExpectationConfiguration(expectation_type=e["expectation_type"], kwargs=e.get("kwargs") or {},
                                        meta=e.get("meta") or {}) for e in suite_doc["expectations"]]
    suite = ExpectationSuite(expectation_suite_name=suite_doc["expectation_suite_name"], expectations=configs,
                             meta=suite_doc.get("meta") or {})
    if engine != "pandas":
        # Day 4 adds the Spark path for runtime checkpoints over live Delta tables.
        # Not shipped yet on purpose: untested code does not go into a gate.
        raise NotImplementedError(f"engine '{engine}' arrives on Day 4")
    validator = ctx.sources.pandas_default.read_dataframe(df)
    res = validator.validate(expectation_suite=suite, result_format={"result_format": "SUMMARY", "partial_unexpected_count": 5},
                             catch_exceptions=True)
    # map results back to suite order
    out: list[ExpectationResult] = []
    remaining = list(res.results)
    for i, cfg in enumerate(configs):
        # GE adds default kwargs (batch_id, mostly, ...) to each result's config, so
        # match on expectation type + every kwarg WE declared (a subset match).
        match = next((r for r in remaining if r.expectation_config.expectation_type == cfg.expectation_type
                      and all(r.expectation_config.kwargs.get(k) == v for k, v in cfg.kwargs.items())), None)
        if match is None:
            out.append(ExpectationResult(i, cfg.expectation_type, _target(cfg.kwargs), False, None, [], "no result returned"))
            continue
        remaining.remove(match)
        info = match.exception_info or {}
        err = None
        if isinstance(info, dict):
            if info.get("raised_exception"):
                err = str(info.get("exception_message"))[:200]
            else:
                nested = [v for v in info.values() if isinstance(v, dict) and v.get("raised_exception")]
                if nested:
                    err = str(nested[0].get("exception_message"))[:200]
        r = match.result or {}
        out.append(ExpectationResult(i, cfg.expectation_type, _target(cfg.kwargs), bool(match.success),
                                     r.get("unexpected_count"), list(r.get("partial_unexpected_list") or [])[:5], err))
    return out
