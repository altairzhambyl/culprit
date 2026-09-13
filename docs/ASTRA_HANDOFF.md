> Historical handoff below. See [release-review.md](release-review.md) for the subsequent September 13 independent verification and current limitations.

# Reviewer handoff (Astra)

This document tells an independent reviewer exactly what exists, what was executed, how to reproduce
it, and what could **not** be executed in the build environment. Nothing here is inferred from memory;
every number comes from a command that ran in this repository.

# Repository status

**PARTIAL.** The product is complete and verified end to end with the deterministic offline policy
(67 tests, CLI, dashboard, autonomous trigger, evaluator; the AgentCore entrypoint is *not* covered). The project's
central claim — a *real* model solving the unseen fraud-risk regression — is implemented and scored by
an evaluator that cannot be fooled by a fabricated report, but **has not been executed**: the build
environment had no route to any AWS endpoint and no real AWS credentials (`docs/evidence/aws-access-attempt.md`).
The same blocker prevented the AgentCore deployment. Both are one command away for anyone with AWS access.

# Exact environment setup

```bash
git clone <this repository> && cd culprit
python3 --version                      # 3.10+ (developed and tested on 3.11.15)
git --version                          # git is required (worktrees)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,agentcore]"      # strands-agents 1.55.1 at the time of writing
cp .env.example .env                   # optional; every setting has a working default
```

No AWS credentials are needed for anything in the "Full test command", "Deterministic demo" and
"Background trigger" sections. Set `CULPRIT_MODEL_PROVIDER=scripted` explicitly for offline runs; the
default provider is `bedrock` and there is **no silent fallback** — a Bedrock run without credentials
fails before any agent work starts (`culprit doctor` explains why).

# Full test command and result

```bash
pytest -q
```

Result in the build environment (Python 3.11.15, strands-agents 1.55.1): **64 passed**, ~3 minutes,
no network, no credentials. `ruff check src tests` passes.

What the suite covers: tools (git, experiments, edits, PR/notify adapters), hooks (approval interrupt,
budget, loop guard, tracing), the scripted `Model` provider, the golden path through the real Strands
`Agent` including interrupt → cross-process resume → structured report, the rejection path,
auto-approve, budget exhaustion, the web API (start/stream/respond/report, token + demo-only mode),
stale-run recovery, both demo scenarios (silent bugs, substantial drops, reference fixes), the evaluator
on both scenarios, hardening (timeouts, bad refs, malformed metrics, loops, dirty trees, preflight,
`--force` safety) and the autonomous trigger.

# Deterministic demo command

```bash
culprit demo init --force                                         # churn-model, 7 commits, F1 0.83 → 0.66
CULPRIT_MODEL_PROVIDER=scripted culprit investigate demo/churn-model   # pauses for approval, answer y
# or non-interactively:
CULPRIT_MODEL_PROVIDER=scripted culprit investigate demo/churn-model --no-input   # exits 3 at the approval
CULPRIT_MODEL_PROVIDER=scripted culprit resume <run-id> --approve                 # from any process
culprit show <run-id>                                                             # incident report
```

What you will see, in order: metric history → good/bad window (last good = the derived-features commit, first bad = HEAD) → calibration of both
boundaries under the fast config → bisection (2 steps) → culprit `refactor(features): simplify categorical
encoding with pandas.factorize` → diff and file read → fix branch `culprit/fix-<run-id>` → verification
experiment (F1 back to 0.8111 fast-config) → guard test `tests/test_encoding_consistency.py` → project
tests pass → **interrupt: approve the PR?** → PR (local Markdown adapter) → notification → structured
`IncidentReport` (`runs/<run-id>/report.md`).

The `ScriptedModel` is a deterministic offline integration-test policy. Its bisection is generic; its
explanation, fix and guard test are hard-coded for this churn repository. **It proves the machinery,
not LLM generalization.** On any other repository it stops after bisection and says so.

Dashboard: `CULPRIT_MODEL_PROVIDER=scripted culprit serve --open`, click *Use demo repo* → *Investigate*.

# Real Bedrock demo command

```bash
export AWS_REGION=us-west-2            # credentials via env / AWS_PROFILE / role
culprit doctor                         # must show "bedrock model ✔ <model id> responded"
culprit demo init --force
culprit investigate demo/churn-model   # default provider = bedrock, default model = Strands default (Claude Sonnet)
```

If `culprit doctor` reports the model as inaccessible, set `CULPRIT_MODEL_ID` to a Claude model enabled
in your account/region (e.g. `us.anthropic.claude-sonnet-4-5-20250929-v1:0`).

**Not executed by the authors** — see the blocker above. Nothing in this repository describes the
behaviour of a real model run as fact.

# Fraud generalization command

```bash
culprit evaluate --scenario fraud      # regenerates demo/fraud-risk, runs the configured model, scores it
```

The agent receives only the repository and its `.culprit.yaml`. It is never given the expected culprit,
root cause, patch, or any evaluator ground truth; the evaluator uses ground truth only after the run
ends. It then re-measures the fix branch and re-runs the project's tests itself and writes
`runs/<run-id>/evaluation.json`. `success` requires: completed ∧ correct culprit ∧ culprit *and* its
parent measured by the agent's own experiments ∧ evaluator-measured metric recovery ∧ project tests
pass on the fix branch. Exit code 0 on success, 2 otherwise.

# Fraud evaluation result

**Real model: NOT RUN** (blocker above). The row below is what the evaluator recorded for the offline
policy — it is evidence that the evaluator and the scenario behave correctly, not a generalization result.

| field | offline policy (`scripted`) — `docs/evidence/offline-evaluation-fraud.json` | real model |
|---|---|---|
| expected culprit | `perf(preprocess): single-pass fit_transform, drop redundant feature recomputation` (index 5 of 7; sha changes with the generation date) | — |
| predicted culprit | same commit (generic bisection) | not run |
| correct | true | not run |
| before metric (nightly `pr_auc`) | 0.7979 → 0.4484 | — |
| after metric (fix branch, evaluator-measured) | none — the offline policy attempts no fix | not run |
| tests on fix branch | n/a (no fix branch) | not run |
| guard test | none | not run |
| experiments | 5 (2 calibration + 3 bisection), culprit and parent both measured | not run |
| runtime | 7.4 s | not run |
| tokens / cost | 0 (no model) | not run |
| `success` | **false** (as designed: no fix) | not run |

To produce the real row: run the command above with Bedrock access and paste the numbers from
`evaluation.json` into `docs/validation.md` → *Results log* (negative results included).

# Background trigger

The engineer does not have to notice the regression. Reproduce the chain offline in about a minute:

```bash
CULPRIT_MODEL_PROVIDER=scripted scripts/nightly.sh
```

What it does: `culprit demo init --force --without-latest-nightly` (metric store does not yet contain
tonight's run) → `culprit record-nightly demo/churn-model` (evaluates HEAD with the nightly config and
appends F1 0.6624) → `culprit watch demo/churn-model --once` (detects the drop above `regression_threshold`,
starts one investigation for that good→bad window, waits) → the run pauses at the PR → the watcher
notifies (Slack webhook if `SLACK_WEBHOOK_URL`, otherwise `runs/<run-id>/notifications.jsonl`) and exits
with status 3, printing `culprit resume <run-id> --approve`. Running `culprit watch --once` again reports
*already handled* (state in `runs/_watch/`). Without `--once` it polls every `--interval` seconds.
`.github/workflows/nightly-culprit.yml` is the same chain as a scheduled Actions job (example; YAML
validated, never executed — a CI job has no human to click, so it uses `--auto-approve` and the PR review
becomes the approval).

# AgentCore

**Status: not deployed.** The entrypoint (`python -m culprit.agentcore_app`, `POST /invocations`,
`GET /ping`, streaming) was exercised locally with the offline policy (investigate → awaiting_human →
respond → completed). Deployment was blocked: every AWS endpoint was denied by the build environment's
egress policy and no real credentials existed (`docs/evidence/aws-access-attempt.md`).

Exact commands to deploy (untested by the authors), from `docs/deploy-agentcore.md`:

```bash
# image (linux/arm64 is required)
aws ecr create-repository --repository-name culprit --region $AWS_REGION
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $REPO
docker buildx build --platform linux/arm64 -t $REPO:latest --push .
# runtime, via the AgentCore CLI
npm install -g @aws/agentcore && agentcore create && agentcore deploy
agentcore invoke '{"action": "investigate", "repo": "demo"}'
agentcore invoke '{"action": "respond", "run_id": "<run id>", "decision": "approve"}'   # same runtimeSessionId
```

# Known limitations

* Runs are stored on the local filesystem (`CULPRIT_RUNS_DIR`); inside AgentCore that is the session's
  microVM, so the approval round-trip must reuse the same `runtimeSessionId` (S3-backed sessions are a
  documented, unimplemented swap).
* Metric store: JSON file only (MLflow/W&B adapters are a one-method protocol away).
* Experiments run on the machine that runs Culprit; heavy training should be wrapped in a job
  submission command that writes the metrics JSON to `{out}`.
* The dashboard has a bearer-token option and a demo-only mode but no user accounts.
* Concurrent runs on the same repository are not serialized against each other.
* The scheduled Actions workflow and the GitHub/Slack adapters were never executed against live services.

# Unverified claims

None intended. Every statement about a *real model* or *AgentCore deployment* in this repository is
phrased as design or as "not executed"; `docs/validation.md` is the authoritative list. If you find a
sentence that reads as a real-model result, treat it as a documentation bug.

# Useful files/logs for the demo video

* `runs/<run-id>/events.jsonl` — the dashboard timeline (thoughts, tool calls, experiments, edit diffs,
  interrupt, decision, report).
* `runs/<run-id>/trace.jsonl` — model/tool lifecycle trace with durations and results.
* `runs/<run-id>/report.md`, `report.json` — the structured incident report.
* `runs/<run-id>/pull_request.md` — title, body and diff of the PR (local adapter).
* `runs/<run-id>/notifications.jsonl` — the approval notification the watcher sent.
* `runs/<run-id>/evaluation.json` — the evaluator's scoring (after `culprit evaluate`).
* `docs/screenshot-*.png`, `docs/architecture.png`.

# Recommended 3–5 minute golden path

1. `CULPRIT_MODEL_PROVIDER=bedrock scripts/nightly.sh` (or offline with `scripted`, said so on camera):
   the nightly job records the drop and the watcher starts Culprit — nobody typed "investigate".
2. `culprit serve --open` → open the running run: watch calibration, bisection board turning
   good/bad/culprit, the diff, the fix, the verification experiment, the guard test.
3. The approval card: read root cause + evidence, click **Approve** — the only human action.
4. PR, notification, incident report appear.
5. For the strongest version: `culprit demo init-secondary` and run the *unseen* fraud-risk repository
   with the real model, then show `culprit evaluate --scenario fraud` writing `evaluation.json`.
