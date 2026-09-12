"""Assemble the Culprit investigator: one Strands ``Agent`` with tools, hooks, session and model."""

from __future__ import annotations

import logging
from typing import Any

from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.models import Model
from strands.session.file_session_manager import FileSessionManager
from strands.tools.executors import SequentialToolExecutor

from culprit.agents.prompts import render_system_prompt
from culprit.agents.scripted_model import GoldenPathPolicy, ScriptedModel
from culprit.context import InvestigationContext
from culprit.hooks import ApprovalHook, BudgetHook, LoopGuardHook, TraceHook
from culprit.settings import Settings
from culprit.tools.investigation import InvestigationTools

log = logging.getLogger(__name__)


def make_model(settings: Settings, metric: str = "f1") -> Model:
    """Instantiate the configured model provider.

    ``bedrock`` (default) uses Amazon Bedrock via boto3 credentials; ``anthropic`` / ``openai`` use the
    matching Strands extras and API keys; ``scripted`` is the deterministic offline model.
    """
    provider = settings.model_provider
    if provider == "scripted":
        return ScriptedModel(GoldenPathPolicy(metric=metric))
    if provider == "bedrock":
        from strands.models.bedrock import DEFAULT_BEDROCK_MODEL_ID, BedrockModel

        kwargs: dict[str, Any] = {
            "model_id": settings.model_id or DEFAULT_BEDROCK_MODEL_ID,
            "temperature": settings.model_temperature,
            "max_tokens": settings.model_max_tokens,
        }
        if settings.aws_region:
            kwargs["region_name"] = settings.aws_region
        return BedrockModel(**kwargs)
    if provider == "anthropic":
        from strands.models.anthropic import AnthropicModel

        return AnthropicModel(
            model_id=settings.model_id or "claude-sonnet-4-5",
            max_tokens=settings.model_max_tokens,
            params={"temperature": settings.model_temperature},
        )
    if provider == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            model_id=settings.model_id or "gpt-4.1", params={"temperature": settings.model_temperature}
        )
    raise ValueError(
        f"unknown CULPRIT_MODEL_PROVIDER '{provider}' (expected bedrock, anthropic, openai or scripted)"
    )


def build_agent(ctx: InvestigationContext, model: Model | None = None) -> Agent:
    """Create the investigator agent for a run.

    Re-creating the agent for the same run (e.g. in a new process to resume after an approval) restores
    the conversation and pending interrupt from the file-backed session.
    """
    settings = ctx.settings
    tools = InvestigationTools(ctx)
    model = model or make_model(settings, metric=ctx.project.metric)
    session = FileSessionManager(session_id=ctx.record.run_id, storage_dir=str(ctx.run_dir / "session"))
    agent = Agent(
        model=model,
        system_prompt=render_system_prompt(ctx.project, settings.max_experiments),
        tools=tools.all(),
        hooks=[
            TraceHook(ctx),
            BudgetHook(ctx, max_experiments=settings.max_experiments),
            LoopGuardHook(
                ctx, max_repeats=settings.max_repeated_calls, max_tool_calls=settings.max_tool_calls
            ),
            ApprovalHook(ctx, guarded_tools=settings.approval_tools, auto_approve=settings.auto_approve),
        ],
        session_manager=session,
        # An investigation is ~15-40 tool calls (2 messages each). The default 40-message window would drop
        # the metric history and the task from context mid-run; keep the first message pinned and the
        # window large. Tool calls are capped separately by the loop guard.
        conversation_manager=SlidingWindowConversationManager(window_size=200, pin_first=1),
        tool_executor=SequentialToolExecutor(),  # experiments share a git repo; run tools one at a time
        callback_handler=None,  # the trace hook feeds the UI/CLI instead of printing to stdout
        agent_id="culprit-investigator",
        name="Culprit",
        description="Finds, explains and fixes ML metric regressions by bisecting commits with real experiments.",
        trace_attributes={"culprit.run_id": ctx.record.run_id, "culprit.metric": ctx.project.metric},
    )
    return agent
