# Quickstart — from clone to a finished investigation

No AWS account, no API key. Every step below runs on the deterministic offline model
(`CULPRIT_MODEL_PROVIDER=scripted`), which drives the same agent loop, tools, hooks and human
interrupt as a real model — but only for the bundled churn demo. **The offline policy replays that
one repository's known solution.** On anything else it bisects, isolates the first bad commit and
stops with "Not determined": it proves the machinery, not that the agent can solve an unseen
regression (see [What the offline model can and cannot do](#what-the-offline-model-can-and-cannot-do)
and [README — Two demo repositories](../README.md#two-demo-repositories-two-very-different-purposes)).

On an otherwise idle machine steps 1–7 take about five minutes, most of it `pip install`. The
optional test suite adds a few minutes more.

**What was executed, and against what.** Every command and output on this page was executed on Linux
(Python 3.10.12, git 2.34.1, 12 cores) while writing it — but not in one sitting from one directory:
steps 1–2 in a fresh clone of `main`, steps 3–8 and the CLI section against this checkout's code from
a scratch working directory. The two sections labelled *not verified here* were not executed.

**Prerequisites:** Python ≥ 3.10 **including the `venv` module** (`requires-python` in
`pyproject.toml`; CI covers 3.10/3.11/3.12) and git ≥ 2.28 (the demo generator runs `git init -b main`).
On Debian/Ubuntu `venv` is a separate package — without `sudo apt install python3-venv` step 2 fails
with `ensurepip is not available` before Culprit is even installed.

## 1. Clone

```bash
git clone https://github.com/danialmukash-cell/culprit.git
cd culprit
```

## 2. Create a virtualenv and install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Ends with `Successfully installed ... culprit-0.1.0 ... strands-agents-1.55.1 ...` — 102 packages
(Strands Agents, FastAPI, pandas, scikit-learn), 1 m 12 s here.

## 3. Switch on the offline model

```bash
export CULPRIT_MODEL_PROVIDER=scripted
```

`export` is per-shell: every terminal you open later needs this line again. Without it Culprit tries
Amazon Bedrock and refuses to start (see Troubleshooting) — it never silently falls back.

## 4. Generate the demo repository

```bash
culprit demo init
```

Builds a seven-commit ML repo in `demo/churn-model`, trains it, and records the nightly history
(3.4 s here):

```
  [1/7] b4e7ccd feat: churn model training pipeline
  ...
  [4/7] c249462 refactor(features): simplify categorical encoding with pandas.factorize
  ...
  nightly f1: 0.830 -> 0.662  (culprit c249462)
✔ ready nightly f1 0.8301 → 0.6624  (culprit c249462 — kept out of the model's sight; only
`culprit evaluate` uses it for scoring)
```

That last SHA is the answer key: the agent is never told it, and `culprit evaluate` grades the
agent's answer against it.

**Your SHAs will differ.** The demo's commit dates are relative to the day you generate it, so the
hashes change daily (`CULPRIT_DEMO_TODAY=2026-09-13 culprit demo init --force` reproduces the ones
above; `2026-10-05` gives `[4/7] 0090006` instead). The metric values are stable.

Re-running this command fails on purpose; use `culprit demo init --force` to rebuild.

## 5. Start the dashboard

```bash
culprit serve
```

Run it **from the repository root**: `runs/` and `demo/churn-model` are resolved against the
server's current directory, so from anywhere else you get an empty run list and a chip that offers
to *create* the demo repo instead of selecting it.

Prints `Culprit dashboard → http://127.0.0.1:8000   (model: scripted (deterministic offline model))`
and stays in the foreground (Ctrl-C stops it). Add `--open` to launch a browser, `--port 8001` if
8000 is taken. It binds to localhost with no authentication — see
[Configuration and security](#configuration-and-security) before exposing it.

## 6. Start an investigation

Open <http://127.0.0.1:8000>. If you already have runs, the dashboard opens the newest one instead
of the start screen — click **+ New** first.

Under the headline *"Which commit broke the model?"* click the **churn-model** chip
(`F1 drops 0.83 → 0.66`) — it fills the repo field with your `demo/churn-model` path — then press
**Investigate**. (The **fraud-risk** chip next to it is a *different* scenario the offline model
cannot solve; it also needs `culprit demo init-secondary` first.)

The timeline streams the agent's thinking and every tool call live — for this run, in order:
`get_metric_history`, `list_commits`, four `run_experiment` calls in isolated git worktrees,
`show_commit`, `read_file`, `start_fix`, `edit_file`, a fifth `run_experiment` to verify the fix,
`write_file`, `run_tests`, `open_pull_request`. **About seven seconds later** (6.9 s measured from
click to card) it stops with an amber card:

![Culprit stops for approval before opening the pull request](screenshot-approval.png)

> **One question before Culprit touches anything** — *Open pull request: fix(features): restore stable
> categorical encoding (regression in nightly F1)* — with the root cause, the bisection table, and the
> buttons **Approve — open the pull request** / **Reject**.

That is the only question Culprit asks. Nothing has been pushed anywhere.

## 7. Approve, then read the report

Click **Approve — open the pull request**. The run completes immediately and the page turns into the
incident report:

![The finished incident report](screenshot-dashboard.png)

```
Proved, fixed and verified — high confidence
0.8301 F1 BEFORE   →   0.6624 BROKEN   →   0.8111 AFTER FIX
Broken by <culprit sha> — refactor(features): simplify categorical encoding with pandas.factorize
Fix on culprit/fix-<run_id> — metric recovered, test suite green
5 experiments actually re-run of 10 allowed · 17 tool calls · 17 model calls
```

with the mechanism (`pandas.factorize` assigns codes per dataframe, so training and evaluation were
encoded differently), the guard test `tests/test_encoding_consistency.py`, the per-commit bisection
table, and links to **Pull request** and **report.md**.

## 8. The same artifacts on disk

`culprit serve` is holding your terminal, so **open a second terminal** and set it up the same way:

```bash
cd culprit
source .venv/bin/activate
export CULPRIT_MODEL_PROVIDER=scripted
```

Then:

```bash
ls runs/                                   # one directory per run, named <run_id>
cat runs/<run_id>/report.md                # the incident report
cat runs/<run_id>/pull_request.md          # the PR body (local file: no GITHUB_TOKEN set)
```

Optional cleanup — removes the run's git worktrees and fix branch from the investigated repo, keeps
the run files:

```bash
culprit clean <run_id> --delete-branch
# deleted branch culprit/fix-<run_id>
# ✔ removed 1 worktree(s) for run <run_id>; run files kept in .../runs/<run_id>
```

## The same thing without a browser

In that second terminal (venv activated, `CULPRIT_MODEL_PROVIDER=scripted` exported):

```bash
culprit investigate demo/churn-model --no-input
```

Streams the identical timeline into the terminal and stops at the approval (~8 s, exit code 3):

```
Human input required. Resume with: culprit resume <run_id> --approve (or --reject)
```

Copy the run id from that line — it is also the newest directory in `runs/`. (`culprit runs` prints a
table, but in an 80-column terminal Rich truncates the id to `20260913-11…`.) Drop `--no-input` and it
prompts you in the terminal instead of exiting. Then:

```bash
culprit resume <run_id> --approve                # or --reject / --answer "..."
culprit show <run_id>                            # prints report.md
```

`culprit resume` ends with `status → completed` and
`✔ completed report: runs/<run_id>/report.md`. It works from any process — the run's Strands session is
persisted under `runs/<run_id>/session/`, so you can also approve a CLI-started run from the dashboard
(verified: `POST /api/runs/<run_id>/respond` with `{"decision":"approve"}` drove a CLI-started run to
`completed`).

## Optional: run the test suite

Not needed to try Culprit — steps 4–7 exercise the same agent loop end to end — but it is the check
that your install is sound:

```bash
pytest
```

`67 passed, 1 warning in 106.03s (0:01:46)` on an idle machine here; expect several minutes on a busy
one (the suite really trains scikit-learn models). Each test is capped at 300 s by `pytest-timeout`
(`pyproject.toml`), so on a loaded box a test can be killed by that cap — that means the machine was
busy, not that the install is broken; re-run it idle. Do not add `-q`: `pyproject.toml`
already sets it, and a second `-q` suppresses the summary line so you only get a row of dots.

## What the offline model can and cannot do

`CULPRIT_MODEL_PROVIDER=scripted` is an integration-test fixture, not a small LLM. Its bisection is
generic, but its explanation, fix and guard test are hard-coded for the churn demo's
`pandas.factorize` bug (`src/culprit/agents/scripted_model.py`). `culprit doctor` says so too:

```
offline policy   • integration-test fixture; knows only the churn demo   use bedrock for real runs
```

Point it at anything else — including the **fraud-risk** chip (`culprit demo init-secondary`) — and
you get a bisected commit and an honest refusal, verified (your SHA will differ):

```
Culprit       eac1fd9 — perf(preprocess): single-pass fit_transform, drop redundant feature recomputation
Root cause    Not determined. The offline scripted policy only knows the churn demo's
              pandas.factorize bug; it isolated the first bad commit by bisection but cannot
              explain or fix this repository.
Fix           No fix attempted (offline policy). Run with a real model provider.
Guard test    None
Confidence    low
```

For an unseen regression you need a real provider — see below, `docs/validation.md`, and
[README — Two demo repositories](../README.md#two-demo-repositories-two-very-different-purposes).

## Next: your own repository

A repository is investigable once it has a `.culprit.yaml` at its root. Without one you get
`ValueError: <repo>/.culprit.yaml not found or missing 'experiment_command'` (verified). The minimum
is two keys — the metric name and a command that writes a JSON metrics file to `{out}`:

```yaml
metric: f1
experiment_command: "python eval.py --out {out}"
```

`{config}` is substituted too, defaults fill in the rest (`default_config: smoke`,
`metrics_history: nightly/metrics_history.json`, `main_branch: main`, `experiment_timeout_s: 600`);
[`config/culprit.example.yaml`](../config/culprit.example.yaml) is the annotated full version. For a
one-off you can skip the file with `culprit investigate <repo> --experiment-command "..."`.

Two things that bite: the clone must have **full git history** (Culprit checks out old commits in
worktrees — `actions/checkout` needs `fetch-depth: 0`), and the target repository must be
**writable**, because experiments and the fix branch are git worktrees created inside it.

Then: [README — Point it at your own repository](../README.md#point-it-at-your-own-repository),
[docs/self-hosting.md](self-hosting.md), [docs/validation.md](validation.md).

## Configuration and security

`culprit serve` binds `127.0.0.1:8000` with **no authentication by default**, and starting a run
executes the target repository's `experiment_command` as a shell command. Do not expose it without
reading the security section of [docs/self-hosting.md](self-hosting.md#security) first.

The settings you are most likely to want (full list in
[docs/self-hosting.md](self-hosting.md#configuration-reference) and `.env.example`):

| Variable | Default | What it does |
| --- | --- | --- |
| `CULPRIT_MODEL_PROVIDER` | `bedrock` | `bedrock` / `anthropic` / `openai` / `scripted` |
| `CULPRIT_MODEL_ID` | provider default | overrides the model |
| `CULPRIT_RUNS_DIR` | `runs` (relative to the CWD) | where every run's files live |
| `CULPRIT_HOST` / `CULPRIT_PORT` | `127.0.0.1` / `8000` | dashboard bind address |
| `CULPRIT_API_TOKEN` | unset | bearer token required on the **mutating** endpoints only |
| `CULPRIT_DEMO_ONLY` | `false` | restrict runs to the bundled demo repositories |
| `CULPRIT_MAX_EXPERIMENTS` | `10` | experiment budget per run |
| `GITHUB_TOKEN` + `GITHUB_REPO` | unset | open real PRs instead of a local `pull_request.md` |
| `SLACK_WEBHOOK_URL` | unset | notify Slack instead of a local `notifications.jsonl` |

## Real model providers (not verified here)

Everything above used the offline policy. The commands below are read from
`src/culprit/settings.py` and `src/culprit/agents/investigator.py`; **they were not executed in this
session** (no valid cloud credentials were available), so treat them as configuration reference, not
as tested output.

```bash
# Amazon Bedrock (default provider; model id defaults to the Strands default)
export CULPRIT_MODEL_PROVIDER=bedrock AWS_REGION=<your-region>   # plus AWS_PROFILE or AWS_ACCESS_KEY_ID/…

# Anthropic
python -m pip install -e ".[anthropic]"
export CULPRIT_MODEL_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-...

# OpenAI
python -m pip install -e ".[openai]"
export CULPRIT_MODEL_PROVIDER=openai OPENAI_API_KEY=sk-...

culprit doctor          # checks python, git, strands-agents, provider, runs dir, demo repos
```

`culprit doctor` itself does run offline — with `CULPRIT_MODEL_PROVIDER=scripted` it printed
`✔ 3.10.12`, `✔ /usr/bin/git`, `✔ 1.55.1`, `✔ scripted (deterministic offline model)`, the resolved
runs dir, one row per demo repo, the `offline policy` note above, and `All required checks passed.`

## Troubleshooting

**`RuntimeError: no AWS credentials found for CULPRIT_MODEL_PROVIDER=bedrock`** — step 3 is missing in
*this* shell (a second terminal does not inherit the `export`). Run
`export CULPRIT_MODEL_PROVIDER=scripted`. Culprit never silently falls back to the offline model.

**`culprit: command not found`** — same cause, other half: the new terminal has no virtualenv. Run
`source .venv/bin/activate` from the repository root first.

**`FileExistsError: .../demo/churn-model already exists (use --force to recreate)`** — `culprit demo
init` refuses to overwrite. Run `culprit demo init --force` (it only deletes directories it generated).

**`ERROR: [Errno 98] error while attempting to bind on address ('127.0.0.1', 8000): address already in
use`** — another dashboard is running. `culprit serve --port 8001` (verified: `/api/health` → 200).

**The dashboard shows an old run instead of the start screen** — expected: it opens the newest run
when `runs/` is not empty. Click **+ New**.

**The chip says `· create`, or the run list is empty even though you have runs** — you started
`culprit serve` from another directory. `runs/` and `demo/churn-model` are resolved against the
server's working directory; restart it from the repository root.

**`ModuleNotFoundError: No module named 'lark'` (or any other stranger) from a traceback rooted in
`/opt/...`** — a system `PYTHONPATH` (ROS, conda, a system package manager) is leaking into your
venv and breaking pytest collection. Run `env -u PYTHONPATH pytest`, or `unset PYTHONPATH`.

**`pytest -q` prints only dots and no `67 passed`** — `pyproject.toml` already passes `-q`; the second
one silences the summary. Just run `pytest`.

**A test in the suite fails with a `Timeout >300.0s`** — the per-test `pytest-timeout` cap in
`pyproject.toml` (read from the config, not reproduced in the idle run above). The suite trains real
models; re-run it on an idle machine.

**A run shows `failed — the Culprit process exited while this run was executing; start a new
investigation`** — you stopped `culprit serve` (or the CLI) mid-run. Only runs that already reached the
approval question survive a restart; anything still executing is marked failed on the next start.

**A run started in the CLI stops updating in the dashboard** — expected. The dashboard streams live
only for runs it started itself; for a CLI-owned run the stream replays the recorded events and closes
(`event: done`). Reload the page to see the latest, or approve it — that still works across processes.

**`ModuleNotFoundError: No module named 'bedrock_agentcore'` from `python -m culprit.agentcore_app`** —
the AgentCore entrypoint needs its own extra: `python -m pip install -e ".[agentcore]"` (verified: the
import then succeeds and the app proceeds to bind port 8080). See `docs/deploy-agentcore.md`.

## Windows (PowerShell)

Same steps; only the shell syntax differs. **Not executed in this session** — this walkthrough was run
on Linux. A recorded Windows run of the suite (Python 3.11.2, `67 passed`) is in
`docs/evidence/release-tests.txt`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:PYTHONUTF8 = "1"
$env:CULPRIT_MODEL_PROVIDER = "scripted"
culprit demo init
culprit serve
```
