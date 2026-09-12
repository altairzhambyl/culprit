"""Shared plumbing for building demo repositories (git history + real nightly metrics).

A :class:`Scenario` describes a small ML project as a list of commits (file deltas), which commit is
the culprit, how to evaluate the project, and which commit the nightly job saw on each day. The
builder turns that into a git repository with believable dates and a metric history produced by
*actually* running the project's evaluation at the relevant commits.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from culprit import gitutil

COMMITTER = ("Demo Bot", "demo@culprit.dev")
DEMO_MARKER = "culprit-demo"


@dataclass
class Scenario:
    key: str
    title: str
    default_dest: Path
    metric: str
    metric_keys: tuple[str, ...]
    eval_module: (
        str  # e.g. "churn.train" — run as `python -m <module> --config <nightly_config> --out <file>`
    )
    nightly_config: str
    commits: Callable[[], list[dict]]  # -> [{"message", "author": (name, email), "files": {path: content}}]
    culprit_index: int
    nightly_schedule: list[int]  # commit index seen by the nightly job on days -N .. today (oldest first)
    commit_offsets: list[timedelta]  # commit time relative to the demo's "today" (midnight UTC)
    description: str
    mechanism_keywords: list[str] = field(default_factory=list)  # for *scoring* evaluations only
    culprit_files: list[str] = field(default_factory=list)  # files the culprit touches (scoring only)


def demo_today() -> datetime:
    """Midnight UTC of the demo's "today" (override with CULPRIT_DEMO_TODAY=YYYY-MM-DD for fixed SHAs)."""
    raw = os.getenv("CULPRIT_DEMO_TODAY")
    if raw:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def run_evaluation(repo: Path, sha: str, module: str, config: str, scratch: Path, timeout: int = 300) -> dict:
    """Evaluate the project at ``sha`` in a detached worktree and return its metrics JSON."""
    wt = scratch / sha[:12]
    gitutil.add_worktree(repo, wt, sha)
    out = wt / "metrics.json"
    proc = subprocess.run(
        [sys.executable, "-m", module, "--config", config, "--out", str(out)],
        cwd=str(wt),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if proc.returncode != 0:
        raise RuntimeError(f"demo evaluation failed at {sha[:7]}: {proc.stderr[-2000:]}")
    metrics = json.loads(out.read_text())
    gitutil.remove_worktree(repo, wt)
    return metrics


def build_repository(
    scenario: Scenario,
    dest: Path | str,
    force: bool = False,
    quiet: bool = False,
    drop_latest_nightly: bool = False,
) -> dict:
    """Create the scenario's repository at ``dest`` and return a summary (incl. the culprit for scoring).

    ``drop_latest_nightly`` omits this morning's (bad) nightly record so the regression is not yet
    visible in the metric store — used to demonstrate the automatic trigger: ``culprit record-nightly``
    then produces the bad record and ``culprit watch`` reacts to it.
    """
    dest = Path(dest).resolve()
    if dest.exists():
        if not force:
            raise FileExistsError(f"{dest} already exists (use --force to recreate)")
        if not (dest / ".git" / DEMO_MARKER).exists():
            raise FileExistsError(
                f"refusing to delete {dest}: it is not a Culprit-generated demo repository "
                f"(missing .git/{DEMO_MARKER}). Choose another --dest or remove it yourself."
            )
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    def say(msg: str) -> None:
        if not quiet:
            print(msg)

    commits = scenario.commits()
    if len(scenario.commit_offsets) != len(commits):
        raise ValueError("scenario.commit_offsets must have one entry per commit")

    gitutil.run_git(dest, "init", "-q", "-b", "main")
    gitutil.run_git(dest, "config", "user.name", COMMITTER[0])
    gitutil.run_git(dest, "config", "user.email", COMMITTER[1])
    # Marker (inside .git, never committed) that lets --force safely recreate this directory later.
    (dest / ".git" / DEMO_MARKER).write_text(f"culprit demo scenario={scenario.key}\n")

    today = demo_today()
    shas: list[str] = []
    for i, commit in enumerate(commits):
        _write_files(dest, commit["files"])
        when = (today + scenario.commit_offsets[i]).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        name, email = commit["author"]
        env = {
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": COMMITTER[0],
            "GIT_COMMITTER_EMAIL": COMMITTER[1],
        }
        gitutil.run_git(dest, "add", "-A")
        gitutil.run_git(dest, "commit", "-q", "-m", commit["message"], env=env)
        sha = gitutil.run_git(dest, "rev-parse", "HEAD")
        shas.append(sha)
        say(f"  [{i + 1}/{len(commits)}] {sha[:7]} {commit['message'].splitlines()[0]}")

    say("  evaluating nightly metrics (this trains the model a few times)...")
    scratch = dest.parent / f".culprit-demo-scratch-{scenario.key}"
    scratch.mkdir(exist_ok=True)
    try:
        needed = {shas[i] for i in scenario.nightly_schedule}
        metrics_by_sha = {
            s: run_evaluation(dest, s, scenario.eval_module, scenario.nightly_config, scratch) for s in needed
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        gitutil.prune_worktrees(dest)

    nightly = today + timedelta(hours=2, minutes=15)  # this morning's run saw yesterday's merges
    runs = []
    n = len(scenario.nightly_schedule)
    for day_offset, commit_index in zip(range(n - 1, -1, -1), scenario.nightly_schedule):
        ts = nightly - timedelta(days=day_offset)
        sha = shas[commit_index]
        m = metrics_by_sha[sha]
        runs.append(
            {
                "run_id": f"nightly-{ts.strftime('%Y-%m-%d')}",
                "timestamp": ts.isoformat(timespec="seconds"),
                "commit": sha,
                "branch": "main",
                "config": scenario.nightly_config,
                "metrics": {k: m[k] for k in scenario.metric_keys if k in m},
                "status": "success",
            }
        )

    # The metric store path comes from the scenario's .culprit.yaml.
    import yaml

    project_cfg = yaml.safe_load((dest / ".culprit.yaml").read_text())
    history_path = dest / project_cfg["metrics_history"]
    history_path.parent.mkdir(parents=True, exist_ok=True)
    stored_runs = runs[:-1] if drop_latest_nightly else runs
    history_path.write_text(json.dumps({"project": dest.name, "runs": stored_runs}, indent=2))

    last_good_sha = shas[scenario.nightly_schedule[-2]]
    first_bad_sha = shas[scenario.nightly_schedule[-1]]
    good, bad = metrics_by_sha[last_good_sha][scenario.metric], metrics_by_sha[first_bad_sha][scenario.metric]
    summary = {
        "scenario": scenario.key,
        "title": scenario.title,
        "path": str(dest),
        "metric": scenario.metric,
        "commits": [
            {"sha": s, "short": s[:7], "subject": c["message"].splitlines()[0]} for s, c in zip(shas, commits)
        ],
        "culprit": shas[scenario.culprit_index],
        "culprit_subject": commits[scenario.culprit_index]["message"].splitlines()[0],
        "metrics_history": str(history_path),
        "metric_good": good,
        "metric_bad": bad,
        # Backwards-compatible keys used by the first scenario's docs/tests.
        "f1_good": good if scenario.metric == "f1" else None,
        "f1_bad": bad if scenario.metric == "f1" else None,
    }
    say(f"  nightly {scenario.metric}: {good:.3f} -> {bad:.3f}  (culprit {shas[scenario.culprit_index][:7]})")
    return summary
