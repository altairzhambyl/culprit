"""Notification adapters: Slack incoming webhook, with a local fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import httpx

from culprit.models import utcnow_iso


class Notifier(Protocol):
    mode: str

    def send(self, channel: str, text: str) -> dict[str, Any]: ...


class LocalNotifier:
    """Appends notifications to ``notifications.jsonl`` in the run directory."""

    mode = "local"

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / "notifications.jsonl"

    def send(self, channel: str, text: str) -> dict[str, Any]:
        record = {"ts": utcnow_iso(), "channel": channel, "text": text}
        with self.path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        return {"mode": self.mode, "channel": channel, "path": str(self.path.resolve()), "delivered": True}


class SlackNotifier:
    mode = "slack"

    def __init__(self, webhook_url: str, timeout: float = 15.0):
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send(self, channel: str, text: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.webhook_url, json={"text": f"*{channel}*\n{text}"})
        if resp.status_code >= 300:
            raise RuntimeError(f"Slack webhook returned {resp.status_code}: {resp.text[:300]}")
        return {"mode": self.mode, "channel": channel, "delivered": True}


def select_notifier(run_dir: Path, webhook_url: str | None) -> Notifier:
    return SlackNotifier(webhook_url) if webhook_url else LocalNotifier(run_dir)
