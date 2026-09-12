# Culprit on Amazon Bedrock AgentCore Runtime (linux/arm64 is required by AgentCore).
FROM --platform=linux/arm64 python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . "bedrock-agentcore>=0.1" "aws-opentelemetry-distro>=0.10.1"

# Runs (sessions, traces, reports, worktrees) live under /tmp inside the session's microVM.
ENV CULPRIT_RUNS_DIR=/tmp/culprit/runs \
    CULPRIT_MODEL_PROVIDER=bedrock \
    PYTHONUNBUFFERED=1 \
    GIT_AUTHOR_NAME=Culprit GIT_AUTHOR_EMAIL=culprit@example.com \
    GIT_COMMITTER_NAME=Culprit GIT_COMMITTER_EMAIL=culprit@example.com

EXPOSE 8080
CMD ["opentelemetry-instrument", "python", "-m", "culprit.agentcore_app"]
