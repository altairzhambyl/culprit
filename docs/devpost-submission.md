# Culprit

## One-line summary
Culprit investigates silent ML regressions with experiments, verifies a repair, and asks for human approval before opening a pull request.

## Problem and audience
ML engineers can wake up to a serious quality regression even when training succeeds and CI is green. Several valid-looking changes have merged, but monitoring cannot tell them which change caused the drop. An engineer then repeats the same work: check out commits, run evaluations, compare measurements, inspect the culprit, implement a repair, and verify it.

## What it does
Culprit watches recorded nightly evaluations. When the metric crosses a regression threshold, it starts an investigation once for that good-to-bad window. A Strands agent calibrates both endpoints, bisects candidate commits through real experiments in isolated Git worktrees, inspects code, proposes a fix, verifies the metric and tests, and pauses for human approval before producing a pull request and an incident report.

The intended audience is small ML teams and engineers who need reproducible evidence and a reviewable fix, rather than a diff-based guess. It removes the repeated investigation work while leaving the consequential decision with the engineer.

## Implementation
One Strands Agent uses twelve tools for Git inspection, experiments, editing, testing, approval and delivery. Strands hooks enforce experiment budgets, limit repeated tool calls, record traces and interrupt before consequential actions. FileSessionManager preserves the session across a pause and a later resume. FastAPI and server-sent events power the dashboard; a Typer CLI provides the same workflow. Amazon Bedrock is the default real-model provider, with Anthropic and OpenAI alternatives.

## What the demonstration proves
The video is an actual recording of the local application using the explicitly labelled **scripted** integration-test provider. This provider knows the churn repair; the Git worktrees, ML training, measurements, tests, approval and report are real. These results are **not evidence of real-model generalization**.

In the recorded investigation, nightly F1 dropped from 0.8301 to 0.6624. Five experiments identified commit `eb367c9`. A separate evaluator subsequently checked the same run and independently measured fast-config F1 recovering from 0.7027 to 0.8111, matching its fast-config good baseline of 0.8111. It reran the project tests successfully and confirmed the new regression-test file. Fast-config values and nightly values are deliberately reported separately.

The approval was explicit in the dashboard. The recorded PR and notification use local adapters. A live GitHub PR for this generated ML repository is not claimed. The source repository is public at https://github.com/danialmukash-cell/culprit.

## Validation and limitations
- Release verification: **67 passed, 1 warning in 235.88 seconds**, Python 3.11.2 on Windows; Ruff passed. GitHub CI also passed on Python 3.10, 3.11 and 3.12.
- The fraud scenario is separate from the scripted policy's repair logic. A real provider must still run its generalization evaluation.
- Real Bedrock / Anthropic / OpenAI investigation: not run; no authorized provider credentials were available.
- AgentCore entrypoint is included; cloud deployment and cloud invocation are not verified.
- The scheduled workflow is an opt-in example for a configured ML repository, rather than an active job in the Culprit source repository.
- Local sessions are filesystem-based. The dashboard has token/demo-only controls, not a full user-account system.

## How we used Codex
Codex reviewed the existing implementation, checked the handoff commit, fixed isolation of per-run approval settings and Windows execution issues, ran the complete tests, independently scored the recorded demonstration, and prepared the diagram, recording and submission materials. The existing project was retained rather than redesigned.

## Testing instructions
Clone the public repository and follow the Linux/macOS or Windows commands in the README. Use `CULPRIT_MODEL_PROVIDER=scripted` for a reproducible, credential-free integration test. Run `pytest -q`, then generate the churn demo and start the dashboard with `culprit serve`. The demo pauses before the local PR; approve in the dashboard to complete it. Run `culprit evaluate --scenario churn` to reproduce the independent offline scoring. For actual model validation, configure a provider, run `culprit doctor`, then `culprit evaluate --scenario fraud`.

The source, generated data/scenarios and offline demonstration remain freely available to judges. A hosted live application is not currently provided.

## Architecture
[View the architecture diagram](https://github.com/danialmukash-cell/culprit/blob/main/docs/architecture-final.png). It shows the nightly trigger, Strands agent, experiment loop, verification and human approval boundary. It accurately labels the AgentCore status.

## Built with
Python, Strands Agents SDK, Amazon Bedrock, FastAPI, Git, scikit-learn, pandas, NumPy, Pydantic, Typer.

## Official form fields
- Project name: Culprit
- Track: Professional Agents
- Public repository: https://github.com/danialmukash-cell/culprit
- Architecture: architecture-final.png
- Video: https://www.youtube.com/watch?v=Qep4Oz1iLXo
- Submitter type: **user must provide** Individual / Team of Individuals / Organization.
- Country of residence: **user must provide**; do not infer from language or timezone.
- AWS Builder ID: **user must provide**.
- Optional live demo: leave blank.
- Optional AWS Builder article: publication-ready draft supplied; no published URL is claimed.
- Eligibility, ownership and agreement to the official rules: **user confirmation required before final submission**.

## Sources checked
Official requirements and rules were fetched from the Devpost connector on September 12, 2026:
- https://agentsforhumans.devpost.com/rules
- https://agentsforhumans.devpost.com/resources
- Deadline: September 15, 2026 at 00:00 UTC (05:00 Asia/Qyzylorda).

## Status
Project page and YouTube video are published; **not submitted to the hackathon**. Required personal form data and final attestations remain outstanding. The architecture file has been uploaded in the contest form; saving the complete form remains pending. Real-model validation is still pending.
