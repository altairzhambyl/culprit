"""Source templates for the *secondary* demo repository (``fraud-risk``).

This scenario exists to evaluate a real model on a regression it has never seen: a different domain
(card-fraud scoring), different files, a different model family (histogram gradient boosting), a
different data set and a different failure mechanism (an inconsistent train/evaluation feature
transform introduced by a "performance" refactor). Nothing in :mod:`culprit.agents.scripted_model`
knows about this repository; the offline policy deliberately refuses to fix it.

``REFERENCE_FIX_*`` at the bottom are used **only** by the test suite to prove the scenario is
solvable (the metric recovers when the transform is made consistent). They are never shown to a model.
"""

from __future__ import annotations

README = """# fraud-risk

Scores card transactions for fraud risk. Nightly, `python -m fraudrisk.evaluate --config nightly`
retrains on the training split and reports PR-AUC (`pr_auc`) on the holdout split.

```bash
pip install -r requirements.txt
python -m fraudrisk.evaluate --config quick --out metrics.json
python -m pytest -q
```
"""

REQUIREMENTS_V1 = """numpy>=1.26
pandas>=2.1
scikit-learn>=1.4
pytest>=8.0
"""

REQUIREMENTS_V2 = """numpy>=1.26
pandas>=2.1
scikit-learn>=1.4,<2
pytest>=8.0
"""

GITIGNORE = """__pycache__/
*.pyc
.pytest_cache/
metrics.json
# written by the nightly evaluation job, not versioned
metrics/
"""

CULPRIT_YAML = """# Culprit configuration: how to reproduce this project's evaluation metric.
metric: pr_auc
higher_is_better: true
regression_threshold: 0.05
experiment_command: "python -m fraudrisk.evaluate --config {config} --out {out}"
test_command: "python -m pytest -q"
metrics_history: "metrics/nightly_history.json"
default_config: quick
nightly_config: nightly
experiment_timeout_s: 300
main_branch: main
"""

CONFIG_QUICK = """{
  "limit": 4000,
  "seed": 0,
  "max_iter": 120
}
"""

CONFIG_NIGHTLY = """{
  "seed": 0,
  "max_iter": 200
}
"""

PYTEST_INI = """[pytest]
testpaths = tests
addopts = -q
"""

MAKEFILE = """.PHONY: eval test lint

eval:
\tpython -m fraudrisk.evaluate --config quick --out metrics.json

test:
\tpython -m pytest -q

lint:
\truff check fraudrisk tests
"""

PRE_COMMIT = """repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
"""

DATA_DICTIONARY = """# Data dictionary — `data/transactions.csv`

| column | type | description |
|---|---|---|
| `txn_id` | string | transaction identifier |
| `amount` | float | transaction amount (account currency) |
| `merchant_category` | category | grocery, fuel, electronics, travel, online_marketplace, restaurant |
| `hour` | int | local hour of day (0-23) |
| `distance_from_home_km` | float | distance between merchant and cardholder home |
| `card_age_days` | int | days since the card was issued |
| `txn_last_24h` | int | number of transactions on the card in the previous 24 hours |
| `is_foreign` | 0/1 | merchant country differs from the card's country |
| `avg_amount_30d` | float | cardholder's average transaction amount over the last 30 days |
| `is_fraud` | 0/1 | label (confirmed fraud) |
"""

INIT_PY = '"""Card-fraud risk scoring."""\n'

DATASET_PY = '''"""Loading and splitting the transactions table."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "transactions.csv"
LABEL = "is_fraud"


def load_transactions(path: Path | str = DATA_PATH, limit: int | None = None, seed: int = 0) -> pd.DataFrame:
    """Load the transactions table; ``limit`` sub-samples rows for quick experiments."""
    df = pd.read_csv(path)
    if limit is not None and limit < len(df):
        df = df.sample(n=limit, random_state=seed).reset_index(drop=True)
    return df


def holdout_split(df: pd.DataFrame, holdout_fraction: float = 0.3, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stratified train/holdout split (fraud is rare, so stratify on the label)."""
    train_df, holdout_df = train_test_split(df, test_size=holdout_fraction, random_state=seed, stratify=df[LABEL])
    return train_df.reset_index(drop=True), holdout_df.reset_index(drop=True)
'''

PREPROCESS_V1 = '''"""Feature preprocessing: log-compress heavy-tailed amounts, standardize, one-hot the merchant category."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

NUMERIC_FEATURES = [
    "amount",
    "hour",
    "distance_from_home_km",
    "card_age_days",
    "txn_last_24h",
    "is_foreign",
    "avg_amount_30d",
]
# Monetary / distance columns are heavy-tailed; compress them before scaling.
LOG_FEATURES = ["amount", "distance_from_home_km", "avg_amount_30d"]
MERCHANT_CATEGORIES = ["grocery", "fuel", "electronics", "travel", "online_marketplace", "restaurant"]


class Preprocessor:
    """Fit on the training frame, then transform any frame the same way."""

    def __init__(self) -> None:
        self.scaler = StandardScaler()

    def _numeric(self, df: pd.DataFrame) -> np.ndarray:
        X = df[NUMERIC_FEATURES].astype(float).copy()
        for col in LOG_FEATURES:
            X[col] = np.log1p(X[col])
        return X.to_numpy()

    @staticmethod
    def _categorical(df: pd.DataFrame) -> np.ndarray:
        cat = df["merchant_category"]
        return np.stack([(cat == c).to_numpy(dtype=float) for c in MERCHANT_CATEGORIES], axis=1)

    def fit(self, df: pd.DataFrame) -> "Preprocessor":
        self.scaler.fit(self._numeric(df))
        return self

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        return np.hstack([self.scaler.transform(self._numeric(df)), self._categorical(df)])

    def fit_transform(self, df: pd.DataFrame) -> np.ndarray:
        return self.fit(df).transform(df)


def feature_names() -> list[str]:
    return NUMERIC_FEATURES + [f"merchant={c}" for c in MERCHANT_CATEGORIES]
'''

# Commit 2: a velocity-style ratio feature (harmless).
PREPROCESS_V2 = (
    PREPROCESS_V1.replace(
        """# Monetary / distance columns are heavy-tailed; compress them before scaling.
LOG_FEATURES = ["amount", "distance_from_home_km", "avg_amount_30d"]
""",
        """DERIVED_FEATURES = ["amount_vs_avg_ratio"]
# Monetary / distance columns (and the spend ratio) are heavy-tailed; compress them before scaling.
LOG_FEATURES = ["amount", "distance_from_home_km", "avg_amount_30d", "amount_vs_avg_ratio"]
""",
    )
    .replace(
        """    def _numeric(self, df: pd.DataFrame) -> np.ndarray:
        X = df[NUMERIC_FEATURES].astype(float).copy()
        for col in LOG_FEATURES:
            X[col] = np.log1p(X[col])
        return X.to_numpy()
""",
        """    def _numeric(self, df: pd.DataFrame) -> np.ndarray:
        X = df[NUMERIC_FEATURES].astype(float).copy()
        # How unusual is this amount for this cardholder?
        X["amount_vs_avg_ratio"] = X["amount"] / np.maximum(X["avg_amount_30d"], 1.0)
        for col in LOG_FEATURES:
            X[col] = np.log1p(X[col])
        return X.to_numpy()
""",
    )
    .replace(
        """def feature_names() -> list[str]:
    return NUMERIC_FEATURES + [f"merchant={c}" for c in MERCHANT_CATEGORIES]
""",
        """def feature_names() -> list[str]:
    return NUMERIC_FEATURES + DERIVED_FEATURES + [f"merchant={c}" for c in MERCHANT_CATEGORIES]
""",
    )
)

# Commit 6 — the culprit. A "single-pass" fit_transform builds the matrix once, applies the log
# compression in place and fits the scaler; ``transform`` now reuses the raw matrix helper and no
# longer applies the log compression. Training and evaluation features are therefore on different
# scales. Shapes are unchanged, nothing raises, the existing tests still pass.
PREPROCESS_V3 = '''"""Feature preprocessing: log-compress heavy-tailed amounts, standardize, one-hot the merchant category."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

NUMERIC_FEATURES = [
    "amount",
    "hour",
    "distance_from_home_km",
    "card_age_days",
    "txn_last_24h",
    "is_foreign",
    "avg_amount_30d",
]
DERIVED_FEATURES = ["amount_vs_avg_ratio"]
MATRIX_COLUMNS = NUMERIC_FEATURES + DERIVED_FEATURES
# Monetary / distance columns (and the spend ratio) are heavy-tailed; compress them before scaling.
LOG_FEATURES = ["amount", "distance_from_home_km", "avg_amount_30d", "amount_vs_avg_ratio"]
LOG_COLUMN_INDEX = [MATRIX_COLUMNS.index(c) for c in LOG_FEATURES]
MERCHANT_CATEGORIES = ["grocery", "fuel", "electronics", "travel", "online_marketplace", "restaurant"]


class Preprocessor:
    """Fit on the training frame, then transform any frame the same way."""

    def __init__(self) -> None:
        self.scaler = StandardScaler()

    @staticmethod
    def _raw_matrix(df: pd.DataFrame) -> np.ndarray:
        X = df[NUMERIC_FEATURES].astype(float).copy()
        # How unusual is this amount for this cardholder?
        X["amount_vs_avg_ratio"] = X["amount"] / np.maximum(X["avg_amount_30d"], 1.0)
        return X.to_numpy()

    @staticmethod
    def _categorical(df: pd.DataFrame) -> np.ndarray:
        cat = df["merchant_category"]
        return np.stack([(cat == c).to_numpy(dtype=float) for c in MERCHANT_CATEGORIES], axis=1)

    def fit_transform(self, df: pd.DataFrame) -> np.ndarray:
        # Single pass: build the matrix once, compress heavy tails in place, fit + scale.
        X = self._raw_matrix(df)
        X[:, LOG_COLUMN_INDEX] = np.log1p(X[:, LOG_COLUMN_INDEX])
        return np.hstack([self.scaler.fit_transform(X), self._categorical(df)])

    def fit(self, df: pd.DataFrame) -> "Preprocessor":
        self.fit_transform(df)
        return self

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        return np.hstack([self.scaler.transform(self._raw_matrix(df)), self._categorical(df)])


def feature_names() -> list[str]:
    return MATRIX_COLUMNS + [f"merchant={c}" for c in MERCHANT_CATEGORIES]
'''

MODEL_PY = '''"""Model definition."""

from __future__ import annotations

from sklearn.ensemble import HistGradientBoostingClassifier


def build_model(config: dict) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(
        max_iter=config.get("max_iter", 200),
        learning_rate=config.get("learning_rate", 0.08),
        max_leaf_nodes=config.get("max_leaf_nodes", 15),
        l2_regularization=config.get("l2", 0.1),
        random_state=config.get("seed", 0),
    )
'''

EVALUATE_V1 = '''"""Train on the training split, score the holdout split, report metrics.

Usage:
    python -m fraudrisk.evaluate --config quick --out metrics.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sklearn.metrics import average_precision_score, roc_auc_score

from fraudrisk.dataset import LABEL, holdout_split, load_transactions
from fraudrisk.model import build_model
from fraudrisk.preprocess import Preprocessor

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"


def load_config(name: str) -> dict:
    return json.loads((CONFIG_DIR / f"{name}.json").read_text())


def run(config: dict) -> dict:
    seed = config.get("seed", 0)
    df = load_transactions(limit=config.get("limit"), seed=seed)
    train_df, holdout_df = holdout_split(df, seed=seed)

    pre = Preprocessor()
    X_train = pre.fit_transform(train_df)
    X_holdout = pre.transform(holdout_df)
    y_train, y_holdout = train_df[LABEL].to_numpy(), holdout_df[LABEL].to_numpy()

    model = build_model(config).fit(X_train, y_train)
    scores = model.predict_proba(X_holdout)[:, 1]
    return {
        "pr_auc": round(float(average_precision_score(y_holdout, scores)), 4),
        "roc_auc": round(float(roc_auc_score(y_holdout, scores)), 4),
        "fraud_rate": round(float(y_holdout.mean()), 4),
        "n_train": int(len(train_df)),
        "n_holdout": int(len(holdout_df)),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="quick", help="name of a JSON file in configs/")
    parser.add_argument("--out", default=None, help="where to write the metrics JSON")
    args = parser.parse_args(argv)

    started = time.time()
    metrics = run(load_config(args.config))
    metrics["duration_s"] = round(time.time() - started, 2)
    print(json.dumps(metrics))
    if args.out:
        Path(args.out).write_text(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
'''

# Commit 4: an extra metric that matters to the fraud-ops team (harmless for pr_auc).
EVALUATE_V2 = (
    EVALUATE_V1.replace(
        """from sklearn.metrics import average_precision_score, roc_auc_score
""",
        """import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score
""",
    )
    .replace(
        """    model = build_model(config).fit(X_train, y_train)
    scores = model.predict_proba(X_holdout)[:, 1]
    return {
        "pr_auc": round(float(average_precision_score(y_holdout, scores)), 4),
        "roc_auc": round(float(roc_auc_score(y_holdout, scores)), 4),
""",
        """    model = build_model(config).fit(X_train, y_train)
    scores = model.predict_proba(X_holdout)[:, 1]
    return {
        "pr_auc": round(float(average_precision_score(y_holdout, scores)), 4),
        "roc_auc": round(float(roc_auc_score(y_holdout, scores)), 4),
        "recall_at_1pct": round(recall_at_alert_budget(y_holdout, scores, budget=0.01), 4),
""",
    )
    .replace(
        """def run(config: dict) -> dict:""",
        '''def recall_at_alert_budget(y_true, scores, budget: float = 0.01) -> float:
    """Share of fraud caught if analysts can review only the top ``budget`` fraction of transactions."""
    k = max(1, int(round(budget * len(scores))))
    top = np.argsort(-scores)[:k]
    return float(y_true[top].sum() / max(1, y_true.sum()))


def run(config: dict) -> dict:''',
    )
)

TEST_PIPELINE = '''"""Pipeline tests: shapes and a quick end-to-end evaluation."""

from fraudrisk.dataset import load_transactions
from fraudrisk.evaluate import load_config, run
from fraudrisk.preprocess import Preprocessor, feature_names


def test_preprocessor_shapes():
    df = load_transactions(limit=500)
    pre = Preprocessor()
    X = pre.fit_transform(df)
    assert X.shape == (500, len(feature_names()))
    assert pre.transform(df).shape == X.shape


def test_quick_evaluation_runs():
    metrics = run(load_config("quick"))
    assert 0.0 <= metrics["pr_auc"] <= 1.0
    assert 0.0 <= metrics["roc_auc"] <= 1.0
    assert metrics["n_train"] > metrics["n_holdout"] > 0
'''


# --- Used ONLY by tests to prove the scenario is solvable. Never shown to any model. -------------
REFERENCE_FIX_OLD = """    def transform(self, df: pd.DataFrame) -> np.ndarray:
        return np.hstack([self.scaler.transform(self._raw_matrix(df)), self._categorical(df)])
"""

REFERENCE_FIX_NEW = """    def transform(self, df: pd.DataFrame) -> np.ndarray:
        X = self._raw_matrix(df)
        X[:, LOG_COLUMN_INDEX] = np.log1p(X[:, LOG_COLUMN_INDEX])  # same compression as fit_transform
        return np.hstack([self.scaler.transform(X), self._categorical(df)])
"""
