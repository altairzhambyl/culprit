# Devpost submission copy

**Track:** Professional Agents
**Required assets checklist:** public GitHub repo with MIT license visible in *About* ✔ · README ✔ ·
architecture diagram (`docs/architecture.png`) ✔ · ≤5-min video (YouTube/Vimeo, public) ☐ ·
AWS Builder ID ☐ · optional builder.aws article (`docs/build-story.md`) ☐ · optional live demo link ☐

---

## Project name

**Culprit**

## Tagline

Culprit doesn't guess which commit broke your model. It reruns the experiments and proves it — then
fixes it, adds a guard test, and asks you once before opening the PR.

## Inspiration

Every ML team has the same bad morning. The nightly evaluation dropped, several pull requests merged
since the last good run, and someone senior loses half a day to a ritual we all know by heart: find
the window, check out commits, retrain, compare numbers, read diffs, write a fix, prove it, write it
up. Monitoring tools are great at raising the alarm. Nothing does the *investigation* — because the
investigation means running experiments and touching code, not chatting about them.

We wanted an agent that does the part of the job people actually dread, in the way a careful
engineer would: measure instead of guess, bisect instead of eyeball, verify before claiming, and
stop for a human only at the moment that genuinely needs one.

## What it does

Culprit is an ML regression investigator built on the Strands Agents SDK. Point it at a git
repository with a `.culprit.yaml` (metric, experiment command, test command, metric history) and it:

1. reads the recorded metric history and finds the last-good → first-bad window;
2. lists the candidate commits and calibrates "good" and "bad" under a fast experiment config;
3. **bisects by actually training and evaluating** each candidate in an isolated git worktree;
4. reads the culprit's diff and explains the mechanism of the regression;
5. writes a fix on a branch, **re-runs the experiment to prove the metric recovers**, adds a
   regression test that would have caught the bug, and runs the test suite;
6. pauses for one human decision — *open the pull request?* — using a Strands interrupt;
7. opens the PR, notifies the team channel, and produces a structured incident report.

It ships with a web dashboard (live timeline, bisection board, metric chart, approval card, report),
a CLI, and an Amazon Bedrock AgentCore Runtime entrypoint.

Two generated demo repositories come with it. `churn-model` is a runnable tabular ML project whose
seven-commit history contains one silent regression: a "harmless" refactor that switched categorical
encoding to `pandas.factorize`, so training and evaluation frames were encoded differently (nightly
F1 0.83 → 0.66). `fraud-risk` is a card-fraud classifier whose holdout PR-AUC collapsed (0.80 → 0.45)
after a "performance" refactor made the training and holdout feature transforms inconsistent. In both,
the project's own tests stay green at the broken commit.

In the deterministic integration run (offline policy), Culprit isolates the churn culprit in four fast
experiments (two to calibrate, two to bisect), fixes it, verifies the recovery, adds a guard test and
opens the PR after one click. **[Replace this sentence with the real-model numbers from
`runs/<id>/evaluation.json` after running `culprit evaluate --scenario fraud` — see docs/validation.md.]**

## How we built it

* **Strands Agents SDK** is the core. One `Agent`, twelve `@tool`s bound to an investigation
  (`get_metric_history`, `list_commits`, `show_commit`, `read_file`, `run_experiment`, `start_fix`,
  `edit_file`, `write_file`, `run_tests`, `open_pull_request`, `notify`, `ask_human`), Bedrock Claude
  as the default model.
* **Human-in-the-loop with Strands interrupts.** An `ApprovalHook` on `BeforeToolCallEvent` calls
  `event.interrupt(...)` before consequential tools; the agent stops with `stop_reason="interrupt"`,
  the run is persisted, and *any* process — dashboard, `culprit resume`, a second AgentCore
  invocation — rebuilds the agent from its `FileSessionManager` session and resumes with the human's
  decision. A rejection becomes `event.cancel_tool` with an explanation the model reasons about.
  `ask_human` does the same from inside a tool via `ToolContext.interrupt`.
* **Hooks for guard-rails and observability.** A `BudgetHook` caps experiments and cancels the tool
  with guidance; a `TraceHook` records every model and tool call to `trace.jsonl` and emits the
  events the dashboard streams over SSE.
* **Structured output.** The final `IncidentReport` is a Pydantic model passed as
  `structured_output_model`, so the post-mortem is typed and validated.
* **A custom Strands `Model` provider for testing.** `ScriptedModel` is a deterministic policy model
  that replays the churn golden path (its bisection is generic; its fix is hard-coded). It powers the
  47-test suite and a credential-free demo mode, exercising the exact same agent loop, hooks, interrupt
  and resume paths — and it is explicitly *not* evidence of general problem-solving.
* **A generalization evaluator.** `culprit evaluate --scenario fraud` runs the real model on the
  second repository (which the offline policy provably knows nothing about), then independently
  re-measures the fix branch and re-runs the project's tests before writing `evaluation.json`.
* **Product layer.** FastAPI + server-sent events + a single-page dashboard; a Typer CLI with a rich
  live timeline; a `RunManager` that persists runs (`run.json`, `events.jsonl`, `trace.jsonl`,
  session, worktrees, PR, report); adapters for the metric store, GitHub and Slack with local
  fallbacks; a Dockerfile (linux/arm64) and `BedrockAgentCoreApp` entrypoint for AgentCore Runtime.

## Challenges we ran into

* **Making experiments trustworthy.** Fast-config numbers differ from nightly full-config numbers, so
  the agent must calibrate on the known-good and known-bad commits before classifying candidates.
  Encoding that as method, not as a hard-coded rule, kept the loop model-driven.
* **Resuming across processes.** The approval may come hours later from a different process. Strands
  sessions plus a persisted run record made this work, but every tool had to be re-bindable to a
  fresh context (experiment cache, fix branch, worktrees) without losing state.
* **A regression that is real but subtle.** We needed a bug that trains fine, passes the existing
  smoke test, reads like a reasonable refactor, and reliably tanks the metric. Order-dependent
  categorical encoding is exactly the kind of thing that slips through review.
* **Safe git automation.** Detached worktrees per commit, a separate worktree for the fix branch, and a
  sequential tool executor so concurrent tools can never fight over a checkout.

## Accomplishments that we're proud of

* A workflow that does the whole job end to end — investigation, verified fix, guard test, PR,
  notification, report — and asks a human exactly once (verified end to end with the deterministic
  policy; real-model results: see the validation log).
* A human-approval flow that survives process restarts, built entirely from Strands primitives.
* Two reproducible scenarios: one command each rebuilds a repository, its history and real nightly
  metrics — and the second one is deliberately unsolvable by our own test fixture.
* 47 tests covering the full golden path (interrupt → resume → report) and the evaluator, without any
  credentials.
* An evaluation harness that scores the agent by re-running experiments, not by reading its report.
* It feels like a product: a dashboard you would actually leave open on a Monday morning.

## What we learned

* Strands' hook and interrupt system is a genuinely good abstraction for consequential actions: the
  approval logic lives in one small class, not scattered through tools.
* "Tools return data, the model draws conclusions" produces better agents than clever tools. Our
  tools got simpler over the build, and the agent got better.
* Deterministic policy models are a superpower for testing agentic systems — you can test the loop,
  the hooks and the resume path exactly, then swap the real model in for the demo.
* They are also a trap: a scripted fixture that "solves" your demo proves nothing about the model.
  Keeping a second scenario the fixture cannot solve, and scoring the real model on it by
  re-measurement, is what turns a demo into evidence.

## What's next

* Metric-store adapters for MLflow, Weights & Biases and SageMaker Experiments (the interface is one
  method).
* A "suspicious improvement" mode: when a metric jumps *up* implausibly, investigate for leakage.
* Run experiments on AgentCore Code Interpreter / SageMaker instead of the local worktree, for
  expensive training jobs.
* A GitHub Action that runs Culprit automatically when the nightly job regresses.
* Persist run state in S3 (`S3SessionManager`) so approvals can span AgentCore sessions.

## Built with

Python · Strands Agents SDK · Amazon Bedrock (Claude Sonnet) · Amazon Bedrock AgentCore Runtime ·
FastAPI · Server-Sent Events · Pydantic · Typer · Rich · scikit-learn · pandas · git · Docker ·
OpenTelemetry

## Try it

```bash
pip install -e ".[dev]" && culprit demo init && culprit serve --open
```

(Offline mode without AWS credentials: `CULPRIT_MODEL_PROVIDER=scripted`.)
