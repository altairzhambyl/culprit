"""The generalization evaluator: scores runs against ground truth and re-verifies fixes itself."""

import json
from pathlib import Path

from culprit.evaluation import evaluate_scenario, format_summary
from culprit.settings import Settings

REQUIRED_KEYS = {
    "scenario",
    "run_id",
    "status",
    "expected_culprit",
    "predicted_culprit",
    "culprit_correct",
    "experiments",
    "tool_calls",
    "model_calls",
    "input_tokens",
    "output_tokens",
    "duration_s",
    "root_cause",
    "verified",
    "guard_test_added",
    "mechanism_mentioned",
    "success",
    "agent_reported",
}


def test_evaluator_scores_the_churn_golden_path(offline_settings: Settings, tmp_path: Path):
    result = evaluate_scenario("churn", offline_settings, dest=tmp_path / "churn-model")
    assert REQUIRED_KEYS <= set(result)
    assert result["culprit_correct"] is True and result["success"] is True
    v = result["verified"]
    assert v["metric_recovered"] is True and v["tests_pass_on_fix_branch"] is True
    assert v["culprit_file_touched"] is True and result["guard_test_added"] is True
    assert ".culprit-metrics" not in " ".join(v["files_changed"])  # experiment outputs never leak into the PR
    assert result["experiments"] == 5 and result["tool_calls"] > 10
    saved = json.loads((Path(offline_settings.runs_dir) / result["run_id"] / "evaluation.json").read_text())
    assert saved["success"] is True
    assert "SUCCESS             True" in format_summary(result)


def test_evaluator_is_honest_about_the_offline_policy_on_the_unseen_scenario(
    offline_settings: Settings, tmp_path: Path
):
    """The scripted policy can only bisect: correct culprit, no fix, no success — recorded as such."""
    result = evaluate_scenario("fraud", offline_settings, dest=tmp_path / "fraud-risk")
    assert result["culprit_correct"] is True  # bisection is generic
    assert result["success"] is False  # ...but there is no fix to verify
    v = result["verified"]
    assert v["fix_branch"] is None and v["metric_on_fix_branch"] is None and v["metric_recovered"] is False
    assert result["mechanism_mentioned"] is False
    assert "offline" in (result["root_cause"] or "").lower()
