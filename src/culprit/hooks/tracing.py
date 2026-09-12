"""Observability: log every model and tool call, and feed the live UI timeline."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from strands.hooks import (
    AfterInvocationEvent,
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

from culprit.context import InvestigationContext

log = logging.getLogger("culprit.trace")

# Tool inputs that would bloat the timeline are abbreviated for display (the full trace keeps them).
_LONG_INPUT_FIELDS = {"content", "body", "new_text", "old_text", "message"}


def _short(value: Any, limit: int = 160) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"… (+{len(value) - limit} chars)"
    return value


def _display_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    return {k: (_short(v) if k in _LONG_INPUT_FIELDS else v) for k, v in tool_input.items()}


def _result_text(result: dict[str, Any]) -> str:
    parts = []
    for block in result.get("content", []):
        if "text" in block:
            parts.append(block["text"])
        elif "json" in block:
            parts.append(json.dumps(block["json"]))
    return "\n".join(parts)


class TraceHook(HookProvider):
    def __init__(self, ctx: InvestigationContext):
        self.ctx = ctx
        self._tool_started: dict[str, float] = {}
        self._model_started: float | None = None

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self.before_invocation)
        registry.add_callback(AfterInvocationEvent, self.after_invocation)
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)
        registry.add_callback(MessageAddedEvent, self.message_added)

    # -- invocation ---------------------------------------------------------------------------------
    def before_invocation(self, event: BeforeInvocationEvent) -> None:
        self.ctx.trace({"event": "invocation_start", "agent": event.agent.name})

    def after_invocation(self, event: AfterInvocationEvent) -> None:
        stop = event.result.stop_reason if event.result else None
        self.ctx.trace({"event": "invocation_end", "stop_reason": stop})

    # -- model ----------------------------------------------------------------------------------------
    def before_model(self, event: BeforeModelCallEvent) -> None:
        self._model_started = time.time()
        self.ctx.record.model_calls += 1
        self.ctx.trace({"event": "model_call_start", "messages": len(event.agent.messages)})
        self.ctx.emit("thinking", {"call": self.ctx.record.model_calls})

    def after_model(self, event: AfterModelCallEvent) -> None:
        duration = round(time.time() - self._model_started, 2) if self._model_started else None
        stop_reason = event.stop_response.stop_reason if event.stop_response else None
        entry: dict[str, Any] = {
            "event": "model_call_end",
            "duration_s": duration,
            "stop_reason": stop_reason,
        }
        if event.exception:
            entry["exception"] = repr(event.exception)
            self.ctx.emit("model_error", {"error": str(event.exception), "retry": event.retry})
        self.ctx.trace(entry)

    # -- tools --------------------------------------------------------------------------------------------
    def before_tool(self, event: BeforeToolCallEvent) -> None:
        tool_use = event.tool_use
        self._tool_started[tool_use["toolUseId"]] = time.time()
        self.ctx.record.tool_calls += 1
        self.ctx.trace({"event": "tool_start", "tool": tool_use["name"], "input": tool_use.get("input", {})})
        log.info("tool %s %s", tool_use["name"], json.dumps(_display_input(tool_use.get("input", {})))[:300])
        self.ctx.emit(
            "tool_start",
            {
                "id": tool_use["toolUseId"],
                "tool": tool_use["name"],
                "input": _display_input(tool_use.get("input", {})),
            },
        )

    def after_tool(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use
        started = self._tool_started.pop(tool_use["toolUseId"], None)
        duration = round(time.time() - started, 2) if started else None
        text = _result_text(event.result)
        status = event.result.get("status", "success")
        self.ctx.trace(
            {
                "event": "tool_end",
                "tool": tool_use["name"],
                "status": status,
                "duration_s": duration,
                "result": text[:4000],
                "cancelled": event.cancel_message,
            }
        )
        self.ctx.emit(
            "tool_end",
            {
                "id": tool_use["toolUseId"],
                "tool": tool_use["name"],
                "status": status,
                "duration_s": duration,
                "result_preview": _short(text, 700),
                "cancelled": event.cancel_message,
            },
        )

    # -- messages -------------------------------------------------------------------------------------------
    def message_added(self, event: MessageAddedEvent) -> None:
        message = event.message
        if message.get("role") != "assistant":
            return
        texts = [
            block["text"]
            for block in message.get("content", [])
            if isinstance(block, dict) and "text" in block
        ]
        thought = "\n".join(t.strip() for t in texts if t.strip())
        if thought:
            self.ctx.emit("thought", {"text": thought})
