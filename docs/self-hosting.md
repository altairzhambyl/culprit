# Self-hosting Culprit

How to run the Culprit **web dashboard** (`culprit serve`) on your own machine or server: with Docker
Compose, with plain Docker, or bare metal under systemd. Read [SECURITY](#security) before you put it
on any address other than `127.0.0.1` — an investigation runs the target repository's
`experiment_command`, which is an arbitrary shell command.

Every command in this document that is marked ✅ was executed against this repository, on
Linux/amd64 with Docker Engine 29.1.3 and Compose v2.39.1, using the offline `scripted` provider.
Anything that could not be executed here is marked **UNVERIFIED** in place. The exact runs are listed
under [Verification status](#verification-status).

**Contents**

1. [Which image is which](#which-image-is-which)
2. [Docker Compose (recommended)](#1-docker-compose-recommended)
3. [Plain Docker](#2-plain-docker)
4. [Investigating your own repository](#3-investigating-your-own-repository)
5. [Bare metal (systemd)](#4-bare-metal-systemd)
6. [Nightly automation](#5-nightly-automation)
7. [Behind a reverse proxy](#6-behind-a-reverse-proxy)
8. [SECURITY](#security)
9. [Configuration reference](#configuration-reference)
10. [Volumes: what lives where](#volumes-what-lives-where)
11. [Upgrading](#upgrading) · [Backup and retention](#backup-and-retention) · [Logs](#logs-and-observability) · [Troubleshooting](#troubleshooting) · [Verification status](#verification-status)

---

## Which image is which

| File | Target | Platform | Port | Entrypoint |
| --- | --- | --- | --- | --- |
| `Dockerfile` | Amazon Bedrock AgentCore Runtime | forced `linux/arm64` | 8080 | `python -m culprit.agentcore_app` |
| `Dockerfile.dashboard` | the web dashboard, self-hosted | your build platform | 8000 | `culprit serve` |

The AgentCore image does **not** serve the dashboard (it hardcodes the AgentCore entrypoint and
`CULPRIT_MODEL_PROVIDER=bedrock`, and its runs dir is ephemeral `/tmp`). Use `Dockerfile.dashboard`
for self-hosting. Deploying to AgentCore is a different document: [deploy-agentcore.md](deploy-agentcore.md).

The deployment options side by side: [diagrams/deployment.svg](diagrams/deployment.svg).

**One hard constraint, everywhere in this document:** exactly one Culprit process may use a runs
directory at a time. Every `RunManager` marks all `running`/`reporting` runs it finds on disk as
`failed` when it starts, because it cannot own their threads. So: do not scale the service to more
than one replica, do not run uvicorn with `--workers > 1`, and do not run `culprit` CLI commands
inside the serving container while an investigation is in flight — use the HTTP API instead. ✅
Verified: with a run in flight on the compose container, a second container on the same volume
printed that run as `FAILED` with `error = "the Culprit process exited while this run was executing;
start a new investigation"`.

---

## 1. Docker Compose (recommended)

Prerequisites: Docker Engine ≥ 24 with the Compose v2 plugin (`docker compose version`), and about
1 GB of disk for the image (828 MB here). Nothing else — no Python, no AWS account.

```bash
git clone https://github.com/danialmukash-cell/culprit.git
cd culprit
docker compose up -d --build     # first build takes a few minutes (pandas/scikit-learn wheels)
docker compose logs -f culprit   # optional: follow the (very quiet) server log
```

Then open <http://127.0.0.1:8000>. ✅ Executed here with `CULPRIT_HOST_PORT=18140`, because port 8000
was already taken on the test host. Defaults, all set in `docker-compose.yml`:

* `CULPRIT_MODEL_PROVIDER=scripted` — the deterministic offline model. **No credentials needed**; the
  bundled churn demo runs end to end. The scripted model only reasons about the bundled churn demo
  (`src/culprit/agents/scripted_model.py`); on any other repository it stops after isolating a commit.
* the port is published on `127.0.0.1` only;
* state lives in the named volume `culprit-data`, mounted at `/data`.

### Compose-only variables

These three are read by `docker-compose.yml` itself (from your shell or from a `.env` next to it),
not by Culprit. They are the only supported way to change where the stack is published:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CULPRIT_HOST_PORT` | `8000` | Host port. Use it when 8000 is busy: `CULPRIT_HOST_PORT=18140 docker compose up -d`. ✅ |
| `CULPRIT_BIND_ADDRESS` | `127.0.0.1` | Host interface the port is published on. `0.0.0.0` exposes the dashboard to the network — only together with `CULPRIT_API_TOKEN` and `CULPRIT_DEMO_ONLY=true`, see [SECURITY](#security). |
| `CULPRIT_CONTAINER_NAME` | `culprit` | Rename the container if another one on the host is already called `culprit`. |

### The compose `.env` file

Compose substitutes a `.env` next to `docker-compose.yml` into the `${...}` values in it. Put
**credentials and provider settings only** in that file:

```dotenv
# .env — next to docker-compose.yml. Never commit it.
CULPRIT_MODEL_PROVIDER=bedrock
AWS_REGION=us-west-2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
CULPRIT_HOST_PORT=8000
```

> **Do not copy `.env.example` over it.** `.env.example` configures a *bare-metal* host: it sets
> `CULPRIT_HOST=127.0.0.1`, `CULPRIT_PORT=8000` and `CULPRIT_RUNS_DIR=runs`, which are all wrong
> inside a container — a loopback bind there means the published port answers nothing. The compose
> file therefore **pins** `CULPRIT_HOST=0.0.0.0`, `CULPRIT_PORT=8000` and `CULPRIT_RUNS_DIR=/data/runs`
> in its `environment:` block, which wins over anything in `.env`. ✅ Verified with the committed
> `.env.example` copied verbatim to `.env`: the container still came up `healthy` and
> `curl http://127.0.0.1:18140/api/health` returned **HTTP 200**, with `CULPRIT_HOST=0.0.0.0` inside.
> One thing it *does* change is the provider: `.env.example` sets `CULPRIT_MODEL_PROVIDER=bedrock`
> uncommented, and the health endpoint then reported `"model":"bedrock:default"` — every run would
> fail preflight without AWS credentials. Set it back to `scripted` if that is not what you wanted.

To use a real model, set the provider and the credentials as above, or keep using an AWS profile by
uncommenting the `~/.aws` mount in `docker-compose.yml` and setting `AWS_PROFILE`.
**UNVERIFIED:** no real-provider run was executed while writing this document (the AWS credentials
available here were expired), so the Bedrock path in a container is code-read only.

### First run

The dashboard's start panel offers the `churn-model` demo and builds it for you — that button calls
`POST /api/demo/init`, which trains the model a few times (✅ 7.4 s here). The second demo repo has no
button; create it while no investigation is running:

```bash
docker compose exec culprit culprit demo init-secondary   # ✅ 9.2 s, pr_auc 0.7921 → 0.4384
```

Useful commands:

```bash
docker compose logs -f culprit                    # ✅ the server log is nearly silent — see Logs below
docker compose down                               # ✅ stop; the culprit-data volume survives
docker compose down -v                            # ✅ stop and DELETE all runs and demo repos
```

`docker compose exec culprit culprit runs` also works, but only when nothing is running — see the
one-process constraint above.

## 2. Plain Docker

```bash
docker build -f Dockerfile.dashboard -t culprit-dash .           # ✅ exit 0, 828 MB
docker run -d --name culprit -p 127.0.0.1:8000:8000 \
  -e CULPRIT_MODEL_PROVIDER=scripted \
  -v culprit-data:/data \
  culprit-dash                                                   # ✅ (published on 18150 here)
```

The port publish is the only thing that differed here: the verification runs used ports in the 181xx
range because 8000 was busy on the test host.

What the image does (all of this is in `Dockerfile.dashboard`):

* two-stage build: the build stage resolves the package into `/install`, the runtime stage is
  `python:3.11-slim` + `git` + that prefix copied into `/usr/local` — no pip cache, no compilers,
  and deliberately **no virtualenv** (see the box in [§4](#4-bare-metal-systemd) for why a venv
  breaks the experiment subprocess). `git` is a hard requirement: every investigation shells out to
  it (a worktree per experiment, a branch and a commit for the fix) and the demo generator needs
  git ≥ 2.28 for `git init -b main`. ✅ In the built image: `git 2.47.3`, `Python 3.11.16`;
* runs as the non-root user `culprit` (uid 10001) — ✅ `id` inside the container prints
  `uid=10001(culprit) gid=10001(culprit)`;
* `WORKDIR=/data`, `CULPRIT_RUNS_DIR=/data/runs`, both writable by that user. `/data` is the working
  directory because Culprit resolves the runs dir and the demo repo paths (`demo/churn-model`,
  `demo/fraud-risk`) **relative to the process CWD**;
* `CULPRIT_HOST=0.0.0.0`, `CULPRIT_PORT=8000`, `EXPOSE 8000`. Binding `0.0.0.0` inside a container is
  normal — restrict exposure at the publish address (`-p 127.0.0.1:8000:8000`), not inside;
* `HEALTHCHECK` polls `GET /api/health` every 30 s with `urllib` (so the image does not need `curl`),
  and **refuses a loopback `CULPRIT_HOST`**: overriding it to `127.0.0.1` would make the server
  unreachable from outside while an inside-the-container probe still passed. ✅ Verified: a container
  started with `-e CULPRIT_HOST=127.0.0.1` goes `unhealthy` after ~90 s with
  `CULPRIT_HOST=127.0.0.1: the server is bound inside the container only; it must be 0.0.0.0`.

Bind-mounting a host directory instead of a named volume: the directory must be writable by uid
10001, or run the container as yourself:

```bash
mkdir -p ./culprit-data
docker run -d -p 127.0.0.1:8000:8000 -e CULPRIT_MODEL_PROVIDER=scripted \
  -u "$(id -u):$(id -g)" -v "$PWD/culprit-data:/data" culprit-dash          # ✅
```

## 3. Investigating your own repository

This is the point of self-hosting, and it has exactly two requirements: the repository must be
**visible inside the container**, and it must be **writable by the container user**. Culprit does not
just read the repo — it registers a git worktree per experiment in the repo's `.git`, and creates the
branch `culprit/fix-<run_id>` there.

Checklist for the target repository:

* it has a `.culprit.yaml` at its root (`experiment_command` is the only required key — see
  `config/culprit.example.yaml`);
* it has its **full git history** — a shallow clone breaks bisection (in CI: `fetch-depth: 0`);
* it has a metric history file (`metrics_history`, default `nightly/metrics_history.json`) with at
  least one good and one bad nightly, or you point Culprit at one;
* it is writable by the user the container runs as.

### With compose

Add the mount to `docker-compose.yml` and make the repo owned by uid 10001:

```yaml
    volumes:
      - culprit-data:/data
      - /srv/ml/churn-model:/repos/churn-model      # rw on purpose
```

```bash
sudo chown -R 10001:10001 /srv/ml/churn-model
docker compose up -d
```

Then start the run against the **container** path — in the dashboard's "repository" field, or:

```bash
curl -sX POST http://127.0.0.1:8000/api/runs \
  -H 'content-type: application/json' \
  -d '{"repo":"/repos/churn-model"}'
```

✅ Verified end to end: a copy of the churn demo repo on the host, `chown`ed to `10001:10001` and
mounted at `/repos/churn-model`, ran through the compose stack to `completed` — culprit `c249462`,
f1 `0.8301 → 0.6624 → 0.8111`.

### With plain Docker, as your own user

If you do not want to change the repository's ownership, run the container as yourself. The state
directory must then also be one you own, so use a bind mount for `/data` rather than a named volume
(a named volume inherits the image's `culprit`-owned `/data`):

```bash
mkdir -p ./culprit-data
docker run -d --name culprit -u "$(id -u):$(id -g)" \
  -p 127.0.0.1:8000:8000 -e CULPRIT_MODEL_PROVIDER=scripted \
  -v "$PWD/culprit-data:/data" \
  -v /srv/ml/churn-model:/repos/churn-model \
  culprit-dash
```

✅ Verified end to end with the same repo copy left owned by the host user: run `completed`, culprit
`c249462`, f1 `0.8301 → 0.6624 → 0.8111`, and the fix branch `culprit/fix-<run_id>` present in the
host repository afterwards.

### Two failure modes worth recognising

* **Repo mounted, container runs as uid 10001, repo owned by someone else.** git refuses the
  repository (`fatal: detected dubious ownership`) and the API answers a misleading
  `400 {"detail":"/repos/churn-model is not a git repository"}`. ✅ Reproduced.
* **`safe.directory` alone does not fix it.** Adding
  `-e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/repos/churn-model`
  gets the run started, and then **every experiment fails silently**:
  `git worktree add ... fatal: could not create directory of '.git/worktrees/<sha>': Permission
  denied`. ✅ Reproduced — and the run still ended `completed`, with `0` experiments and the **wrong**
  culprit (`5022740`, the newest commit in the window, "confidence: low"). Ownership, not
  `safe.directory`, is the fix.

Private git URLs: `POST /api/runs` also accepts an `http(s)://`, `git@` or `ssh://` URL, which
`RunManager._materialize_repo` clones into `<runs_dir>/_repos/`. The clone uses the container's git
credentials, so a private URL needs a credential helper, a mounted `~/.ssh`, or a token embedded in
the URL. **UNVERIFIED:** only local paths were exercised here.

Cleanup: `culprit clean <run_id> [--delete-branch]` removes the worktrees a run registered in the
target repository. Note that the worktree is registered with the **container** path
(`/data/runs/<id>/fix`), so from the host the entry shows as `prunable` — run `culprit clean` inside
the same container layout, or `git worktree prune` in the repository. ✅ Observed in
`git worktree list` on the host after a container run.

## 4. Bare metal (systemd)

> **Read this before installing into a virtualenv — it changes the install command.** To run a
> project's experiment command, Culprit creates `<run_dir>/bin/python` as a **symlink to
> `sys.executable`** and puts that directory first on `PATH`, so that a bare `python` in your
> `experiment_command` runs in Culprit's environment (`src/culprit/tools/investigation.py`,
> `_python_shim_path`). A symlink pointing into a virtualenv's `bin/` **defeats CPython's venv
> detection**: the child process starts with the base interpreter's `sys.prefix`, so Culprit's own
> dependencies (pandas, scikit-learn) are not importable and every experiment fails with
> `ModuleNotFoundError`.
>
> Verified here (Python 3.10 on this host): calling `<venv>/bin/python` directly gives
> `sys.prefix = <venv>`, while calling a symlink to it from another directory gives
> `sys.prefix = /usr` — for a `python -m venv` **and** for a `python -m venv --copies` venv.
> Verified consequence: an earlier build of the dashboard image that installed into `/opt/venv`
> failed every experiment with `ModuleNotFoundError: No module named 'sklearn'`. That is why
> `Dockerfile.dashboard` installs into the image's base interpreter (verified: the shim resolves to
> `/usr/local/bin/python3.11` and the full investigation completes).
>
> So for bare metal, in order of preference: **run the container instead** (§1/§2); or install into
> an interpreter that already exposes the dependencies to the service user (`pip install --user`,
> distribution packages); or — only if you insist on a venv — keep it *and* make every target
> repository's `experiment_command` call the interpreter by absolute path
> (`/var/lib/culprit/venv/bin/python -m churn.train ...`), which the PATH shim does not intercept.

Recommended install (no virtualenv, so the PATH shim keeps working):

```bash
sudo useradd --system --create-home --home-dir /var/lib/culprit culprit
sudo -u culprit python3 -m pip install --user \
  'culprit @ git+https://github.com/danialmukash-cell/culprit.git'
# → /var/lib/culprit/.local/bin/culprit
```

Python ≥ 3.10 is required (`pyproject.toml`); CI covers 3.10, 3.11 and 3.12. `git` must be on `PATH`.

`/etc/culprit.env` (mode 0600, owned by the service user):

```dotenv
CULPRIT_MODEL_PROVIDER=bedrock
AWS_REGION=us-west-2
CULPRIT_RUNS_DIR=/var/lib/culprit/runs
CULPRIT_HOST=127.0.0.1
CULPRIT_PORT=8000
CULPRIT_API_TOKEN=<random 32+ chars>
CULPRIT_DEMO_ONLY=true
```

`/etc/systemd/system/culprit.service`:

```ini
[Unit]
Description=Culprit dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=culprit
Group=culprit
WorkingDirectory=/var/lib/culprit
EnvironmentFile=/etc/culprit.env
ExecStart=/var/lib/culprit/.local/bin/culprit serve
Restart=on-failure
RestartSec=5
# Culprit executes the target repo's experiment_command, so keep the sandbox tight.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/culprit
# ...plus every repository you want it to investigate, e.g.:
# ReadWritePaths=/srv/ml-repos/churn-model

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now culprit
```

**UNVERIFIED:** this unit file was not installed or started anywhere, and neither bare-metal install
variant was executed — they are written from the verified runtime facts (working directory matters,
the process needs write access to the runs dir *and* to the target repository, `culprit serve` reads
`CULPRIT_HOST`/`CULPRIT_PORT`, the venv shim mechanism above), not from a live systemd run.

The target repository must be writable by the service user and needs its full git history — the same
two requirements as [§3](#3-investigating-your-own-repository).

## 5. Nightly automation

The dashboard is the human end. The autonomous end is two commands, meant for a timer:

```bash
culprit record-nightly /repos/churn-model    # run the project's nightly config, append the metric
culprit watch /repos/churn-model --once      # investigate if the last nightly regressed
```

`watch --once` compares the two most recent nightlies, and starts at most one investigation per
good→bad window (the dedup key lives in `<runs_dir>/_watch/<hash>.json`). Its exit code is the
interface:

| Exit code | Meaning |
| --- | --- |
| `0` | no regression, or the window was already investigated, or the run completed unattended |
| `3` | an investigation is **awaiting human approval** — go to the dashboard |
| `1` | the investigation failed |

In containers, run these as one-shot containers on the same volume — not inside the serving
container, because of the one-process constraint (both would be a `RunManager` over the same runs
directory; the timer's run is safe only while the dashboard has nothing in flight):

```bash
docker run --rm -v culprit-data:/data -v /srv/ml/churn-model:/repos/churn-model \
  -e CULPRIT_MODEL_PROVIDER=scripted culprit-dash \
  culprit record-nightly /repos/churn-model                      # ✅ exit 0

docker run --rm -v culprit-data:/data -v /srv/ml/churn-model:/repos/churn-model \
  -e CULPRIT_MODEL_PROVIDER=scripted culprit-dash \
  culprit watch /repos/churn-model --once                        # ✅ exit 3
```

✅ Executed here against the mounted repo copy: `record-nightly` appended
`nightly-2026-09-13-83ca: commit 5022740 full → f1=0.6624` and exited 0; `watch --once` printed
`regression detected f1 0.8301 → 0.6624; started run … → awaiting_human` and exited **3**; the run
appeared in the dashboard's run list and `POST /api/runs/{id}/respond {"decision":"approve"}` from the
dashboard container took it to `completed`. That cross-process approval is supported by design — only
`awaiting_human` runs survive a process boundary.

A systemd timer (or a cron line) around those two commands is all the scheduling there is; Culprit
ships no scheduler. **UNVERIFIED:** no timer/cron unit was installed here.

After any install, `culprit doctor` is the built-in self-check — ✅ inside the image it reported
`python ✔ 3.11.16`, `git ✔ /usr/bin/git`, `strands-agents ✔ 1.55.1`, the provider, the runs dir and
both demo repos, and exited 0.

## 6. Behind a reverse proxy

The dashboard streams the investigation timeline with Server-Sent Events
(`GET /api/runs/{id}/stream`). The app already sends `Cache-Control: no-cache` and
`X-Accel-Buffering: no` and emits a `: keep-alive` comment every 10 s, but the proxy must not buffer
or time the connection out.

**Serve it at the root of a host or subdomain.** Sub-path proxying (`location /culprit/`) is not
supported: every call in the bundled UI is a root-absolute `/api/...` URL and the page has no
`<base>` tag (verified by reading `src/culprit/web/static/index.html`).

nginx:

```nginx
location / {
    proxy_pass         http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header   Host $host;
    proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;

    # Server-Sent Events: no buffering, no idle timeout.
    proxy_buffering    off;
    proxy_cache        off;
    proxy_read_timeout 3600s;
}
```

Caddy needs no SSE-specific configuration:

```
culprit.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

**UNVERIFIED:** neither proxy configuration was executed here; both are written against the verified
SSE behaviour of the app (10 s keep-alives, `X-Accel-Buffering: no`).

A reverse proxy is also the only place you can put real authentication in front of the *read* API —
see below.

---

## SECURITY

### The blast radius, stated plainly

An investigation runs the target repository's `experiment_command` (and `test_command`) as a **shell
command**, in a subprocess of the Culprit process, with that process's environment and credentials
(`src/culprit/tools/investigation.py`, `_run_command` → `subprocess.Popen(command, shell=True, ...)`).
That is the design: Culprit reproduces your training run. It also means:

> **Anyone who can successfully `POST /api/runs` with a repository path or git URL they control can
> execute arbitrary code as the Culprit user.**

A git URL is enough — `RunManager._materialize_repo` clones `http(s)://`, `git@` and `ssh://` URLs into
`<runs_dir>/_repos/`, and the cloned `.culprit.yaml` supplies the command that gets run.

Therefore a publicly reachable instance **must** set both:

```dotenv
CULPRIT_API_TOKEN=<long random secret>
CULPRIT_DEMO_ONLY=true
```

…and still be treated as executing untrusted code. **`CULPRIT_DEMO_ONLY` limits *which repository* is
investigated, not *what the agent does inside it*.** The `task` field of `POST /api/runs` is free text
that goes straight into the agent's prompt, and the agent has `write_file` (writes any content into
the fix worktree) and `run_tests` (runs the project's `test_command` — `python -m pytest -q` for the
bundled demo, which imports whatever was just written). With a real model provider, a token holder on
a demo-only instance can therefore still get code execution as the container user. The **container**
is the boundary, which is why this document recommends running it in one; demo-only is a scope limit
on top. (Mechanism read from the code; not exercised here, because the offline scripted model cannot
be steered by the task string.)

### What those two settings actually do

`CULPRIT_API_TOKEN` (`require_token`, `src/culprit/web/app.py:27-31`) rejects a request with
HTTP 401 unless the `Authorization` header equals `Bearer <token>` exactly. It is attached to
**three** routes. It is a no-op when the variable is unset: no token configured means every endpoint
is open. In the browser, a protected instance prompts once for the token
(`window.prompt(...)` in `index.html`) and keeps it in `localStorage` — so an operator who sets the
token needs to hand it to each user once, and clearing site data means re-entering it.

`CULPRIT_DEMO_ONLY=true` (`src/culprit/web/app.py:130-140`) makes `POST /api/runs` return 403 for any
repository other than the two bundled demos (`demo/churn-model`, `demo/fraud-risk`, resolved against
the server's working directory), and forces `auto_approve=False` for those runs. It also rewrites
`POST /api/demo/init` to always (re)generate `demo/churn-model` with `force=True`.

Route-by-route, from the FastAPI decorators in `src/culprit/web/app.py`:

| Route | Token required? | Restricted by `CULPRIT_DEMO_ONLY`? |
| --- | --- | --- |
| `GET /` (dashboard) | no | no |
| `GET /api/health` | no | no |
| `GET /api/config` | no | no |
| `POST /api/demo/init` | **yes** | yes — dest forced to `demo/churn-model` |
| `GET /api/runs` | no | no |
| `POST /api/runs` | **yes** | **yes — 403 for any non-demo repo** |
| `GET /api/runs/{id}` | no | no |
| `GET /api/runs/{id}/events` | no | no |
| `GET /api/runs/{id}/stream` (SSE) | no | no |
| `POST /api/runs/{id}/respond` | **yes** | no |
| `GET /api/runs/{id}/report.md` | no | no |
| `GET /api/runs/{id}/pull_request.md` | no | no |
| `GET /api/runs/{id}/trace` | no | no |

### What the token does NOT protect

**Every `GET` in that table stays unauthenticated, even with `CULPRIT_API_TOKEN` set** (✅ verified
against a running container: `GET /api/runs`, `/api/runs/{id}`, `/report.md`, `/pull_request.md`,
`/trace` and `/events` all returned 200 with real content while `POST /api/runs` returned 401). So
`CULPRIT_API_TOKEN` prevents *writes* — starting runs, approving PRs, regenerating the demo. It does
**not** make the instance private: an anonymous visitor still reads the absolute path of every
repository you investigated, each run's task text and experiment results, the live reasoning stream,
`report.md` and `pull_request.md` (i.e. the proposed fix diff and quoted source), and `trace.jsonl`.

If your run data is sensitive, put HTTP auth (basic auth, an OAuth2 proxy, mTLS, a VPN or an SSH
tunnel) in front of the whole app at the reverse proxy, and treat the built-in token as defence in
depth only. Two more honest caveats about it:

* it is compared with a plain `!=` on the whole header (`app.py:30`), not a constant-time compare;
* the browser's `EventSource` cannot send an `Authorization` header, which is why the SSE endpoint has
  no dependency in the first place. Any auth you add in a proxy must be cookie- or IP-based for the
  stream to keep working.

### Other hardening worth doing

* **Publish on loopback and proxy** (`-p 127.0.0.1:8000:8000`), which is the compose default. Binding
  the process itself to `0.0.0.0` on a host — `culprit serve --host 0.0.0.0` — removes the only
  protection an untokened instance has, and nothing in Culprit warns you about that combination.
* **A token alone is not a sandbox.** Anyone holding the token can start a run on any repository
  unless `CULPRIT_DEMO_ONLY=true` is also set. And the demo allow-list is resolved against the
  serving process's working directory, so `demo/churn-model` means *that server's* `demo/`
  directory — do not let untrusted users write there.
* **Never mount host credentials you do not want the experiment command to read.** The subprocess
  inherits the container's environment: `AWS_*`, `GITHUB_TOKEN`, `SLACK_WEBHOOK_URL`. With
  `GITHUB_TOKEN` set, `open_pull_request` really pushes a branch and opens a PR — behind the approval
  interrupt, but approval is a single unauthenticated-if-untokened POST away.
* **Keep `CULPRIT_AUTO_APPROVE=false`** on any shared instance. `true` removes the human approval
  interrupt entirely, so `open_pull_request` fires without asking.
* **Run it in the container, not on the host**, so the arbitrary shell command is confined to the
  container filesystem and the mounted volume. Consider `--read-only` with a writable `/data`,
  `--cap-drop ALL`, `--pids-limit`, `--memory`, and a `--network` that cannot reach your metadata
  service (`169.254.169.254`) if you are on a cloud VM. **UNVERIFIED:** these hardening flags were not
  exercised in this session.
* The guard-rails (`CULPRIT_MAX_EXPERIMENTS`, `CULPRIT_MAX_TOOL_CALLS`, `CULPRIT_MAX_REPEATED_CALLS`,
  `CULPRIT_EXPERIMENT_TIMEOUT`) bound cost and runaway loops, not privilege. A timed-out experiment
  kills the whole process group (`os.killpg`, `investigation.py:257`) on POSIX.

---

## Configuration reference

Complete list of the 21 variable names read by `src/culprit/settings.py`, plus the three read elsewhere in
the code (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CULPRIT_DEMO_TODAY`). Standard AWS credential
variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE`) are not
read by Culprit at all — boto3 picks them up. The three compose-only variables
(`CULPRIT_HOST_PORT`, `CULPRIT_BIND_ADDRESS`, `CULPRIT_CONTAINER_NAME`) are in
[§1](#compose-only-variables); they are read by `docker-compose.yml`, not by Culprit.

A `.env` file in the **working directory** is loaded automatically by python-dotenv, but it never
overrides a variable that is already in the process environment. ✅ Verified inside the image:
`/data/.env` with `CULPRIT_MAX_EXPERIMENTS=7` produced `max_experiments 7`, the same process started
from `/tmp` fell back to `10`, and adding `-e CULPRIT_MAX_EXPERIMENTS=10` to the container made the
environment win (`10`) over the file. Since `docker-compose.yml` sets every guard-rail explicitly, a
`/data/.env` cannot change them under compose — use the compose `.env` (which feeds those same
`${...}` values) instead. Note also that for every variable below, an **empty value counts as unset**
and the default is used (`_env()`, `settings.py:18-20`).

### Model

| Variable | Default | Meaning |
| --- | --- | --- |
| `CULPRIT_MODEL_PROVIDER` | `bedrock` | `bedrock` \| `anthropic` \| `openai` \| `scripted`. Lower-cased. Anything else fails preflight. `scripted` is the offline deterministic model used by the tests and the compose default. |
| `CULPRIT_MODEL_ID` | provider default | Bedrock: the Strands default (`global.anthropic.claude-sonnet-4-6` with strands-agents 1.55.1). Anthropic: `claude-sonnet-4-5`. OpenAI: `gpt-4.1`. |
| `CULPRIT_MODEL_TEMPERATURE` | `0.2` | Parsed with a bare `float()` — a non-numeric value raises `ValueError` and crashes the process at startup, unlike the integer settings, which fall back to their default. |
| `CULPRIT_MODEL_MAX_TOKENS` | `4096` | Passed to the Bedrock and Anthropic models. **Not** passed to the OpenAI model. |
| `AWS_REGION` | unset | Region for the Bedrock client. Required for `bedrock`: preflight fails without a resolvable region. |
| `AWS_DEFAULT_REGION` | unset | Fallback used when `AWS_REGION` is not set. |
| `ANTHROPIC_API_KEY` | unset | Read directly with `os.getenv` in `RunManager.preflight`; required for `CULPRIT_MODEL_PROVIDER=anthropic`. |
| `OPENAI_API_KEY` | unset | Same, for `CULPRIT_MODEL_PROVIDER=openai`. |

There is deliberately **no fallback** to the offline model: if the provider is misconfigured, the run
refuses to start.

### Storage

| Variable | Default | Meaning |
| --- | --- | --- |
| `CULPRIT_RUNS_DIR` | `runs` | Relative to the process working directory, `~` expanded, resolved to an absolute path and created at startup. The image sets `/data/runs`; compose pins it. |

### Guard-rails

| Variable | Default | Meaning |
| --- | --- | --- |
| `CULPRIT_MAX_EXPERIMENTS` | `10` | Experiments per investigation; the budget hook cancels `run_experiment` beyond it. Bisection needs about `log2(n)+3`. |
| `CULPRIT_EXPERIMENT_TIMEOUT` | `600` | **Ceiling**, in seconds: the effective per-experiment timeout is `min(project.experiment_timeout_s, this)`. It does not apply to `test_command`, which uses `min(project.experiment_timeout_s, 900)`. |
| `CULPRIT_MAX_TOOL_CALLS` | `60` | Hard cap on tool calls per investigation (loop guard). |
| `CULPRIT_MAX_REPEATED_CALLS` | `3` | Identical (name + input) tool calls tolerated before the loop guard cancels them. |
| `CULPRIT_APPROVAL_TOOLS` | `open_pull_request` | Comma-separated tools that pause for human approval. Setting it to `""` does **not** disable approvals (empty = unset → default restored); only a separator-only value such as `" , "` yields an empty set. |
| `CULPRIT_AUTO_APPROVE` | `false` | `true` skips the approval interrupt entirely. Truthy values: `1`, `true`, `yes`, `on` (case-insensitive). |

### Web

| Variable | Default | Meaning |
| --- | --- | --- |
| `CULPRIT_HOST` | `127.0.0.1` | Bind address for `culprit serve`. The image sets `0.0.0.0` and compose pins it — inside a container it must stay `0.0.0.0` (the healthcheck fails otherwise). |
| `CULPRIT_PORT` | `8000` | Bind port (container-internal under compose; use `CULPRIT_HOST_PORT` for the host side). |
| `CULPRIT_API_TOKEN` | unset | Bearer token for the three mutating POST routes. Unset = no auth at all. See [SECURITY](#security). |
| `CULPRIT_DEMO_ONLY` | `false` | Restricts `POST /api/runs` to the two bundled demo repositories. |

### Integrations

| Variable | Default | Meaning |
| --- | --- | --- |
| `GITHUB_TOKEN` | unset | Without it, `open_pull_request` writes `pull_request.md` into the run directory instead of pushing. With it, the branch is pushed and a real PR is opened. |
| `GITHUB_REPO` | auto-detected | `owner/name`, overriding remote detection. |
| `GITHUB_API_URL` | `https://api.github.com` | Point at GitHub Enterprise. |
| `SLACK_WEBHOOK_URL` | unset | Without it, `notify` appends to `notifications.jsonl` in the run directory. |

### Demo generation

| Variable | Default | Meaning |
| --- | --- | --- |
| `CULPRIT_DEMO_TODAY` | today's date | `YYYY-MM-DD`; overrides the demo's notion of "today" so generated commit SHAs are reproducible. Read only by `src/culprit/demo/builder.py`. |

### Provider extras

The dashboard image installs the base package only, which covers `scripted` and `bedrock` (boto3
arrives as a hard dependency of strands-agents). The `anthropic` and `openai` providers need the
matching extra — build a derived image if you want them:

```dockerfile
FROM culprit-dash
USER root
RUN pip install --no-cache-dir 'strands-agents[anthropic]'
USER culprit
```

**UNVERIFIED:** that derived image was not built here.

### `.culprit.yaml`

Per-repository configuration, parsed into `ProjectConfig` (`src/culprit/models.py`). Only
`experiment_command` is required; unknown keys are silently ignored. A full annotated example is
`config/culprit.example.yaml`.

---

## Volumes: what lives where

Everything Culprit persists is under one directory (`CULPRIT_RUNS_DIR`, `/data/runs` in the image),
plus the demo repositories it generates in the working directory (`/data/demo/...`). The compose file
mounts one named volume at `/data`, so both are covered. That volume is pinned to the name
`culprit-data` (`volumes: culprit-data: name: culprit-data` in `docker-compose.yml`) rather than
Compose's default project prefix, so the plain-docker and compose paths address the same volume — ✅
verified with `docker volume ls` after `docker compose up`.

```
/data
├── demo/churn-model/          generated demo repo (only if you built it)
├── demo/fraud-risk/           generated by `culprit demo init-secondary`
└── runs/
    ├── _repos/<name>/         clones of investigated git URLs
    ├── _watch/<hash>.json     `culprit watch` dedup state (one investigation per regression window)
    └── <run_id>/
        ├── run.json           the run record the API serves
        ├── project.json       the resolved .culprit.yaml
        ├── events.jsonl       the timeline the dashboard replays (append-only)
        ├── trace.jsonl        every model call and tool call
        ├── session/           the Strands conversation session (needed to resume an approval)
        ├── metrics/           raw metric JSON per experiment
        ├── bin/               a `python` symlink shim put on PATH for the experiment command
        ├── fix/               git worktree of the fix branch — kept after completion, on purpose
        ├── worktrees/         per-commit experiment worktrees — deleted when the run finishes
        ├── report.md/.json    the incident report
        ├── pull_request.md    only when no GITHUB_TOKEN was configured
        └── notifications.jsonl only when no SLACK_WEBHOOK_URL was configured
```

Two consequences worth knowing:

* **A run is only resumable across a restart if it is `awaiting_human`.** On startup `RunManager`
  rewrites every run that is still `running`/`reporting` on disk to `failed`
  (`recover_stale_runs`, `service.py:84-102`) — an interrupted investigation cannot be continued.
  An `awaiting_human` run resumes fine from a different process, which is what makes
  `docker compose restart` safe for pending approvals, and what lets the nightly watcher hand a run
  over to the dashboard ([§5](#5-nightly-automation)). It is also the mechanism behind the
  one-process constraint at the top of this document.
* **`fix/` worktrees are registered inside the *target* repository's `.git`.** Removing a run
  directory by hand leaves a dangling worktree registration there. `culprit clean <run-id>
  [--delete-branch]` is the supported way to remove them.

## Upgrading

```bash
git pull
docker compose build --pull
docker compose up -d
```

The volume is untouched, so all previous runs stay readable. Before upgrading, check that no run is
mid-investigation: a restart marks `running` runs as `failed` (see above). `awaiting_human` runs
survive and can be approved after the restart.

Downgrade path: run records are plain JSON validated by pydantic models. Older records missing fields
that a newer model requires would fail to load; there is no migration tooling. **UNVERIFIED:** no
cross-version upgrade was executed here — this repository has a single released version (0.1.0).

## Backup and retention

There is no built-in retention or pruning. Everything is files, so back up the volume. The commands
below use the compose volume name `culprit-data` and the compose image `culprit-dashboard:local`
(with plain Docker, the image you built is `culprit-dash` — both contain `tar`):

```bash
# back up (-u 0 so tar can read files owned by uid 10001)
docker run --rm -u 0 -v culprit-data:/data -v "$PWD:/backup" culprit-dashboard:local \
  tar czf /backup/culprit-$(date +%F).tar.gz -C /data .

# restore into a fresh volume (-u 0 so tar can restore ownership)
docker volume create culprit-restored
docker run --rm -u 0 -v culprit-restored:/data -v "$PWD:/backup" culprit-dashboard:local \
  tar xzf /backup/culprit-2026-09-13.tar.gz -C /data
```

✅ Both executed here against the live compose volume: a 2.1 MB archive with 1 357 entries, and the
restored volume listed the same six `runs/<run_id>` directories plus `demo/churn-model` and
`demo/fraud-risk`. **Check the archive size**: a name typo creates an empty volume on the fly and
produces a ~135-byte archive containing only `./` and `./runs/` — a silent backup failure. Take the
backup while no run is executing; `session/` and `events.jsonl` are written continuously.

Retention notes:

* Run directories are self-contained; deleting `runs/<run_id>/` deletes that run from the dashboard.
  Run `culprit clean <run_id>` **first** so the git worktrees it registered in the target repository
  are removed too.
* Size is dominated by `session/` (one JSON file per conversation message) and `fix/` (a full git
  worktree of the target repo). Experiment worktrees are already deleted automatically when a run
  completes or fails.
* `events.jsonl` and `trace.jsonl` contain the model's reasoning, tool inputs and source excerpts —
  treat backups with the same sensitivity as the source repository.

## Logs and observability

`culprit serve` starts uvicorn with `log_level="warning"` hardcoded (`src/culprit/cli.py`), and there
is no setting to raise it. The server log is therefore two lines and stays that way: ✅ after three
full investigations, `docker compose logs culprit | wc -l` printed `2` (the startup banner). Do not
expect an access log.

The real trace is per run, inside `CULPRIT_RUNS_DIR`:

* `runs/<id>/events.jsonl` — the timeline the dashboard renders, also served by
  `GET /api/runs/{id}/events` and streamed by `/stream`;
* `runs/<id>/trace.jsonl` — every model call and tool call with durations, served by
  `GET /api/runs/{id}/trace`;
* `docker inspect --format '{{json .State.Health}}' culprit` — the healthcheck's own probe output.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Container is `healthy` but the browser gets an empty reply / connection reset | `CULPRIT_HOST` is `127.0.0.1` *inside* the container (usually a `.env` copied from `.env.example`). Compose pins `0.0.0.0`; with plain `docker run`, drop the `-e CULPRIT_HOST=...`. Current images fail the healthcheck loudly in this case. |
| Every run fails preflight with a credentials/region error right after adding a `.env` | `.env.example` sets `CULPRIT_MODEL_PROVIDER=bedrock`. Set it back to `scripted`, or supply AWS credentials. |
| `400 {"detail":"<path> is not a git repository"}` for a repo you mounted | git's dubious-ownership guard: the repo is not owned by the container user. `chown -R 10001:10001` it, or run the container with `-u "$(id -u):$(id -g)"` ([§3](#3-investigating-your-own-repository)). |
| Run finishes `completed` with `0` experiments and a low-confidence culprit | The container user cannot write the target repo's `.git`: the events show `git worktree add … Permission denied`. Same fix as above — `safe.directory` alone is not enough. |
| `PermissionError` on `/data` at startup | Bind-mounted host directory not writable by uid 10001. `chown 10001:10001` it, or run with `-u "$(id -u):$(id -g)"`. |
| Dashboard loads but a run fails instantly with "no AWS credentials found" | `CULPRIT_MODEL_PROVIDER=bedrock` (the code default) with no credentials in the container. Set `CULPRIT_MODEL_PROVIDER=scripted` or mount credentials. |
| An in-flight run suddenly shows `failed: the Culprit process exited while this run was executing` | A second Culprit process opened the same runs directory (a CLI command in the serving container, a second replica, `--workers 2`). One process per runs dir. |
| The SSE timeline never updates for a run started elsewhere | Only the process that started the run streams live events; another process replays `events.jsonl` and then sends `event: done`. |
| `Error response from daemon: Conflict. The container name "/culprit" is already in use` | Another container already owns that name — set `CULPRIT_CONTAINER_NAME=something-else`. |
| Port 8000 already in use | `CULPRIT_HOST_PORT=18080 docker compose up -d`. |
| Backup archive is ~135 bytes | The volume name in the `docker run` line does not exist, so Docker created an empty one. Use `culprit-data` (see [Backup](#backup-and-retention)). |
| `FileExistsError: ... demo/churn-model already exists (use --force to recreate)` | `culprit demo init` refuses to overwrite. Add `--force`. |
| Timeline stops updating behind a proxy | SSE is being buffered — set `proxy_buffering off` (nginx) and a long `proxy_read_timeout`. Also check you are not proxying under a sub-path, which is unsupported. |
| A run is stuck at `running` after a restart | Expected: it was marked `failed` on startup. Start a new investigation. |
| Container is `unhealthy` | `docker inspect --format '{{json .State.Health}}' culprit` — the healthcheck hits `/api/health` on `CULPRIT_PORT` and refuses a loopback `CULPRIT_HOST`. |
| Every experiment fails with `ModuleNotFoundError` for a package Culprit itself depends on | The `<run_dir>/bin/python` shim is a symlink and Culprit is installed in a virtualenv — see the box in [§4](#4-bare-metal-systemd). |
| `ValueError: could not convert string to float` at startup | A malformed `CULPRIT_MODEL_TEMPERATURE`. Unlike the integer settings it has no fallback. |

## Verification status

Executed while writing and revising this document, on Linux/amd64 (Docker Engine 29.1.3, Compose
v2.39.1), always with `CULPRIT_MODEL_PROVIDER=scripted` and a host port in the 181xx range because
8000 was busy:

* **Build and run:** `docker build -f Dockerfile.dashboard -t culprit-dash .` → exit 0, 828 MB,
  `amd64/linux`, user `culprit` (uid 10001), `git 2.47.3`, `Python 3.11.16`; both `docker compose up
  -d --build` and `docker run` reached `Up (healthy)` and served `/api/health` and `/api/config`.
* **Golden path in the container** (compose): `POST /api/demo/init` → 200 in 7.4 s; a run on
  `demo/churn-model` reached `awaiting_human`; `POST …/respond {"decision":"approve"}` → `completed`,
  5 experiments, culprit `c249462`, f1 `0.8301 → 0.6624 → 0.8111`, guard test
  `tests/test_encoding_consistency.py`, `report.md` 200 (1 633 B).
* **Your own repository** ([§3](#3-investigating-your-own-repository)): both recipes ran a mounted
  host repo to `completed` with the same numbers; the two documented failure modes (dubious
  ownership → misleading 400; `safe.directory` only → 0 experiments and a wrong culprit) were
  reproduced.
* **Nightly automation** ([§5](#5-nightly-automation)): `record-nightly` exit 0, `watch --once`
  exit 3, the resulting run approved from the dashboard container → `completed`; `culprit doctor`
  exit 0 inside the image.
* **Configuration and safety rails:** a `.env` copied verbatim from the committed `.env.example`
  still yields a reachable dashboard (HTTP 200) because compose pins `CULPRIT_HOST`/`CULPRIT_PORT`/
  `CULPRIT_RUNS_DIR`; `-e CULPRIT_HOST=127.0.0.1` makes the container go `unhealthy`; a second
  `RunManager` on the same volume marked an in-flight run `failed`; `docker compose exec … demo
  init-secondary` created `demo/fraud-risk` (`pr_auc 0.7921 → 0.4384`); `down`/`down -v` behave as
  documented; the server log stayed at 2 lines.
* **Security behaviour** of the running container with `CULPRIT_API_TOKEN` and `CULPRIT_DEMO_ONLY=true`:
  unauthenticated `POST /api/runs` and `POST /api/demo/init` → 401; with the token, a non-demo repo →
  403; and with no token at all, every `GET` route → 200 with real content (the leak documented in
  [SECURITY](#security)).
* **Backup/restore** of the compose volume: 2.1 MB archive, 1 357 entries, restored into a fresh
  volume with the same contents.

Not executed anywhere, and therefore **not** claimed: any real model provider (bedrock / anthropic /
openai) in a container, the bare-metal installs and the systemd unit, the nightly timer/cron unit,
the nginx and Caddy configurations, private git URL clones, the derived image for the
anthropic/openai extras, the container-hardening flags, and the AgentCore deployment chain.
