"""Pull-request adapters: a real GitHub client and a local fallback that never needs credentials."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Protocol

import httpx

from culprit import gitutil

log = logging.getLogger(__name__)


class PullRequestClient(Protocol):
    mode: str

    def open_pull_request(
        self, repo: Path, branch: str, base: str, title: str, body: str
    ) -> dict[str, Any]: ...


def parse_github_repo(remote: str | None) -> str | None:
    """Turn ``git@github.com:owner/name.git`` or ``https://github.com/owner/name`` into ``owner/name``."""
    if not remote:
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?/?$", remote)
    return f"{m.group(1)}/{m.group(2)}" if m else None


class LocalPullRequests:
    """Writes the PR as a reviewable Markdown file (title, body, full diff) next to the run.

    Used automatically when no GitHub token is configured, so the whole workflow can be demonstrated
    on a laptop or in CI without touching the network.
    """

    mode = "local"

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)

    def open_pull_request(self, repo: Path, branch: str, base: str, title: str, body: str) -> dict[str, Any]:
        diff = gitutil.run_git(repo, "diff", "--no-color", f"{base}...{branch}")
        path = self.run_dir / "pull_request.md"
        path.write_text(
            f"# {title}\n\n`{branch}` → `{base}`\n\n{body}\n\n---\n\n## Diff\n\n```diff\n{diff}\n```\n"
        )
        return {
            "mode": self.mode,
            "url": path.resolve().as_uri(),
            "path": str(path.resolve()),
            "branch": branch,
            "base": base,
            "title": title,
            "note": "No GITHUB_TOKEN configured: the pull request was written locally and the fix branch exists in the repo.",
        }


class GitHubPullRequests:
    """Pushes the branch and opens a PR through the GitHub REST API."""

    mode = "github"

    def __init__(
        self, token: str, repo_slug: str, api_url: str = "https://api.github.com", timeout: float = 30.0
    ):
        self.token = token
        self.repo_slug = repo_slug
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def open_pull_request(self, repo: Path, branch: str, base: str, title: str, body: str) -> dict[str, Any]:
        gitutil.run_git(repo, "push", "-u", "origin", branch, timeout=180)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        payload = {"title": title, "body": body, "head": branch, "base": base}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(f"{self.api_url}/repos/{self.repo_slug}/pulls", json=payload, headers=headers)
        if resp.status_code >= 300:
            raise RuntimeError(f"GitHub API returned {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        return {
            "mode": self.mode,
            "url": data.get("html_url"),
            "number": data.get("number"),
            "branch": branch,
            "base": base,
            "title": title,
        }


def select_pull_request_client(
    repo: Path, run_dir: Path, token: str | None, repo_slug: str | None, api_url: str
) -> PullRequestClient:
    """GitHub when we have a token and can figure out the repository, otherwise the local adapter."""
    slug = repo_slug or parse_github_repo(gitutil.remote_url(repo))
    if token and slug:
        return GitHubPullRequests(token=token, repo_slug=slug, api_url=api_url)
    if token and not slug:
        log.warning(
            "GITHUB_TOKEN is set but the repository has no GitHub remote (set GITHUB_REPO=owner/name to force); "
            "using the local pull-request adapter"
        )
    return LocalPullRequests(run_dir)
