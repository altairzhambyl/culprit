# Demo script (≤ 5 minutes)

Format: screen recording of the dashboard with voiceover. Record at 1440×900 or larger, dark theme.
Speed up the middle of the bisection (2×) if the model is slow; never cut the approval moment.

**Before recording**

Record the real model (`CULPRIT_MODEL_PROVIDER=bedrock`). If you want the strongest possible video,
record the *unseen* scenario instead of (or after) the churn one: `culprit demo init-secondary` and
paste `demo/fraud-risk` into the dashboard — then nothing on screen is something our own fixture
could have replayed. Adjust the numbers in the narration to what you actually see.

```bash
culprit demo init --force                 # fresh repo: 'five PRs merged yesterday', bad nightly this morning
culprit serve --open                      # dashboard at http://127.0.0.1:8000
# in the sidebar: "Use demo repo"; leave the metric empty (f1 from .culprit.yaml)
# extra instructions: "Five PRs merged yesterday; the nightly F1 dropped."
```

Have a second browser tab open with `demo/churn-model` in your git viewer (or a terminal with
`git log --oneline`), and the `.culprit.yaml` file visible.

---

## 0:00 — Hook (15–20 s)

*Screen: dashboard, empty state; then the nightly metric chart after clicking Investigate.*

> "This is a real ML repo. Every test is green. But last night its nightly F1 fell from 0.83 to 0.66,
> and five pull requests merged yesterday. Somewhere in there is the one that broke the model.
> Culprit doesn't guess which one. It reruns the experiments and proves it."

## 0:20 — Problem (30 s)

> "Every ML team knows this ritual: find the last good run, check out commits one by one, retrain,
> compare, read diffs, write a fix, prove it, write the post-mortem. It's repetitive, urgent, and it
> eats a senior engineer's day. Monitoring tools tell you *that* something regressed. Nobody does the
> *investigation* — because the investigation means running experiments and touching code."

## 0:50 — User (15 s)

> "Culprit is for ML engineers and applied scientists who own a model in CI. If you have a git repo,
> a training command, and a metric history, you drop in one YAML file and Culprit can investigate
> your repo."

*Show `.culprit.yaml` briefly: metric, experiment_command, test_command.*

## 1:05 — Live workflow (2 min 15 s)

*Screen: the timeline and bisection board updating live.*

> "I click Investigate. Culprit reads the metric history and finds the window: last good commit,
> first bad commit. It lists the five candidates."

> "Now watch the bisection board. It calibrates first — it runs the smoke experiment on the known-good
> and known-bad commits so it knows what 'good' and 'bad' look like under the fast config. Then it
> bisects: middle commit — bad. Next — good. Two calibration runs and two bisection steps later, it has
> isolated the culprit:
> a refactor called 'simplify categorical encoding with pandas.factorize'."

*(If needed, 2× speed here.)*

> "It reads the diff and explains the mechanism: factorize assigns integer codes by order of first
> appearance — per dataframe — so training and evaluation got different encodings. Nothing crashed.
> The model just quietly got worse."

> "Then it does the work: it creates a fix branch, restores stable category codes, and — this is the
> part I care about — it *re-runs the experiment on the fix branch*. F1 is back to 0.81. It writes a
> regression test that would have caught this, and runs the test suite. Green."

*Pause on the approval card.*

> "And now it stops. This is the only question it asks me: open the pull request? I can read the
> root cause, the evidence table, the fix. I click Approve."

*Click Approve. Show PR + Slack notification + incident report appearing.*

> "The PR is opened, the team channel is notified, and I get a structured incident report — culprit,
> evidence, fix, guard test, follow-ups."

## 3:20 — What Strands is doing (40 s)

*Screen: `investigator.py` and `approval.py` side by side, or the architecture diagram.*

> "Under the hood this is one Strands agent with twelve tools. The loop you just watched — observe,
> reason, run an experiment, inspect, repeat — is the model's own; the tools only return data.
> The approval is a Strands **interrupt** raised from a `BeforeToolCall` hook: the agent loop stops,
> the session is persisted with `FileSessionManager`, and I can approve from the web UI, the CLI, or
> another AgentCore invocation — even hours later, in a different process. A budget hook caps
> experiments, a trace hook feeds this timeline, and the final report is Pydantic structured output."

## 4:00 — Architecture (25 s)

*Screen: `docs/architecture.png`.*

> "Three front doors — CLI, dashboard, and an Amazon Bedrock AgentCore Runtime entrypoint — share one
> RunManager and one agent. Adapters for the metric store, GitHub and Slack have local fallbacks, so
> the whole workflow runs without credentials, and the test suite drives the exact same agent loop
> with a deterministic offline model."

## 4:25 — Result / impact (20 s)

> "Before: hours of a senior engineer's morning, and a post-mortem nobody writes. After: one
> investigation you can watch, a fix that is measured rather than asserted, a guard test, and one
> click. The metric history and the repo are real, and there is a second repository with a different
> bug that our own test fixture cannot solve — `culprit evaluate` scores the real model on it and
> writes the numbers to evaluation.json."

## 4:45 — Closing line (10 s)

> "Culprit: when your model gets worse overnight, it finds the culprit, proves it, fixes it — and
> asks you once. Built with Strands Agents, ready for AgentCore."

---

### Fallbacks while recording

* Bedrock is slow or throttled → set `CULPRIT_MODEL_PROVIDER=scripted` (deterministic, well under a
  minute) and say on camera that this is the offline integration-test policy replaying the churn
  scenario, not the model. Never present a scripted run as a model run.
* The model picks a different (valid) fix → that's fine; the verification experiment is the proof.
* Model writes a failing guard test → let it iterate once on camera ("it noticed and fixed the test");
  if it loops, cut and restart from `culprit demo init --force`.
* Approval accidentally rejected → `culprit resume <id> --approve` from a terminal — a nice moment
  to show cross-process resume.
