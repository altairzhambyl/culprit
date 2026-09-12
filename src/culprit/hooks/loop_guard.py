"""Guard-rail against runaway agents: identical repeated tool calls and unbounded tool use.

Real models occasionally re-issue the same call when a result surprises them, or keep exploring past
the point of usefulness. Both waste budget (experiments are expensive) and can prevent a run from ever
finishing. This hook cancels a tool call that repeats an identical earlier call more than
``max_repeats`` times, and cancels everything once ``max_tool_calls`` is reached — in both cases with
an instruction the model can act on (use the earlier result / wrap up and report).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

from culprit.context import InvestigationContext


class LoopGuardHook(HookProvider):
    def __init__(self, ctx: InvestigationContext, max_repeats: int = 3, max_tool_calls: int = 60):
        self.ctx = ctx
        self.max_repeats = max_repeats
        self.max_tool_calls = max_tool_calls
        self._seen: dict[str, int] = {}
        self._calls = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)

    @staticmethod
    def _fingerprint(name: str, tool_input: dict[str, Any]) -> str:
        payload = json.dumps({"tool": name, "input": tool_input}, sort_keys=True, default=str)
        return hashlib.sha1(payload.encode()).hexdigest()

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use["name"]
        if name == "IncidentReport":  # the structured-output tool is never a loop
            return
        self._calls += 1
        if self._calls > self.max_tool_calls:
            self.ctx.emit("loop_guard", {"reason": "max_tool_calls", "tool": name, "calls": self._calls})
            event.cancel_tool = (
                f"Tool-call limit reached ({self.max_tool_calls}). Do not call more tools: summarize what you "
                "established (culprit or narrowest commit range, evidence, whether a fix was verified) and finish."
            )
            return
        key = self._fingerprint(name, event.tool_use.get("input", {}))
        self._seen[key] = self._seen.get(key, 0) + 1
        if self._seen[key] > self.max_repeats:
            self.ctx.emit("loop_guard", {"reason": "repeated_call", "tool": name, "repeats": self._seen[key]})
            event.cancel_tool = (
                f"You have already called {name} with exactly these arguments {self._seen[key] - 1} times; the "
                "result will not change. Use the earlier result, change the arguments, or move on."
            )
