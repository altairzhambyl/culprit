"""Run lifecycle: create, execute, pause for humans, resume, report.

The :class:`RunManager` is the single entry point used by the CLI, the web API and the AgentCore
entrypoint. Runs are persisted under ``runs/<run_id>/`` (record, event log, trace, Strands session,
worktrees), so a run can be resumed from another process — the approval can happen hours later.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterator

from culprit import gitutil
from culprit.adapters.github import select_pull_request_client
from culprit.adapters.metric_store import JsonMetricStore
from culprit.adapters.slack import select_notifier
from culprit.agents.investigator import build_agent
from culprit.agents.prompts import KICKOFF_PROMPT, REPORT_PROMPT
from culprit.context import InvestigationContext, load_project_config
from culprit.models import IncidentReport, PendingInterrupt, RunRecord, RunStatus, TraceEvent, utcnow_iso
from culprit.settings import Settings, get_settings

log = logging.getLogger(__name__)


class RunNotFound(KeyError):
    pass


class _EventBus:
    """Fan-out of TraceEvents for one run to any number of live subscribers (SSE, CLI)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._events: list[TraceEvent] = []
        self.closed = False

    def publish(self, event: TraceEvent) -> None:
        with self._cond:
            self._events.append(event)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    def iter_from(self, seq: int, timeout: float = 15.0) -> Iterator[TraceEvent | None]:
        """Yield events with ``seq`` greater than the given one; yields ``None`` on timeout (keep-alive)."""
        while True:
            with self._cond:
                pending = [e for e in self._events if e.seq > seq]
                if not pending:
                    if self.closed:
                        return
                    self._cond.wait(timeout)
                    pending = [e for e in self._events if e.seq > seq]
                    if not pending:
                        yield None
                        continue
            for event in pending:
                seq = event.seq
                yield event


class RunManager:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        # Absolute: git commands run with cwd=<repo>, so relative paths would resolve against the repo.
        self.runs_dir = Path(self.settings.runs_dir).expanduser().resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._buses: dict[str, _EventBus] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self.recover_stale_runs()

    def recover_stale_runs(self) -> list[str]:
        """Mark runs that were executing when a previous process died as failed (they cannot be resumed).

        A run that is ``running``/``reporting`` on disk but has no thread in this process was interrupted
        by a restart; leaving it "running" forever would confuse the UI and its SSE subscribers.
        """
        recovered: list[str] = []
        for record in self.list_runs():
            if record.status in (RunStatus.RUNNING, RunStatus.REPORTING) and not self.is_active(
                record.run_id
            ):
                record.status = RunStatus.FAILED
                record.error = (
                    "the Culprit process exited while this run was executing; start a new investigation"
                )
                record.updated_at = utcnow_iso()
                (self._run_dir(record.run_id) / "run.json").write_text(record.model_dump_json(indent=2))
                recovered.append(record.run_id)
        return recovered

    # -- persistence ---------------------------------------------------------------------------------------
    def _run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def get(self, run_id: str) -> RunRecord:
        path = self._run_dir(run_id) / "run.json"
        if not path.exists():
            raise RunNotFound(run_id)
        return RunRecord.model_validate_json(path.read_text())

    def list_runs(self) -> list[RunRecord]:
        records = []
        for path in self.runs_dir.glob("*/run.json"):
            try:
                records.append(RunRecord.model_validate_json(path.read_text()))
            except Exception:  # corrupt / partial run dirs must not break listing
                continue
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    def events(self, run_id: str, after_seq: int = 0) -> list[TraceEvent]:
        path = self._run_dir(run_id) / "events.jsonl"
        if not path.exists():
            return []
        out = []
        with path.open() as fh:
            for line in fh:
                if line.strip():
                    event = TraceEvent.model_validate_json(line)
                    if event.seq > after_seq:
                        out.append(event)
        return out

    def report_markdown(self, run_id: str) -> str | None:
        path = self._run_dir(run_id) / "report.md"
        return path.read_text() if path.exists() else None

    def is_active(self, run_id: str) -> bool:
        thread = self._threads.get(run_id)
        return bool(thread and thread.is_alive())

    def _bus(self, run_id: str) -> _EventBus:
        with self._lock:
            bus = self._buses.get(run_id)
            if bus is None:
                bus = _EventBus()
                self._buses[run_id] = bus
            return bus

    def subscribe(
        self, run_id: str, after_seq: int = 0, timeout: float = 15.0
    ) -> Iterator[TraceEvent | None]:
        """Replay persisted events, then stream live ones while the run is active."""
        last = after_seq
        for event in self.events(run_id, after_seq):
            last = event.seq
            yield event
        if not self.is_active(run_id):  # nothing more will ever be published for this run
            return
        yield from self._bus(run_id).iter_from(last, timeout=timeout)

    # -- creation --------------------------------------------------------------------------------------------
    def create_run(
        self,
        repo: str | Path,
        metric: str | None = None,
        task: str = "",
        project_overrides: dict[str, Any] | None = None,
        run_id: str | None = None,
        auto_approve: bool | None = None,
    ) -> RunRecord:
        repo_path = self._materialize_repo(repo)
        overrides = dict(project_overrides or {})
        if metric:
            overrides["metric"] = metric
        project = load_project_config(repo_path, overrides)
        run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        record = RunRecord(
            run_id=run_id,
            repo_path=str(repo_path),
            metric=project.metric,
            status=RunStatus.QUEUED,
            model=self.settings.describe_model(),
            auto_approve=self.settings.auto_approve if auto_approve is None else auto_approve,
            task=task,
            warnings=self._repo_warnings(repo_path, project.main_branch),
        )
        (run_dir / "project.json").write_text(project.model_dump_json(indent=2))
        (run_dir / "run.json").write_text(record.model_dump_json(indent=2))
        return record

    @staticmethod
    def _repo_warnings(repo: Path, main_branch: str) -> list[str]:
        """Non-fatal observations about the target repository, surfaced to the human and the agent."""
        warnings: list[str] = []
        try:
            if gitutil.run_git(repo, "status", "--porcelain", "--untracked-files=no").strip():
                warnings.append(
                    "The repository's working tree has uncommitted changes. Culprit only investigates committed "
                    "history in isolated worktrees and never touches the main working tree; uncommitted changes "
                    "are not part of any experiment."
                )
        except gitutil.GitError:
            pass
        try:
            gitutil.resolve_sha(repo, main_branch)
        except gitutil.GitError:
            warnings.append(
                f"Branch '{main_branch}' does not exist; the fix branch will be created from HEAD instead."
            )
        return warnings

    def _materialize_repo(self, repo: str | Path) -> Path:
        """Accept a local path or a git URL (cloned under runs/_repos)."""
        text = str(repo)
        if text.startswith(("http://", "https://", "git@", "ssh://")):
            name = text.rstrip("/").split("/")[-1].removesuffix(".git")
            dest = self.runs_dir / "_repos" / name
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                gitutil.run_git(self.runs_dir, "clone", "--quiet", text, str(dest), timeout=600)
            return dest.resolve()
        path = Path(text).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"repository path does not exist: {path}")
        if not gitutil.is_repo(path):
            raise ValueError(f"{path} is not a git repository")
        return path

    # -- context ---------------------------------------------------------------------------------------------
    def _context(self, record: RunRecord) -> InvestigationContext:
        run_dir = self._run_dir(record.run_id)
        from culprit.models import ProjectConfig  # local import keeps module import light

        project = ProjectConfig.model_validate_json((run_dir / "project.json").read_text())
        repo = Path(record.repo_path)
        ctx = InvestigationContext(
            record=record,
            repo=repo,
            run_dir=run_dir,
            project=project,
            settings=replace(self.settings, auto_approve=record.auto_approve),
            metric_store=JsonMetricStore(
                project.metrics_history
                if Path(project.metrics_history).is_absolute()
                else repo / project.metrics_history
            ),
            pr_client=select_pull_request_client(
                repo,
                run_dir,
                self.settings.github_token,
                self.settings.github_repo,
                self.settings.github_api_url,
            ),
            notifier=select_notifier(run_dir, self.settings.slack_webhook_url),
        )
        bus = self._bus(record.run_id)
        ctx.listeners.append(bus.publish)
        return ctx

    # -- execution ---------------------------------------------------------------------------------------------
    def start(self, run_id: str, background: bool = True) -> RunRecord:
        record = self.get(run_id)
        if record.status not in (RunStatus.QUEUED, RunStatus.FAILED):
            raise ValueError(f"run {run_id} is {record.status.value}; only queued runs can be started")
        self.preflight()
        task = record.task or ""
        if record.warnings:
            task = (task + "\n" if task else "") + "Notes: " + " ".join(record.warnings)
        prompt = KICKOFF_PROMPT.format(metric=record.metric, repo=record.repo_path, task=task)
        return self._launch(record, prompt, background)

    def preflight(self) -> None:
        """Fail fast — before any agent work — when the configured model provider cannot possibly work.

        There is deliberately no fallback to the offline policy: a run either uses the provider that was
        configured or does not start.
        """
        provider = self.settings.model_provider
        if provider == "bedrock":
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("boto3 is required for the bedrock provider (pip install boto3)") from exc
            from botocore.exceptions import BotoCoreError

            session = boto3.Session(region_name=self.settings.aws_region)
            try:
                credentials = session.get_credentials()
            except BotoCoreError as exc:
                raise RuntimeError(f"AWS credentials could not be resolved: {exc}") from exc
            if credentials is None:
                raise RuntimeError(
                    "no AWS credentials found for CULPRIT_MODEL_PROVIDER=bedrock (configure AWS_PROFILE / "
                    "AWS_ACCESS_KEY_ID+AWS_SECRET_ACCESS_KEY / an instance role, then run `culprit doctor`)"
                )
            if not session.region_name:
                raise RuntimeError("no AWS region configured (set AWS_REGION, e.g. us-west-2)")
        elif provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set for CULPRIT_MODEL_PROVIDER=anthropic")
        elif provider == "openai" and not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set for CULPRIT_MODEL_PROVIDER=openai")
        elif provider not in ("bedrock", "anthropic", "openai", "scripted"):
            raise RuntimeError(f"unknown CULPRIT_MODEL_PROVIDER '{provider}'")

    def respond(self, run_id: str, response: Any, background: bool = True) -> RunRecord:
        """Answer the pending interrupt(s) (approval decision or free-text answer) and resume."""
        record = self.get(run_id)
        if record.status != RunStatus.AWAITING_HUMAN or not record.pending_interrupts:
            raise ValueError(f"run {run_id} is not waiting for a human (status={record.status.value})")
        responses = [
            {"interruptResponse": {"interruptId": intr.id, "response": response}}
            for intr in record.pending_interrupts
        ]
        return self._launch(record, responses, background)

    def _launch(self, record: RunRecord, agent_input: Any, background: bool) -> RunRecord:
        if self.is_active(record.run_id):
            raise ValueError(f"run {record.run_id} is already executing")
        record.status = RunStatus.RUNNING
        record.pending_interrupts = []
        ctx = self._context(record)
        ctx.save()
        ctx.emit("status", {"status": record.status.value})
        if background:
            thread = threading.Thread(
                target=self._execute, args=(ctx, agent_input), name=f"culprit-{record.run_id}", daemon=True
            )
            self._threads[record.run_id] = thread
            thread.start()
            return record
        self._execute(ctx, agent_input)
        return self.get(record.run_id)

    def _execute(self, ctx: InvestigationContext, agent_input: Any) -> None:
        record = ctx.record
        bus = self._bus(record.run_id)
        try:
            agent = build_agent(ctx)
            result = agent(agent_input)
            self._accumulate_usage(ctx, result)

            if result.stop_reason == "interrupt":
                record.status = RunStatus.AWAITING_HUMAN
                record.pending_interrupts = [
                    PendingInterrupt(id=i.id, name=i.name, reason=i.reason) for i in (result.interrupts or [])
                ]
                ctx.save()
                ctx.emit(
                    "interrupt",
                    {
                        "interrupts": [p.model_dump() for p in record.pending_interrupts],
                        "status": record.status.value,
                    },
                )
                return

            if result.stop_reason == "max_tokens":
                raise RuntimeError(
                    "the model hit its output token limit mid-investigation (stop_reason=max_tokens); "
                    "raise CULPRIT_MODEL_MAX_TOKENS or use a model with a larger output window"
                )
            if result.stop_reason not in ("end_turn", "stop_sequence"):
                raise RuntimeError(f"agent stopped unexpectedly: {result.stop_reason}")

            record.summary = str(result).strip()
            record.status = RunStatus.REPORTING
            ctx.save()
            ctx.emit("status", {"status": record.status.value})

            report: IncidentReport | None = None
            last_error = ""
            for attempt in range(2):  # a malformed structured output gets exactly one retry
                prompt = (
                    REPORT_PROMPT
                    if attempt == 0
                    else (
                        REPORT_PROMPT
                        + "\nYour previous attempt did not produce a valid IncidentReport ("
                        + last_error[:300]
                        + "). Call the IncidentReport tool with all required fields."
                    )
                )
                try:
                    report_result = agent(prompt, structured_output_model=IncidentReport)
                    self._accumulate_usage(ctx, report_result)
                    candidate = report_result.structured_output
                    if isinstance(candidate, IncidentReport):
                        report = candidate
                        break
                    last_error = f"stop_reason={report_result.stop_reason}, no structured output"
                except Exception as exc:  # pydantic validation or provider errors
                    last_error = f"{type(exc).__name__}: {exc}"
                    ctx.emit("report_retry", {"attempt": attempt + 1, "error": last_error[:300]})
            if report is None:
                raise RuntimeError(
                    f"the model did not return a valid structured IncidentReport ({last_error})"
                )
            record.report = report
            record.status = RunStatus.COMPLETED
            (ctx.run_dir / "report.json").write_text(report.model_dump_json(indent=2))
            (ctx.run_dir / "report.md").write_text(render_report_markdown(record))
            ctx.save()
            ctx.emit("report", {"report": report.model_dump(), "summary": record.summary})
            ctx.emit("status", {"status": record.status.value})
        except Exception as exc:  # surface failures to the UI instead of dying silently
            log.exception("run %s failed", record.run_id)
            record.status = RunStatus.FAILED
            record.error = f"{type(exc).__name__}: {exc}"
            ctx.save()
            ctx.emit("error", {"error": record.error})
            ctx.emit("status", {"status": record.status.value})
        finally:
            self._cleanup_worktrees(ctx)
            bus.close()
            with self._lock:
                self._buses.pop(record.run_id, None)

    @staticmethod
    def _accumulate_usage(ctx: InvestigationContext, result: Any) -> None:
        try:
            usage = result.metrics.accumulated_usage
            ctx.record.input_tokens += int(usage.get("inputTokens", 0))
            ctx.record.output_tokens += int(usage.get("outputTokens", 0))
        except Exception:
            pass

    @staticmethod
    def _cleanup_worktrees(ctx: InvestigationContext) -> None:
        """Remove detached experiment worktrees (the fix worktree is kept so the branch stays reviewable)."""
        if ctx.record.status not in (RunStatus.COMPLETED, RunStatus.FAILED):
            return
        if ctx.worktrees_dir.exists():
            for wt in ctx.worktrees_dir.iterdir():
                gitutil.remove_worktree(ctx.repo, wt)
            shutil.rmtree(ctx.worktrees_dir, ignore_errors=True)
        gitutil.prune_worktrees(ctx.repo)

    # -- convenience -----------------------------------------------------------------------------------------
    def wait(self, run_id: str, timeout: float | None = None) -> RunRecord:
        thread = self._threads.get(run_id)
        if thread:
            thread.join(timeout)
        return self.get(run_id)

    def run_to_completion(
        self,
        run_id: str,
        decide: Callable[[list[PendingInterrupt]], Any],
    ) -> RunRecord:
        """Drive a run synchronously, calling ``decide`` for every human interrupt (used by the CLI/tests)."""
        record = self.start(run_id, background=False)
        while record.status == RunStatus.AWAITING_HUMAN:
            record = self.respond(run_id, decide(record.pending_interrupts), background=False)
        return record


def render_report_markdown(record: RunRecord) -> str:
    report = record.report
    if report is None:
        return f"# Culprit run {record.run_id}\n\nNo report (status: {record.status.value})."
    rows = "\n".join(f"| `{e.ref}` | {e.metric_value} | {e.verdict} |" for e in report.evidence)
    pr = report.pull_request or "not opened"
    follow = "\n".join(f"- {f}" for f in report.follow_ups) or "- none"
    return f"""# {report.title}

| | |
|---|---|
| Run | `{record.run_id}` |
| Repository | `{record.repo_path}` |
| Metric | `{report.metric}` |
| Baseline → regressed | **{report.baseline_value} → {report.regressed_value}** |
| Recovered (fix branch) | **{report.recovered_value if report.recovered_value is not None else "n/a"}** |
| Culprit | `{report.culprit_commit}` — {report.culprit_subject} |
| Confidence | {report.confidence} |
| Pull request | {pr} |
| Model | {record.model} |
| Tool calls / model calls | {record.tool_calls} / {record.model_calls} |

## Root cause

{report.root_cause}

## Evidence

| ref | {report.metric} | verdict |
|---|---|---|
{rows}

## Fix

{report.fix_summary}

Files changed: {", ".join(f"`{f}`" for f in report.files_changed) or "none"}

Guard test: {f"`{report.guard_test}`" if report.guard_test else "none"}

## Follow-ups

{follow}

---
Generated by Culprit at {utcnow_iso()}.
"""


def load_report(run_dir: Path) -> IncidentReport | None:
    path = run_dir / "report.json"
    return IncidentReport.model_validate_json(path.read_text()) if path.exists() else None


__all__ = ["RunManager", "RunNotFound", "render_report_markdown", "load_report"]
