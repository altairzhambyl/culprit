"""FastAPI application: JSON API + server-sent events + the single-page dashboard."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from culprit.models import RunStatus
from culprit.service import RunManager, RunNotFound
from culprit.settings import get_settings

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Culprit", version="0.1.0", description="ML regression investigator built on Strands Agents."
)
manager = RunManager(get_settings())


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Protect mutating endpoints when CULPRIT_API_TOKEN is set (for publicly hosted demos)."""
    token = manager.settings.api_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "missing or invalid API token")


DEMO_REPO = Path("demo/churn-model")
DEMO_REPOS = (DEMO_REPO, Path("demo/fraud-risk"))  # both bundled scenarios are allowed in demo-only mode


class StartRequest(BaseModel):
    repo: str = Field(..., description="Local path or git URL of the repository to investigate")
    metric: str | None = None
    task: str = ""
    auto_approve: bool = False


class RespondRequest(BaseModel):
    decision: str | None = Field(None, description="approve | reject")
    comment: str = ""
    answer: str | None = Field(None, description="Free-text answer to an ask_human question")


class DemoRequest(BaseModel):
    dest: str = "demo/churn-model"
    force: bool = True


def _record_payload(run_id: str) -> dict[str, Any]:
    try:
        record = manager.get(run_id)
    except RunNotFound:
        raise HTTPException(404, f"run {run_id} not found")
    payload = record.model_dump(mode="json")
    payload["active"] = manager.is_active(run_id)
    return payload


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "model": manager.settings.describe_model()}


@app.get("/api/config")
def config() -> dict[str, Any]:
    settings = manager.settings
    demo_path = DEMO_REPO
    return {
        "model": settings.describe_model(),
        "auth_required": bool(settings.api_token),
        "demo_only": settings.demo_only,
        "provider": settings.model_provider,
        "runs_dir": str(manager.runs_dir.resolve()),
        "max_experiments": settings.max_experiments,
        "approval_tools": list(settings.approval_tools),
        "demo_repo": str(demo_path.resolve()),
        "demo_repo_exists": demo_path.exists(),
        "secondary_demo_repo": str(DEMO_REPOS[1].resolve()),
        "secondary_demo_repo_exists": DEMO_REPOS[1].exists(),
        "github": bool(settings.github_token),
        "slack": bool(settings.slack_webhook_url),
    }


@app.post("/api/demo/init", dependencies=[Depends(require_token)])
def demo_init(req: DemoRequest) -> dict[str, Any]:
    from culprit.demo.generator import generate

    if manager.settings.demo_only:
        req = DemoRequest(dest=str(DEMO_REPO), force=True)
    try:
        return generate(req.dest, force=req.force, quiet=True)
    except FileExistsError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    out = []
    for r in manager.list_runs():
        out.append(
            {
                "run_id": r.run_id,
                "status": r.status.value,
                "metric": r.metric,
                "repo_path": r.repo_path,
                "created_at": r.created_at,
                "culprit": r.report.culprit_commit if r.report else None,
                "experiments": len(r.experiments),
                "active": manager.is_active(r.run_id),
            }
        )
    return out


@app.post("/api/runs", status_code=201, dependencies=[Depends(require_token)])
def start_run(req: StartRequest) -> dict[str, Any]:
    if manager.settings.demo_only:
        # Public demo hosting: only the bundled demo repository may be investigated (its experiment
        # command is known); arbitrary repositories would let anyone run shell commands here.
        allowed = {p.resolve() for p in DEMO_REPOS}
        if Path(req.repo).expanduser().resolve() not in allowed:
            raise HTTPException(
                403,
                "this instance only investigates the bundled demo repositories: "
                + ", ".join(map(str, sorted(allowed))),
            )
        req.auto_approve = False
    try:
        record = manager.create_run(
            req.repo, metric=req.metric, task=req.task, auto_approve=req.auto_approve
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    manager.start(record.run_id, background=True)
    return _record_payload(record.run_id)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _record_payload(run_id)


@app.get("/api/runs/{run_id}/events")
def get_events(run_id: str, after: int = 0) -> list[dict[str, Any]]:
    _record_payload(run_id)
    return [e.model_dump(mode="json") for e in manager.events(run_id, after)]


@app.get("/api/runs/{run_id}/stream")
def stream_events(run_id: str, after: int = 0) -> StreamingResponse:
    _record_payload(run_id)

    def generate() -> Iterator[str]:
        for event in manager.subscribe(run_id, after_seq=after, timeout=10.0):
            if event is None:
                yield ": keep-alive\n\n"
                continue
            yield f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/runs/{run_id}/respond", dependencies=[Depends(require_token)])
def respond(run_id: str, req: RespondRequest) -> dict[str, Any]:
    record = manager.get(run_id) if (manager.runs_dir / run_id).exists() else None
    if record is None:
        raise HTTPException(404, f"run {run_id} not found")
    if record.status != RunStatus.AWAITING_HUMAN:
        raise HTTPException(409, f"run is {record.status.value}, not awaiting a human")
    response: Any
    if req.answer is not None:
        response = req.answer
    elif req.decision:
        response = {"decision": req.decision, "comment": req.comment}
    else:
        raise HTTPException(400, "provide decision (approve/reject) or answer")
    try:
        manager.respond(run_id, response, background=True)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return _record_payload(run_id)


@app.get("/api/runs/{run_id}/report.md", response_class=PlainTextResponse)
def report_markdown(run_id: str) -> str:
    md = manager.report_markdown(run_id)
    if md is None:
        raise HTTPException(404, "no report yet")
    return md


@app.get("/api/runs/{run_id}/pull_request.md", response_class=PlainTextResponse)
def pull_request_markdown(run_id: str) -> str:
    path = manager.runs_dir / run_id / "pull_request.md"
    if not path.exists():
        raise HTTPException(404, "no local pull request file")
    return path.read_text()


@app.get("/api/runs/{run_id}/trace")
def trace(run_id: str) -> list[dict[str, Any]]:
    path = manager.runs_dir / run_id / "trace.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
