"""Amazon Bedrock AgentCore Runtime entrypoint for Culprit.

Run locally with ``python -m culprit.agentcore_app`` (serves ``POST /invocations`` and ``GET /ping`` on
port 8080 — the AgentCore Runtime contract) and deploy with the AgentCore CLI or a container image; see
``docs/deploy-agentcore.md``.

Payloads
--------
Start an investigation (streams timeline events, then the final run record)::

    {"action": "investigate", "repo": "https://github.com/acme/churn-model.git", "metric": "f1"}
    {"action": "investigate", "repo": "demo"}          # generate + investigate the bundled demo repo

Answer the pending approval / question of a paused run::

    {"action": "respond", "run_id": "...", "decision": "approve", "comment": "LGTM"}
    {"action": "respond", "run_id": "...", "answer": "revert it"}

Inspect::

    {"action": "status", "run_id": "..."}
    {"action": "runs"}
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterator

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from culprit.service import RunManager
from culprit.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("strands").setLevel(logging.WARNING)
log = logging.getLogger("culprit.agentcore")

app = BedrockAgentCoreApp()
_manager: RunManager | None = None


def manager() -> RunManager:
    global _manager
    if _manager is None:
        _manager = RunManager(get_settings())
    return _manager


def ensure_demo_repo() -> Path:
    """Materialize the bundled demo repository next to the runs directory (idempotent)."""
    dest = Path(get_settings().runs_dir).parent / "demo" / "churn-model"
    if not (dest / ".git").exists():
        from culprit.demo.generator import generate

        generate(dest, force=True, quiet=True)
    return dest


def _record(run_id: str) -> dict[str, Any]:
    return manager().get(run_id).model_dump(mode="json")


@app.entrypoint
def invoke(payload: dict[str, Any]) -> Iterator[dict[str, Any]] | dict[str, Any]:
    """Route an AgentCore invocation to the run manager. Streams events for long-running actions."""
    action = (payload or {}).get("action", "investigate")
    mgr = manager()

    if action == "runs":
        return {"runs": [r.model_dump(mode="json") for r in mgr.list_runs()]}
    if action == "status":
        return {"run": _record(payload["run_id"])}

    if action == "investigate":
        repo = payload.get("repo") or "demo"
        if repo == "demo":
            repo = str(ensure_demo_repo())
        record = mgr.create_run(repo, metric=payload.get("metric"), task=payload.get("task", ""))
        mgr.start(record.run_id, background=True)
    elif action == "respond":
        run_id = payload["run_id"]
        response: Any = payload.get("answer")
        if response is None:
            response = {"decision": payload.get("decision", "approve"), "comment": payload.get("comment", "")}
        record = mgr.get(run_id)
        mgr.respond(run_id, response, background=True)
    else:
        return {
            "error": f"unknown action '{action}'",
            "actions": ["investigate", "respond", "status", "runs"],
        }

    def stream() -> Iterator[dict[str, Any]]:
        yield {"run_id": record.run_id, "status": "running"}
        for event in mgr.subscribe(record.run_id, after_seq=0, timeout=10.0):
            if event is None:
                continue
            yield {"event": event.model_dump(mode="json")}
        final = mgr.wait(record.run_id)
        yield {"run": final.model_dump(mode="json")}

    return stream()


if __name__ == "__main__":  # pragma: no cover
    app.run()
