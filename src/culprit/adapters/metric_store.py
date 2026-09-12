"""Metric store adapters.

Culprit reads evaluation history from a *metric store*. The reference implementation is a JSON
file written by a nightly job (the shape mirrors what MLflow / W&B / SageMaker Experiments expose:
one record per run with a commit sha and a metrics dict). Swapping in a tracking server means
implementing :class:`MetricStore` — nothing else in the system knows where metrics come from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from culprit.models import MetricRun, RegressionWindow


class MetricStore(Protocol):
    def list_runs(self, branch: str | None = None) -> list[MetricRun]:
        """Return runs ordered oldest -> newest."""
        ...


class JsonMetricStore:
    """Metric history stored as ``{"runs": [...]}`` in a JSON file."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def list_runs(self, branch: str | None = None) -> list[MetricRun]:
        if not self.path.exists():
            raise FileNotFoundError(f"metric history not found at {self.path}")
        payload = json.loads(self.path.read_text())
        raw_runs = payload["runs"] if isinstance(payload, dict) else payload
        runs = [MetricRun.model_validate(r) for r in raw_runs]
        if branch:
            runs = [r for r in runs if r.branch == branch]
        return sorted(runs, key=lambda r: r.timestamp)

    def append(self, run: MetricRun) -> None:
        payload = {"runs": []}
        if self.path.exists():
            payload = json.loads(self.path.read_text())
        payload.setdefault("runs", []).append(run.model_dump())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2))


def detect_regression(
    runs: list[MetricRun],
    metric: str,
    higher_is_better: bool = True,
    threshold: float = 0.03,
) -> RegressionWindow | None:
    """Find the most recent good->bad transition in a run history.

    Walks the successful runs oldest to newest and flags the latest step where the metric moved
    against the "better" direction by at least ``threshold``. Returns ``None`` if no regression.
    """
    ok = [r for r in runs if r.status == "success" and metric in r.metrics]
    window: RegressionWindow | None = None
    for prev, cur in zip(ok, ok[1:]):
        before, after = prev.metrics[metric], cur.metrics[metric]
        drop = (before - after) if higher_is_better else (after - before)
        if drop >= threshold:
            window = RegressionWindow(
                metric=metric,
                last_good_run=prev,
                first_bad_run=cur,
                baseline_value=before,
                regressed_value=after,
                delta=round(after - before, 4),
            )
    return window
