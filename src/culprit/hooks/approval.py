"""Human approval for consequential tool calls, implemented with Strands interrupts.

When the agent decides to call a guarded tool (by default ``open_pull_request``), the hook raises
an interrupt *before* the tool runs. The agent invocation returns with ``stop_reason == "interrupt"``,
the service persists the run (the Strands session manager persists the conversation), and a human
approves or rejects from the web UI, the CLI or the AgentCore payload — possibly from a different
process, hours later. On resume the hook receives the decision and either lets the tool execute or
cancels it with an explanation the model can act on.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

from culprit.context import InvestigationContext

APPROVAL_INTERRUPT_PREFIX = "culprit-approval:"


def normalize_decision(response: Any) -> tuple[str, str]:
    """Accept ``"approve"``/``"y"``/``{"decision": "reject", "comment": "..."}`` etc. -> (decision, comment)."""
    comment = ""
    if isinstance(response, dict):
        comment = str(response.get("comment", "") or "")
        response = response.get("decision", response.get("response", ""))
    text = str(response or "").strip().lower()
    if text in {"approve", "approved", "yes", "y", "ok", "accept", "lgtm", "true"}:
        return "approve", comment
    return "reject", comment or (
        text if text not in {"reject", "rejected", "no", "n", "deny", "denied", "false"} else ""
    )


class ApprovalHook(HookProvider):
    def __init__(self, ctx: InvestigationContext, guarded_tools: Iterable[str], auto_approve: bool = False):
        self.ctx = ctx
        self.guarded_tools = set(guarded_tools)
        self.auto_approve = auto_approve

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name not in self.guarded_tools:
            return
        tool_input = event.tool_use.get("input", {})
        if self.auto_approve:
            self.ctx.emit("auto_approved", {"tool": name, "input": tool_input})
            return

        summary = self._summarize(name, tool_input)
        # Raises InterruptException the first time; returns the human's response when resumed.
        response = event.interrupt(
            f"{APPROVAL_INTERRUPT_PREFIX}{name}",
            reason={"tool": name, "input": tool_input, "summary": summary},
        )
        decision, comment = normalize_decision(response)
        self.ctx.record.human_responses.append({"tool": name, "decision": decision, "comment": comment})
        self.ctx.save()
        self.ctx.emit("decision", {"tool": name, "decision": decision, "comment": comment})
        if decision != "approve":
            event.cancel_tool = (
                f"The human REJECTED the '{name}' action"
                + (f" with the comment: {comment}" if comment else "")
                + ". Do not retry it. Leave the fix branch in place, explain the situation and finish with a summary."
            )

    @staticmethod
    def _summarize(name: str, tool_input: dict[str, Any]) -> str:
        if name == "open_pull_request":
            return f"Open pull request: {tool_input.get('title', '')}"
        if name == "notify":
            return f"Notify {tool_input.get('channel', '')}: {str(tool_input.get('message', ''))[:120]}"
        return f"{name}({json.dumps(tool_input)[:200]})"
