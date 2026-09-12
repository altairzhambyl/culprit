"""Shared fixtures: a generated demo repository and an offline (scripted) Culprit configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from culprit.demo.generator import generate
from culprit.settings import Settings


@pytest.fixture(scope="session")
def demo_repo(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Generate the demo repository once per test session (it trains a few tiny models)."""
    dest = tmp_path_factory.mktemp("repo") / "churn-model"
    return generate(dest, quiet=True)


@pytest.fixture
def offline_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings that never touch the network: scripted model, local PR/notification adapters."""
    monkeypatch.setenv("CULPRIT_MODEL_PROVIDER", "scripted")
    monkeypatch.setenv("CULPRIT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("CULPRIT_AUTO_APPROVE", raising=False)
    monkeypatch.delenv("CULPRIT_MAX_EXPERIMENTS", raising=False)
    return Settings()


@pytest.fixture
def manager(offline_settings: Settings):
    from culprit.service import RunManager

    return RunManager(offline_settings)


@pytest.fixture(autouse=True)
def _quiet_strands_logs():
    import logging

    logging.getLogger("strands").setLevel(logging.WARNING)
    yield


def pytest_configure(config: pytest.Config) -> None:
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
