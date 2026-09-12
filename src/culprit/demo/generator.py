"""Build the reproducible demo target repository.

``culprit demo init`` materializes ``churn-model``: a small tabular ML project with a seven-commit
history in which one innocent-looking refactor silently degrades the model. The nightly metric
history is produced by *actually* training the model at the relevant commits, so every number the
agent sees is real and reproducible.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from culprit.demo import project as P
from culprit.demo.builder import Scenario, build_repository

DEFAULT_DEST = Path("demo") / "churn-model"
AUTHORS = [
    ("Priya Natarajan", "priya@example.com"),
    ("Tom Becker", "tom@example.com"),
    ("Aiko Tanaka", "aiko@example.com"),
]
CULPRIT_INDEX = 3  # zero-based index of the culprit commit in COMMITS


def make_customers(n: int = 4000, seed: int = 7) -> pd.DataFrame:
    """Deterministic synthetic customer table with a realistic churn signal."""
    rng = np.random.default_rng(seed)
    contract = rng.choice(["month-to-month", "one-year", "two-year"], size=n, p=[0.55, 0.25, 0.20])
    plan = rng.choice(["basic", "standard", "premium"], size=n, p=[0.40, 0.35, 0.25])
    payment = rng.choice(["credit_card", "bank_transfer", "electronic_check"], size=n, p=[0.45, 0.30, 0.25])
    region = rng.choice(["north", "south", "east", "west"], size=n)

    base_tenure = {"month-to-month": 14, "one-year": 30, "two-year": 48}
    tenure = np.array([rng.geometric(1 / base_tenure[c]) for c in contract]).clip(1, 72)
    plan_price = {"basic": 35.0, "standard": 65.0, "premium": 95.0}
    monthly = np.array([plan_price[p] for p in plan]) + rng.normal(0, 8, size=n)
    monthly = monthly.clip(15, 160).round(2)
    total = (monthly * tenure * rng.uniform(0.92, 1.0, size=n)).round(2)
    tickets = rng.poisson(1.1, size=n)

    logit = 2.5 * (
        -1.1
        + 1.5 * (contract == "month-to-month")
        - 0.9 * (contract == "two-year")
        + 0.5 * (plan == "basic")
        - 0.4 * (plan == "premium")
        + 0.6 * (payment == "electronic_check")
        + 0.35 * tickets
        - 0.045 * tenure
        + 0.012 * (monthly - 65)
        + 0.15 * (region == "south")
    )
    churn_prob = 1 / (1 + np.exp(-logit))
    churned = (rng.uniform(size=n) < churn_prob).astype(int)

    return pd.DataFrame(
        {
            "customer_id": [f"C{100000 + i}" for i in range(n)],
            "tenure_months": tenure.astype(int),
            "monthly_charges": monthly,
            "total_charges": total,
            "num_support_tickets": tickets.astype(int),
            "contract": contract,
            "plan": plan,
            "payment_method": payment,
            "region": region,
            "churned": churned,
        }
    )


def _csv_text(df: pd.DataFrame) -> str:
    buf = io.StringIO()
    df.to_csv(buf, index=False, lineterminator="\n")
    return buf.getvalue()


def build_commits(customers_csv: str) -> list[dict]:
    """The file tree after each commit, as a list of ``{message, author, files}`` deltas."""
    return [
        {
            "message": "feat: churn model training pipeline\n\nLogistic regression baseline with nightly evaluation config.",
            "author": AUTHORS[0],
            "files": {
                "README.md": P.README_V1,
                "requirements.txt": P.REQUIREMENTS_V1,
                ".culprit.yaml": P.CULPRIT_YAML,
                "configs/smoke.json": P.CONFIG_SMOKE,
                "configs/full.json": P.CONFIG_FULL,
                "data/customers.csv": customers_csv,
                "churn/__init__.py": P.INIT_PY,
                "churn/data.py": P.DATA_PY,
                "churn/features.py": P.FEATURES_V1,
                "churn/train.py": P.TRAIN_V1,
                "tests/__init__.py": "",
                "tests/test_smoke.py": P.TEST_SMOKE,
            },
        },
        {
            "message": "feat(features): add tenure bucket and charges-per-month features",
            "author": AUTHORS[1],
            "files": {"churn/features.py": P.FEATURES_V2},
        },
        {
            "message": "chore(train): add --seed flag, verbose logging",
            "author": AUTHORS[2],
            "files": {"churn/train.py": P.TRAIN_V2},
        },
        {
            "message": (
                "refactor(features): simplify categorical encoding with pandas.factorize\n\n"
                "Removes the hard-coded CATEGORY_MAPS so new category values no longer raise."
            ),
            "author": AUTHORS[1],
            "files": {"churn/features.py": P.FEATURES_V3},
        },
        {
            "message": "docs: add model card, link it from README",
            "author": AUTHORS[0],
            "files": {"MODEL_CARD.md": P.MODEL_CARD, "README.md": P.README_V2},
        },
        {
            "message": "feat(train): make class_weight configurable",
            "author": AUTHORS[2],
            "files": {"churn/train.py": P.TRAIN_V3},
        },
        {
            "message": "chore: pin dependency upper bounds, add .gitignore",
            "author": AUTHORS[0],
            "files": {"requirements.txt": P.REQUIREMENTS_V2, ".gitignore": P.GITIGNORE},
        },
    ]


SCENARIO = Scenario(
    key="churn",
    title="churn-model: order-dependent categorical encoding",
    default_dest=DEFAULT_DEST,
    metric="f1",
    metric_keys=("f1", "auc", "accuracy"),
    eval_module="churn.train",
    nightly_config="full",
    commits=lambda: build_commits(_csv_text(make_customers())),
    culprit_index=CULPRIT_INDEX,
    # nightlies on days -5..-3 saw the baseline, -2..-1 the derived features, today the five merges
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
        "Churn classifier whose nightly F1 dropped after five merges. A refactor switched categorical "
        "encoding to pandas.factorize, so training and evaluation frames received different integer codes."
    ),
    mechanism_keywords=["factorize", "encod", "order", "categor"],
    culprit_files=["churn/features.py"],
)


def generate(dest: Path | str = DEFAULT_DEST, force: bool = False, quiet: bool = False) -> dict:
    """Create the churn demo repository at ``dest`` and return a summary dict."""
    return build_repository(SCENARIO, dest, force=force, quiet=quiet)


if __name__ == "__main__":  # pragma: no cover
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DEST
    print(json.dumps(generate(target, force="--force" in sys.argv), indent=2))
