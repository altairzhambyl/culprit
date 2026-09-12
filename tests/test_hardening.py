"""Reliability: bad inputs, failing commands, timeouts, loops, dirty trees, preflight, cleanup."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from strands import Agent, tool

from culprit import gitutil
from culprit.adapters.github import LocalPullRequests
from culprit.adapters.metric_store import JsonMetricStore
from culprit.agents.scripted_model import AssistantTurn, ScriptedModel, ToolCall, observed_calls
from culprit.context import InvestigationContext, load_project_config
from culprit.demo.scenarios import generate_scenario
from culprit.hooks.loop_guard import LoopGuardHook
from culprit.models import RunRecord, RunStatus
from culprit.service import RunManager
from culprit.settings import Settings
from culprit.tools.investigation import InvestigationTools


@pytest.fixture
def ctx(demo_repo, offline_settings, tmp_path: Path) -> InvestigationContext:
    from culprit.adapters.slack import LocalNotifier

    repo = Path(demo_repo["path"])
    project = load_project_config(repo)
    run_dir = tmp_path / "run"
    record = RunRecord(run_id="hardening", repo_path=str(repo), metric=project.metric)
    context = InvestigationContext(
        record=record,
        repo=repo,
        run_dir=run_dir,
        project=project,
        settings=offline_settings,
        metric_store=JsonMetricStore(repo / project.metrics_history),
        pr_client=LocalPullRequests(run_dir),
        notifier=LocalNotifier(run_dir),
    )
    yield context
    if context.worktrees_dir.exists():
        for wt in context.worktrees_dir.iterdir():
            gitutil.remove_worktree(repo, wt)
    if context.fix_worktree and context.fix_worktree.exists():
        gitutil.remove_worktree(repo, context.fix_worktree)
    if record.fix_branch:
        gitutil.run_git(repo, "branch", "-D", record.fix_branch, check=False)
    gitutil.prune_worktrees(repo)


# -- experiments ---------------------------------------------------------------------------------------


def test_invalid_refs_are_reported_not_raised(ctx: InvestigationContext):
    tools = InvestigationTools(ctx)
    assert "unknown ref" in tools.run_experiment("no-such-ref")["error"]
    assert "unknown ref" in tools.show_commit("0000000")["error"]
    assert tools.read_file("churn/features.py", ref="deadbeef")["error"]
    assert tools.list_commits("HEAD", "no-such-ref")["error"]


def test_metric_missing_from_output_is_a_clear_failure(ctx: InvestigationContext):
    ctx.project.experiment_command = "python -c \"import json,sys; open(sys.argv[1],'w').write(json.dumps({'other': 1.0, 'flag': True}))\" {out}"
    result = InvestigationTools(ctx).run_experiment("HEAD")
    assert result["status"] == "failed"
    assert "does not contain the tracked metric 'f1'" in result["error"]
    assert "['other']" in result["error"]  # booleans are not metrics


def test_no_metrics_json_is_a_clear_failure_without_retry_storm(ctx: InvestigationContext):
    ctx.project.experiment_command = "echo hello"
    started = time.time()
    result = InvestigationTools(ctx).run_experiment("HEAD")
    assert result["status"] == "failed" and "no metrics JSON" in result["error"]
    assert time.time() - started < 20


def test_timeout_kills_the_process_tree(ctx: InvestigationContext):
    ctx.project.experiment_command = "sh -c 'sleep 30; echo {}'"
    ctx.project.experiment_timeout_s = 1
    ctx.settings.experiment_timeout_s = 1
    started = time.time()
    result = InvestigationTools(ctx).run_experiment("HEAD", note="should time out")
    assert result["status"] == "failed" and "timed out" in result["error"]
    assert time.time() - started < 15  # two attempts of 1 s each, not 30 s of orphaned sleep


def test_stale_half_created_worktree_is_recreated(ctx: InvestigationContext, demo_repo):
    tools = InvestigationTools(ctx)
    sha = demo_repo["commits"][1]["sha"]
    stale = ctx.worktrees_dir / sha[:12]
    stale.mkdir(parents=True)
    (stale / "junk.txt").write_text("left behind by a crash")
    result = tools.run_experiment(sha)
    assert result["status"] == "ok"
    assert not (stale / "junk.txt").exists()


# -- editing and PR guards ---------------------------------------------------------------------------


def test_edit_and_write_guards(ctx: InvestigationContext):
    tools = InvestigationTools(ctx)
    tools.start_fix()
    assert tools.edit_file("churn/features.py", "import pandas as pd", "import pandas as pd")["error"]
    assert tools.write_file(".git/config", "x")["error"]
    assert tools.write_file("../outside.py", "x")["error"]
    assert tools.edit_file("does/not/exist.py", "a", "b")["error"]


def test_pull_request_requires_real_changes(ctx: InvestigationContext):
    tools = InvestigationTools(ctx)
    assert "call start_fix" in tools.open_pull_request("t", "b")["error"]
    tools.start_fix()
    assert "no changes" in tools.open_pull_request("t", "b")["error"]


# -- loop guard ---------------------------------------------------------------------------------------


def test_loop_guard_cancels_repeated_identical_calls(ctx: InvestigationContext):
    calls: list[int] = []

    @tool
    def probe(x: int) -> dict:
        """Probe.

        Args:
            x: value
        """
        calls.append(x)
        return {"x": x}

    def policy(messages, tool_specs):
        seen = observed_calls(messages)
        if len(seen) >= 6:
            return AssistantTurn("done")
        return AssistantTurn("again", [ToolCall("probe", {"x": 1})])

    agent = Agent(
        model=ScriptedModel(policy, max_turns=20),
        tools=[probe],
        hooks=[LoopGuardHook(ctx, max_repeats=3, max_tool_calls=100)],
        callback_handler=None,
    )
    result = agent("go")
    assert result.stop_reason == "end_turn"
    assert len(calls) == 3  # the 4th, 5th and 6th identical calls were cancelled, not executed
    events = ctx.run_dir.joinpath("events.jsonl").read_text()
    assert '"reason":"repeated_call"' in events.replace(" ", "")


def test_loop_guard_enforces_global_tool_cap(ctx: InvestigationContext):
    executed: list[int] = []

    @tool
    def step(i: int) -> dict:
        """Step.

        Args:
            i: index
        """
        executed.append(i)
        return {"i": i}

    def policy(messages, tool_specs):
        n = len(observed_calls(messages))
        if n >= 8:
            return AssistantTurn("finished")
        return AssistantTurn("next", [ToolCall("step", {"i": n})])

    agent = Agent(
        model=ScriptedModel(policy, max_turns=30),
        tools=[step],
        hooks=[LoopGuardHook(ctx, max_repeats=99, max_tool_calls=5)],
        callback_handler=None,
    )
    agent("go")
    assert executed == [0, 1, 2, 3, 4]


# -- run manager --------------------------------------------------------------------------------------


def test_preflight_refuses_bedrock_without_credentials(demo_repo, offline_settings: Settings, monkeypatch):
    for var in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent/credentials")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent/config")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    offline_settings.model_provider = "bedrock"
    offline_settings.aws_region = "us-west-2"
    mgr = RunManager(offline_settings)
    record = mgr.create_run(demo_repo["path"])
    with pytest.raises(RuntimeError, match="AWS credentials"):
        mgr.start(record.run_id, background=False)
    assert mgr.get(record.run_id).status == RunStatus.QUEUED  # nothing started, nothing half-done


def test_unknown_provider_never_falls_back(demo_repo, offline_settings: Settings):
    offline_settings.model_provider = "nonsense"
    mgr = RunManager(offline_settings)
    record = mgr.create_run(demo_repo["path"])
    with pytest.raises(RuntimeError, match="unknown CULPRIT_MODEL_PROVIDER"):
        mgr.start(record.run_id, background=False)


def test_dirty_working_tree_is_reported_and_left_alone(demo_repo, offline_settings: Settings):
    repo = Path(demo_repo["path"])
    readme = repo / "README.md"
    original = readme.read_text()
    readme.write_text(original + "\nlocal uncommitted note\n")
    try:
        mgr = RunManager(offline_settings)
        record = mgr.create_run(repo)
        assert any("uncommitted changes" in w for w in record.warnings)
        offline_settings.auto_approve = True
        record = mgr.start(record.run_id, background=False)
        assert record.status == RunStatus.COMPLETED, record.error
        assert readme.read_text().endswith("local uncommitted note\n")  # the user's edit survived untouched
        gitutil.run_git(repo, "branch", "-D", record.fix_branch, check=False)
    finally:
        readme.write_text(original)
        gitutil.prune_worktrees(repo)


def test_demo_force_refuses_to_delete_non_demo_directories(tmp_path: Path):
    victim = tmp_path / "my-real-project"
    victim.mkdir()
    (victim / "important.txt").write_text("do not delete")
    with pytest.raises(FileExistsError, match="not a Culprit-generated demo"):
        generate_scenario("churn", victim, force=True, quiet=True)
    assert (victim / "important.txt").exists()


def test_demo_force_recreates_generated_repositories(tmp_path: Path):
    dest = tmp_path / "churn-model"
    first = generate_scenario("churn", dest, quiet=True)
    second = generate_scenario("churn", dest, force=True, quiet=True)
    assert first["culprit"] == second["culprit"]
