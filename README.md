# Culprit — the ML regression investigator

![CI](https://github.com/<your-org>/culprit/actions/workflows/ci.yml/badge.svg) ![License: MIT](https://img.shields.io/badge/license-MIT-green.svg) ![Strands Agents](https://img.shields.io/badge/built%20with-Strands%20Agents-232F3E) ![Bedrock AgentCore](https://img.shields.io/badge/deploys%20to-Bedrock%20AgentCore-FF9900)

> An ML metric silently regresses after several code changes. Tests are still green. **Culprit**
> autonomously isolates the causal commit by re-running experiments, identifies the mechanism, writes
> and verifies a fix, adds a guard test, and asks the engineer only before opening the PR.

**Culprit doesn't guess which commit broke your model. It reruns the experiments and proves it.**

Built with the [Strands Agents SDK](https://strandsagents.com) for the AWS **Agents for Humans**
hackathon (Professional Agents track). Amazon Bedrock is the default model provider; an Amazon Bedrock
AgentCore Runtime entrypoint is included.

![Culprit dashboard — live investigation](docs/screenshot-dashboard.png)

<p align="center"><img src="docs/screenshot-approval.png" alt="The one question Culprit asks: approve the pull request?" width="880"></p>

<sub>Screenshots are from the offline integration-test policy (`CULPRIT_MODEL_PROVIDER=scripted`), which
drives the same agent loop, tools, hooks and interrupt as a real model run. See
<a href="docs/validation.md">validation status</a> for what has and has not been executed with a real model.</sub>

## The problem

Five pull requests merged yesterday. This morning's nightly evaluation is 17 points worse. Every test
is green. Someone senior now spends the morning doing the same ritual as last time: find the last good
run, check out commits one by one, retrain, compare, read diffs, write a fix, prove it, write it up.
Monitoring tools raise the alarm. Nothing does the *investigation*, because the investigation means
running experiments and touching code.

## What Culprit does

1. **Observes** — reads the recorded evaluation history and finds the last-good → first-bad window.
2. **Reasons** — lists the candidate commits and calibrates what "good" and "bad" look like under the
   project's fast experiment config.
3. **Proves** — bisects by checking each candidate out in an isolated git worktree and *running the
   training and evaluation*. Diffs suggest hypotheses; measurements decide.
4. **Explains** — reads the culprit's diff and the current code and states the mechanism.
5. **Fixes** — edits the code on a fix branch, re-runs the experiment to show the metric recovers, adds a
   regression test that would have caught the bug, runs the test suite.
6. **Asks once** — pauses (a Strands *interrupt*) for the engineer to approve the pull request.
7. **Delivers** — opens the PR, notifies the team channel, and emits a structured incident report.

## Two demo repositories, two very different purposes

| | `churn-model` (`culprit demo init`) | `fraud-risk` (`culprit demo init-secondary`) |
|---|---|---|
| Regression | categorical encoding became order-dependent (`pandas.factorize`) → F1 0.83 → 0.66 | training and holdout features transformed differently after a "perf" refactor → PR-AUC 0.80 → 0.45 |
| Model | logistic regression | histogram gradient boosting |
| Known to the offline `ScriptedModel`? | **yes** — it replays this exact solution; it is the deterministic integration test of the whole loop | **no** — the offline policy stops after bisection and says so |
| Purpose | prove the *machinery* (tools, hooks, interrupt, resume, report) works, without credentials | prove a *real model* can investigate a regression it has never seen |

Both repositories are generated (seeded data, seven commits with realistic dates and authors, nightly
metric history produced by actually training at each commit). Their own test suites stay green at the
broken commit — the regressions are silent by construction.

## Quick start

```bash
git clone <this repo> && cd culprit
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest -q                                   # 47 tests, no credentials needed

culprit demo init                           # churn-model: the golden path
culprit demo init-secondary                 # fraud-risk: the unseen regression

# Real model (Amazon Bedrock; default model = Strands' default Claude Sonnet)
export AWS_REGION=us-west-2                 # + AWS credentials with bedrock:InvokeModel*
culprit doctor                              # checks git, credentials and that the Bedrock model answers
culprit investigate demo/churn-model        # terminal, live timeline, prompts for approval
culprit serve --open                        # ...or the dashboard: paste demo/fraud-risk to try the unseen one

# Offline integration-test policy (what CI uses; knows only the churn demo)
CULPRIT_MODEL_PROVIDER=scripted culprit investigate demo/churn-model
```

Other providers: `CULPRIT_MODEL_PROVIDER=anthropic` (+ `ANTHROPIC_API_KEY`) or `openai`
(+ `OPENAI_API_KEY`), after `pip install "strands-agents[anthropic]"` / `[openai]`.

### Approve from anywhere

The investigation pauses at the pull request. Approve in the dashboard, or from any terminal — even
after a restart, because the Strands session is persisted:

```bash
culprit runs                         # find the run id
culprit resume <run-id> --approve    # or --reject --comment "wait for the data team"
culprit show <run-id>                # the incident report
```

## Does the real model actually solve an unseen regression?

That is the question this project must answer, and it is answered by measurement, not by the demo:

```bash
culprit evaluate --scenario fraud    # regenerate fraud-risk, run the configured model, score the run
```

The evaluator tells the model nothing beyond the repository and its `.culprit.yaml`. Afterwards it
compares the run with the scenario's ground truth and **independently** re-runs the fast experiment on
the agent's fix branch and the project's tests, then writes `runs/<run_id>/evaluation.json` with the
number of experiments, the predicted vs. expected culprit, the explanation, files changed, recovered
metric, guard-test status, tool calls, token usage and duration.

**Current status:** the evaluator and both scenarios are verified with the offline policy (which,
as expected, isolates the fraud-risk culprit by bisection but cannot fix it). **A real-model run has
not yet been executed by the authors** — see [`docs/validation.md`](docs/validation.md) for the exact
list of verified and unverified claims and a results log to fill in from `evaluation.json`.

## Point it at your own repository

Add a `.culprit.yaml` to the repository root (see `config/culprit.example.yaml`):

```yaml
metric: f1
higher_is_better: true
regression_threshold: 0.03
experiment_command: "python -m churn.train --config {config} --out {out}"   # must write a metrics JSON to {out}
test_command: "python -m pytest -q"
metrics_history: "nightly/metrics_history.json"    # your nightly job's metric records
default_config: smoke
```

The metric store is a JSON list of runs (`run_id`, `timestamp`, `commit`, `metrics`) — the shape
MLflow / W&B / SageMaker Experiments give you; `culprit/adapters/metric_store.py` is the one place
to plug a tracking server in. With `GITHUB_TOKEN` (and a GitHub remote) the PR is real; with
`SLACK_WEBHOOK_URL` the notification is real; without them both are written locally so the workflow
never blocks on credentials.

## How Strands is used

| Strands feature | Where | Why it matters |
|---|---|---|
| `Agent` + `@tool` (12 tools, instance-bound) | `culprit/tools/investigation.py` | Real work: git worktrees, subprocess experiments, file edits, PRs |
| **Interrupts** from a `BeforeToolCallEvent` hook and from a tool via `ToolContext.interrupt` | `culprit/hooks/approval.py`, `ask_human` | Human approval for consequential actions; questions only when judgment is needed |
| `FileSessionManager` | `culprit/agents/investigator.py` | Resume an interrupted run from another process (CLI, web, AgentCore) |
| Hooks: `BeforeToolCallEvent`, `AfterToolCallEvent`, `Before/AfterModelCallEvent`, `MessageAddedEvent` | `culprit/hooks/` | Budget guard-rail (`cancel_tool`), tracing, live UI events |
| `structured_output_model=IncidentReport` | `culprit/service.py` | Typed, validated post-mortem |
| `SequentialToolExecutor`, `trace_attributes`, built-in model retries | `investigator.py` | Safe git access, observability, resilience |
| Custom `Model` provider | `culprit/agents/scripted_model.py` | Deterministic integration-test fixture / offline demo (churn only) |
| `BedrockModel` (default), Anthropic/OpenAI providers | `make_model()` | Bedrock first; swap providers with one env var |

## Repository layout

```
src/culprit/
  agents/        investigator.py (agent assembly), prompts.py, scripted_model.py (offline test policy)
  tools/         investigation.py — the 12 tools
  hooks/         approval.py (interrupts), budget.py, tracing.py
  adapters/      metric_store.py, github.py, slack.py
  demo/          builder.py (shared plumbing), generator.py + project.py (churn),
                 fraud_generator.py + fraud_project.py (fraud-risk), scenarios.py (registry)
  evaluation.py  generalization evaluator (culprit evaluate)
  web/           app.py (FastAPI + SSE) and static/index.html (dashboard)
  service.py     RunManager: lifecycle, persistence, resume, reporting
  cli.py         Typer CLI
  agentcore_app.py  Bedrock AgentCore Runtime entrypoint
config/          example .culprit.yaml
docs/            architecture, validation status, demo script, Devpost copy, build story, AgentCore deployment
examples/        sample incident report, PR, metric history (from the offline policy)
tests/           47 tests incl. the full golden path with interrupt + resume, both scenarios, the evaluator
```

## Deploy to Amazon Bedrock AgentCore

`culprit.agentcore_app` implements the AgentCore Runtime contract (`POST /invocations`, `GET /ping`,
port 8080) and streams the investigation timeline. The approval is a second invocation:

```bash
python -m culprit.agentcore_app          # local: same contract as the runtime
curl -N -X POST localhost:8080/invocations -H 'content-type: application/json' \
     -d '{"action": "investigate", "repo": "demo"}'
curl -N -X POST localhost:8080/invocations -H 'content-type: application/json' \
     -d '{"action": "respond", "run_id": "<run id>", "decision": "approve"}'
```

Container build and deployment steps are in [`docs/deploy-agentcore.md`](docs/deploy-agentcore.md)
(the contract is exercised locally in tests; an actual AgentCore deployment has not been performed yet).

### Hosting the dashboard as a live demo

`culprit serve --host 0.0.0.0` works on any box with AWS credentials. Before exposing it, set
`CULPRIT_API_TOKEN=<secret>` (the UI asks for it once) and `CULPRIT_DEMO_ONLY=true`, which restricts
investigations to the bundled demo repository — an arbitrary repository's `experiment_command` is a
shell command, so a public instance must never run strangers' repos.

## Documentation

* [Validation status](docs/validation.md) · [Architecture](docs/architecture.md) ·
  [Demo script](docs/demo-script.md) · [Devpost submission](docs/devpost-submission.md) ·
  [Build story](docs/build-story.md) · [AgentCore deployment](docs/deploy-agentcore.md) ·
  [Status checklist](TODO_CHECKLIST.md)

## License

MIT — see [LICENSE](LICENSE).
