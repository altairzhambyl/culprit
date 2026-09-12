#!/usr/bin/env bash
# One-command golden-path demo. Usage: scripts/demo.sh [scripted|bedrock]
set -euo pipefail
cd "$(dirname "$0")/.."
export CULPRIT_MODEL_PROVIDER="${1:-${CULPRIT_MODEL_PROVIDER:-bedrock}}"
culprit demo init --force
culprit investigate demo/churn-model --task "Five PRs merged yesterday; the nightly F1 dropped."
