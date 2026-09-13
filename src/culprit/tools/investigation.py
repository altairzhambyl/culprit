"""Strands tools the Culprit agent uses to investigate, verify, fix and deliver.

Every tool returns a JSON-serializable dict (Strands serializes it into the tool result). Tools never
raise for expected failures — they return ``{"error": ...}`` with enough context for the model to
adapt — and they publish UI events through the investigation context.
"""

from __future__ import annotations

import difflib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from strands import tool
from strands.types.tools import ToolContext

from culprit import gitutil
from culprit.adapters.metric_store import detect_regression
from culprit.context import InvestigationContext
from culprit.models import ExperimentResult

MAX_FILE_CHARS = 12_000
MAX_DIFF_CHARS = 7_000


def quote_arg(value: str) -> str:
    """Quote a template argument for the platform shell used by subprocess."""
    if os.name == "nt":
        quoted = subprocess.list2cmdline([value])
        if not quoted.startswith('"') and any(c in value for c in "&|<>^()"):
            quoted = '"' + quoted + '"'
        return quoted
    return shlex.quote(value)


def render_command(template: str, **substitutions: str) -> str:
    """Substitute ``{name}`` placeholders without interpreting any other braces in the command.

    ``str.format`` would choke on shell commands that legitimately contain braces (``jq '{...}'``,
    ``echo {}``); only the documented placeholders are replaced.
    """
    out = template
    for name, value in substitutions.items():
        out = out.replace("{" + name + "}", value)
    return out


def _tail(text: str, lines: int = 25, chars: int = 2500) -> str:
    tail = "\n".join(text.strip().splitlines()[-lines:])
    return tail[-chars:]


class InvestigationTools:
    """Tool set bound to one investigation. Pass ``tools.all()`` to the Strands ``Agent``."""

    def __init__(self, ctx: InvestigationContext):
        self.ctx = ctx
        self._shim_dir: Path | None = None

    def all(self) -> list[Any]:
        return [
            self.get_metric_history,
            self.list_commits,
            self.show_commit,
            self.read_file,
            self.run_experiment,
            self.start_fix,
            self.edit_file,
            self.write_file,
            self.run_tests,
            self.open_pull_request,
            self.notify,
            self.ask_human,
        ]

    # ------------------------------------------------------------------------------------------
    # Observe
    # ------------------------------------------------------------------------------------------
    @tool
    def get_metric_history(self, limit: int = 14) -> dict:
        """Read the recorded evaluation history (e.g. nightly runs) for the tracked metric.

        Returns the most recent runs (oldest first) with their commit shas, plus a
        ``detected_regression`` block describing the latest good->bad transition, if any.

        Args:
            limit: Maximum number of recent runs to return.
        """
        project = self.ctx.project
        try:
            runs = self.ctx.metric_store.list_runs(branch=project.main_branch)
        except FileNotFoundError as exc:
            return {"error": str(exc)}
        window = detect_regression(
            runs, project.metric, project.higher_is_better, project.regression_threshold
        )
        recent = runs[-limit:]
        payload: dict[str, Any] = {
            "metric": project.metric,
            "higher_is_better": project.higher_is_better,
            "regression_threshold": project.regression_threshold,
            "runs": [
                {
                    "run_id": r.run_id,
                    "timestamp": r.timestamp,
                    "commit": r.commit[:12],
                    "config": r.config,
                    "status": r.status,
                    "metrics": r.metrics,
                }
                for r in recent
            ],
        }
        if window:
            payload["detected_regression"] = {
                "last_good_commit": window.last_good_run.commit[:12],
                "last_good_run": window.last_good_run.run_id,
                "first_bad_commit": window.first_bad_run.commit[:12],
                "first_bad_run": window.first_bad_run.run_id,
                "baseline_value": window.baseline_value,
                "regressed_value": window.regressed_value,
                "delta": window.delta,
            }
        else:
            payload["detected_regression"] = None
        self.ctx.emit(
            "metrics",
            {
                "metric": project.metric,
                "runs": payload["runs"],
                "regression": payload["detected_regression"],
            },
        )
        return payload

    @tool
    def list_commits(self, good_ref: str, bad_ref: str) -> dict:
        """List the commits that landed after ``good_ref`` up to and including ``bad_ref`` (oldest first).

        These are the candidate commits that may have introduced the regression.

        Args:
            good_ref: Commit sha or ref of the last known-good state.
            bad_ref: Commit sha or ref of the first known-bad state.
        """
        try:
            commits = gitutil.log_range(self.ctx.repo, good_ref, bad_ref)
        except gitutil.GitError as exc:
            return {"error": str(exc)}
        data = [c.to_dict() for c in commits]
        self.ctx.emit(
            "candidates",
            {"good_ref": good_ref[:12], "bad_ref": bad_ref[:12], "commits": data},
        )
        return {"good_ref": good_ref, "bad_ref": bad_ref, "count": len(data), "commits": data}

    @tool
    def show_commit(self, ref: str) -> dict:
        """Show a commit's message, changed files and unified diff (truncated for very large diffs).

        Args:
            ref: Commit sha (short or full) or any git ref.
        """
        try:
            info = gitutil.show_commit(self.ctx.repo, ref, max_chars=MAX_DIFF_CHARS)
        except gitutil.GitError as exc:
            return {"error": str(exc)}
        self.ctx.emit("commit", {"short": info["short"], "subject": info["subject"], "stat": info["stat"]})
        return info

    @tool
    def read_file(self, path: str, ref: str = "HEAD") -> dict:
        """Read a file from the repository at a given ref (default HEAD), or from the fix branch.

        Args:
            path: Path relative to the repository root.
            ref: Commit sha / branch name. Use the fix branch name to read your in-progress changes.
        """
        fix_wt = self.ctx.fix_worktree
        if fix_wt is not None and ref == self.ctx.record.fix_branch:
            target = fix_wt / path
            if not target.exists():
                return {"error": f"{path} does not exist on the fix branch"}
            content = target.read_text()
        else:
            try:
                content = gitutil.read_file_at(self.ctx.repo, path, ref)
            except gitutil.GitError as exc:
                return {"error": str(exc)}
        truncated = len(content) > MAX_FILE_CHARS
        if truncated:
            content = content[:MAX_FILE_CHARS] + "\n... [truncated]"
        return {
            "path": path,
            "ref": ref,
            "content": content,
            "truncated": truncated,
            "lines": content.count("\n") + 1,
        }

    # ------------------------------------------------------------------------------------------
    # Act: experiments
    # ------------------------------------------------------------------------------------------
    def _python_shim_path(self) -> Path:
        """A tiny bin dir mapping ``python`` to the current interpreter so target projects run in our env."""
        if os.name == "nt":
            return Path(sys.executable).parent
        if self._shim_dir is None:
            shim = self.ctx.run_dir / "bin"
            shim.mkdir(exist_ok=True)
            link = shim / "python"
            if not link.exists():
                try:
                    link.symlink_to(sys.executable)
                except OSError:
                    link.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
                    link.chmod(0o755)
            self._shim_dir = shim
        return self._shim_dir

    def _run_command(self, command: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
        """Run a shell command in its own process group so a timeout kills the whole tree."""
        env = {**os.environ}
        env["PATH"] = f"{self._python_shim_path()}{os.pathsep}{env.get('PATH', '')}"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
                if proc.poll() is None:
                    proc.kill()
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
            proc.communicate()
            raise
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)

    def _resolve_experiment_target(self, ref: str) -> tuple[Path, str, bool]:
        """Return (working directory, sha, is_fix_branch) for a ref."""
        record = self.ctx.record
        fix_wt = self.ctx.fix_worktree
        if fix_wt is not None and ref in {record.fix_branch, "fix", "fix-branch"}:
            return fix_wt, gitutil.resolve_sha(fix_wt, "HEAD") + "+wip", True
        sha = gitutil.resolve_sha(self.ctx.repo, ref)
        wt = self.ctx.worktrees_dir / sha[:12]
        if wt.exists() and not (wt / ".git").exists():  # half-created directory from an interrupted run
            shutil.rmtree(wt, ignore_errors=True)
            gitutil.prune_worktrees(self.ctx.repo)
        gitutil.add_worktree(self.ctx.repo, wt, sha)
        return wt, sha, False

    @tool
    def run_experiment(self, ref: str, config: str | None = None, note: str = "") -> dict:
        """Check out a commit in an isolated worktree, run the project's training/evaluation command and return its metrics.

        Results are cached per (commit, config). Pass the fix branch name as ``ref`` to evaluate
        your uncommitted changes on the fix branch. This is the ground truth for deciding whether a
        commit is good or bad — use it instead of guessing from diffs.

        Args:
            ref: Commit sha / ref to evaluate, or the fix branch name.
            config: Experiment config name (defaults to the project's fast config).
            note: Short note on why you are running this experiment (shown in the timeline).
        """
        project = self.ctx.project
        config = config or project.default_config
        try:
            cwd, sha, is_fix = self._resolve_experiment_target(ref)
        except gitutil.GitError as exc:
            return {"error": str(exc)}

        if not is_fix:
            for prior in self.ctx.record.experiments:
                if prior.sha == sha and prior.config == config and prior.status == "ok":
                    cached = prior.model_copy(update={"cached": True, "note": note or prior.note})
                    payload = cached.model_dump()
                    # Same shape as a fresh run below: consumers (the dashboard) read `value`, and a
                    # cache hit that omitted it used to shadow the real measurement for this commit.
                    payload["tracked_metric"] = project.metric
                    payload["value"] = cached.metrics.get(project.metric)
                    self.ctx.emit("experiment", payload)
                    return payload

        self.ctx.emit("experiment_start", {"ref": ref, "sha": sha[:12], "config": config, "note": note})
        # Metrics go outside the worktree so they never end up in the fix branch's commit.
        metrics_dir = self.ctx.run_dir / "metrics"
        metrics_dir.mkdir(exist_ok=True)
        out_path = metrics_dir / f"{sha.replace('+', '-')[:16]}-{config}-{int(time.time() * 1000)}.json"
        command = render_command(
            project.experiment_command, config=quote_arg(config), out=quote_arg(str(out_path))
        )
        timeout = min(project.experiment_timeout_s, self.ctx.settings.experiment_timeout_s)

        attempts, last_error, metrics, started = 0, "", None, time.time()
        while attempts < 2 and metrics is None:
            attempts += 1
            try:
                proc = self._run_command(command, cwd, timeout)
            except subprocess.TimeoutExpired:
                last_error = f"timed out after {timeout}s"
                continue
            if proc.returncode != 0:
                last_error = _tail(proc.stderr or proc.stdout)
                continue
            metrics = self._parse_metrics(out_path, proc.stdout)
            if metrics is None:
                last_error = "command succeeded but produced no metrics JSON (expected a JSON object at {out} or on stdout)"
                break  # deterministic: retrying will not help
            if project.metric not in metrics:
                last_error = (
                    f"metrics JSON does not contain the tracked metric '{project.metric}'; "
                    f"available keys: {sorted(metrics)}"
                )
                metrics = None
                break
        duration = round(time.time() - started, 2)

        result = ExperimentResult(
            ref=ref,
            sha=sha,
            config=config,
            status="ok" if metrics is not None else "failed",
            metrics=metrics or {},
            duration_s=duration,
            note=note,
            stderr_tail="" if metrics is not None else last_error,
        )
        self.ctx.record.experiments.append(result)
        self.ctx.save()
        payload = result.model_dump()
        payload["tracked_metric"] = project.metric
        payload["value"] = (metrics or {}).get(project.metric)
        self.ctx.emit("experiment", payload)
        if metrics is None:
            payload["error"] = f"experiment failed after {attempts} attempt(s): {last_error}"
        return payload

    @staticmethod
    def _numeric(data: Any) -> dict[str, float] | None:
        if not isinstance(data, dict):
            return None
        return {
            k: float(v) for k, v in data.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
        }

    @classmethod
    def _parse_metrics(cls, out_path: Path, stdout: str) -> dict[str, float] | None:
        if out_path.exists():
            try:
                parsed = cls._numeric(json.loads(out_path.read_text()))
                if parsed is not None:
                    return parsed
            except (json.JSONDecodeError, OSError):
                pass
        for line in reversed(stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    parsed = cls._numeric(json.loads(line))
                    if parsed is not None:
                        return parsed
                except json.JSONDecodeError:
                    continue
        return None

    # ------------------------------------------------------------------------------------------
    # Act: fixing
    # ------------------------------------------------------------------------------------------
    @tool
    def start_fix(self, branch_name: str | None = None) -> dict:
        """Create the fix branch (from the main branch head) in an isolated worktree so you can edit files safely.

        Call this once before edit_file / write_file. Returns the branch name to use with
        run_experiment and read_file.

        Args:
            branch_name: Optional branch name; defaults to culprit/fix-<run id>.
        """
        record = self.ctx.record
        if record.fix_branch and self.ctx.fix_worktree and self.ctx.fix_worktree.exists():
            return {"branch": record.fix_branch, "worktree": record.fix_worktree, "already_started": True}
        branch = branch_name or f"culprit/fix-{record.run_id}"
        wt = self.ctx.run_dir / "fix"
        base = self.ctx.project.main_branch
        try:
            gitutil.resolve_sha(self.ctx.repo, base)
        except gitutil.GitError:
            base = "HEAD"
        try:
            gitutil.add_worktree(self.ctx.repo, wt, base, new_branch=branch)
        except gitutil.GitError as exc:
            return {"error": str(exc)}
        record.fix_branch, record.fix_worktree = branch, str(wt)
        self.ctx.save()
        self.ctx.emit("fix_started", {"branch": branch, "base": base})
        return {"branch": branch, "base": base, "worktree": str(wt), "already_started": False}

    def _fix_path(self, path: str) -> tuple[Path | None, dict | None]:
        wt = self.ctx.fix_worktree
        if wt is None or not wt.exists():
            return None, {"error": "call start_fix before editing files"}
        target = (wt / path).resolve()
        if wt.resolve() not in target.parents and target != wt.resolve():
            return None, {"error": "path escapes the repository"}
        return target, None

    @tool
    def edit_file(self, path: str, old_text: str, new_text: str) -> dict:
        """Replace one exact occurrence of ``old_text`` with ``new_text`` in a file on the fix branch.

        ``old_text`` must match exactly once (copy it verbatim from read_file). Returns the resulting
        diff hunk so you can verify the change.

        Args:
            path: File path relative to the repository root.
            old_text: Exact existing text to replace (must be unique in the file).
            new_text: Replacement text.
        """
        target, err = self._fix_path(path)
        if err:
            return err
        assert target is not None
        if not target.exists():
            return {"error": f"{path} does not exist on the fix branch (use write_file to create files)"}
        original = target.read_text()
        count = original.count(old_text)
        if count == 0:
            close = difflib.get_close_matches(
                old_text.strip().splitlines()[0] if old_text.strip() else "",
                original.splitlines(),
                n=3,
                cutoff=0.5,
            )
            return {
                "error": "old_text not found in file",
                "hint": "closest lines: " + " | ".join(close)
                if close
                else "re-read the file and copy the text verbatim",
            }
        if count > 1:
            return {"error": f"old_text occurs {count} times; include more context so it is unique"}
        if old_text == new_text:
            return {"error": "old_text and new_text are identical; nothing to change"}
        updated = original.replace(old_text, new_text, 1)
        target.write_text(updated)
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                f"a/{path}",
                f"b/{path}",
            )
        )
        self.ctx.emit("edit", {"path": path, "diff": diff})
        return {"path": path, "diff": diff, "branch": self.ctx.record.fix_branch}

    @tool
    def write_file(self, path: str, content: str) -> dict:
        """Create or overwrite a file on the fix branch (e.g. a new regression test).

        Args:
            path: File path relative to the repository root.
            content: Full file content.
        """
        target, err = self._fix_path(path)
        if err:
            return err
        assert target is not None
        if ".git" in Path(path).parts:
            return {"error": "refusing to write inside .git"}
        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.ctx.emit("write", {"path": path, "created": not existed, "lines": content.count("\n") + 1})
        return {"path": path, "created": not existed, "branch": self.ctx.record.fix_branch}

    @tool
    def run_tests(self) -> dict:
        """Run the project's test command on the fix branch and return pass/fail with the output tail."""
        project = self.ctx.project
        if not project.test_command:
            return {"error": "the project has no test_command configured"}
        cwd = self.ctx.fix_worktree or self.ctx.repo
        started = time.time()
        try:
            proc = self._run_command(project.test_command, cwd, min(project.experiment_timeout_s, 900))
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": "tests timed out"}
        output = _tail((proc.stdout or "") + "\n" + (proc.stderr or ""), lines=40, chars=3500)
        result = {
            "passed": proc.returncode == 0,
            "exit_code": proc.returncode,
            "duration_s": round(time.time() - started, 2),
            "output_tail": output,
        }
        self.ctx.emit("tests", result)
        return result

    # ------------------------------------------------------------------------------------------
    # Deliver (consequential actions — guarded by the approval hook)
    # ------------------------------------------------------------------------------------------
    @tool
    def open_pull_request(self, title: str, body: str) -> dict:
        """Commit the fix branch and open a pull request against the main branch.

        This is a consequential action: it pauses for human approval before executing. Include the
        root cause, the evidence (metric before/after, experiments) and the fix in ``body`` (Markdown).

        Args:
            title: Pull request title.
            body: Pull request description in Markdown.
        """
        record = self.ctx.record
        wt = self.ctx.fix_worktree
        if not record.fix_branch or wt is None or not wt.exists():
            return {"error": "no fix branch: call start_fix and make changes first"}
        status = gitutil.run_git(wt, "status", "--porcelain")
        if status:
            try:
                gitutil.commit_all(wt, title)
            except gitutil.GitError as exc:
                return {"error": f"could not commit fix: {exc}"}
        base = self.ctx.project.main_branch
        try:
            changed = gitutil.run_git(self.ctx.repo, "diff", "--name-only", f"{base}...{record.fix_branch}")
        except gitutil.GitError:
            changed = ""
        if not changed.strip():
            return {
                "error": "the fix branch has no changes compared to the main branch; make and verify a fix first"
            }
        try:
            result = self.ctx.pr_client.open_pull_request(self.ctx.repo, record.fix_branch, base, title, body)
        except Exception as exc:  # network / auth problems -> keep the workflow alive
            result = {
                "mode": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "branch": record.fix_branch,
                "note": "The fix is committed on the branch; the PR could not be opened remotely.",
            }
        record.pull_request = result
        self.ctx.save()
        self.ctx.emit("pull_request", {**result, "title": title, "body": body})
        return result

    @tool
    def notify(self, channel: str, message: str) -> dict:
        """Post a short notification (e.g. to the team's Slack channel) summarizing the outcome.

        Args:
            channel: Channel name, e.g. "#ml-alerts".
            message: Plain-text message (keep it under ~600 characters).
        """
        try:
            result = self.ctx.notifier.send(channel, message)
        except Exception as exc:
            result = {"mode": "failed", "delivered": False, "error": f"{type(exc).__name__}: {exc}"}
        self.ctx.record.notifications.append({"channel": channel, "message": message, **result})
        self.ctx.save()
        self.ctx.emit("notification", {"channel": channel, "message": message, **result})
        return result

    @tool(context=True)
    def ask_human(self, question: str, tool_context: ToolContext, options: list[str] | None = None) -> dict:
        """Pause and ask the human a question when a real judgment call is needed.

        Use sparingly — only when the evidence is genuinely ambiguous (e.g. two candidate commits
        both regress the metric, or the experiment budget is exhausted before the culprit is isolated).

        Args:
            question: The question to ask.
            options: Optional list of suggested answers.
        """
        self.ctx.emit("question", {"question": question, "options": options or []})
        answer = tool_context.interrupt(
            "culprit-question", reason={"question": question, "options": options or []}
        )
        self.ctx.emit("answer", {"question": question, "answer": answer})
        return {"question": question, "answer": answer}
