"""Culprit command-line interface."""

from __future__ import annotations

import json
import logging
import sys
import webbrowser
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from culprit.models import RunStatus, TraceEvent
from culprit.settings import get_settings

app = typer.Typer(
    help="Culprit — finds the commit that broke your model, proves it, fixes it, and asks you once.",
    no_args_is_help=True,
)
demo_app = typer.Typer(help="Reproducible demo repository.")
app.add_typer(demo_app, name="demo")
console = Console()


def _manager():
    from culprit.service import RunManager

    return RunManager(get_settings(refresh=True))


# ------------------------------------------------------------------------------------------------
# Event rendering
# ------------------------------------------------------------------------------------------------


def _render(event: TraceEvent, verbose: bool = False) -> None:
    d = event.data
    kind = event.kind
    if kind == "thought":
        console.print(f"[italic dim]{d['text']}[/]")
    elif kind == "tool_start":
        if d["tool"] == "IncidentReport":
            console.print("[bold cyan]▶ composing the structured incident report[/]")
            return
        args = ", ".join(f"{k}={_fmt(v)}" for k, v in d.get("input", {}).items())
        console.print(f"[bold cyan]▶ {d['tool']}[/]([dim]{args}[/])")
    elif kind == "tool_end" and (verbose or d.get("status") != "success" or d.get("cancelled")):
        note = d.get("cancelled") or d.get("result_preview", "")
        style = "yellow" if d.get("cancelled") else ("red" if d.get("status") != "success" else "dim")
        console.print(f"  [{style}]↳ {str(note)[:300]}[/]")
    elif kind == "experiment":
        val = d.get("value")
        if d.get("status") == "ok":
            cached = " (cached)" if d.get("cached") else ""
            console.print(
                f"  [green]↳ {d.get('tracked_metric', 'metric')} = {val}[/] [dim]{d['sha'][:7]} {d.get('config')} {d.get('duration_s')}s{cached}[/]"
            )
        else:
            console.print(f"  [red]↳ experiment failed: {d.get('stderr_tail', '')[:200]}[/]")
    elif kind == "candidates":
        console.print(f"  [dim]↳ {len(d['commits'])} candidate commit(s) in the window[/]")
    elif kind == "edit":
        console.print(Panel(Text(d["diff"]), title=f"edit {d['path']}", border_style="magenta", expand=False))
    elif kind == "tests":
        console.print(
            f"  [{'green' if d.get('passed') else 'red'}]↳ tests {'passed' if d.get('passed') else 'FAILED'}[/]"
        )
    elif kind == "pull_request":
        console.print(f"  [green]↳ pull request ({d.get('mode')}): {d.get('url') or d.get('error')}[/]")
    elif kind == "notification":
        console.print(f"  [green]↳ notified {d.get('channel')} via {d.get('mode')}[/]")
    elif kind == "decision":
        console.print(
            f"[bold]{'✔ approved' if d['decision'] == 'approve' else '✘ rejected'}[/] {d['tool']} {d.get('comment', '')}"
        )
    elif kind == "budget_exhausted":
        console.print(f"[yellow]experiment budget exhausted ({d['spent']}/{d['max']})[/]")
    elif kind == "error":
        console.print(f"[bold red]error: {d['error']}[/]")
    elif kind == "status":
        console.print(f"[dim]status → {d['status']}[/]")
    elif kind == "report":
        r = d["report"]
        table = Table.grid(padding=(0, 2))
        table.add_row(
            "Metric",
            f"{r['metric']}: {r['baseline_value']} → {r['regressed_value']} → fixed {r.get('recovered_value')}",
        )
        table.add_row("Culprit", f"{r['culprit_commit']} — {r['culprit_subject']}")
        table.add_row("Root cause", r["root_cause"])
        table.add_row("Fix", r["fix_summary"])
        table.add_row("Guard test", str(r.get("guard_test")))
        table.add_row("Pull request", str(r.get("pull_request")))
        table.add_row("Confidence", r["confidence"])
        console.print(Panel(table, title=f"Incident report — {r['title']}", border_style="green"))


def _fmt(value: Any) -> str:
    text = json.dumps(value) if not isinstance(value, str) else value
    return text if len(text) <= 60 else text[:57] + "…"


def _follow(mgr, run_id: str, after_seq: int, verbose: bool) -> int:
    last = after_seq
    for event in mgr.subscribe(run_id, after_seq=last):
        if event is None:
            continue
        last = event.seq
        _render(event, verbose)
    return last


def _ask_decision(pending) -> dict[str, Any]:
    for intr in pending:
        reason = intr.reason or {}
        if isinstance(reason, dict) and "question" in reason:
            console.print(Panel(reason["question"], title="Culprit asks", border_style="yellow"))
            answer = typer.prompt("Your answer")
            return {"decision": answer, "comment": answer}
        summary = reason.get("summary") if isinstance(reason, dict) else str(reason)
        body = ""
        if isinstance(reason, dict):
            body = str(reason.get("input", {}).get("body", ""))[:1500]
        console.print(
            Panel(f"[bold]{summary}[/]\n\n{body}", title="Approval required", border_style="yellow")
        )
    approve = typer.confirm("Approve?", default=True)
    comment = "" if approve else typer.prompt("Reason (optional)", default="", show_default=False)
    return {"decision": "approve" if approve else "reject", "comment": comment}


# ------------------------------------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------------------------------------


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Show tool results and debug logs.")):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("strands").setLevel(logging.WARNING)
    _main.verbose = verbose  # type: ignore[attr-defined]


@demo_app.command("init")
def demo_init(
    scenario: str = typer.Option(
        "churn", help="Scenario to build: churn (golden path) or fraud (unseen regression)."
    ),
    dest: Optional[Path] = typer.Option(
        None, help="Where to create the repository (default demo/<scenario repo>)."
    ),
    force: bool = typer.Option(
        False, help="Recreate if it exists (only Culprit-generated demo repositories are ever deleted)."
    ),
    without_latest_nightly: bool = typer.Option(
        False,
        "--without-latest-nightly",
        help="Omit this morning's bad nightly record, to demo `culprit record-nightly` + `culprit watch`.",
    ),
):
    """Create a reproducible demo repository (seven commits, one silent regression, real nightly metrics)."""
    from culprit.demo.scenarios import generate_scenario, get_scenario

    try:
        sc = get_scenario(scenario)
    except KeyError as exc:
        raise typer.BadParameter(str(exc))
    target = dest or sc.default_dest
    console.print(f"[bold]Creating {sc.title}[/] at {target}")
    summary = generate_scenario(scenario, target, force=force, drop_latest_nightly=without_latest_nightly)
    console.print(
        f"[green]✔ ready[/] nightly {summary['metric']} {summary['metric_good']} → {summary['metric_bad']}"
        f"  (culprit {summary['culprit'][:7]} — kept out of the model's sight; only `culprit evaluate` uses it for scoring)"
    )
    console.print(f"\nNext: [bold]culprit investigate {target}[/]")


@demo_app.command("init-secondary")
def demo_init_secondary(
    dest: Optional[Path] = typer.Option(
        None, help="Where to create the repository (default demo/fraud-risk)."
    ),
    force: bool = typer.Option(False, help="Recreate if it exists."),
):
    """Create the secondary, *unseen* demo repository (fraud-risk) used to evaluate a real model."""
    from culprit.demo.scenarios import SECONDARY_SCENARIO

    demo_init(scenario=SECONDARY_SCENARIO, dest=dest, force=force, without_latest_nightly=False)


@demo_app.command("list")
def demo_list():
    """List available demo scenarios."""
    from culprit.demo.scenarios import SCENARIOS

    table = Table(title="Demo scenarios")
    for col in ("key", "repository", "metric", "known to offline policy", "scenario"):
        table.add_column(col)
    for key, sc in SCENARIOS.items():
        table.add_row(key, str(sc.default_dest), sc.metric, "yes" if key == "churn" else "no", sc.title)
    console.print(table)
    console.print(
        "[dim]Mechanisms are documented in src/culprit/demo/*_generator.py — read them only after evaluating.[/]"
    )


@app.command()
def evaluate(
    scenario: str = typer.Option("fraud", help="Scenario to evaluate the configured model on."),
    dest: Optional[Path] = typer.Option(None, help="Where to (re)generate the scenario repository."),
    task: str = typer.Option("", help="Extra instructions passed to the agent (no hints about the answer!)."),
):
    """Generalization evaluation: run the configured (real) model on a scenario and score the result.

    The model is told nothing about the expected culprit. Afterwards the evaluator re-measures the fix
    branch and re-runs the project's tests itself and writes runs/<run_id>/evaluation.json.
    """
    from culprit.evaluation import evaluate_scenario, format_summary

    settings = get_settings(refresh=True)
    console.print(
        Panel(
            f"scenario [bold]{scenario}[/]   model [bold]{settings.describe_model()}[/]",
            title="Culprit evaluation",
            border_style="cyan",
        )
    )
    if settings.model_provider == "scripted" and scenario != "churn":
        console.print(
            "[yellow]Note: the scripted offline policy only knows the churn demo; expect it to stop after bisection.[/]"
        )
    result = evaluate_scenario(scenario, settings, dest=dest, task=task)
    console.print(
        Panel(
            format_summary(result),
            title=f"evaluation.json → runs/{result['run_id']}/",
            border_style="green" if result["success"] else "red",
        )
    )
    raise typer.Exit(code=0 if result["success"] else 2)


@app.command()
def investigate(
    repo: str = typer.Argument(..., help="Path (or git URL) of the repository to investigate."),
    metric: Optional[str] = typer.Option(None, help="Metric to investigate (defaults to .culprit.yaml)."),
    task: str = typer.Option("", help="Extra instructions for the agent."),
    auto_approve: bool = typer.Option(False, help="Skip human approval (CI / unattended)."),
    no_input: bool = typer.Option(False, help="Never prompt; exit when human input is required."),
    experiment_command: Optional[str] = typer.Option(None, help="Override the project's experiment command."),
):
    """Start an investigation and follow it in the terminal."""
    settings = get_settings(refresh=True)
    if auto_approve:
        settings.auto_approve = True
    mgr = _manager()
    mgr.settings = settings
    record = mgr.create_run(
        repo, metric=metric, task=task, project_overrides={"experiment_command": experiment_command}
    )
    console.print(
        Panel(
            f"run [bold]{record.run_id}[/]  repo {record.repo_path}\nmetric [bold]{record.metric}[/]  model {record.model}",
            title="Culprit",
            border_style="cyan",
        )
    )
    verbose = getattr(_main, "verbose", False)
    mgr.start(record.run_id, background=True)
    last = _follow(mgr, record.run_id, 0, verbose)
    record = mgr.wait(record.run_id)
    while record.status == RunStatus.AWAITING_HUMAN:
        if no_input:
            console.print(
                f"[yellow]Human input required.[/] Resume with: culprit resume {record.run_id} --approve  (or --reject)"
            )
            raise typer.Exit(code=3)
        decision = _ask_decision(record.pending_interrupts)
        mgr.respond(record.run_id, decision, background=True)
        last = _follow(mgr, record.run_id, last, verbose)
        record = mgr.wait(record.run_id)
    _finish(record)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Run id (see `culprit runs`)."),
    approve: bool = typer.Option(False, "--approve", help="Approve the pending action."),
    reject: bool = typer.Option(False, "--reject", help="Reject the pending action."),
    answer: Optional[str] = typer.Option(None, help="Free-text answer to a question from the agent."),
    comment: str = typer.Option("", help="Optional comment for the agent."),
):
    """Resume a run that is waiting for a human (works from any process — sessions are persisted)."""
    if not (approve or reject or answer):
        raise typer.BadParameter("pass --approve, --reject or --answer")
    mgr = _manager()
    response: Any = {"decision": "approve" if approve else "reject", "comment": comment}
    if answer:
        response = answer
    mgr.get(run_id)  # raises RunNotFound for unknown ids
    existing = mgr.events(run_id)
    last = existing[-1].seq if existing else 0
    mgr.respond(run_id, response, background=True)
    _follow(mgr, run_id, last, getattr(_main, "verbose", False))
    _finish(mgr.wait(run_id))


def _finish(record) -> None:
    if record.status == RunStatus.COMPLETED:
        console.print(
            f"\n[bold green]✔ completed[/] report: {Path(get_settings().runs_dir) / record.run_id / 'report.md'}"
        )
    elif record.status == RunStatus.FAILED:
        console.print(f"\n[bold red]✘ failed[/] {record.error}")
        raise typer.Exit(code=1)
    else:
        console.print(f"\nstatus: {record.status.value}")


@app.command("record-nightly")
def record_nightly_cmd(
    repo: Path = typer.Argument(..., help="Repository with a .culprit.yaml."),
    config: Optional[str] = typer.Option(
        None, help="Experiment config to run (default: nightly_config from .culprit.yaml)."
    ),
):
    """What a nightly job does: evaluate HEAD and append the result to the metric store."""
    from culprit.automation import record_nightly

    run = record_nightly(repo, config=config)
    console.print(
        f"[green]✔[/] recorded {run.run_id}: commit {run.commit[:7]} {run.config} → "
        + ", ".join(f"{k}={v}" for k, v in run.metrics.items())
    )


@app.command()
def watch(
    repo: Path = typer.Argument(..., help="Repository with a .culprit.yaml and a metric store."),
    once: bool = typer.Option(False, "--once", help="Check once and exit (for cron / scheduled workflows)."),
    interval: int = typer.Option(900, help="Polling interval in seconds when not using --once."),
    channel: str = typer.Option("#ml-alerts", help="Notification channel used when approval is needed."),
    dashboard_url: Optional[str] = typer.Option(
        None, help="Dashboard URL to include in the approval notification."
    ),
    auto_approve: bool = typer.Option(False, help="Unattended mode: open the PR without asking (CI)."),
):
    """Autonomous trigger: if the metric store shows a regression above threshold, start Culprit.

    Each good→bad window is investigated once (state in runs/_watch). A human is contacted only when
    the run pauses for approval. Exit codes with --once: 0 nothing to do or completed, 3 awaiting
    human approval, 1 failed.
    """
    from culprit.automation import check_and_trigger, watch_forever

    settings = get_settings(refresh=True)
    if auto_approve:
        settings.auto_approve = True
    if not once:
        console.print(f"[bold]watching {repo}[/] every {interval}s (model {settings.describe_model()})")
        watch_forever(repo, settings, interval_s=interval, channel=channel, dashboard_url=dashboard_url)
        return
    outcome = check_and_trigger(repo, settings, channel=channel, wait=True, dashboard_url=dashboard_url)
    status = outcome["status"]
    if status == "no_regression":
        console.print(
            f"[green]✔[/] no regression in {outcome['metric']} ({outcome['runs_seen']} runs in the store)"
        )
        raise typer.Exit(code=0)
    if status == "already_handled":
        console.print(f"[dim]window {outcome['window']} already investigated by run {outcome['run_id']}[/]")
        raise typer.Exit(code=0)
    console.print(
        f"[bold]regression detected[/] {outcome['metric']} {outcome['baseline_value']} → "
        f"{outcome['regressed_value']}; started run [bold]{outcome['run_id']}[/] → {outcome.get('run_status')}"
    )
    if outcome.get("run_status") == "awaiting_human":
        note = outcome.get("notification", {})
        console.print(
            Panel(
                outcome["approval_command"],
                title=f"approval required (notified via {note.get('mode', '?')})",
                border_style="yellow",
            )
        )
        raise typer.Exit(code=3)
    if outcome.get("run_status") == "failed":
        console.print(f"[red]run failed:[/] {outcome.get('error')}")
        raise typer.Exit(code=1)
    console.print(f"[green]✔ completed[/] see runs/{outcome['run_id']}/report.md")


@app.command()
def clean(
    run_id: str = typer.Argument(..., help="Run id whose git worktrees should be removed."),
    delete_branch: bool = typer.Option(False, "--delete-branch", help="Also delete the run's fix branch."),
):
    """Remove a run's experiment/fix worktrees from the target repository (the run's files are kept)."""
    from culprit import gitutil

    mgr = _manager()
    record = mgr.get(run_id)
    repo = Path(record.repo_path)
    run_dir = Path(mgr.runs_dir) / run_id
    removed = 0
    for wt in list((run_dir / "worktrees").glob("*")) + (
        [run_dir / "fix"] if (run_dir / "fix").exists() else []
    ):
        gitutil.remove_worktree(repo, wt)
        removed += 1
    gitutil.prune_worktrees(repo)
    if delete_branch and record.fix_branch:
        gitutil.run_git(repo, "branch", "-D", record.fix_branch, check=False)
        console.print(f"deleted branch {record.fix_branch}")
    console.print(f"[green]✔[/] removed {removed} worktree(s) for run {run_id}; run files kept in {run_dir}")


@app.command()
def doctor():
    """Check the environment before a real run: git, Strands, AWS credentials and Bedrock model access."""
    import shutil as _shutil

    settings = get_settings(refresh=True)
    table = Table(title="Culprit doctor")
    table.add_column("check")
    table.add_column("result")
    table.add_column("hint")
    failures = 0

    def row(name: str, ok: bool | None, detail: str, hint: str = "") -> None:
        nonlocal failures
        mark = "[green]✔[/]" if ok else ("[yellow]•[/]" if ok is None else "[red]✘[/]")
        if ok is False:
            failures += 1
        table.add_row(name, f"{mark} {detail}", hint)

    row("python", True, sys.version.split()[0])
    row("git", bool(_shutil.which("git")), _shutil.which("git") or "not found", "install git")
    try:
        from importlib.metadata import version

        row("strands-agents", True, version("strands-agents"))
    except Exception as exc:  # pragma: no cover
        row("strands-agents", False, str(exc), 'pip install -e ".[dev]"')
    row("model provider", True, settings.describe_model(), "CULPRIT_MODEL_PROVIDER / CULPRIT_MODEL_ID")
    row("runs dir", True, str(Path(settings.runs_dir).resolve()))
    for name, path in (
        ("demo/churn-model", Path("demo/churn-model")),
        ("demo/fraud-risk", Path("demo/fraud-risk")),
    ):
        row(
            name,
            None if not path.exists() else True,
            "present" if path.exists() else "missing",
            "culprit demo init / demo init-secondary",
        )

    if settings.model_provider == "bedrock":
        try:
            import boto3
            from botocore.exceptions import BotoCoreError, ClientError

            session = boto3.Session(region_name=settings.aws_region)
            region = session.region_name
            row("aws region", bool(region), region or "not set", "export AWS_REGION=us-west-2")
            creds = session.get_credentials()
            row(
                "aws credentials",
                bool(creds),
                "found" if creds else "none",
                "aws configure / AWS_PROFILE / env vars",
            )
            if creds and region:
                try:
                    ident = session.client("sts").get_caller_identity()
                    row("aws identity", True, ident.get("Arn", "?"))
                except (ClientError, BotoCoreError) as exc:
                    row("aws identity", False, str(exc)[:120], "credentials invalid or expired")
                from strands.models.bedrock import DEFAULT_BEDROCK_MODEL_ID

                model_id = settings.model_id or DEFAULT_BEDROCK_MODEL_ID
                try:
                    session.client("bedrock-runtime").converse(
                        modelId=model_id,
                        messages=[{"role": "user", "content": [{"text": "Reply with the single word: ok"}]}],
                        inferenceConfig={"maxTokens": 5},
                    )
                    row("bedrock model", True, f"{model_id} responded", "")
                except (ClientError, BotoCoreError) as exc:
                    row(
                        "bedrock model",
                        False,
                        f"{model_id}: {str(exc)[:160]}",
                        "enable the model in the Bedrock console for this region, or set CULPRIT_MODEL_ID",
                    )
        except ImportError as exc:  # pragma: no cover
            row("boto3", False, str(exc), "pip install boto3")
    elif settings.model_provider == "scripted":
        row(
            "offline policy",
            None,
            "integration-test fixture; knows only the churn demo",
            "use bedrock for real runs",
        )

    console.print(table)
    if failures:
        console.print(f"[red]{failures} check(s) failed.[/]")
        raise typer.Exit(code=1)
    console.print("[green]All required checks passed.[/]")


@app.command()
def runs():
    """List investigations."""
    mgr = _manager()
    table = Table(title="Culprit runs")
    for col in ("run id", "status", "metric", "culprit", "experiments", "created"):
        table.add_column(col)
    for r in mgr.list_runs():
        culprit = r.report.culprit_commit if r.report else "-"
        table.add_row(r.run_id, r.status.value, r.metric, culprit, str(len(r.experiments)), r.created_at)
    console.print(table)


@app.command()
def show(run_id: str = typer.Argument(...)):
    """Print a run's incident report (Markdown)."""
    mgr = _manager()
    md = mgr.report_markdown(run_id)
    if md:
        console.print(md)
    else:
        record = mgr.get(run_id)
        console.print(f"run {run_id}: {record.status.value} {record.error or ''}")


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, help="Bind host (default 127.0.0.1)."),
    port: Optional[int] = typer.Option(None, help="Port (default 8000)."),
    open_browser: bool = typer.Option(False, "--open", help="Open the dashboard in a browser."),
):
    """Run the web dashboard (live timeline, approvals, reports)."""
    import uvicorn

    settings = get_settings(refresh=True)
    host = host or settings.host
    port = port or settings.port
    url = f"http://{host}:{port}"
    console.print(f"[bold]Culprit dashboard[/] → {url}   (model: {settings.describe_model()})")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run("culprit.web.app:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
