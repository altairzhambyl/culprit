"""Typed domain models shared by tools, hooks, the service layer and the UI."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------------
# Target-project configuration (lives in the investigated repository as ``.culprit.yaml``)
# ------------------------------------------------------------------------------------------------


class ProjectConfig(BaseModel):
    """How Culprit runs experiments for a given repository.

    Any repository can be investigated by adding a ``.culprit.yaml`` at its root, for example::

        metric: f1
        higher_is_better: true
        regression_threshold: 0.03
        experiment_command: "python -m churn.train --config {config} --out {out}"
        test_command: "python -m pytest -q"
        metrics_history: "nightly/metrics_history.json"
        default_config: smoke
        nightly_config: full
    """

    metric: str = "f1"
    higher_is_better: bool = True
    regression_threshold: float = Field(0.03, description="Absolute drop that counts as a regression")
    experiment_command: str = Field(..., description="Shell command; {config} and {out} are substituted")
    test_command: str | None = Field(None, description="Shell command that runs the project's tests")
    metrics_history: str = Field(
        "nightly/metrics_history.json", description="Path (relative to repo) of the metric store"
    )
    default_config: str = "smoke"
    nightly_config: str | None = Field(
        None, description="Config the nightly job uses (culprit record-nightly)"
    )
    experiment_timeout_s: int = 600
    main_branch: str = "main"


# ------------------------------------------------------------------------------------------------
# Metrics & experiments
# ------------------------------------------------------------------------------------------------


class MetricRun(BaseModel):
    """One recorded evaluation run (e.g. a nightly job) from the metric store."""

    run_id: str
    timestamp: str
    commit: str
    branch: str = "main"
    config: str = "full"
    metrics: dict[str, float]
    status: Literal["success", "failed"] = "success"


class RegressionWindow(BaseModel):
    metric: str
    last_good_run: MetricRun
    first_bad_run: MetricRun
    baseline_value: float
    regressed_value: float
    delta: float


class ExperimentResult(BaseModel):
    ref: str
    sha: str
    config: str
    status: Literal["ok", "failed"]
    metrics: dict[str, float] = Field(default_factory=dict)
    duration_s: float = 0.0
    cached: bool = False
    note: str = ""
    stderr_tail: str = ""
    started_at: str = Field(default_factory=utcnow_iso)


# ------------------------------------------------------------------------------------------------
# Final structured output of an investigation
# ------------------------------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    ref: str = Field(..., description="Commit sha (short) or branch the evidence refers to")
    metric_value: float = Field(..., description="Value of the tracked metric measured for this ref")
    verdict: Literal["good", "bad", "fixed"]


class IncidentReport(BaseModel):
    """Structured post-mortem produced at the end of every investigation."""

    title: str = Field(
        ..., description="One-line headline, e.g. 'F1 regression caused by categorical encoding refactor'"
    )
    metric: str
    baseline_value: float = Field(..., description="Metric value before the regression")
    regressed_value: float = Field(..., description="Metric value after the regression")
    recovered_value: float | None = Field(
        None, description="Metric value measured on the fix branch, if a fix was verified"
    )
    culprit_commit: str = Field(..., description="Short sha of the commit that introduced the regression")
    culprit_subject: str = Field(..., description="Commit subject line of the culprit")
    root_cause: str = Field(..., description="2-4 sentence explanation of the mechanism of the regression")
    evidence: list[EvidenceItem] = Field(
        default_factory=list, description="Experiments that support the conclusion"
    )
    fix_summary: str = Field(..., description="What the fix changes and why it is safe")
    files_changed: list[str] = Field(default_factory=list)
    guard_test: str | None = Field(
        None, description="Path of the regression test added to prevent recurrence"
    )
    pull_request: str | None = Field(None, description="URL or local path of the pull request")
    confidence: Literal["high", "medium", "low"]
    follow_ups: list[str] = Field(
        default_factory=list, description="Recommended follow-up actions for the team"
    )


# ------------------------------------------------------------------------------------------------
# Run bookkeeping
# ------------------------------------------------------------------------------------------------


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"


class PendingInterrupt(BaseModel):
    id: str
    name: str
    reason: Any = None


class RunRecord(BaseModel):
    """Persisted state of one investigation (``runs/<run_id>/run.json``)."""

    run_id: str
    repo_path: str
    metric: str
    status: RunStatus = RunStatus.QUEUED
    created_at: str = Field(default_factory=utcnow_iso)
    updated_at: str = Field(default_factory=utcnow_iso)
    model: str = ""
    task: str = ""
    warnings: list[str] = Field(default_factory=list)
    experiments: list[ExperimentResult] = Field(default_factory=list)
    fix_branch: str | None = None
    fix_worktree: str | None = None
    pending_interrupts: list[PendingInterrupt] = Field(default_factory=list)
    human_responses: list[dict[str, Any]] = Field(default_factory=list)
    report: IncidentReport | None = None
    summary: str | None = None
    error: str | None = None
    pull_request: dict[str, Any] | None = None
    notifications: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class TraceEvent(BaseModel):
    """One line of ``events.jsonl`` — what the UI timeline renders."""

    seq: int
    ts: str = Field(default_factory=utcnow_iso)
    kind: str  # status | thought | tool_start | tool_end | experiment | interrupt | resumed | report | error | metrics
    data: dict[str, Any] = Field(default_factory=dict)
