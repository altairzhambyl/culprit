"""Minimal autonomous trigger: nightly evaluation → regression detected → Culprit starts itself.

Two small pieces, both driven by the repository's own ``.culprit.yaml``:

* :func:`record_nightly` — what a nightly job does: evaluate the current ``HEAD`` with the project's
  full config and append the result to the metric store.
* :func:`check_and_trigger` — what ``culprit watch`` does: read the metric store, detect a regression
  above the configured threshold, and — if that good→bad window has not been investigated yet — start
  an investigation. A human is contacted (notification) only when the run pauses for approval.

No monitoring platform: a JSON state file per repository remembers which windows were handled, so a
cron job / scheduled workflow can call ``culprit watch --once`` every night idempotently.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from culprit import gitutil
from culprit.adapters.metric_store import JsonMetricStore, detect_regression
from culprit.adapters.slack import select_notifier
from culprit.context import load_project_config
from culprit.models import MetricRun, RunStatus
from culprit.service import RunManager
from culprit.settings import Settings
from culprit.tools.investigation import render_command


def _metric_store(repo: Path, project) -> JsonMetricStore:
    path = Path(project.metrics_history)
    return JsonMetricStore(path if path.is_absolute() else repo / path)


def record_nightly(repo: Path | str, config: str | None = None, run_id: str | None = None) -> MetricRun:
    """Evaluate HEAD with ``config`` (default: the project's nightly/full config) and append it to the store."""
    repo = Path(repo).resolve()
    project = load_project_config(repo)
    config = config or project.nightly_config or project.default_config
    sha = gitutil.resolve_sha(repo, "HEAD")
    branch = gitutil.current_branch(repo)
    with tempfile.TemporaryDirectory(prefix="culprit-nightly-") as tmp:
        wt = Path(tmp) / "wt"
        gitutil.add_worktree(repo, wt, sha)
        try:
            out = Path(tmp) / "metrics.json"
            command = render_command(
                project.experiment_command, config=shlex.quote(config), out=shlex.quote(str(out))
            )
            shim = Path(tmp) / "bin"
            shim.mkdir()
            (shim / "python").symlink_to(sys.executable)
            import os

            env = {**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '')}"}
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(wt),
                capture_output=True,
                text=True,
                timeout=project.experiment_timeout_s,
                env=env,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"nightly evaluation failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-1500:]}"
                )
            if not out.exists():
                raise RuntimeError("nightly evaluation produced no metrics JSON")
            data = json.loads(out.read_text())
        finally:
            gitutil.remove_worktree(repo, wt)
            gitutil.prune_worktrees(repo)
    metrics = {
        k: float(v) for k, v in data.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    if project.metric not in metrics:
        raise RuntimeError(f"metric '{project.metric}' missing from nightly output keys {sorted(metrics)}")
    now = datetime.now(timezone.utc)
    run = MetricRun(
        run_id=run_id or f"nightly-{now.strftime('%Y-%m-%d')}-{uuid.uuid4().hex[:4]}",
        timestamp=now.isoformat(timespec="seconds"),
        commit=sha,
        branch=branch if branch != "HEAD" else project.main_branch,
        config=config,
        metrics=metrics,
        status="success",
    )
    _metric_store(repo, project).append(run)
    return run


def _state_path(runs_dir: Path, repo: Path) -> Path:
    digest = hashlib.sha1(str(repo).encode()).hexdigest()[:12]
    return runs_dir / "_watch" / f"{digest}.json"


def _load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {"handled": {}}


def check_and_trigger(
    repo: Path | str,
    settings: Settings,
    channel: str = "#ml-alerts",
    wait: bool = True,
    dashboard_url: str | None = None,
) -> dict[str, Any]:
    """Detect a regression in the metric store and start an investigation for it once.

    Returns a dict with ``status``: ``no_regression`` | ``already_handled`` | ``started`` plus, when
    ``wait`` is true, the run's final status (``awaiting_human`` / ``completed`` / ``failed``).
    """
    repo = Path(repo).resolve()
    project = load_project_config(repo)
    runs = _metric_store(repo, project).list_runs(branch=project.main_branch)
    window = detect_regression(runs, project.metric, project.higher_is_better, project.regression_threshold)
    if window is None:
        return {"status": "no_regression", "metric": project.metric, "runs_seen": len(runs)}

    mgr = RunManager(settings)
    state_path = _state_path(mgr.runs_dir, repo)
    state = _load_state(state_path)
    key = f"{window.last_good_run.commit[:12]}..{window.first_bad_run.commit[:12]}"
    if key in state["handled"]:
        return {"status": "already_handled", "window": key, "run_id": state["handled"][key]}

    task = (
        f"Automatic trigger: nightly run {window.first_bad_run.run_id} reported {project.metric}="
        f"{window.regressed_value} vs {window.baseline_value} in {window.last_good_run.run_id} "
        f"(threshold {project.regression_threshold})."
    )
    record = mgr.create_run(repo, task=task)
    state["handled"][key] = record.run_id
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))
    mgr.start(record.run_id, background=True)
    result: dict[str, Any] = {
        "status": "started",
        "window": key,
        "run_id": record.run_id,
        "metric": project.metric,
        "baseline_value": window.baseline_value,
        "regressed_value": window.regressed_value,
    }
    if not wait:
        return result

    record = mgr.wait(record.run_id)
    result["run_status"] = record.status.value
    if record.status == RunStatus.AWAITING_HUMAN:
        # The only moment a human is involved: approval of the pull request.
        summary = ""
        for intr in record.pending_interrupts:
            reason = intr.reason if isinstance(intr.reason, dict) else {}
            summary = reason.get("summary") or reason.get("question") or intr.name
        how = f"culprit resume {record.run_id} --approve   (or --reject --comment '...')"
        if dashboard_url:
            how += f"\nor open {dashboard_url}/?run={record.run_id}"
        message = (
            f"Culprit investigated the {project.metric} regression ({window.baseline_value} -> {window.regressed_value}) "
            f"in {repo.name} and needs your approval: {summary}\n{how}"
        )
        notifier = select_notifier(mgr.runs_dir / record.run_id, settings.slack_webhook_url)
        try:
            result["notification"] = notifier.send(channel, message)
        except Exception as exc:  # the run is already safely paused; notification failure is not fatal
            result["notification"] = {"delivered": False, "error": str(exc)}
        result["approval_command"] = how
    elif record.status == RunStatus.FAILED:
        result["error"] = record.error
    return result


def watch_forever(
    repo: Path | str, settings: Settings, interval_s: int, channel: str, dashboard_url: str | None
) -> None:
    """Poll the metric store every ``interval_s`` seconds (a tiny daemon for machines without cron)."""
    while True:
        outcome = check_and_trigger(repo, settings, channel=channel, wait=True, dashboard_url=dashboard_url)
        print(
            json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **outcome}),
            flush=True,
        )
        time.sleep(interval_s)
