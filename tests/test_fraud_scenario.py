"""The secondary (fraud-risk) scenario: independent, solvable, invisible to the offline policy."""

import inspect
import json
from pathlib import Path

import pytest

from culprit import gitutil
from culprit.adapters.metric_store import JsonMetricStore, detect_regression
from culprit.demo import fraud_project, scenarios
from culprit.demo.fraud_generator import CULPRIT_INDEX, make_transactions


@pytest.fixture(scope="session")
def fraud_repo(tmp_path_factory: pytest.TempPathFactory) -> dict:
    dest = tmp_path_factory.mktemp("repo") / "fraud-risk"
    return scenarios.generate_scenario("fraud", dest, quiet=True)


def _quick_metric(repo: Path, ref: str, scratch: Path, patch: tuple[str, str] | None = None) -> float:
    from culprit.demo.builder import run_evaluation

    sha = gitutil.resolve_sha(repo, ref)
    if patch is None:
        return run_evaluation(repo, sha, "fraudrisk.evaluate", "quick", scratch)["pr_auc"]
    wt = scratch / "patched"
    gitutil.add_worktree(repo, wt, sha)
    try:
        target = wt / "fraudrisk" / "preprocess.py"
        src = target.read_text()
        assert patch[0] in src
        target.write_text(src.replace(patch[0], patch[1]))
        import subprocess
        import sys

        out = wt / "m.json"
        proc = subprocess.run(
            [sys.executable, "-m", "fraudrisk.evaluate", "--config", "quick", "--out", str(out)],
            cwd=wt,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(out.read_text())["pr_auc"]
    finally:
        gitutil.remove_worktree(repo, wt)


def test_transactions_are_deterministic_and_imbalanced():
    a, b = make_transactions(n=1000, seed=11), make_transactions(n=1000, seed=11)
    assert a.equals(b)
    assert 0.04 < a["is_fraud"].mean() < 0.15


def test_history_window_and_culprit(fraud_repo):
    repo = Path(fraud_repo["path"])
    shas = gitutil.run_git(repo, "log", "--reverse", "--format=%H").splitlines()
    assert len(shas) == 7 and fraud_repo["culprit"] == shas[CULPRIT_INDEX]
    runs = JsonMetricStore(fraud_repo["metrics_history"]).list_runs(branch="main")
    window = detect_regression(runs, "pr_auc", True, 0.05)
    assert window is not None
    assert window.last_good_run.commit == shas[1] and window.first_bad_run.commit == shas[-1]
    assert fraud_repo["metric_good"] - fraud_repo["metric_bad"] > 0.2


def test_existing_tests_do_not_catch_the_bug(fraud_repo, tmp_path: Path):
    """The project's own test suite is green at the broken HEAD — the regression is silent."""
    import subprocess
    import sys

    repo = Path(fraud_repo["path"])
    wt = tmp_path / "head"
    gitutil.add_worktree(repo, wt, "HEAD")
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=wt, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        gitutil.remove_worktree(repo, wt)


def test_bug_is_substantial_and_reference_fix_restores_it(fraud_repo, tmp_path: Path):
    repo = Path(fraud_repo["path"])
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    good = _quick_metric(repo, fraud_repo["commits"][1]["sha"], scratch)
    bad = _quick_metric(repo, "HEAD", scratch)
    fixed = _quick_metric(
        repo, "HEAD", scratch, patch=(fraud_project.REFERENCE_FIX_OLD, fraud_project.REFERENCE_FIX_NEW)
    )
    assert good - bad > 0.15, (good, bad)
    assert abs(fixed - good) < 0.02, (fixed, good)
    gitutil.prune_worktrees(repo)


def test_offline_policy_knows_nothing_about_the_secondary_scenario():
    from culprit.agents import scripted_model

    source = inspect.getsource(scripted_model)
    # The policy may *mention* the evaluation command in its docstring, but it must not contain any
    # knowledge of the secondary repository: its module, files, mechanism or fix.
    for forbidden in (
        "fraudrisk",
        "log1p",
        "fit_transform",
        "preprocess.py",
        "REFERENCE_FIX",
        "fraud_project",
        "fraud_generator",
    ):
        assert forbidden not in source, f"scripted policy must not reference '{forbidden}'"


def test_scenarios_are_independent():
    churn = scenarios.get_scenario("churn")
    fraud = scenarios.get_scenario("fraud")
    churn_files = set(churn.commits()[0]["files"])
    fraud_files = set(fraud.commits()[0]["files"])
    # different code, different data, different config — only the conventional file names overlap
    assert not (churn_files & fraud_files) - {
        "README.md",
        "requirements.txt",
        ".culprit.yaml",
        "tests/__init__.py",
        ".gitignore",
    }
    assert churn.metric != fraud.metric and churn.eval_module != fraud.eval_module
    with pytest.raises(KeyError):
        scenarios.get_scenario("nope")
