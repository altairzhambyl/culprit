"""Generalization evaluation: run Culprit with a *real* model on a scenario and score the result.

The evaluator never tells the model anything about the expected answer. It creates the scenario
repository, starts an ordinary investigation (the same code path the CLI and dashboard use), plays the
human by approving the pull request when asked, and only *afterwards* compares the run against the
scenario's ground truth — re-measuring the fix branch and re-running the project's tests itself rather
than trusting the agent's claims. Results land in ``runs/<run_id>/evaluation.json``.
"""

from __future__ import annotations

import json
import re
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from culprit import gitutil
from culprit.demo.scenarios import generate_scenario, get_scenario
from culprit.models import RunRecord, RunStatus
from culprit.service import RunManager
from culprit.settings import Settings
from culprit.tools.investigation import quote_arg, render_command

_PYTHON_SHIM_DIR: Path | None = None


def _python_shim() -> Path:
    """Ensure ``python`` resolves to the current interpreter for the project's commands."""
    global _PYTHON_SHIM_DIR
    if os.name == "nt":
        return Path(sys.executable).parent
    if _PYTHON_SHIM_DIR is None:
        shim = Path(sys.prefix) / "culprit-shim"
        try:
            shim.mkdir(exist_ok=True)
            link = shim / "python"
            if not link.exists():
                link.symlink_to(sys.executable)
        except OSError:
            shim = Path(sys.executable).parent
        _PYTHON_SHIM_DIR = shim
    return _PYTHON_SHIM_DIR


def _run_in(repo: Path, command: str, timeout: int = 600) -> subprocess.CompletedProcess:
    import os

    env = {
        **os.environ,
        "PATH": f"{_python_shim()}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        command, shell=True, cwd=str(repo), capture_output=True, text=True, timeout=timeout, env=env
    )


def _measure(repo: Path, ref: str, project: dict[str, Any], scratch: Path) -> float | None:
    """Independently run the project's fast experiment at ``ref`` and return the tracked metric."""
    sha = gitutil.resolve_sha(repo, ref)
    wt = scratch / f"eval-{sha[:12]}"
    gitutil.add_worktree(repo, wt, sha)
    try:
        out = wt / ".culprit-eval-metrics.json"
        cmd = render_command(
            project["experiment_command"],
            config=quote_arg(project["default_config"]),
            out=quote_arg(str(out)),
        )
        proc = _run_in(wt, cmd, timeout=int(project.get("experiment_timeout_s", 600)))
        if proc.returncode != 0 or not out.exists():
            return None
        return json.loads(out.read_text()).get(project["metric"])
    finally:
        gitutil.remove_worktree(repo, wt)


def _tests_pass(repo: Path, ref: str, project: dict[str, Any], scratch: Path) -> bool | None:
    if not project.get("test_command"):
        return None
    sha = gitutil.resolve_sha(repo, ref)
    wt = scratch / f"tests-{sha[:12]}"
    gitutil.add_worktree(repo, wt, sha)
    try:
        return _run_in(wt, project["test_command"], timeout=900).returncode == 0
    finally:
        gitutil.remove_worktree(repo, wt)


def _new_test_files(repo: Path, base: str, branch: str) -> list[str]:
    try:
        names = gitutil.run_git(
            repo, "diff", "--name-only", "--diff-filter=A", f"{base}...{branch}"
        ).splitlines()
    except gitutil.GitError:
        return []
    return [n for n in names if re.search(r"(^|/)tests?/|test_.*\.py$|_test\.py$", n)]


def _changed_files(repo: Path, base: str, branch: str) -> list[str]:
    try:
        return gitutil.run_git(repo, "diff", "--name-only", f"{base}...{branch}").splitlines()
    except gitutil.GitError:
        return []


def evaluate_scenario(
    scenario_key: str,
    settings: Settings,
    dest: Path | str | None = None,
    regenerate: bool = True,
    task: str = "",
) -> dict[str, Any]:
    """Run a full investigation on a scenario with the configured model and score it against ground truth."""
    scenario = get_scenario(scenario_key)
    dest = Path(dest or scenario.default_dest)
    if regenerate or not (dest / ".git").exists():
        truth = generate_scenario(scenario_key, dest, force=True, quiet=True)
    else:
        raise ValueError("evaluation requires a freshly generated repository (use regenerate=True)")

    mgr = RunManager(settings)
    started = time.time()
    record = mgr.create_run(dest, task=task)
    run_id = record.run_id
    approvals: list[dict[str, Any]] = []

    def decide(pending) -> dict[str, Any]:
        approvals.append({"interrupts": [p.model_dump() for p in pending]})
        return {"decision": "approve", "comment": "approved by the evaluation harness"}

    record = mgr.run_to_completion(run_id, decide=decide)
    duration = round(time.time() - started, 1)
    result = score_run(record, truth, scenario_key, mgr.runs_dir / run_id)
    result.update(
        {
            "duration_s": duration,
            "approvals_requested": len(approvals),
            "model": record.model,
            "task": task,
        }
    )
    (mgr.runs_dir / run_id / "evaluation.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def score_run(record: RunRecord, truth: dict[str, Any], scenario_key: str, run_dir: Path) -> dict[str, Any]:
    """Compare a finished run with the scenario's ground truth, re-measuring the fix independently."""
    scenario = get_scenario(scenario_key)
    repo = Path(record.repo_path)
    project = json.loads((run_dir / "project.json").read_text())
    report = record.report
    predicted = report.culprit_commit if report else None
    culprit_correct = bool(predicted) and truth["culprit"].startswith(predicted.strip())

    result: dict[str, Any] = {
        "scenario": scenario_key,
        "scenario_title": scenario.title,
        "run_id": record.run_id,
        "status": record.status.value,
        "error": record.error,
        "metric": record.metric,
        "expected_culprit": truth["culprit"][:7],
        "expected_culprit_subject": truth["culprit_subject"],
        "predicted_culprit": predicted,
        "culprit_correct": culprit_correct,
        "experiments": len([e for e in record.experiments if not e.cached]),
        "experiments_detail": [
            {
                "ref": e.ref if e.ref == record.fix_branch else e.sha[:7],
                "config": e.config,
                "value": e.metrics.get(record.metric),
                "status": e.status,
            }
            for e in record.experiments
        ],
        "tool_calls": record.tool_calls,
        "model_calls": record.model_calls,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "root_cause": report.root_cause if report else None,
        "confidence": report.confidence if report else None,
        "agent_reported": {
            "baseline_value": report.baseline_value if report else None,
            "regressed_value": report.regressed_value if report else None,
            "recovered_value": report.recovered_value if report else None,
            "files_changed": report.files_changed if report else [],
            "guard_test": report.guard_test if report else None,
            "pull_request": report.pull_request if report else None,
        },
        "nightly_good": truth["metric_good"],
        "nightly_bad": truth["metric_bad"],
    }

    # Soft check: does the explanation mention the mechanism?
    text = (report.root_cause + " " + report.fix_summary).lower() if report else ""
    result["mechanism_keywords_found"] = [k for k in scenario.mechanism_keywords if k.lower() in text]
    result["mechanism_mentioned"] = bool(result["mechanism_keywords_found"])

    # Independent verification of the fix branch (never trust the agent's own numbers).
    fix_branch = record.fix_branch
    base = project.get("main_branch", "main")
    scratch = run_dir / "evaluation-scratch"
    scratch.mkdir(exist_ok=True)
    verified: dict[str, Any] = {
        "fix_branch": fix_branch,
        "files_changed": [],
        "new_test_files": [],
        "culprit_file_touched": False,
        "metric_on_fix_branch": None,
        "metric_at_last_good": None,
        "metric_at_first_bad": None,
        "metric_recovered": False,
        "tests_pass_on_fix_branch": None,
    }
    try:
        if fix_branch:
            try:
                gitutil.resolve_sha(repo, fix_branch)
                verified["files_changed"] = _changed_files(repo, base, fix_branch)
                verified["new_test_files"] = _new_test_files(repo, base, fix_branch)
                verified["culprit_file_touched"] = any(
                    f in verified["files_changed"] for f in scenario.culprit_files
                )
                verified["metric_on_fix_branch"] = _measure(repo, fix_branch, project, scratch)
                verified["tests_pass_on_fix_branch"] = _tests_pass(repo, fix_branch, project, scratch)
            except gitutil.GitError:
                pass
        # Calibration values under the fast config (these are what the agent's fix must match).
        good_sha, bad_sha = (
            truth["commits"][scenario.nightly_schedule[-2]]["sha"],
            truth["commits"][-1]["sha"],
        )
        verified["metric_at_last_good"] = _measure(repo, good_sha, project, scratch)
        verified["metric_at_first_bad"] = _measure(repo, bad_sha, project, scratch)
        fix_v, good_v, bad_v = (
            verified["metric_on_fix_branch"],
            verified["metric_at_last_good"],
            verified["metric_at_first_bad"],
        )
        if None not in (fix_v, good_v, bad_v):
            verified["metric_recovered"] = abs(fix_v - good_v) < abs(fix_v - bad_v)
    finally:
        import shutil

        shutil.rmtree(scratch, ignore_errors=True)
        gitutil.prune_worktrees(repo)

    # "Proves it": the culprit must have been *measured*, not guessed — an experiment at the culprit
    # commit and one at its parent (or the good boundary) must both exist in the run.
    measured = {e.sha[:12] for e in record.experiments if e.status == "ok" and "+wip" not in e.sha}
    culprit_sha = truth["culprit"]
    idx = next((i for i, c in enumerate(truth["commits"]) if c["sha"] == culprit_sha), None)
    parent_sha = truth["commits"][idx - 1]["sha"] if idx else None
    verified["culprit_measured"] = culprit_sha[:12] in measured
    verified["parent_measured"] = bool(parent_sha) and parent_sha[:12] in measured
    verified["culprit_bracketed_by_experiments"] = (
        verified["culprit_measured"] and verified["parent_measured"]
    )

    result["verified"] = verified
    result["guard_test_added"] = bool(verified["new_test_files"])
    result["success"] = bool(
        record.status == RunStatus.COMPLETED
        and culprit_correct
        and verified["culprit_bracketed_by_experiments"]
        and verified["metric_recovered"]
        and result["guard_test_added"]
        and (verified["tests_pass_on_fix_branch"] in (True, None))
    )
    return result


def format_summary(result: dict[str, Any]) -> str:
    v = result.get("verified", {})
    lines = [
        f"scenario            {result['scenario']} — {result.get('scenario_title', '')}",
        f"run                 {result['run_id']}  ({result['status']}, model {result.get('model', '?')})",
        f"culprit             expected {result['expected_culprit']}  predicted {result['predicted_culprit']}  -> {'CORRECT' if result['culprit_correct'] else 'WRONG'}",
        f"experiments         {result['experiments']}   tool calls {result['tool_calls']}   model calls {result['model_calls']}",
        f"tokens              {result['input_tokens']} in / {result['output_tokens']} out   duration {result.get('duration_s', '?')}s",
        f"{result['metric']:<19} nightly {result['nightly_good']} -> {result['nightly_bad']}; fast config good {v.get('metric_at_last_good')} / bad {v.get('metric_at_first_bad')} / fix branch {v.get('metric_on_fix_branch')}",
        f"culprit measured    culprit {v.get('culprit_measured')} / parent {v.get('parent_measured')}  (bracketed by experiments: {v.get('culprit_bracketed_by_experiments')})",
        f"fix verified        metric recovered: {v.get('metric_recovered')}   tests pass: {v.get('tests_pass_on_fix_branch')}   files: {', '.join(v.get('files_changed', [])) or '-'}",
        f"guard test added    {result.get('guard_test_added')}  {', '.join(v.get('new_test_files', []))}",
        f"mechanism mentioned {result.get('mechanism_mentioned')}  {result.get('mechanism_keywords_found')}",
        f"root cause          {(result.get('root_cause') or '').strip()[:300]}",
        f"SUCCESS             {result.get('success')}",
    ]
    return "\n".join(lines)
