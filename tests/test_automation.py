"""The autonomous trigger: nightly record → regression → Culprit starts once → human contacted for approval."""

import json
from pathlib import Path

from culprit import gitutil
from culprit.adapters.metric_store import JsonMetricStore
from culprit.automation import check_and_trigger, record_nightly
from culprit.demo.scenarios import generate_scenario
from culprit.models import RunStatus
from culprit.service import RunManager
from culprit.settings import Settings


def test_record_nightly_appends_a_real_measurement(tmp_path: Path):
    repo = Path(
        generate_scenario("churn", tmp_path / "churn-model", quiet=True, drop_latest_nightly=True)["path"]
    )
    store = JsonMetricStore(repo / "nightly" / "metrics_history.json")
    before = store.list_runs()
    run = record_nightly(repo)
    after = store.list_runs()
    assert len(after) == len(before) + 1
    assert run.config == "full" and run.commit == gitutil.resolve_sha(repo, "HEAD")
    assert abs(run.metrics["f1"] - 0.6624) < 0.02  # HEAD is the broken commit
    assert gitutil.run_git(repo, "status", "--porcelain", "--untracked-files=no") == ""  # repo untouched


def test_trigger_flow_starts_once_and_contacts_the_human_only_for_approval(
    tmp_path: Path, offline_settings: Settings
):
    repo = Path(
        generate_scenario("churn", tmp_path / "churn-model", quiet=True, drop_latest_nightly=True)["path"]
    )

    # Before tonight's nightly run there is nothing to do.
    assert check_and_trigger(repo, offline_settings)["status"] == "no_regression"

    record_nightly(repo)
    outcome = check_and_trigger(repo, offline_settings, channel="#ml-alerts", wait=True)
    assert outcome["status"] == "started" and outcome["run_status"] == "awaiting_human"
    assert "culprit resume" in outcome["approval_command"]
    assert outcome["notification"]["delivered"] is True and outcome["notification"]["mode"] == "local"
    run_id = outcome["run_id"]
    note = json.loads(
        (Path(offline_settings.runs_dir) / run_id / "notifications.jsonl").read_text().splitlines()[-1]
    )
    assert "needs your approval" in note["text"] and run_id in note["text"]

    # The same window is never investigated twice.
    again = check_and_trigger(repo, offline_settings)
    assert again["status"] == "already_handled" and again["run_id"] == run_id

    # The human approves later, from another process.
    mgr = RunManager(offline_settings)
    record = mgr.respond(run_id, {"decision": "approve"}, background=False)
    assert record.status == RunStatus.COMPLETED and record.report is not None
    gitutil.run_git(repo, "branch", "-D", record.fix_branch, check=False)


def test_trigger_state_is_per_repository(tmp_path: Path, offline_settings: Settings):
    from culprit.automation import _state_path

    a = _state_path(Path(offline_settings.runs_dir), tmp_path / "a")
    b = _state_path(Path(offline_settings.runs_dir), tmp_path / "b")
    assert a != b and a.parent == b.parent
