#!/usr/bin/env bash
# Reproducible demonstration of the autonomous trigger:
#   nightly evaluation -> regression above threshold -> Culprit starts itself -> human is contacted
#   only when the pull request needs approval.
#
# Usage: scripts/nightly.sh [repo-path] [scenario]        (defaults: demo/churn-model, churn)
# Model: whatever CULPRIT_MODEL_PROVIDER says (bedrock by default; `scripted` runs offline).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${1:-demo/churn-model}"
SCENARIO="${2:-churn}"

echo "== 1. Yesterday: five PRs merged. The metric store does not know about tonight's run yet."
culprit demo init --scenario "$SCENARIO" --dest "$REPO" --force --without-latest-nightly

echo
echo "== 2. 02:15 — the nightly job evaluates HEAD and records the result (this is what a cron job does)."
culprit record-nightly "$REPO"

echo
echo "== 3. 02:16 — the watcher notices the regression and starts Culprit (idempotent per good->bad window)."
set +e
culprit watch "$REPO" --once --channel "#ml-alerts"
STATUS=$?
set -e
case $STATUS in
  3) echo; echo "== Culprit paused for human approval. The notification above is the only time a person is involved."
     echo "   Approve with the printed command, or open the dashboard (culprit serve)." ;;
  0) echo "== nothing to do or run completed" ;;
  *) echo "== watch exited with status $STATUS"; exit $STATUS ;;
esac
