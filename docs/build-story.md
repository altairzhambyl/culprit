# Agents for Humans: Building Culprit, an ML regression investigator with Strands Agents

*Draft for builder.aws.com. Title contains "Agents for Humans" as the hackathon bonus requires.*

---

Every ML team has the same bad Monday. The nightly evaluation dropped, five pull requests merged
since the last good run, and someone has to find out which one broke the model. The ritual is always
the same: find the window, check out commits one by one, retrain, compare numbers, read diffs, write
a fix, prove it, write it up. It's repetitive, it's urgent, and it usually costs a senior engineer
half a day.

For the **Agents for Humans** hackathon I built [Culprit](https://github.com/<your-org>/culprit): a
Strands Agents SDK agent that does this investigation end to end — it bisects the commits by actually
re-running the experiments, explains the mechanism, writes and verifies a fix, adds a guard test,
and asks a human exactly once before opening the pull request. Culprit doesn't guess which commit
broke your model; it reruns the experiments and proves it. This post is about how the build went,
what Strands made easy, how I keep myself honest about what the agent can and cannot do yet, and what
I'd tell someone building a "does real work" agent.

## Start from the workflow, not the model

I wrote down the steps a careful engineer follows before writing a line of agent code:

1. Establish the window: last good run, first bad run, the commits in between.
2. Calibrate: what do "good" and "bad" look like under the *fast* experiment config? (Nightly numbers
   come from the full config; you can't compare a 20-second smoke run to them directly.)
3. Bisect with real experiments — diffs suggest hypotheses, measurements confirm them.
4. Explain the mechanism from the culprit's diff and the current code.
5. Fix on a branch, re-run to prove recovery, add a regression test, run the suite.
6. Get a human to approve the PR. Notify the team. Write the post-mortem.

That list became the system prompt's *method* section and, almost one-to-one, the tool set:
`get_metric_history`, `list_commits`, `show_commit`, `read_file`, `run_experiment`, `start_fix`,
`edit_file`, `write_file`, `run_tests`, `open_pull_request`, `notify`, `ask_human`. Twelve tools,
each small and honest — they return data and errors, never conclusions.

```python
@tool
def run_experiment(self, ref: str, config: str | None = None, note: str = "") -> dict:
    """Check out a commit in an isolated worktree, run the project's training/evaluation
    command and return its metrics. Results are cached per (commit, config)."""
```

Binding tools to an *investigation context* (repo path, run directory, adapters, run record) was
the first thing Strands made pleasant: `@tool` works on instance methods, so the tool set is just a
class with state, and the agent gets `tools=InvestigationTools(ctx).all()`.

## The agent loop is the model's, not mine

The temptation with a bisection task is to hard-code the algorithm and let the model narrate. I
didn't. The system prompt describes the method; the model decides which commit to test next, how to
classify a result (closer to the good or the bad calibration value?), what the root cause is, and
what the fix should be. The tools deliberately return data and errors, never conclusions, so that
whatever intelligence shows up in the run is the model's.

Strands' `SequentialToolExecutor` mattered here: experiments share a git repository, and the default
concurrent executor would happily run two checkouts at once.

## Human approval with interrupts — the part I'd show a friend

Opening a pull request is consequential, so it needs a human. Strands has a first-class primitive
for exactly this: **interrupts**. Culprit's approval is one small hook:

```python
class ApprovalHook(HookProvider):
    def register_hooks(self, registry, **kwargs):
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)

    def before_tool_call(self, event: BeforeToolCallEvent):
        if event.tool_use["name"] not in self.guarded_tools:
            return
        response = event.interrupt("culprit-approval:open_pull_request",
                                   reason={"tool": ..., "input": event.tool_use["input"]})
        decision, comment = normalize_decision(response)
        if decision != "approve":
            event.cancel_tool = f"The human REJECTED the action: {comment}. Do not retry; wrap up."
```

The first time through, `event.interrupt` stops the agent loop and `agent(...)` returns with
`stop_reason == "interrupt"`. Because the agent uses a `FileSessionManager`, a **different process**
can rebuild it with the same session id and call `agent([{"interruptResponse": {...}}])`. The hook
runs again, gets the decision, and either lets the tool execute or cancels it with a message the
model reasons about. That one mechanism gave me approvals from the web dashboard, from
`culprit resume <run-id> --approve` in a terminal, and from a second AgentCore invocation — hours
later if needed. The `ask_human` tool uses the same primitive from inside a tool via
`ToolContext.interrupt`, for the rare genuinely ambiguous case.

## Hooks for guard-rails and observability

Two more `HookProvider`s round out the agent. A `BudgetHook` counts experiments and, when the budget
is spent, sets `event.cancel_tool` with instructions ("conclude from the evidence you have, or ask
the human") — the model adapts instead of crashing. A `TraceHook` listens to
`Before/AfterModelCallEvent`, `Before/AfterToolCallEvent` and `MessageAddedEvent`, writes a
`trace.jsonl`, and emits the events the dashboard streams over server-sent events. The live timeline,
the bisection board turning commits good/bad/culprit, and the approval card are all just renderings
of hook events.

## Structured output for the post-mortem

The last step is a typed report. `agent(REPORT_PROMPT, structured_output_model=IncidentReport)`
returns a validated Pydantic object — culprit commit, baseline/regressed/recovered values, the
evidence table, the fix, the guard test, the PR link, confidence, follow-ups — which becomes
`report.md` and the report card in the UI. No regex over model prose.

## Testing an agent without a model — and not fooling yourself

Strands lets you implement the `Model` interface yourself, so I wrote `ScriptedModel`: a provider
whose "inference" is a small policy that reads the tool results in the conversation and decides the
next call. It drives the whole test suite — 47 tests including the golden path with interrupt,
cross-process resume and structured report — with zero credentials, and doubles as an offline demo
mode. Same tools, same hooks, same session manager, same interrupt path; only the model is
deterministic.

It is important to be precise about what that policy is. Its bisection step is generic, but its
explanation, fix and guard test are hard-coded for the churn demo's `pandas.factorize` bug. It is an
integration-test fixture, not evidence that Culprit can solve regressions it has never seen. To keep
that distinction honest, the policy refuses to go further than bisection on any other repository, a
source-level test forbids it from referencing the second scenario, and the second scenario exists
specifically to evaluate a real model:

```bash
culprit demo init-secondary          # fraud-risk: a different domain, model, data and bug
culprit evaluate --scenario fraud    # real model in, evaluation.json out
```

`culprit evaluate` tells the model nothing beyond the repository. Afterwards it compares the run with
the ground truth and re-measures the agent's fix branch and re-runs the project's tests *itself*,
because an agent's claim that "the metric recovered" is not a measurement. At the time of writing
this article, that real-model evaluation had not yet been executed — the results table lives in
`docs/validation.md` and is filled in only from `evaluation.json`. I'd recommend both halves of this
pattern to anyone shipping an agent: test the *loop* exactly with a scripted model, and keep a
scenario the scripted model cannot solve so you know what the real model is actually doing.

## Demos you can reproduce

Both demo repositories are generated, not hand-written. `churn-model` is a runnable churn classifier
with a seven-commit history in which one refactor switches categorical encoding to `pandas.factorize`;
codes then depend on the order values first appear in *each* dataframe, so training and evaluation
frames are encoded differently — nothing crashes, the model just gets worse. `fraud-risk` is a card-fraud
classifier (histogram gradient boosting) whose holdout PR-AUC collapses after a "performance" refactor
makes the training and holdout feature transforms inconsistent. In both, the project's own tests stay
green at the broken commit. Commit dates are relative to today (so the story reads "five PRs merged
yesterday"; pin `CULPRIT_DEMO_TODAY` for byte-identical SHAs), and the nightly metric history is
produced by actually training at each commit. Each rebuilds in about ten seconds.

## Deploying on Amazon Bedrock AgentCore

`culprit.agentcore_app` wraps the run manager in a `BedrockAgentCoreApp` entrypoint. An
`investigate` invocation streams the timeline and ends with the run record (`awaiting_human` plus the
interrupt payload); a `respond` invocation resumes it. The container is linux/arm64, exposes
`/invocations` and `/ping`, and starts under `opentelemetry-instrument` so Strands' spans can land in
AgentCore Observability. Bedrock is the default model provider throughout, so the same code is meant
to run on a laptop with an AWS profile and in the runtime with its execution role. To be precise about
status: the entrypoint contract is exercised locally in the test suite; the actual AgentCore
deployment is documented in `docs/deploy-agentcore.md` and had not been performed when this was written.

## What I'd tell another builder

* Write the tools so they could be used by a careful human; the model will use them the same way.
* Put consequential actions behind interrupts, not behind prompts asking the model to "be careful".
* Make the run resumable from day one — approvals don't happen on the agent's schedule.
* Build a deterministic model provider early. It turns "does the agent work?" into a unit test.
* Keep one scenario your scripted model cannot solve, and score the real model on it with an evaluator
  that re-measures instead of trusting the agent. Write down what you have and have not run.

Culprit is MIT-licensed. Point it at your own repository with a six-line `.culprit.yaml`, and the
next time your model gets worse overnight, let the agent have the bad Monday.
