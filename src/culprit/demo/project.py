"""Source templates for the demo target repository (``churn-model``).

The demo repository is a small but realistic tabular ML project. Its git history is built by
:mod:`culprit.demo.generator`: seven commits, one of which silently breaks the model. Every file
version below is real, runnable code — the nightly metrics in the demo are produced by actually
training the model at each commit.
"""

from __future__ import annotations

README_V1 = """# churn-model

Predicts which customers are likely to churn next month.

```bash
pip install -r requirements.txt
python -m churn.train --config smoke --out metrics.json
python -m pytest -q
```

Nightly evaluation runs `python -m churn.train --config full` on `main` and records the metrics.
"""

README_V2 = (
    README_V1
    + """
## Model card

See [MODEL_CARD.md](MODEL_CARD.md) for intended use, metrics and limitations.
"""
)

REQUIREMENTS_V1 = """numpy>=1.26
pandas>=2.1
scikit-learn>=1.4
pytest>=8.0
"""

REQUIREMENTS_V2 = """numpy>=1.26,<3
pandas>=2.1,<3
scikit-learn>=1.4,<2
pytest>=8.0
"""

GITIGNORE = """__pycache__/
*.pyc
.pytest_cache/
metrics.json
# nightly job artifacts are stored by the metrics service, not in git
nightly/
"""

CULPRIT_YAML = """# Culprit configuration: how to reproduce this project's evaluation metric.
metric: f1
higher_is_better: true
regression_threshold: 0.03
experiment_command: "python -m churn.train --config {config} --out {out}"
test_command: "python -m pytest -q"
metrics_history: "nightly/metrics_history.json"
default_config: smoke
nightly_config: full
experiment_timeout_s: 300
main_branch: main
"""

CONFIG_SMOKE = """{
  "sample": 2000,
  "seed": 42,
  "max_iter": 500
}
"""

CONFIG_FULL = """{
  "seed": 42,
  "max_iter": 1000
}
"""

INIT_PY = '"""Customer churn model."""\n'

DATA_PY = '''"""Data loading and splitting for the churn model."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "customers.csv"
TARGET = "churned"


def load_customers(path: Path | str = DATA_PATH, sample: int | None = None, seed: int = 42) -> pd.DataFrame:
    """Load the customer table, optionally sub-sampling it for quick experiments."""
    df = pd.read_csv(path)
    if sample is not None and sample < len(df):
        df = df.sample(n=sample, random_state=seed).reset_index(drop=True)
    return df


def split(df: pd.DataFrame, test_size: float = 0.25, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stratified train/test split with a fixed seed so evaluations are reproducible."""
    train_df, test_df = train_test_split(df, test_size=test_size, random_state=seed, stratify=df[TARGET])
    return train_df.reset_index(drop=True), test_df.reset_index(drop=True)
'''

FEATURES_V1 = '''"""Feature engineering for the churn model."""

from __future__ import annotations

import numpy as np
import pandas as pd

NUMERIC_COLUMNS = ["tenure_months", "monthly_charges", "total_charges", "num_support_tickets"]
CATEGORICAL_COLUMNS = ["contract", "plan", "payment_method", "region"]

# Explicit, stable integer codes shared by training and evaluation.
CATEGORY_MAPS = {
    "contract": {"month-to-month": 0, "one-year": 1, "two-year": 2},
    "plan": {"basic": 0, "standard": 1, "premium": 2},
    "payment_method": {"electronic_check": 0, "bank_transfer": 1, "credit_card": 2},
    "region": {"north": 0, "south": 1, "east": 2, "west": 3},
}


def feature_columns() -> list[str]:
    return NUMERIC_COLUMNS + CATEGORICAL_COLUMNS


def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """Map categorical strings to the integer codes in CATEGORY_MAPS."""
    out = df.copy()
    for col in CATEGORICAL_COLUMNS:
        out[col] = out[col].map(CATEGORY_MAPS[col]).fillna(-1).astype(int)
    return out


def build_matrix(df: pd.DataFrame) -> np.ndarray:
    """Return the model input matrix for a dataframe of raw customer rows."""
    encoded = encode_categoricals(df)
    return encoded[feature_columns()].to_numpy(dtype=float)
'''

FEATURES_V2 = '''"""Feature engineering for the churn model."""

from __future__ import annotations

import numpy as np
import pandas as pd

NUMERIC_COLUMNS = ["tenure_months", "monthly_charges", "total_charges", "num_support_tickets"]
DERIVED_COLUMNS = ["tenure_bucket", "charges_per_month"]
CATEGORICAL_COLUMNS = ["contract", "plan", "payment_method", "region"]

# Explicit, stable integer codes shared by training and evaluation.
CATEGORY_MAPS = {
    "contract": {"month-to-month": 0, "one-year": 1, "two-year": 2},
    "plan": {"basic": 0, "standard": 1, "premium": 2},
    "payment_method": {"electronic_check": 0, "bank_transfer": 1, "credit_card": 2},
    "region": {"north": 0, "south": 1, "east": 2, "west": 3},
}


def feature_columns() -> list[str]:
    return NUMERIC_COLUMNS + DERIVED_COLUMNS + CATEGORICAL_COLUMNS


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add cheap derived signals: tenure in years (capped) and average spend per month."""
    out = df.copy()
    out["tenure_bucket"] = np.minimum(out["tenure_months"] // 12, 5)
    out["charges_per_month"] = out["total_charges"] / np.maximum(out["tenure_months"], 1)
    return out


def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """Map categorical strings to the integer codes in CATEGORY_MAPS."""
    out = df.copy()
    for col in CATEGORICAL_COLUMNS:
        out[col] = out[col].map(CATEGORY_MAPS[col]).fillna(-1).astype(int)
    return out


def build_matrix(df: pd.DataFrame) -> np.ndarray:
    """Return the model input matrix for a dataframe of raw customer rows."""
    encoded = encode_categoricals(add_derived_features(df))
    return encoded[feature_columns()].to_numpy(dtype=float)
'''

# The culprit: "simplify" categorical encoding with pandas.factorize. Codes now depend on the order in
# which values first appear in *each* dataframe, so the training and evaluation frames get different
# encodings. Nothing crashes; the model just quietly gets worse.
FEATURES_V3 = '''"""Feature engineering for the churn model."""

from __future__ import annotations

import numpy as np
import pandas as pd

NUMERIC_COLUMNS = ["tenure_months", "monthly_charges", "total_charges", "num_support_tickets"]
DERIVED_COLUMNS = ["tenure_bucket", "charges_per_month"]
CATEGORICAL_COLUMNS = ["contract", "plan", "payment_method", "region"]


def feature_columns() -> list[str]:
    return NUMERIC_COLUMNS + DERIVED_COLUMNS + CATEGORICAL_COLUMNS


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add cheap derived signals: tenure in years (capped) and average spend per month."""
    out = df.copy()
    out["tenure_bucket"] = np.minimum(out["tenure_months"] // 12, 5)
    out["charges_per_month"] = out["total_charges"] / np.maximum(out["tenure_months"], 1)
    return out


def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """Encode categorical columns as integer codes.

    Uses pandas.factorize so that unseen category values never raise a KeyError and we no longer
    need to maintain hard-coded category maps.
    """
    out = df.copy()
    for col in CATEGORICAL_COLUMNS:
        out[col] = pd.factorize(out[col])[0]
    return out


def build_matrix(df: pd.DataFrame) -> np.ndarray:
    """Return the model input matrix for a dataframe of raw customer rows."""
    encoded = encode_categoricals(add_derived_features(df))
    return encoded[feature_columns()].to_numpy(dtype=float)
'''

TRAIN_V1 = '''"""Train and evaluate the churn classifier.

Usage:
    python -m churn.train --config smoke --out metrics.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from churn.data import TARGET, load_customers, split
from churn.features import build_matrix

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"


def load_config(name: str) -> dict:
    return json.loads((CONFIG_DIR / f"{name}.json").read_text())


def train_and_evaluate(config: dict) -> dict:
    seed = config.get("seed", 42)
    df = load_customers(sample=config.get("sample"), seed=seed)
    train_df, test_df = split(df, seed=seed)

    X_train, y_train = build_matrix(train_df), train_df[TARGET].to_numpy()
    X_test, y_test = build_matrix(test_df), test_df[TARGET].to_numpy()

    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression(max_iter=config.get("max_iter", 1000), C=config.get("C", 1.0))
    model.fit(scaler.transform(X_train), y_train)

    proba = model.predict_proba(scaler.transform(X_test))[:, 1]
    pred = (proba >= 0.5).astype(int)
    return {
        "f1": round(float(f1_score(y_test, pred)), 4),
        "auc": round(float(roc_auc_score(y_test, proba)), 4),
        "accuracy": round(float(accuracy_score(y_test, pred)), 4),
        "n_train": int(len(train_df)),
        "n_test": int(len(test_df)),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="smoke", help="name of a JSON file in configs/")
    parser.add_argument("--out", default=None, help="where to write the metrics JSON")
    args = parser.parse_args(argv)

    started = time.time()
    metrics = train_and_evaluate(load_config(args.config))
    metrics["duration_s"] = round(time.time() - started, 2)
    print(json.dumps(metrics))
    if args.out:
        Path(args.out).write_text(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
'''

TRAIN_V2 = '''"""Train and evaluate the churn classifier.

Usage:
    python -m churn.train --config smoke --out metrics.json [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from churn.data import TARGET, load_customers, split
from churn.features import build_matrix

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
log = logging.getLogger("churn.train")


def load_config(name: str) -> dict:
    return json.loads((CONFIG_DIR / f"{name}.json").read_text())


def train_and_evaluate(config: dict) -> dict:
    seed = config.get("seed", 42)
    df = load_customers(sample=config.get("sample"), seed=seed)
    train_df, test_df = split(df, seed=seed)
    log.info("loaded %d rows (train=%d, test=%d)", len(df), len(train_df), len(test_df))

    X_train, y_train = build_matrix(train_df), train_df[TARGET].to_numpy()
    X_test, y_test = build_matrix(test_df), test_df[TARGET].to_numpy()

    scaler = StandardScaler().fit(X_train)
    model = LogisticRegression(max_iter=config.get("max_iter", 1000), C=config.get("C", 1.0))
    model.fit(scaler.transform(X_train), y_train)

    proba = model.predict_proba(scaler.transform(X_test))[:, 1]
    pred = (proba >= 0.5).astype(int)
    metrics = {
        "f1": round(float(f1_score(y_test, pred)), 4),
        "auc": round(float(roc_auc_score(y_test, proba)), 4),
        "accuracy": round(float(accuracy_score(y_test, pred)), 4),
        "n_train": int(len(train_df)),
        "n_test": int(len(test_df)),
    }
    log.info("metrics: %s", metrics)
    return metrics


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="smoke", help="name of a JSON file in configs/")
    parser.add_argument("--out", default=None, help="where to write the metrics JSON")
    parser.add_argument("--seed", type=int, default=None, help="override the config seed")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    config = load_config(args.config)
    if args.seed is not None:
        config["seed"] = args.seed

    started = time.time()
    metrics = train_and_evaluate(config)
    metrics["duration_s"] = round(time.time() - started, 2)
    print(json.dumps(metrics))
    if args.out:
        Path(args.out).write_text(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
'''

TRAIN_V3 = TRAIN_V2.replace(
    '    model = LogisticRegression(max_iter=config.get("max_iter", 1000), C=config.get("C", 1.0))\n',
    "    model = LogisticRegression(\n"
    '        max_iter=config.get("max_iter", 1000),\n'
    '        C=config.get("C", 1.0),\n'
    '        class_weight=config.get("class_weight"),  # e.g. "balanced"\n'
    "    )\n",
)

TEST_SMOKE = '''"""Smoke test: the pipeline trains end-to-end on the small config."""

from churn.train import load_config, train_and_evaluate


def test_smoke_training_runs():
    metrics = train_and_evaluate(load_config("smoke"))
    assert 0.0 <= metrics["f1"] <= 1.0
    assert 0.0 <= metrics["auc"] <= 1.0
    assert metrics["n_train"] > metrics["n_test"] > 0
'''

MODEL_CARD = """# Model card: churn-model

**Task.** Binary classification — will a customer churn in the next billing cycle?

**Data.** Internal customer table (tenure, charges, support tickets, contract, plan, payment method, region).

**Model.** Logistic regression on standardized numeric features and integer-coded categoricals.

**Metrics.** Tracked nightly on the held-out split: F1 (primary), ROC-AUC, accuracy.

**Limitations.** Trained on one region set; do not use for pricing decisions without a fairness review.
"""


# The fix Culprit is expected to apply (used by the deterministic demo model and by tests).
FIX_OLD_TEXT = '''def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """Encode categorical columns as integer codes.

    Uses pandas.factorize so that unseen category values never raise a KeyError and we no longer
    need to maintain hard-coded category maps.
    """
    out = df.copy()
    for col in CATEGORICAL_COLUMNS:
        out[col] = pd.factorize(out[col])[0]
    return out
'''

FIX_NEW_TEXT = '''# Explicit, stable integer codes shared by training and evaluation. Encoding must never depend on
# the order in which values appear in a particular dataframe (see tests/test_encoding_consistency.py).
CATEGORY_MAPS = {
    "contract": {"month-to-month": 0, "one-year": 1, "two-year": 2},
    "plan": {"basic": 0, "standard": 1, "premium": 2},
    "payment_method": {"electronic_check": 0, "bank_transfer": 1, "credit_card": 2},
    "region": {"north": 0, "south": 1, "east": 2, "west": 3},
}


def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """Map categorical strings to the stable integer codes in CATEGORY_MAPS.

    Unknown values map to -1 instead of raising, which keeps the robustness of the previous
    factorize-based version without making codes depend on row order.
    """
    out = df.copy()
    for col in CATEGORICAL_COLUMNS:
        out[col] = out[col].map(CATEGORY_MAPS[col]).fillna(-1).astype(int)
    return out
'''

GUARD_TEST = '''"""Regression guard: categorical encoding must not depend on row order or on the split.

Added after an incident where switching to pandas.factorize made the training and evaluation
frames receive different integer codes, silently degrading the model.
"""

import pandas as pd

from churn.features import CATEGORICAL_COLUMNS, encode_categoricals


def _sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "contract": ["two-year", "month-to-month", "one-year", "month-to-month"],
            "plan": ["premium", "basic", "standard", "basic"],
            "payment_method": ["credit_card", "electronic_check", "bank_transfer", "credit_card"],
            "region": ["west", "north", "east", "south"],
        }
    )


def test_encoding_is_independent_of_row_order():
    df = _sample_frame()
    forward = encode_categoricals(df)
    backward = encode_categoricals(df.iloc[::-1].reset_index(drop=True)).iloc[::-1].reset_index(drop=True)
    for col in CATEGORICAL_COLUMNS:
        assert forward[col].tolist() == backward[col].tolist(), f"{col} codes changed with row order"


def test_encoding_is_consistent_across_splits():
    df = _sample_frame()
    full = encode_categoricals(df)
    for i in range(len(df)):
        single = encode_categoricals(df.iloc[[i]])
        for col in CATEGORICAL_COLUMNS:
            assert int(single[col].iloc[0]) == int(full[col].iloc[i]), f"{col} code differs when encoded alone"
'''
