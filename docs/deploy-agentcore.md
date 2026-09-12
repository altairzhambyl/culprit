# Deploying Culprit to Amazon Bedrock AgentCore Runtime

Culprit's `culprit.agentcore_app` module implements the AgentCore Runtime service contract:

* `POST /invocations` — JSON payload in, streamed JSON events out (SSE)
* `GET /ping` — health
* port **8080**, container platform **linux/arm64**

The same module runs locally, which is how the contract is exercised in this repository. An actual
deployment to AgentCore has **not** been performed by the authors yet; the steps below follow the
AgentCore documentation and should be treated as untested until you run them.

## 1. Try the contract locally

```bash
pip install -e ".[agentcore]"
CULPRIT_MODEL_PROVIDER=scripted python -m culprit.agentcore_app      # or bedrock with AWS credentials
curl localhost:8080/ping
curl -N -X POST localhost:8080/invocations -H 'content-type: application/json' \
     -d '{"action": "investigate", "repo": "demo", "task": "nightly F1 dropped"}'
# ... streams {"event": {...}} lines and ends with {"run": {"status": "awaiting_human", ...}}
curl -N -X POST localhost:8080/invocations -H 'content-type: application/json' \
     -d '{"action": "respond", "run_id": "<run_id from above>", "decision": "approve", "comment": "LGTM"}'
```

Payload reference:

| action | fields | result |
|---|---|---|
| `investigate` | `repo` (git URL, path inside the container, or `"demo"`), `metric?`, `task?` | streams timeline events, then the run record (`awaiting_human` or `completed`) |
| `respond` | `run_id`, `decision` (`approve`/`reject`) + `comment?`, or `answer` for `ask_human` | streams the rest of the run, then the record with the `report` |
| `status` | `run_id` | the run record |
| `runs` | — | all runs in this session's storage |

## 2. Build and push the image

```bash
AWS_REGION=us-west-2
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO=$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/culprit

aws ecr create-repository --repository-name culprit --region $AWS_REGION || true
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $REPO
docker buildx build --platform linux/arm64 -t $REPO:latest --push .
```

## 3. Create the runtime

Either with the AgentCore CLI (recommended; it also creates the execution role):

```bash
npm install -g @aws/agentcore
agentcore create      # wizard: name "culprit", existing container image → $REPO:latest
agentcore deploy
agentcore invoke '{"action": "investigate", "repo": "demo"}'
```

or directly with boto3:

```python
import boto3
client = boto3.client("bedrock-agentcore-control", region_name="us-west-2")
runtime = client.create_agent_runtime(
    agentRuntimeName="culprit",
    agentRuntimeArtifact={"containerConfiguration": {"containerUri": f"{REPO}:latest"}},
    networkConfiguration={"networkMode": "PUBLIC"},
    roleArn="arn:aws:iam::<account>:role/CulpritAgentRuntimeRole",
    environmentVariables={"CULPRIT_MODEL_PROVIDER": "bedrock", "AWS_REGION": "us-west-2"},
)
```

The execution role needs `bedrock:InvokeModel` / `bedrock:InvokeModelWithResponseStream` on the
Claude model you use, ECR pull permissions, and CloudWatch Logs. Add `GITHUB_TOKEN` /
`SLACK_WEBHOOK_URL` as environment variables (or fetch them from Secrets Manager) to make the PR and
notification real; without them Culprit writes both locally inside the session.

## 4. Invoke

```python
import boto3, json, uuid
rt = boto3.client("bedrock-agentcore", region_name="us-west-2")
session_id = str(uuid.uuid4())                     # keep it for the approval round-trip
resp = rt.invoke_agent_runtime(
    agentRuntimeArn=runtime["agentRuntimeArn"],
    runtimeSessionId=session_id,
    payload=json.dumps({"action": "investigate", "repo": "https://github.com/acme/churn-model.git"}).encode(),
)
for chunk in resp["response"].iter_lines():
    if chunk.startswith(b"data:"):
        print(json.loads(chunk[5:]))
# ...later, same runtimeSessionId:
rt.invoke_agent_runtime(agentRuntimeArn=..., runtimeSessionId=session_id,
    payload=json.dumps({"action": "respond", "run_id": run_id, "decision": "approve"}).encode())
```

### Notes and limits

* **Session scope.** Runs are stored under `CULPRIT_RUNS_DIR` (`/tmp/culprit/runs` in the image), which
  lives inside the session's microVM. The approval round-trip must reuse the same `runtimeSessionId`.
  For approvals that span sessions, swap `FileSessionManager` for Strands' `S3SessionManager` and point
  `CULPRIT_RUNS_DIR` at a mounted/persistent location — a one-line change in
  `culprit/agents/investigator.py` plus S3 permissions on the role (listed as *not done* in
  `TODO_CHECKLIST.md`).
* **Repositories.** `repo` may be a clone URL (cloned into the session), or `"demo"` to generate the
  bundled demo project. Private repos need credentials in the URL or a git credential helper.
* **Observability.** The container starts with `opentelemetry-instrument`; enable CloudWatch
  Transaction Search / AgentCore Observability to see Strands' spans (the `trace_attributes` carry
  `culprit.run_id` and `culprit.metric`).
* **Experiments run inside the runtime.** The demo trains in ~1.5 s per experiment; for heavy training
  jobs, replace the `experiment_command` with a job submission (SageMaker, Batch) that writes the
  metrics JSON to `{out}` — the tool only needs the JSON.
