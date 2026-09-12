"""Per-investigation context shared by tools, hooks and the service layer."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from culprit.adapters.github import PullRequestClient
from culprit.adapters.metric_store import MetricStore
from culprit.adapters.slack import Notifier
from culprit.models import ProjectConfig, RunRecord, TraceEvent, utcnow_iso
from culprit.settings import Settings

EmitFn = Callable[[str, dict[str, Any]], TraceEvent]


def load_project_config(repo: Path, overrides: dict[str, Any] | None = None) -> ProjectConfig:
    """Read ``.culprit.yaml`` from the repository root (with optional CLI overrides)."""
    cfg_path = repo / ".culprit.yaml"
    data: dict[str, Any] = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text()) or {}
    data.update({k: v for k, v in (overrides or {}).items() if v is not None})
    if "experiment_command" not in data:
        raise ValueError(
            f"{cfg_path} not found or missing 'experiment_command'. Add a .culprit.yaml to the repository "
            "(see README) or pass --experiment-command."
        )
    return ProjectConfig.model_validate(data)


@dataclass
class InvestigationContext:
    """Everything a tool needs to act on one run.

    The context also owns the run's *event log*: tools and hooks call :meth:`emit` and the service/UI
    consume the resulting :class:`TraceEvent` stream (persisted to ``events.jsonl``).
    """

    record: RunRecord
    repo: Path
    run_dir: Path
    project: ProjectConfig
    settings: Settings
    metric_store: MetricStore
    pr_client: PullRequestClient
    notifier: Notifier
    listeners: list[Callable[[TraceEvent], None]] = field(default_factory=list)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        events_path = self.run_dir / "events.jsonl"
        if events_path.exists():
            with events_path.open() as fh:
                self._seq = sum(1 for _ in fh)

    # -- event log ------------------------------------------------------------------------------
    def emit(self, kind: str, data: dict[str, Any] | None = None) -> TraceEvent:
        with self._lock:
            self._seq += 1
            event = TraceEvent(seq=self._seq, kind=kind, data=data or {})
            with (self.run_dir / "events.jsonl").open("a") as fh:
                fh.write(event.model_dump_json() + "\n")
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # listeners must never break the agent
                pass
        return event

    def save(self) -> None:
        """Persist the run record atomically."""
        self.record.updated_at = utcnow_iso()
        tmp = self.run_dir / "run.json.tmp"
        tmp.write_text(self.record.model_dump_json(indent=2))
        # Windows can briefly deny atomic replacement while the dashboard reads the file.
        # Preserve atomic writes; retry only sharing/access errors, with a bounded wait.
        for attempt in range(10):
            try:
                tmp.replace(self.run_dir / "run.json")
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32) or attempt == 9:
                    raise
                time.sleep(0.02 * (attempt + 1))

    # -- paths ------------------------------------------------------------------------------------
    @property
    def worktrees_dir(self) -> Path:
        return self.run_dir / "worktrees"

    @property
    def fix_worktree(self) -> Path | None:
        return Path(self.record.fix_worktree) if self.record.fix_worktree else None

    def metrics_history_path(self) -> Path:
        p = Path(self.project.metrics_history)
        return p if p.is_absolute() else (self.repo / p)

    def trace(self, entry: dict[str, Any]) -> None:
        """Append a raw trace line (model/tool lifecycle) to ``trace.jsonl``."""
        entry = {"ts": utcnow_iso(), **entry}
        with (self.run_dir / "trace.jsonl").open("a") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
