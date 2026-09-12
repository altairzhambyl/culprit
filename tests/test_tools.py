"""Direct tests of the Strands tools (called as plain functions, outside the agent loop)."""

from pathlib import Path

import pytest

from culprit import gitutil
from culprit.adapters.github import LocalPullRequests
from culprit.adapters.metric_store import JsonMetricStore
from culprit.adapters.slack import LocalNotifier
from culprit.context import InvestigationContext, load_project_config
from culprit.demo import project as demo_project
from culprit.models import RunRecord
from culprit.tools.investigation import InvestigationTools


@pytest.fixture
def ctx(demo_repo, offline_settings, tmp_path: Path) -> InvestigationContext:
    repo = Path(demo_repo["path"])
    project = load_project_config(repo)
    run_dir = tmp_path / "run"
    record = RunRecord(run_id="t-run", repo_path=str(repo), metric=project.metric)
    return InvestigationContext(
        record=record,
        repo=repo,
        run_dir=run_dir,
        project=project,
        settings=offline_settings,
        metric_store=JsonMetricStore(repo / project.metrics_history),
        pr_client=LocalPullRequests(run_dir),
        notifier=LocalNotifier(run_dir),
    )


@pytest.fixture
def tools(ctx: InvestigationContext) -> InvestigationTools:
    yield InvestigationTools(ctx)
    # clean up worktrees created by the test
    if ctx.worktrees_dir.exists():
        for wt in ctx.worktrees_dir.iterdir():
            gitutil.remove_worktree(ctx.repo, wt)
    if ctx.fix_worktree and ctx.fix_worktree.exists():
        gitutil.remove_worktree(ctx.repo, ctx.fix_worktree)
    if ctx.record.fix_branch:
        gitutil.run_git(ctx.repo, "branch", "-D", ctx.record.fix_branch, check=False)
    gitutil.prune_worktrees(ctx.repo)


def test_tool_specs_are_well_formed(tools: InvestigationTools):
    names = [t.tool_name for t in tools.all()]
    assert names == [
        "get_metric_history",
        "list_commits",
        "show_commit",
        "read_file",
        "run_experiment",
        "start_fix",
        "edit_file",
        "write_file",
        "run_tests",
        "open_pull_request",
        "notify",
        "ask_human",
    ]
    spec = tools.run_experiment.tool_spec
    assert "ref" in spec["inputSchema"]["json"]["properties"]
    assert "self" not in spec["inputSchema"]["json"]["properties"]
    assert "tool_context" not in tools.ask_human.tool_spec["inputSchema"]["json"]["properties"]


def test_metric_history_and_candidates(tools: InvestigationTools, demo_repo):
    history = tools.get_metric_history(limit=10)
    reg = history["detected_regression"]
    assert reg and reg["baseline_value"] > reg["regressed_value"]
    commits = tools.list_commits(reg["last_good_commit"], reg["first_bad_commit"])
    assert commits["count"] == 5
    assert commits["commits"][0]["sha"] == demo_repo["commits"][2]["sha"]
    assert commits["commits"][-1]["sha"] == demo_repo["commits"][-1]["sha"]
    assert tools.list_commits("nope", "HEAD")["error"]


def test_show_commit_and_read_file(tools: InvestigationTools, demo_repo):
    info = tools.show_commit(demo_repo["culprit"])
    assert "pd.factorize" in info["diff"] and "churn/features.py" in info["stat"]
    content = tools.read_file("churn/features.py", ref="HEAD")["content"]
    assert "factorize" in content
    assert tools.read_file("missing.py")["error"]


def test_run_experiment_caches_and_detects_regression(tools: InvestigationTools, demo_repo):
    good = tools.run_experiment(demo_repo["commits"][1]["sha"], note="good")
    bad = tools.run_experiment(demo_repo["commits"][-1]["sha"], note="bad")
    assert good["status"] == "ok" and bad["status"] == "ok"
    assert good["value"] - bad["value"] > 0.05
    again = tools.run_experiment(demo_repo["commits"][1]["sha"])
    assert again["cached"] is True
    assert len(tools.ctx.record.experiments) == 2  # cached results are not re-recorded
    assert tools.run_experiment("does-not-exist")["error"]


def test_run_experiment_reports_failures_gracefully(tools: InvestigationTools):
    tools.ctx.project.experiment_command = "python -c 'import sys; sys.exit(3)'"
    result = tools.run_experiment("HEAD")
    assert result["status"] == "failed" and "error" in result


def test_fix_flow_edit_verify_test_pr_notify(tools: InvestigationTools, demo_repo):
    assert tools.edit_file("churn/features.py", "a", "b")["error"]  # must start_fix first
    started = tools.start_fix()
    branch = started["branch"]
    assert started["already_started"] is False and tools.start_fix()["already_started"] is True

    bad_edit = tools.edit_file("churn/features.py", "THIS TEXT DOES NOT EXIST", "x")
    assert bad_edit["error"] == "old_text not found in file"
    edit = tools.edit_file("churn/features.py", demo_project.FIX_OLD_TEXT, demo_project.FIX_NEW_TEXT)
    assert "+CATEGORY_MAPS" in edit["diff"]

    fixed = tools.run_experiment(branch, note="verify")
    baseline = tools.run_experiment(demo_repo["commits"][1]["sha"])
    assert fixed["status"] == "ok" and abs(fixed["value"] - baseline["value"]) < 0.02

    written = tools.write_file("tests/test_encoding_consistency.py", demo_project.GUARD_TEST)
    assert written["created"] is True
    assert tools.read_file("tests/test_encoding_consistency.py", ref=branch)["lines"] > 10
    tests = tools.run_tests()
    assert tests["passed"] is True, tests["output_tail"]

    pr = tools.open_pull_request("fix: stable encoding", "body")
    assert pr["mode"] == "local" and Path(pr["path"]).exists()
    assert gitutil.run_git(tools.ctx.repo, "log", "-1", "--format=%s", branch) == "fix: stable encoding"

    note = tools.notify("#ml-alerts", "done")
    assert note["delivered"] is True
    assert tools.ctx.record.pull_request["mode"] == "local"
    assert (tools.ctx.run_dir / "events.jsonl").exists()
