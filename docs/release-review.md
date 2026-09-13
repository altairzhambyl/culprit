# Independent release review — September 13, 2026

Handoff base: `4882b01` (verified). Changes preserve the existing single-agent architecture.

## Checked and changed
- Full suite: 67 passed, 1 warning in 235.88s; Ruff clean. Warning is Starlette/AnyIO deprecation.
- Scoped and persisted auto-approval per run; one API request no longer changes later investigations.
- Evaluation success now also requires a newly added test file.
- Fixed Windows command arguments, interpreter selection, timeout process-tree cleanup, temporary state-file sharing errors and read-only Git-object cleanup for marked demos.
- Removed unverified AgentCore badge and enabled the scheduled workflow only with explicit repository configuration.
- Historical publication scan inspected 94 reachable Git blobs and found no matches for the checked credential patterns and no files over 10 MB. This pattern scan is not a universal guarantee.

## Recorded evidence
Run: `20260912-215825-56bb`, churn, scripted provider. The watcher started it following record-nightly; dashboard approval was explicit. Both PR and notification adapters were local.
Expected/predicted culprit: `eb367c9`. Independently measured fast F1: bad 0.7027, good/fixed 0.8111. Tests passed; a new guard-test file exists. Five experiments, 17 tool attempts, 17 scripted model calls, zero tokens. See recorded-demo-evaluation.json and recorded-demo-trace.jsonl.

## Outstanding
Live provider validation, AgentCore deployment, required user-specific form data and final submission attestations remain unverified. No successful cloud deployment or real-model result is claimed.

## Publication verified
- Public repository: https://github.com/danialmukash-cell/culprit
- Code commit: 2f2fcc7e9109e2071cd69f1fa7ccb99f84e56f94
- GitHub CI succeeded on Python 3.10, 3.11 and 3.12: https://github.com/danialmukash-cell/culprit/actions/runs/34720896129
- Public YouTube video: https://www.youtube.com/watch?v=Qep4Oz1iLXo
- Devpost project: https://devpost.com/software/culprit-0dvbjp
- Architecture uploaded as project thumbnail and attached in the contest form; final form save remains pending personal fields.
- Contest submission is not complete: required personal fields and final attestations are missing.

