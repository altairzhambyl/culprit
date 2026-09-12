# Status checklist

Honest state of the project at submission time. Legend: ✅ working · 🟡 partially working · ❌ missing ·
🔑 requires credentials · 🧪 requires manual testing.

## Working ✅

- Demo repository generator (`culprit demo init`): deterministic 7-commit history, one silent
  regression, real nightly metrics (F1 0.83 → 0.66), `.culprit.yaml`, untracked metric store.
- Strands agent assembly: Bedrock default model, 12 instance-bound `@tool`s, `FileSessionManager`,
  `SequentialToolExecutor`, `trace_attributes`, three `HookProvider`s.
- Tools: metric history + regression detection, commit listing, diffs, file reads at any ref,
  experiments in detached worktrees with per-(sha, config) cache, retries and timeouts, fix branch
  + worktree, exact-match edits with diff output, file writes, project test runs, PR (local adapter),
  notification (local adapter), `ask_human` (interrupt from a tool).
- Human approval via Strands interrupts (`BeforeToolCallEvent.interrupt`) with approve/reject +
  comment; rejection cancels the tool with guidance; resume from a **different process** (CLI
  `culprit resume`, web UI, AgentCore `respond`).
- Budget hook (experiment cap → `cancel_tool` with guidance), trace hook (`trace.jsonl` + UI events).
- Structured `IncidentReport` via `structured_output_model`; rendered `report.md`.
- Run manager persistence (`run.json`, `events.jsonl`, session dir, PR file, notifications),
  worktree cleanup, token/tool/model-call accounting.
- CLI: `demo init [--scenario]`, `demo init-secondary`, `demo list`, `investigate` (live timeline,
  prompts for approval, `--no-input`, `--auto-approve`), `resume`, `runs`, `show`, `serve`,
  `evaluate`, `doctor` (environment + Bedrock model access check).
- Web dashboard: start runs, live SSE timeline, nightly metric chart, bisection board, experiments
  table, approval card (approve/reject/comment, questions), incident report card, run list, light/dark.
- AgentCore Runtime entrypoint (`/invocations`, `/ping`) with streaming; verified locally with the
  offline model (investigate → awaiting_human → respond → completed).
- Deterministic offline test policy (`ScriptedModel` + `GoldenPathPolicy`) exercising the full loop on
  the churn scenario **only**; on any other repository it stops after bisection and says so.
- Secondary, unseen scenario `fraud-risk` (`culprit demo init-secondary`): different domain, files,
  model, data, mechanism and fix; its own tests stay green at the broken commit; PR-AUC 0.80 → 0.45;
  a reference fix (tests only) restores it; a source-level test forbids the offline policy from
  referencing it.
- Generalization evaluator (`culprit evaluate --scenario fraud|churn`): runs the configured model,
  approves the PR as the human, then independently re-measures the fix branch and re-runs the
  project's tests; writes `runs/<id>/evaluation.json`. Verified with the offline policy on both
  scenarios (churn → success; fraud → correct bisection, no fix, success=false).
- Test suite: 47 tests (tools, adapters, hooks, scripted model, golden path with cross-process
  resume, rejection path, auto-approve, budget, web API, both scenarios, evaluator). Runs in ~2
  minutes without credentials.
- Docs: README, architecture (Mermaid + PNG/SVG), demo script, Devpost copy, build story, AgentCore
  deployment guide, examples.

## Partially working 🟡

- **Real-model run (Bedrock Claude) — the project's central claim.** The agent, prompts and tools are
  built for it, but no AWS credentials were available while building, so **no real-model run has been
  executed**: neither the churn golden path nor the unseen fraud-risk scenario. Everything about
  "the model investigates" is therefore a design statement until `culprit evaluate --scenario fraud`
  has been run and its `evaluation.json` recorded in `docs/validation.md`.
- **GitHub PR adapter.** Implemented (push + REST `POST /repos/{owner}/{repo}/pulls`), falls back to
  the local adapter on any failure; not executed against a live repository.
- **Slack adapter.** Implemented (incoming webhook), not executed against a live workspace.
- **Metric store.** Only the JSON implementation exists; MLflow/W&B/SageMaker adapters are a
  one-method `Protocol` away but not written.
- **AgentCore state across sessions.** Runs live in the session's filesystem; approvals must reuse the
  same `runtimeSessionId`. `S3SessionManager` + shared run storage documented, not implemented.

## Missing ❌

- Authentication on the web dashboard (intended for local / trusted-network use).
- Concurrency limits: many simultaneous runs against the same repository will contend for worktrees
  (runs are sequential per run, not across runs).
- "Suspicious improvement" (leakage) investigation mode.
- Language/framework-specific experiment runners (only the generic shell command is supported).
- Demo video, builder.aws article publication, live demo URL (submission assets the author must produce).

## Requires credentials 🔑

- `AWS_REGION` + AWS credentials with `bedrock:InvokeModel*` on a Claude model (default Bedrock
  model id comes from Strands: `global.anthropic.claude-sonnet-4-6`; override with `CULPRIT_MODEL_ID`).
- Optional: `GITHUB_TOKEN` (+ a GitHub remote on the target repo) for real pull requests.
- Optional: `SLACK_WEBHOOK_URL` for real notifications.
- Optional: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` for non-Bedrock providers.
- AgentCore deployment: an AWS account with ECR, IAM (execution role), Bedrock AgentCore access.

## Requires manual testing 🧪

- Full golden path with Bedrock Claude (`culprit demo init --force && culprit investigate demo/churn-model`):
  confirm the model calibrates, bisects in ≤ 4 experiments, writes a correct fix, and the guard test passes.
- **Unseen scenario with Bedrock Claude: `culprit evaluate --scenario fraud`** — record the resulting
  `evaluation.json` in `docs/validation.md` (success, experiments, tokens, duration), including
  negative results.
- Web dashboard with the real model (timing of the SSE stream, approval card content).
- `docker buildx build --platform linux/arm64` and `agentcore deploy` / `create_agent_runtime`.
- GitHub and Slack adapters against real endpoints.
- Screen recording following `docs/demo-script.md`.
