"""Guard-rail: cap the number of experiments an investigation may run.

Experiments are the expensive part of an investigation (they train models). The budget hook counts
``run_experiment`` calls and cancels the tool once the budget is spent, telling the model how to
proceed (conclude with the evidence gathered, or ask the human).
"""

from __future__ import annotations

from typing import Any

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from culprit.context import InvestigationContext


class BudgetHook(HookProvider):
    def __init__(self, ctx: InvestigationContext, max_experiments: int, tool_name: str = "run_experiment"):
        self.ctx = ctx
        self.max_experiments = max_experiments
        self.tool_name = tool_name

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)
        registry.add_callback(AfterToolCallEvent, self.after_tool_call)

    def _spent(self) -> int:
        return sum(1 for e in self.ctx.record.experiments if not e.cached)

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        if event.tool_use["name"] != self.tool_name:
            return
        spent = self._spent()
        if spent >= self.max_experiments:
            self.ctx.emit("budget_exhausted", {"spent": spent, "max": self.max_experiments})
            event.cancel_tool = (
                f"Experiment budget exhausted ({spent}/{self.max_experiments}). Do not run more experiments: "
                "either conclude from the evidence you already have (report the narrowest range of commits) "
                "or use ask_human to decide how to proceed."
            )

    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        if event.tool_use["name"] != self.tool_name:
            return
        self.ctx.emit("budget", {"spent": self._spent(), "max": self.max_experiments})
