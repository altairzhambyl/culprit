"""Thin, well-behaved wrapper around the ``git`` CLI used by tools and the demo generator."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass
class CommitInfo:
    sha: str
    short: str
    author: str
    date: str
    subject: str
    files: list[str]

    def to_dict(self) -> dict:
        return {
            "sha": self.sha,
            "short": self.short,
            "author": self.author,
            "date": self.date,
            "subject": self.subject,
            "files": self.files,
        }


def run_git(
    repo: Path | str, *args: str, check: bool = True, env: dict | None = None, timeout: int = 120
) -> str:
    """Run a git command inside ``repo`` and return stdout (stripped)."""
    full_env = {**os.environ, **(env or {})}
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=full_env,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def is_repo(path: Path | str) -> bool:
    try:
        return run_git(path, "rev-parse", "--is-inside-work-tree") == "true"
    except (GitError, FileNotFoundError, NotADirectoryError):
        return False


def resolve_sha(repo: Path | str, ref: str) -> str:
    try:
        return run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    except GitError as exc:
        raise GitError(f"unknown ref '{ref}'") from exc


def short_sha(repo: Path | str, ref: str) -> str:
    return run_git(repo, "rev-parse", "--short", ref)


def current_branch(repo: Path | str) -> str:
    return run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def log_range(repo: Path | str, good_ref: str, bad_ref: str) -> list[CommitInfo]:
    """Commits reachable from ``bad_ref`` but not ``good_ref``, oldest first."""
    sep = "\x1f"
    out = run_git(
        repo,
        "log",
        "--reverse",
        f"--format=%H{sep}%h{sep}%an{sep}%ad{sep}%s",
        "--date=short",
        "--name-only",
        f"{good_ref}..{bad_ref}",
    )
    commits: list[CommitInfo] = []
    current: CommitInfo | None = None
    for line in out.splitlines():
        if sep in line:
            sha, short, author, date, subject = line.split(sep)
            current = CommitInfo(sha=sha, short=short, author=author, date=date, subject=subject, files=[])
            commits.append(current)
        elif line.strip() and current is not None:
            current.files.append(line.strip())
    return commits


def show_commit(repo: Path | str, ref: str, max_chars: int = 6000) -> dict:
    """Commit metadata plus a (possibly truncated) unified diff."""
    sha = resolve_sha(repo, ref)
    meta = run_git(repo, "show", "-s", "--format=%h%n%an%n%ad%n%s%n%b", "--date=short", sha)
    lines = meta.splitlines()
    short, author, date, subject = lines[0], lines[1], lines[2], lines[3]
    body = "\n".join(lines[4:]).strip()
    stat = run_git(repo, "show", "--stat", "--format=", sha)
    diff = run_git(repo, "show", "--format=", "--no-color", "--unified=3", sha)
    truncated = len(diff) > max_chars
    if truncated:
        diff = diff[:max_chars] + f"\n... [diff truncated at {max_chars} chars; use read_file for full files]"
    return {
        "sha": sha,
        "short": short,
        "author": author,
        "date": date,
        "subject": subject,
        "body": body,
        "stat": stat,
        "diff": diff,
        "diff_truncated": truncated,
    }


def read_file_at(repo: Path | str, path: str, ref: str = "HEAD") -> str:
    try:
        return run_git(repo, "show", f"{ref}:{path}")
    except GitError as exc:
        raise GitError(f"cannot read '{path}' at '{ref}': {exc}") from exc


def add_worktree(repo: Path | str, dest: Path, ref: str, new_branch: str | None = None) -> Path:
    dest = Path(dest).resolve()  # git resolves relative paths against the repo, not our cwd
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    if new_branch:
        run_git(repo, "worktree", "add", "-b", new_branch, str(dest), ref)
    else:
        run_git(repo, "worktree", "add", "--detach", str(dest), ref)
    return dest


def remove_worktree(repo: Path | str, dest: Path) -> None:
    try:
        run_git(repo, "worktree", "remove", "--force", str(dest))
    except GitError:
        pass


def prune_worktrees(repo: Path | str) -> None:
    try:
        run_git(repo, "worktree", "prune")
    except GitError:
        pass


def commit_all(repo: Path | str, message: str, author: str = "Culprit <culprit@example.com>") -> str:
    run_git(repo, "add", "-A")
    run_git(
        repo,
        "-c",
        "user.name=Culprit",
        "-c",
        "user.email=culprit@example.com",
        "commit",
        "-q",
        "-m",
        message,
        f"--author={author}",
    )
    return run_git(repo, "rev-parse", "HEAD")


def diff_worktree(worktree: Path | str) -> str:
    """Unified diff of uncommitted changes (including new files) in a worktree."""
    run_git(worktree, "add", "-N", "-A")  # make new files show up in the diff
    return run_git(worktree, "diff", "--no-color")


def remote_url(repo: Path | str) -> str | None:
    try:
        return run_git(repo, "remote", "get-url", "origin") or None
    except GitError:
        return None
