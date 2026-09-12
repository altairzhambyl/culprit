"""Runtime settings for Culprit.

Everything is configurable through environment variables (a ``.env`` file is loaded if present)
so the same code runs on a laptop, in CI, and inside an Amazon Bedrock AgentCore runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # no-op when there is no .env file


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Process-wide configuration, resolved from the environment once."""

    # --- model -------------------------------------------------------------------------------
    model_provider: str = field(
        default_factory=lambda: (_env("CULPRIT_MODEL_PROVIDER", "bedrock") or "bedrock").lower()
    )
    model_id: str | None = field(default_factory=lambda: _env("CULPRIT_MODEL_ID"))
    aws_region: str | None = field(default_factory=lambda: _env("AWS_REGION", _env("AWS_DEFAULT_REGION")))
    model_temperature: float = field(
        default_factory=lambda: float(_env("CULPRIT_MODEL_TEMPERATURE", "0.2") or 0.2)
    )
    model_max_tokens: int = field(default_factory=lambda: _env_int("CULPRIT_MODEL_MAX_TOKENS", 4096))

    # --- storage ---------------------------------------------------------------------------------
    runs_dir: Path = field(
        default_factory=lambda: Path(_env("CULPRIT_RUNS_DIR", "runs") or "runs").expanduser()
    )

    # --- guard-rails -----------------------------------------------------------------------------
    max_experiments: int = field(default_factory=lambda: _env_int("CULPRIT_MAX_EXPERIMENTS", 10))
    experiment_timeout_s: int = field(default_factory=lambda: _env_int("CULPRIT_EXPERIMENT_TIMEOUT", 600))
    max_tool_calls: int = field(default_factory=lambda: _env_int("CULPRIT_MAX_TOOL_CALLS", 60))
    max_repeated_calls: int = field(default_factory=lambda: _env_int("CULPRIT_MAX_REPEATED_CALLS", 3))
    approval_tools: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip()
            for t in (_env("CULPRIT_APPROVAL_TOOLS", "open_pull_request") or "").split(",")
            if t.strip()
        )
    )
    auto_approve: bool = field(default_factory=lambda: _env_bool("CULPRIT_AUTO_APPROVE", False))

    # --- integrations ------------------------------------------------------------------------------
    github_token: str | None = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    github_repo: str | None = field(default_factory=lambda: _env("GITHUB_REPO"))  # "owner/name"
    github_api_url: str = field(
        default_factory=lambda: _env("GITHUB_API_URL", "https://api.github.com") or ""
    )
    slack_webhook_url: str | None = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL"))

    # --- web ---------------------------------------------------------------------------------------
    host: str = field(default_factory=lambda: _env("CULPRIT_HOST", "127.0.0.1") or "127.0.0.1")
    port: int = field(default_factory=lambda: _env_int("CULPRIT_PORT", 8000))
    api_token: str | None = field(default_factory=lambda: _env("CULPRIT_API_TOKEN"))
    demo_only: bool = field(default_factory=lambda: _env_bool("CULPRIT_DEMO_ONLY", False))

    def describe_model(self) -> str:
        if self.model_provider == "scripted":
            return "scripted (deterministic offline model)"
        return f"{self.model_provider}:{self.model_id or 'default'}"


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    """Return the cached Settings, re-reading the environment when ``refresh`` is true."""
    global _settings
    if _settings is None or refresh:
        _settings = Settings()
    return _settings
