"""The demo repository must be deterministic and contain exactly one silent regression."""

import json
from pathlib import Path

from culprit import gitutil
from culprit.adapters.metric_store import JsonMetricStore, detect_regression
from culprit.demo.generator import CULPRIT_INDEX, make_customers


def test_customers_are_deterministic():
    a, b = make_customers(n=500, seed=7), make_customers(n=500, seed=7)
    assert a.equals(b)
    assert 0.25 < a["churned"].mean() < 0.55


def test_history_has_seven_commits_and_a_culprit(demo_repo):
    repo = Path(demo_repo["path"])
    shas = gitutil.run_git(repo, "log", "--reverse", "--format=%H").splitlines()
    assert len(shas) == 7
    assert demo_repo["culprit"] == shas[CULPRIT_INDEX]
    assert "factorize" in gitutil.show_commit(repo, demo_repo["culprit"])["subject"]
    assert (repo / ".culprit.yaml").exists()


def test_nightly_history_shows_the_regression(demo_repo):
    store = JsonMetricStore(demo_repo["metrics_history"])
    runs = store.list_runs(branch="main")
    assert len(runs) == 6
    window = detect_regression(runs, "f1", higher_is_better=True, threshold=0.03)
    assert window is not None
    assert window.first_bad_run.commit == demo_repo["commits"][-1]["sha"]
    assert window.last_good_run.commit == demo_repo["commits"][1]["sha"]
    assert window.baseline_value - window.regressed_value > 0.1


def test_metrics_history_is_untracked(demo_repo):
    repo = Path(demo_repo["path"])
    assert gitutil.run_git(repo, "status", "--porcelain") == ""
    payload = json.loads(Path(demo_repo["metrics_history"]).read_text())
    assert payload["project"] == "churn-model"
