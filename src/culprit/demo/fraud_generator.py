"""Secondary demo: ``fraud-risk`` — an *unseen* regression for evaluating a real model.

Domain: card-fraud scoring. Model: histogram gradient boosting. Failure: a "single-pass"
``fit_transform`` refactor applies the log compression of heavy-tailed features during training but
``transform`` (used for the holdout split) no longer does, so training and evaluation features are on
different scales. PR-AUC collapses; shapes are unchanged; the project's tests stay green.

The offline :class:`~culprit.agents.scripted_model.GoldenPathPolicy` knows nothing about this
repository — that is the point.
"""

from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from culprit.demo import fraud_project as F
from culprit.demo.builder import Scenario

DEFAULT_DEST = Path("demo") / "fraud-risk"
AUTHORS = [
    ("Marta Kowalczyk", "marta@example.com"),
    ("Dev Ramanathan", "dev@example.com"),
    ("Yusuf Adeyemi", "yusuf@example.com"),
]
CULPRIT_INDEX = 5  # zero-based index of the culprit commit
CATEGORIES = ["grocery", "fuel", "electronics", "travel", "online_marketplace", "restaurant"]


def make_transactions(n: int = 8000, seed: int = 11) -> pd.DataFrame:
    """Deterministic synthetic card transactions with a realistic (rare, structured) fraud signal."""
    rng = np.random.default_rng(seed)
    category = rng.choice(CATEGORIES, size=n, p=[0.28, 0.17, 0.13, 0.08, 0.20, 0.14])
    hour = rng.integers(0, 24, size=n)
    is_foreign = (rng.random(n) < 0.06).astype(int)
    card_age_days = rng.integers(30, 3000, size=n)
    txn_last_24h = rng.poisson(1.8, size=n)
    distance = rng.exponential(18.0, size=n).round(1)
    avg_amount_30d = np.exp(rng.normal(3.4, 0.7, size=n)).round(2)
    amount = np.exp(rng.normal(3.5, 1.0, size=n))

    night = ((hour < 6) | (hour >= 22)).astype(float)
    risky_cat = np.isin(category, ["electronics", "online_marketplace", "travel"]).astype(float)
    # Most of the signal lives in the heavy-tailed columns (spend vs. the cardholder's usual spend,
    # distance, card age) — exactly the ones the culprit commit stops log-compressing at evaluation.
    logit = (
        -4.2
        + 0.6 * night
        + 0.9 * is_foreign
        + 0.5 * risky_cat
        + 0.15 * txn_last_24h
        + 0.9 * (np.log1p(distance) - 3.0)
        - 0.0009 * (card_age_days - 1500)
        + 1.4 * (np.log1p(amount) - np.log1p(avg_amount_30d))
    )
    is_fraud = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    # Fraudulent transactions skew larger than the cardholder's usual spend.
    amount = np.where(is_fraud == 1, amount * rng.uniform(1.5, 4.0, size=n), amount).round(2)

    return pd.DataFrame(
        {
            "txn_id": [f"T{500000 + i}" for i in range(n)],
            "amount": amount,
            "merchant_category": category,
            "hour": hour.astype(int),
            "distance_from_home_km": distance,
            "card_age_days": card_age_days.astype(int),
            "txn_last_24h": txn_last_24h.astype(int),
            "is_foreign": is_foreign,
            "avg_amount_30d": avg_amount_30d,
            "is_fraud": is_fraud,
        }
    )


def _csv_text(df: pd.DataFrame) -> str:
    buf = io.StringIO()
    df.to_csv(buf, index=False, lineterminator="\n")
    return buf.getvalue()


def build_commits() -> list[dict]:
    csv_text = _csv_text(make_transactions())
    return [
        {
            "message": "feat: fraud-risk scoring pipeline\n\nHistogram gradient boosting on log-compressed, standardized features.",
            "author": AUTHORS[0],
            "files": {
                "README.md": F.README,
                "requirements.txt": F.REQUIREMENTS_V1,
                ".gitignore": F.GITIGNORE,
                ".culprit.yaml": F.CULPRIT_YAML,
                "configs/quick.json": F.CONFIG_QUICK,
                "configs/nightly.json": F.CONFIG_NIGHTLY,
                "data/transactions.csv": csv_text,
                "fraudrisk/__init__.py": F.INIT_PY,
                "fraudrisk/dataset.py": F.DATASET_PY,
                "fraudrisk/preprocess.py": F.PREPROCESS_V1,
                "fraudrisk/model.py": F.MODEL_PY,
                "fraudrisk/evaluate.py": F.EVALUATE_V1,
                "tests/__init__.py": "",
                "tests/test_pipeline.py": F.TEST_PIPELINE,
            },
        },
        {
            "message": "feat(preprocess): add amount-vs-30d-average ratio feature",
            "author": AUTHORS[1],
            "files": {"fraudrisk/preprocess.py": F.PREPROCESS_V2},
        },
        {
            "message": "chore: add Makefile and pre-commit config",
            "author": AUTHORS[2],
            "files": {"Makefile": F.MAKEFILE, ".pre-commit-config.yaml": F.PRE_COMMIT},
        },
        {
            "message": "feat(evaluate): report recall at a 1% alert budget",
            "author": AUTHORS[0],
            "files": {"fraudrisk/evaluate.py": F.EVALUATE_V2},
        },
        {
            "message": "docs: add data dictionary",
            "author": AUTHORS[2],
            "files": {"docs/DATA_DICTIONARY.md": F.DATA_DICTIONARY},
        },
        {
            "message": (
                "perf(preprocess): single-pass fit_transform, drop redundant feature recomputation\n\n"
                "Build the feature matrix once and compress heavy tails in place instead of materializing\n"
                "an intermediate DataFrame twice."
            ),
            "author": AUTHORS[1],
            "files": {"fraudrisk/preprocess.py": F.PREPROCESS_V3},
        },
        {
            "message": "chore(ci): pin scikit-learn upper bound, add pytest config",
            "author": AUTHORS[0],
            "files": {"requirements.txt": F.REQUIREMENTS_V2, "pytest.ini": F.PYTEST_INI},
        },
    ]


SCENARIO = Scenario(
    key="fraud",
    title="fraud-risk: holdout PR-AUC regression after five merges (unseen by the offline policy)",
    default_dest=DEFAULT_DEST,
    metric="pr_auc",
    metric_keys=("pr_auc", "roc_auc", "recall_at_1pct", "fraud_rate"),
    eval_module="fraudrisk.evaluate",
    nightly_config="nightly",
    commits=build_commits,
    culprit_index=CULPRIT_INDEX,
    # nightlies on days -5..-3 saw the baseline, -2..-1 the ratio feature, today the five merges
    nightly_schedule=[0, 0, 0, 1, 1, 6],
    commit_offsets=[
        timedelta(days=-8, hours=9),
        timedelta(days=-3, hours=11),
        timedelta(days=-1, hours=10),
        timedelta(days=-1, hours=12),
        timedelta(days=-1, hours=14),
        timedelta(days=-1, hours=16),
        timedelta(days=-1, hours=18),
    ],
    description=(
        "Card-fraud classifier whose holdout PR-AUC collapsed after five merges. One 'perf' refactor "
        "made fit_transform (training) log-compress heavy-tailed features while transform (evaluation) no "
        "longer does."
    ),
    mechanism_keywords=["log1p", "log", "transform", "fit_transform", "scale", "compress"],
    culprit_files=["fraudrisk/preprocess.py"],
)


def generate(dest: Path | str = DEFAULT_DEST, force: bool = False, quiet: bool = False) -> dict:
    from culprit.demo.builder import build_repository

    return build_repository(SCENARIO, dest, force=force, quiet=quiet)
