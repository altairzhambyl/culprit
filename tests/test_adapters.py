"""Metric store, regression detection, and the local PR / notification adapters."""

from pathlib import Path

import pytest

from culprit import gitutil
from culprit.adapters.github import LocalPullRequests, parse_github_repo, select_pull_request_client
from culprit.adapters.metric_store import JsonMetricStore, detect_regression
from culprit.adapters.slack import LocalNotifier, select_notifier
from culprit.models import MetricRun


def _run(i: int, value: float, commit: str = "abc") -> MetricRun:
    return MetricRun(
        run_id=f"r{i}", timestamp=f"2026-09-{i:02d}T02:00:00+00:00", commit=commit * 10, metrics={"f1": value}
    )


def test_detect_regression_finds_latest_drop():
    runs = [
        _run(1, 0.80, "a"),
        _run(2, 0.81, "b"),
        _run(3, 0.70, "c"),
        _run(4, 0.71, "d"),
        _run(5, 0.60, "e"),
    ]
    window = detect_regression(runs, "f1", True, 0.05)
    assert window is not None
    assert window.last_good_run.run_id == "r4" and window.first_bad_run.run_id == "r5"


def test_detect_regression_respects_direction_and_threshold():
    runs = [_run(1, 0.80), _run(2, 0.79)]
    assert detect_regression(runs, "f1", True, 0.03) is None
    loss_runs = [_run(1, 0.20), _run(2, 0.35)]
    assert detect_regression(loss_runs, "f1", higher_is_better=False, threshold=0.1) is not None


def test_json_metric_store_roundtrip(tmp_path: Path):
    store = JsonMetricStore(tmp_path / "m.json")
    with pytest.raises(FileNotFoundError):
        store.list_runs()
    store.append(_run(2, 0.5))
    store.append(_run(1, 0.6))
    assert [r.run_id for r in store.list_runs()] == ["r1", "r2"]


def test_parse_github_repo():
    assert parse_github_repo("git@github.com:acme/churn-model.git") == "acme/churn-model"
    assert parse_github_repo("https://github.com/acme/churn-model") == "acme/churn-model"
    assert parse_github_repo("https://gitlab.com/acme/x.git") is None
    assert parse_github_repo(None) is None


def test_select_clients_fall_back_to_local(tmp_path: Path, demo_repo):
    client = select_pull_request_client(
        Path(demo_repo["path"]), tmp_path, token=None, repo_slug=None, api_url=""
    )
    assert isinstance(client, LocalPullRequests)
    client = select_pull_request_client(
        Path(demo_repo["path"]), tmp_path, token="x", repo_slug=None, api_url=""
    )
    assert isinstance(client, LocalPullRequests)  # token without a GitHub remote -> local
    assert isinstance(select_notifier(tmp_path, None), LocalNotifier)


def test_local_pull_request_writes_reviewable_markdown(tmp_path: Path, demo_repo):
    repo = Path(demo_repo["path"])
    wt = tmp_path / "wt"
    gitutil.add_worktree(repo, wt, "main", new_branch="test/local-pr")
    (wt / "NOTE.md").write_text("hello\n")
    gitutil.commit_all(wt, "add note")
    result = LocalPullRequests(tmp_path).open_pull_request(
        repo, "test/local-pr", "main", "Add note", "body text"
    )
    assert result["mode"] == "local"
    text = Path(result["path"]).read_text()
    assert "# Add note" in text and "+hello" in text
    gitutil.remove_worktree(repo, wt)


def test_local_notifier_appends(tmp_path: Path):
    n = LocalNotifier(tmp_path)
    n.send("#ml-alerts", "one")
    n.send("#ml-alerts", "two")
    assert (tmp_path / "notifications.jsonl").read_text().count("\n") == 2
