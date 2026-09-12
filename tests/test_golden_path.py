"""End-to-end: the whole investigation through the Strands agent loop with the offline model.

Covers: tool loop, budget/trace hooks, the approval interrupt, resuming from a *new* RunManager
(simulating a fresh process), structured IncidentReport output, and the rejection path.
"""

from pathlib import Path

import pytest

from culprit import gitutil
from culprit.models import RunStatus
from culprit.service import RunManager
from culprit.settings import Settings


@pytest.fixture
def cleanup_branches(demo_repo):
    yield
    repo = Path(demo_repo["path"])
    gitutil.prune_worktrees(repo)
    for line in gitutil.run_git(repo, "branch", "--list", "culprit/*").splitlines():
        gitutil.run_git(repo, "branch", "-D", line.strip().lstrip("+ "), check=False)


def test_golden_path_with_approval_across_processes(demo_repo, offline_settings: Settings, cleanup_branches):
    mgr = RunManager(offline_settings)
    record = mgr.create_run(demo_repo["path"], task="Five PRs merged yesterday.")
    assert record.status == RunStatus.QUEUED

    record = mgr.start(record.run_id, background=False)
    assert record.status == RunStatus.AWAITING_HUMAN
    assert (
        record.pending_interrupts
        and record.pending_interrupts[0].name == "culprit-approval:open_pull_request"
    )
    reason = record.pending_interrupts[0].reason
    assert reason["tool"] == "open_pull_request" and "Root cause" in reason["input"]["body"]
    # bisection: 2 calibration + 2 bisect + 1 verification experiments
    assert len(record.experiments) == 5
    assert record.fix_branch and record.fix_branch.startswith("culprit/fix-")

    # A brand-new manager (fresh process) resumes from the persisted Strands session.
    mgr2 = RunManager(Settings())
    record = mgr2.respond(record.run_id, {"decision": "approve", "comment": "LGTM"}, background=False)
    assert record.status == RunStatus.COMPLETED, record.error
    report = record.report
    assert report is not None
    assert report.culprit_commit == demo_repo["culprit"][:7]
    assert report.recovered_value is not None and report.recovered_value > report.regressed_value
    assert report.guard_test == "tests/test_encoding_consistency.py"
    assert report.pull_request and report.pull_request.startswith("file://")
    assert {e.verdict for e in report.evidence} == {"good", "bad", "fixed"}

    run_dir = Path(offline_settings.runs_dir) / record.run_id
    assert (run_dir / "report.md").exists() and (run_dir / "pull_request.md").exists()
    assert (run_dir / "notifications.jsonl").exists() and (run_dir / "trace.jsonl").exists()
    assert record.human_responses == [{"tool": "open_pull_request", "decision": "approve", "comment": "LGTM"}]
    kinds = [e.kind for e in mgr2.events(record.run_id)]
    assert "interrupt" in kinds and "decision" in kinds and "report" in kinds
    # experiment worktrees are cleaned up; the fix branch + commit remain in the repo
    assert not (run_dir / "worktrees").exists()
    repo = Path(demo_repo["path"])
    assert gitutil.run_git(repo, "log", "-1", "--format=%s", record.fix_branch).startswith("fix(features)")
    assert "test_encoding_consistency" in gitutil.run_git(
        repo, "ls-tree", "-r", "--name-only", record.fix_branch
    )


def test_rejection_leaves_branch_and_reports_no_pr(demo_repo, offline_settings: Settings, cleanup_branches):
    mgr = RunManager(offline_settings)
    record = mgr.create_run(demo_repo["path"])
    record = mgr.run_to_completion(
        record.run_id, decide=lambda pending: {"decision": "reject", "comment": "not now"}
    )
    assert record.status == RunStatus.COMPLETED, record.error
    assert record.pull_request is None
    assert record.report is not None and record.report.pull_request is None
    assert record.human_responses[0]["decision"] == "reject"
    assert (
        "not opened" in (record.summary or "").lower() or "human decision" in (record.summary or "").lower()
    )


def test_auto_approve_needs_no_human(demo_repo, offline_settings: Settings, cleanup_branches):
    offline_settings.auto_approve = True
    mgr = RunManager(offline_settings)
    record = mgr.create_run(demo_repo["path"])
    record = mgr.start(record.run_id, background=False)
    assert record.status == RunStatus.COMPLETED, record.error
    assert record.pull_request and record.pull_request["mode"] == "local"
    kinds = [e.kind for e in mgr.events(record.run_id)]
    assert "auto_approved" in kinds and "interrupt" not in kinds


def test_experiment_budget_is_enforced(demo_repo, offline_settings: Settings, cleanup_branches):
    offline_settings.max_experiments = 1
    mgr = RunManager(offline_settings)
    record = mgr.create_run(demo_repo["path"])
    record = mgr.start(record.run_id, background=False)
    # The budget hook cancels the second experiment; the scripted policy stops gracefully and the
    # run still finishes (the report phase runs on whatever evidence exists).
    assert record.status in (RunStatus.COMPLETED, RunStatus.FAILED)
    kinds = [e.kind for e in mgr.events(record.run_id)]
    assert "budget_exhausted" in kinds
    assert len([e for e in record.experiments if not e.cached]) == 1


def test_relative_runs_dir_works_from_another_cwd(
    demo_repo, offline_settings: Settings, cleanup_branches, tmp_path, monkeypatch
):
    """Regression: a relative CULPRIT_RUNS_DIR must not be resolved against the target repository by git."""
    monkeypatch.chdir(tmp_path)
    offline_settings.runs_dir = Path("relative-runs")
    offline_settings.auto_approve = True
    mgr = RunManager(offline_settings)
    record = mgr.create_run(demo_repo["path"])
    record = mgr.start(record.run_id, background=False)
    assert record.status == RunStatus.COMPLETED, record.error
    assert len(record.experiments) == 5
    assert (tmp_path / "relative-runs" / record.run_id / "report.md").exists()
    assert not (Path(demo_repo["path"]) / "relative-runs").exists()
