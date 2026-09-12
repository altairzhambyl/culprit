"""Unit tests for the approval decision parsing and the scripted Strands model provider."""

import pytest
from pydantic import BaseModel
from strands import Agent, tool

from culprit.agents.scripted_model import AssistantTurn, ScriptedModel, ToolCall, observed_calls
from culprit.hooks.approval import normalize_decision


@pytest.mark.parametrize(
    "response, expected",
    [
        ("approve", ("approve", "")),
        ("y", ("approve", "")),
        ("LGTM", ("approve", "")),
        ("no", ("reject", "")),
        ("please wait for QA", ("reject", "please wait for qa")),
        ({"decision": "approve", "comment": "ship"}, ("approve", "ship")),
        ({"decision": "reject", "comment": "not yet"}, ("reject", "not yet")),
        ({"response": "yes"}, ("approve", "")),
        (None, ("reject", "")),
    ],
)
def test_normalize_decision(response, expected):
    assert normalize_decision(response) == expected


def test_scripted_model_drives_a_strands_agent_loop():
    seen: list[int] = []

    @tool
    def double(x: int) -> dict:
        """Double a number.

        Args:
            x: the number
        """
        seen.append(x)
        return {"result": x * 2}

    def policy(messages, tool_specs):
        calls = observed_calls(messages)
        if not calls:
            return AssistantTurn("doubling", [ToolCall("double", {"x": 21})])
        return AssistantTurn(f"the answer is {calls[-1].result['result']}")

    agent = Agent(model=ScriptedModel(policy), tools=[double], callback_handler=None)
    result = agent("go")
    assert result.stop_reason == "end_turn"
    assert seen == [21]
    assert "42" in str(result)


def test_scripted_model_structured_output_via_tool_spec():
    class Answer(BaseModel):
        value: int
        note: str

    def policy(messages, tool_specs):
        names = [s["name"] for s in (tool_specs or [])]
        if "Answer" in names:
            return AssistantTurn(None, [ToolCall("Answer", {"value": 7, "note": "seven"})])
        return AssistantTurn("hello")

    agent = Agent(model=ScriptedModel(policy), callback_handler=None)
    result = agent("what is the answer?", structured_output_model=Answer)
    assert isinstance(result.structured_output, Answer)
    assert result.structured_output.value == 7


def test_scripted_model_safety_cap_stops_runaway_policies():
    @tool
    def noop() -> dict:
        """Do nothing."""
        return {"ok": True}

    agent = Agent(
        model=ScriptedModel(lambda m, t: AssistantTurn("again", [ToolCall("noop", {})]), max_turns=3),
        tools=[noop],
        callback_handler=None,
    )
    result = agent("loop forever")
    assert result.stop_reason == "end_turn"
    assert "safety cap" in str(result)
