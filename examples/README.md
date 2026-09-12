# Examples

| File | What it is |
|---|---|
| `sample.culprit.yaml` | The configuration the demo repository ships with — copy it to your repo root as `.culprit.yaml`. |
| `sample_metrics_history.json` | The metric-store format Culprit reads (one record per nightly run: `run_id`, `timestamp`, `commit`, `metrics`). |
| `sample_incident_report.md` | The Markdown rendering of the structured `IncidentReport` at the end of a run — produced by the **offline test policy**, not by a real model. |
| `sample_pull_request.md` | The pull request the offline policy opened on the churn demo (local adapter output — title, body, diff). |

Reproduce them yourself:

```bash
culprit demo init --force
CULPRIT_MODEL_PROVIDER=scripted culprit investigate demo/churn-model      # offline, deterministic
culprit investigate demo/churn-model                                        # Amazon Bedrock
```
