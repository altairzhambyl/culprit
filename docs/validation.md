# Validation status

This page is the single source of truth for **what has actually been executed** and what has not.
Everything else in the documentation should be read against it.

## Verified in this repository (no credentials)

| What | How | Evidence |
|---|---|---|
| The complete agent loop — tools, hooks (budget, loop guard, approval interrupt, tracing), cross-process resume, structured report, PR/notification adapters, CLI, web API, AgentCore entrypoint contract | 64 automated tests driving the *real Strands `Agent`* with the deterministic `ScriptedModel` | `pytest -q` |
| Reliability under failure: invalid refs, missing/malformed metrics JSON, experiment timeouts (whole process tree killed), stale worktrees, identical repeated tool calls, global tool cap, dirty working trees left untouched, `--force` refusing to delete non-demo directories, provider preflight (no credentials → run never starts, never falls back to the offline policy) | `tests/test_hardening.py` | `pytest -q tests/test_hardening.py` |
| Autonomous trigger: nightly record → regression detected → one investigation per good→bad window → human notified only for approval → resume from another process | `tests/test_automation.py`; `scripts/nightly.sh` end to end with the offline policy | `CULPRIT_MODEL_PROVIDER=scripted scripts/nightly.sh` |
| The churn golden path end to end (bisection → fix → guard test → approval → PR → report) | `culprit evaluate --scenario churn` with `CULPRIT_MODEL_PROVIDER=scripted`; the evaluator re-measures the fix branch and re-runs the project's tests itself | `runs/<id>/evaluation.json` → `success: true` |
| The secondary (fraud-risk) scenario is real: the bug is silent (its tests stay green), the metric drops substantially (PR-AUC ≈ 0.80 → 0.45 nightly, ≈ 0.68 → 0.40 fast config), and a correct fix restores it | `tests/test_fraud_scenario.py` (reference fix applied only inside the test) | `pytest -q tests/test_fraud_scenario.py` |
| The offline policy contains **no knowledge** of the secondary scenario | Source-level test forbidding any reference to its module, files, mechanism or fix; the evaluator records that the policy stops after bisection with no fix (`success: false`) | `tests/test_fraud_scenario.py`, `tests/test_evaluation.py` |

## NOT yet verified (requires credentials the author did not have while building)

| What | Status | How to verify |
|---|---|---|
| **A real model (Amazon Bedrock Claude) completing the churn golden path** | not executed — the build environment's egress policy denied every AWS endpoint and its only AWS credentials were proxy placeholders (`evidence/aws-access-attempt.md`) | `culprit doctor && culprit demo init --force && culprit investigate demo/churn-model` |
| **A real model solving the unseen fraud-risk regression** — the central claim of the project | not executed (same blocker) | `culprit evaluate --scenario fraud` (see below) |
| Amazon Bedrock AgentCore Runtime deployment | entrypoint contract exercised locally only; deployment blocked by the same egress policy | `docs/deploy-agentcore.md` |
| Scheduled GitHub Actions example (`.github/workflows/nightly-culprit.yml`) | YAML validated, never executed | add to an ML repository with AWS OIDC credentials |
| GitHub pull-request adapter against a live repository, Slack webhook adapter | implemented, not executed | set `GITHUB_TOKEN` / `SLACK_WEBHOOK_URL` and re-run an investigation |
| Timing / token cost of a real run | unknown | `evaluation.json` records both |

Until the first two rows are executed, **no statement in this repository should be read as evidence
that an LLM-driven Culprit solves regressions in general.** The `ScriptedModel` is an integration-test
fixture and offline demo policy; it replays one known solution and is honest about that on any other
repository.

## Running the generalization evaluation

```bash
export AWS_REGION=us-west-2          # + credentials with bedrock:InvokeModel* on a Claude model
# optional: CULPRIT_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0

culprit evaluate --scenario fraud    # regenerates demo/fraud-risk, runs the agent, scores the run
culprit evaluate --scenario churn    # the known scenario with the real model, for comparison
```

The evaluator:

1. regenerates the scenario repository (the model is told nothing about it beyond `.culprit.yaml`);
2. runs an ordinary investigation — the same code path as the CLI and dashboard — and plays the
   human by approving the pull request when the agent asks;
3. **afterwards** compares the run with the scenario's ground truth and *independently* re-runs the
   project's fast experiment on the agent's fix branch and the project's test suite;
4. writes `runs/<run_id>/evaluation.json` and prints a summary; exit code 0 on success, 2 otherwise.

`evaluation.json` fields:

| field | meaning |
|---|---|
| `expected_culprit` / `predicted_culprit` / `culprit_correct` | ground-truth vs. the agent's report |
| `experiments`, `experiments_detail` | number of (non-cached) experiments and each measurement |
| `tool_calls`, `model_calls`, `input_tokens`, `output_tokens`, `duration_s` | cost of the run |
| `root_cause`, `confidence`, `mechanism_mentioned`, `mechanism_keywords_found` | the agent's explanation and a soft keyword check against the known mechanism |
| `agent_reported.*` | what the agent *claimed* (recovered value, files, guard test, PR) |
| `verified.files_changed`, `verified.new_test_files`, `verified.culprit_file_touched` | what the fix branch actually contains |
| `verified.metric_on_fix_branch`, `metric_at_last_good`, `metric_at_first_bad`, `metric_recovered` | the evaluator's own measurements; recovered = closer to good than to bad |
| `verified.tests_pass_on_fix_branch` | the project's test suite, run by the evaluator on the fix branch |
| `verified.culprit_measured`, `parent_measured`, `culprit_bracketed_by_experiments` | the agent actually ran experiments at the culprit and at its parent — it measured, it did not guess |
| `guard_test_added` | a new test file exists on the fix branch |
| `success` | completed **and** correct culprit **and** culprit bracketed by experiments **and** metric recovered **and** tests pass |

## Results log

Fill this in from `evaluation.json` after each real run. Do not fill it in from memory.

| date | model | scenario | culprit correct | experiments | metric recovered | tests pass | tokens (in/out) | duration | run id |
|---|---|---|---|---|---|---|---|---|---|
| — | — | fraud | — | — | — | — | — | — | — |
| — | — | churn | — | — | — | — | — | — | — |

If a run fails, keep the row: an honest negative result plus the trace (`runs/<id>/trace.jsonl`) is
more useful to a reviewer than a missing one.
