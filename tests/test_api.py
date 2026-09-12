"""Web API: start a run, stream/poll events, approve, read the report."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from culprit import gitutil


@pytest.fixture
def client(demo_repo, offline_settings, monkeypatch):
    from culprit.service import RunManager
    from culprit.web import app as web

    monkeypatch.setattr(web, "manager", RunManager(offline_settings))
    with TestClient(web.app) as c:
        yield c
    repo = Path(demo_repo["path"])
    gitutil.prune_worktrees(repo)
    for line in gitutil.run_git(repo, "branch", "--list", "culprit/*").splitlines():
        gitutil.run_git(repo, "branch", "-D", line.strip().lstrip("+ "), check=False)


def _wait_for(client: TestClient, run_id: str, statuses: set[str], timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if data["status"] in statuses:
            return data
        time.sleep(0.5)
    raise AssertionError(
        f"run {run_id} did not reach {statuses}; last status {data['status']} ({data.get('error')})"
    )


def test_health_config_and_index(client: TestClient):
    assert client.get("/api/health").json()["status"] == "ok"
    cfg = client.get("/api/config").json()
    assert cfg["provider"] == "scripted" and cfg["max_experiments"] >= 1
    assert "Culprit" in client.get("/").text


def test_full_run_through_the_api(client: TestClient, demo_repo):
    resp = client.post("/api/runs", json={"repo": demo_repo["path"], "task": "nightly F1 dropped"})
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["run_id"]

    data = _wait_for(client, run_id, {"awaiting_human", "failed"})
    assert data["status"] == "awaiting_human", data.get("error")
    assert client.post(f"/api/runs/{run_id}/respond", json={}).status_code == 400

    events = client.get(f"/api/runs/{run_id}/events").json()
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "status" and "candidates" in kinds and "interrupt" in kinds
    assert client.get(f"/api/runs/{run_id}/report.md").status_code == 404

    resp = client.post(f"/api/runs/{run_id}/respond", json={"decision": "approve", "comment": "ship"})
    assert resp.status_code == 200
    assert client.post(f"/api/runs/{run_id}/respond", json={"decision": "approve"}).status_code == 409

    data = _wait_for(client, run_id, {"completed", "failed"})
    assert data["status"] == "completed", data.get("error")
    assert data["report"]["culprit_commit"] == demo_repo["culprit"][:7]
    assert "Root cause" in client.get(f"/api/runs/{run_id}/report.md").text
    assert client.get(f"/api/runs/{run_id}/pull_request.md").status_code == 200
    runs = client.get("/api/runs").json()
    assert runs[0]["run_id"] == run_id and runs[0]["culprit"] == demo_repo["culprit"][:7]
    trace = client.get(f"/api/runs/{run_id}/trace").json()
    assert any(t["event"] == "tool_start" for t in trace)


def test_bad_repo_is_rejected(client: TestClient, tmp_path: Path):
    assert client.post("/api/runs", json={"repo": str(tmp_path / "missing")}).status_code == 400
    assert client.get("/api/runs/nope").status_code == 404


def test_api_token_and_demo_only_mode(demo_repo, offline_settings, monkeypatch, tmp_path):
    from culprit.service import RunManager
    from culprit.web import app as web

    offline_settings.api_token = "s3cret"
    offline_settings.demo_only = True
    monkeypatch.setattr(web, "manager", RunManager(offline_settings))
    monkeypatch.chdir(tmp_path)  # DEMO_REPO resolves relative to cwd; nothing exists here
    with TestClient(web.app) as c:
        assert c.get("/api/config").json()["auth_required"] is True
        assert c.post("/api/runs", json={"repo": demo_repo["path"]}).status_code == 401
        headers = {"Authorization": "Bearer s3cret"}
        # authenticated, but not the demo repository -> refused in demo-only mode
        assert c.post("/api/runs", json={"repo": demo_repo["path"]}, headers=headers).status_code == 403
        assert c.post("/api/runs/nope/respond", json={"decision": "approve"}).status_code == 401


def test_stale_running_runs_are_marked_failed_on_restart(demo_repo, offline_settings, tmp_path):
    """A run left 'running' by a dead process must not hang subscribers after a restart."""
    from culprit.models import RunStatus
    from culprit.service import RunManager

    mgr = RunManager(offline_settings)
    record = mgr.create_run(demo_repo["path"])
    record.status = RunStatus.RUNNING
    (mgr.runs_dir / record.run_id / "run.json").write_text(record.model_dump_json(indent=2))
    fresh = RunManager(offline_settings)  # simulates a process restart
    assert record.run_id in fresh.recover_stale_runs() or fresh.get(record.run_id).status == RunStatus.FAILED
    assert fresh.get(record.run_id).status == RunStatus.FAILED
    assert list(fresh.subscribe(record.run_id)) == []  # terminates immediately
