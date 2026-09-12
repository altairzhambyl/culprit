"""A deterministic Strands ``Model`` used as an integration-test fixture and offline demo.

Strands lets you plug in any model provider by implementing :class:`strands.models.Model`. The
``ScriptedModel`` below is a tiny "policy model": instead of calling an LLM it inspects the
conversation (the tool results so far) and decides the next tool call.

**What the bundled policy is:** a scripted replay of the churn demo's golden path. Its bisection
step is generic (it reads shas and metric values from tool results and narrows the range), but its
*explanation, fix and guard test are hard-coded for the churn repository's pandas.factorize bug*.
On any other repository it stops after isolating a commit and says so.

**What it is not:** evidence that Culprit can solve unseen regressions. That evidence can only come
from a real model — see ``culprit evaluate --scenario fraud`` and ``docs/validation.md``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, AsyncIterable, Callable, TypeVar

from pydantic import BaseModel
from strands.models import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

from culprit.demo import project as demo_project

T = TypeVar("T", bound=BaseModel)


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any]


@dataclass
class AssistantTurn:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


Policy = Callable[[Messages, list[ToolSpec] | None], AssistantTurn]


class ScriptedModel(Model):
    """Streams whatever the policy decides, in the Bedrock-style event format Strands expects."""

    def __init__(self, policy: Policy, model_id: str = "scripted/golden-path", max_turns: int = 80):
        self.policy = policy
        self.config = {"model_id": model_id}
        self.max_turns = max_turns
        self.turns = 0

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self.config

    async def structured_output(
        self, output_model: type[T], prompt: Messages, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        turn = self.policy(
            prompt, [{"name": output_model.__name__, "description": "", "inputSchema": {"json": {}}}]
        )
        for call in turn.tool_calls:
            if call.name == output_model.__name__:
                yield {"output": output_model.model_validate(call.input)}
                return
        raise ValueError("scripted policy did not produce structured output")

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.turns += 1
        if self.turns > self.max_turns:
            turn = AssistantTurn("Scripted policy stopped: too many turns without finishing (safety cap).")
        else:
            turn = self.policy(messages, tool_specs)
        yield {"messageStart": {"role": "assistant"}}
        if turn.text:
            yield {"contentBlockDelta": {"delta": {"text": turn.text}}}
            yield {"contentBlockStop": {}}
        for call in turn.tool_calls:
            tool_use_id = f"scripted-{uuid.uuid4().hex[:12]}"
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_use_id, "name": call.name}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(call.input)}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if turn.tool_calls else "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                "metrics": {"latencyMs": 0},
            }
        }


# ------------------------------------------------------------------------------------------------
# Conversation inspection helpers
# ------------------------------------------------------------------------------------------------


@dataclass
class ObservedCall:
    name: str
    input: dict[str, Any]
    result: Any  # parsed JSON when possible, else raw text
    status: str


def observed_calls(messages: Messages) -> list[ObservedCall]:
    """Pair every assistant toolUse with its toolResult, in conversation order."""
    uses: dict[str, tuple[str, dict[str, Any]]] = {}
    calls: list[ObservedCall] = []
    for message in messages:
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            if "toolUse" in block:
                tu = block["toolUse"]
                uses[tu["toolUseId"]] = (tu["name"], tu.get("input", {}))
            elif "toolResult" in block:
                tr = block["toolResult"]
                name, tool_input = uses.get(tr["toolUseId"], ("?", {}))
                text = "\n".join(c.get("text", "") for c in tr.get("content", []) if isinstance(c, dict))
                try:
                    parsed: Any = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    parsed = text
                calls.append(
                    ObservedCall(
                        name=name, input=tool_input, result=parsed, status=tr.get("status", "success")
                    )
                )
    return calls


def _has_tool(tool_specs: list[ToolSpec] | None, name: str) -> bool:
    return any(spec.get("name") == name for spec in (tool_specs or []))


def short_ref(ref: str) -> str:
    """Abbreviate hex shas to 7 chars; leave branch names untouched."""
    return ref[:7] if len(ref) >= 12 and all(c in "0123456789abcdef" for c in ref) else ref


# ------------------------------------------------------------------------------------------------
# Golden-path policy
# ------------------------------------------------------------------------------------------------


OFFLINE_LIMIT_MESSAGE = (
    "Bisection isolated commit {culprit} ({subject}) as the first bad commit. I am the offline scripted "
    "policy — an integration-test fixture that only knows how to explain and fix the churn demo's "
    "pandas.factorize regression. I cannot explain or fix this repository, so I am stopping here without a "
    "fix or a pull request. Run Culprit with a real model provider (e.g. CULPRIT_MODEL_PROVIDER=bedrock) "
    "to investigate it."
)


class GoldenPathPolicy:
    """Scripted replay of the churn golden path: calibrate, bisect, explain, fix, verify, guard, deliver, report.

    Only the bisection is generic; everything after it is specific to the churn demo repository.
    """

    @staticmethod
    def _is_churn_demo(show_call: ObservedCall) -> bool:
        text = json.dumps(show_call.result) if isinstance(show_call.result, dict) else str(show_call.result)
        return "pd.factorize" in text and "churn/features.py" in text

    def __init__(self, metric: str = "f1", channel: str = "#ml-alerts"):
        self.metric = metric
        self.channel = channel

    # -- helpers ------------------------------------------------------------------------------------------
    @staticmethod
    def _first(calls: list[ObservedCall], name: str) -> ObservedCall | None:
        return next((c for c in calls if c.name == name), None)

    @staticmethod
    def _last(calls: list[ObservedCall], name: str) -> ObservedCall | None:
        return next((c for c in reversed(calls) if c.name == name), None)

    def _value(self, call: ObservedCall) -> float | None:
        if isinstance(call.result, dict) and call.result.get("status") == "ok":
            return call.result.get("metrics", {}).get(self.metric)
        return None

    def _experiments(self, calls: list[ObservedCall]) -> dict[str, float]:
        """ref -> metric value for successful experiments (keyed by the ref the policy asked for)."""
        out: dict[str, float] = {}
        for c in calls:
            if c.name == "run_experiment":
                v = self._value(c)
                if v is not None:
                    out[c.input.get("ref", "")] = v
        return out

    # -- the policy -------------------------------------------------------------------------------------------
    def __call__(self, messages: Messages, tool_specs: list[ToolSpec] | None) -> AssistantTurn:
        calls = observed_calls(messages)

        if _has_tool(tool_specs, "IncidentReport"):
            return self._report(calls)

        history = self._first(calls, "get_metric_history")
        if history is None:
            return AssistantTurn(
                "Reading the recorded metric history to locate the regression window.",
                [ToolCall("get_metric_history", {"limit": 14})],
            )
        reg = history.result.get("detected_regression") if isinstance(history.result, dict) else None
        if not reg:
            return AssistantTurn(
                "No regression is visible in the metric history, so there is nothing to bisect."
            )
        good, bad = reg["last_good_commit"], reg["first_bad_commit"]

        commits_call = self._first(calls, "list_commits")
        if commits_call is None:
            return AssistantTurn(
                f"{self.metric} fell from {reg['baseline_value']} to {reg['regressed_value']} between {good[:7]} and {bad[:7]}. Listing the commits in that window.",
                [ToolCall("list_commits", {"good_ref": good, "bad_ref": bad})],
            )
        candidates = (
            [c["sha"] for c in commits_call.result.get("commits", [])]
            if isinstance(commits_call.result, dict)
            else []
        )
        subjects = (
            {c["sha"]: c["subject"] for c in commits_call.result.get("commits", [])}
            if isinstance(commits_call.result, dict)
            else {}
        )

        exps = self._experiments(calls)
        last_exp = self._last(calls, "run_experiment")
        if last_exp is not None and self._value(last_exp) is None:
            failures = 0
            for c in reversed(calls):
                if c.name != "run_experiment":
                    continue
                if self._value(c) is None:
                    failures += 1
                else:
                    break
            if failures >= 2 or "budget" in str(last_exp.result).lower():
                return AssistantTurn(
                    "I cannot run further experiments (" + str(last_exp.result)[:200] + "). "
                    f"Narrowest range established so far: candidates between {good[:7]} and {bad[:7]}; stopping here."
                )
        if good not in exps:
            return AssistantTurn(
                "Calibrating: measuring the last known-good commit with the fast config.",
                [ToolCall("run_experiment", {"ref": good, "note": "calibration: last good"})],
            )
        if bad not in exps:
            return AssistantTurn(
                "Calibrating: measuring the first known-bad commit with the fast config.",
                [ToolCall("run_experiment", {"ref": bad, "note": "calibration: first bad"})],
            )
        good_v, bad_v = exps[good], exps[bad]

        def is_bad(v: float) -> bool:
            return abs(v - bad_v) < abs(v - good_v)

        # Bisection over candidates (oldest -> newest). `lo` is the index of the newest known-good
        # candidate (-1 = the good boundary), `hi` the index of the oldest known-bad candidate.
        lo, hi = -1, len(candidates) - 1
        for i, sha in enumerate(candidates):
            if sha in exps:
                if is_bad(exps[sha]):
                    hi = min(hi, i)
                else:
                    lo = max(lo, i)
        if hi - lo > 1:
            mid = (lo + hi) // 2
            return AssistantTurn(
                f"Bisecting: {hi - lo - 1} untested commit(s) remain between good and bad; testing the middle one {candidates[mid][:7]}.",
                [
                    ToolCall(
                        "run_experiment",
                        {"ref": candidates[mid], "note": f"bisect step (range {lo + 1}..{hi})"},
                    )
                ],
            )
        culprit = candidates[hi]

        show = self._first(calls, "show_commit")
        if show is None:
            return AssistantTurn(
                f"Isolated the culprit: {culprit[:7]} — {subjects.get(culprit, '')}. Inspecting its diff.",
                [ToolCall("show_commit", {"ref": culprit})],
            )
        if not self._is_churn_demo(show):
            return AssistantTurn(
                OFFLINE_LIMIT_MESSAGE.format(culprit=culprit[:7], subject=subjects.get(culprit, ""))
            )
        if self._first(calls, "read_file") is None:
            return AssistantTurn(
                "The diff replaced explicit category maps with pandas.factorize. Reading the current file to prepare a fix.",
                [ToolCall("read_file", {"path": "churn/features.py", "ref": "HEAD"})],
            )
        fix = self._first(calls, "start_fix")
        if fix is None:
            return AssistantTurn(
                "Root cause: factorize assigns integer codes by order of first appearance, so training and evaluation frames get different encodings. Starting a fix branch.",
                [ToolCall("start_fix", {})],
            )
        branch = fix.result.get("branch", "fix") if isinstance(fix.result, dict) else "fix"
        if self._first(calls, "edit_file") is None:
            return AssistantTurn(
                "Restoring stable, explicit category codes shared by training and evaluation.",
                [
                    ToolCall(
                        "edit_file",
                        {
                            "path": "churn/features.py",
                            "old_text": demo_project.FIX_OLD_TEXT,
                            "new_text": demo_project.FIX_NEW_TEXT,
                        },
                    )
                ],
            )
        if branch not in exps:
            return AssistantTurn(
                "Verifying the fix by re-running the experiment on the fix branch.",
                [ToolCall("run_experiment", {"ref": branch, "note": "verify fix"})],
            )
        if self._first(calls, "write_file") is None:
            return AssistantTurn(
                f"{self.metric} recovered to {exps[branch]} (good calibration {good_v}). Adding a regression test so this cannot silently happen again.",
                [
                    ToolCall(
                        "write_file",
                        {"path": "tests/test_encoding_consistency.py", "content": demo_project.GUARD_TEST},
                    )
                ],
            )
        if self._first(calls, "run_tests") is None:
            return AssistantTurn(
                "Running the project's test suite on the fix branch.", [ToolCall("run_tests", {})]
            )
        pr = self._first(calls, "open_pull_request")
        if pr is None:
            title = "fix(features): restore stable categorical encoding (regression in nightly F1)"
            body = self._pr_body(
                reg, culprit, subjects.get(culprit, ""), good_v, bad_v, exps[branch], exps, candidates
            )
            return AssistantTurn(
                "Everything is verified. Opening a pull request for human review.",
                [ToolCall("open_pull_request", {"title": title, "body": body})],
            )
        rejected = pr.status == "error" or (isinstance(pr.result, str) and "REJECTED" in pr.result)
        if rejected:
            return AssistantTurn(
                f"The pull request was not opened (human decision). The fix remains on branch `{branch}` with the guard test; "
                f"culprit {culprit[:7]} ({subjects.get(culprit, '')}) regressed {self.metric} from {good_v} to {bad_v} in the fast config."
            )
        if self._first(calls, "notify") is None:
            url = pr.result.get("url", "") if isinstance(pr.result, dict) else ""
            msg = (
                f"Culprit found the nightly {self.metric} regression ({reg['baseline_value']} -> {reg['regressed_value']}): "
                f'commit {culprit[:7]} "{subjects.get(culprit, "")}" made categorical encoding order-dependent. '
                f"Fix verified ({self.metric} {bad_v} -> {exps[branch]} on the fast config) + guard test added. PR: {url}"
            )
            return AssistantTurn(
                "Notifying the team.", [ToolCall("notify", {"channel": self.channel, "message": msg})]
            )
        url = pr.result.get("url", "") if isinstance(pr.result, dict) else ""
        return AssistantTurn(
            f"Done. The regression was introduced by {culprit[:7]} ({subjects.get(culprit, '')}): switching to pandas.factorize made "
            f"integer codes depend on row order, so the model was evaluated on differently-encoded features. "
            f"Restoring explicit category maps brings {self.metric} back to {exps[branch]} (fast config; regressed value {bad_v}). "
            f"A regression test guards the encoding, and the pull request is open: {url}"
        )

    def _pr_body(
        self,
        reg: dict,
        culprit: str,
        subject: str,
        good_v: float,
        bad_v: float,
        fixed_v: float,
        exps: dict[str, float],
        candidates: list[str],
    ) -> str:
        def verdict(ref: str, v: float) -> str:
            if ref not in candidates and ref not in (reg["last_good_commit"], reg["first_bad_commit"]):
                return "fixed"
            return "bad" if abs(v - bad_v) < abs(v - good_v) else "good"

        rows = "\n".join(f"| `{short_ref(ref)}` | {v} | {verdict(ref, v)} |" for ref, v in exps.items())
        return (
            f"## Root cause\n\n"
            f"Commit `{culprit[:7]}` (*{subject}*) replaced the explicit `CATEGORY_MAPS` with `pd.factorize`, which assigns "
            f"integer codes by order of first appearance **per dataframe**. Training and evaluation frames therefore received "
            f"different encodings for `contract`, `plan`, `payment_method` and `region`; nothing crashed, the model just scored "
            f"garbage features at evaluation time.\n\n"
            f"## Evidence\n\n"
            f"- Nightly `{self.metric}`: **{reg['baseline_value']} → {reg['regressed_value']}** ({reg['last_good_run']} → {reg['first_bad_run']}).\n"
            f"- Bisection with the fast config (good ≈ {good_v}, bad ≈ {bad_v}):\n\n| ref | {self.metric} | verdict |\n|---|---|---|\n{rows}\n\n"
            f"## Fix\n\n"
            f"Restore stable, explicit category codes shared by training and evaluation (unknown values map to -1, keeping the "
            f"robustness the refactor was after). Fast-config `{self.metric}` recovers to **{fixed_v}**.\n\n"
            f"## Guard\n\n"
            f"`tests/test_encoding_consistency.py` asserts that categorical encoding is independent of row order and of the split.\n\n"
            f"---\n*Opened by Culprit after human approval.*"
        )

    def _report(self, calls: list[ObservedCall]) -> AssistantTurn:
        history = self._first(calls, "get_metric_history")
        reg = (
            history.result.get("detected_regression", {})
            if history and isinstance(history.result, dict)
            else {}
        )
        commits_call = self._first(calls, "list_commits")
        commits = (
            commits_call.result.get("commits", [])
            if commits_call and isinstance(commits_call.result, dict)
            else []
        )
        candidates = [c["sha"] for c in commits]
        subjects = {c["sha"]: c["subject"] for c in commits}
        exps = self._experiments(calls)
        good, bad = reg.get("last_good_commit", ""), reg.get("first_bad_commit", "")
        good_v, bad_v = exps.get(good, 0.0), exps.get(bad, 0.0)
        fix = self._first(calls, "start_fix")
        branch = fix.result.get("branch", "") if fix and isinstance(fix.result, dict) else ""
        culprit = ""
        for sha in candidates:
            v = exps.get(sha)
            if v is not None and abs(v - bad_v) < abs(v - good_v):
                culprit = sha
                break
        if not culprit and candidates:
            culprit = candidates[-1]
        pr = self._first(calls, "open_pull_request")
        pr_url = pr.result.get("url") if pr and isinstance(pr.result, dict) else None
        show = self._first(calls, "show_commit")
        if show is None or not self._is_churn_demo(show):
            report = {
                "title": f"{self.metric} regression isolated to commit {culprit[:7]} (offline policy: no fix)",
                "metric": self.metric,
                "baseline_value": reg.get("baseline_value", good_v),
                "regressed_value": reg.get("regressed_value", bad_v),
                "recovered_value": None,
                "culprit_commit": culprit[:7],
                "culprit_subject": subjects.get(culprit, ""),
                "root_cause": (
                    "Not determined. The offline scripted policy only knows the churn demo's pandas.factorize bug; "
                    "it isolated the first bad commit by bisection but cannot explain or fix this repository."
                ),
                "evidence": [
                    {
                        "ref": short_ref(ref),
                        "metric_value": v,
                        "verdict": "bad" if abs(v - bad_v) < abs(v - good_v) else "good",
                    }
                    for ref, v in exps.items()
                ],
                "fix_summary": "No fix attempted (offline policy). Run with a real model provider.",
                "files_changed": [],
                "guard_test": None,
                "pull_request": None,
                "confidence": "low",
                "follow_ups": ["Re-run this investigation with CULPRIT_MODEL_PROVIDER=bedrock."],
            }
            return AssistantTurn(None, [ToolCall("IncidentReport", report)])
        evidence = []
        for ref, v in exps.items():
            verdict = "fixed" if ref == branch else ("bad" if abs(v - bad_v) < abs(v - good_v) else "good")
            evidence.append({"ref": short_ref(ref), "metric_value": v, "verdict": verdict})
        report = {
            "title": f"{self.metric} regression caused by order-dependent categorical encoding",
            "metric": self.metric,
            "baseline_value": reg.get("baseline_value", good_v),
            "regressed_value": reg.get("regressed_value", bad_v),
            "recovered_value": exps.get(branch),
            "culprit_commit": culprit[:7],
            "culprit_subject": subjects.get(culprit, ""),
            "root_cause": (
                "The refactor replaced explicit category maps with pandas.factorize, which assigns integer codes by order of first "
                "appearance in each dataframe. Training and evaluation frames were encoded differently, so the model scored "
                "mismatched features at evaluation time. No error was raised; the metric silently dropped."
            ),
            "evidence": evidence,
            "fix_summary": "Restore explicit, shared category codes (unknown values map to -1). Verified by re-running the experiment on the fix branch.",
            "files_changed": ["churn/features.py", "tests/test_encoding_consistency.py"],
            "guard_test": "tests/test_encoding_consistency.py",
            "pull_request": pr_url,
            "confidence": "high",
            "follow_ups": [
                "Add the fast-config metric check to CI so encoding regressions fail the pull request, not the nightly.",
                "Consider fitting a single encoder on training data and persisting it with the model artifact.",
            ],
        }
        return AssistantTurn(None, [ToolCall("IncidentReport", report)])
