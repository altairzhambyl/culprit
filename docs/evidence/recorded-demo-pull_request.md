# fix(features): restore stable categorical encoding (regression in nightly F1)

`culprit/fix-20260912-215825-56bb` → `main`

## Root cause

Commit `eb367c9` (*refactor(features): simplify categorical encoding with pandas.factorize*) replaced the explicit `CATEGORY_MAPS` with `pd.factorize`, which assigns integer codes by order of first appearance **per dataframe**. Training and evaluation frames therefore received different encodings for `contract`, `plan`, `payment_method` and `region`; nothing crashed, the model just scored garbage features at evaluation time.

## Evidence

- Nightly `f1`: **0.8301 → 0.6624** (nightly-2026-09-11 → nightly-2026-09-12-2093).
- Bisection with the fast config (good ≈ 0.8111, bad ≈ 0.7027):

| ref | f1 | verdict |
|---|---|---|
| `0791e50` | 0.8111 | good |
| `ab56a44` | 0.7027 | bad |
| `eb367c9` | 0.7027 | bad |
| `9cc2b36` | 0.8111 | good |
| `culprit/fix-20260912-215825-56bb` | 0.8111 | fixed |

## Fix

Restore stable, explicit category codes shared by training and evaluation (unknown values map to -1, keeping the robustness the refactor was after). Fast-config `f1` recovers to **0.8111**.

## Guard

`tests/test_encoding_consistency.py` asserts that categorical encoding is independent of row order and of the split.

---
*Opened by Culprit after human approval.*

---

## Diff

```diff
diff --git a/churn/features.py b/churn/features.py
index 7af8ba1..dab776c 100644
--- a/churn/features.py
+++ b/churn/features.py
@@ -22,15 +22,25 @@ def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
     return out
 
 
+# Explicit, stable integer codes shared by training and evaluation. Encoding must never depend on
+# the order in which values appear in a particular dataframe (see tests/test_encoding_consistency.py).
+CATEGORY_MAPS = {
+    "contract": {"month-to-month": 0, "one-year": 1, "two-year": 2},
+    "plan": {"basic": 0, "standard": 1, "premium": 2},
+    "payment_method": {"electronic_check": 0, "bank_transfer": 1, "credit_card": 2},
+    "region": {"north": 0, "south": 1, "east": 2, "west": 3},
+}
+
+
 def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
-    """Encode categorical columns as integer codes.
+    """Map categorical strings to the stable integer codes in CATEGORY_MAPS.
 
-    Uses pandas.factorize so that unseen category values never raise a KeyError and we no longer
-    need to maintain hard-coded category maps.
+    Unknown values map to -1 instead of raising, which keeps the robustness of the previous
+    factorize-based version without making codes depend on row order.
     """
     out = df.copy()
     for col in CATEGORICAL_COLUMNS:
-        out[col] = pd.factorize(out[col])[0]
+        out[col] = out[col].map(CATEGORY_MAPS[col]).fillna(-1).astype(int)
     return out
 
 
diff --git a/tests/test_encoding_consistency.py b/tests/test_encoding_consistency.py
new file mode 100644
index 0000000..8dd691f
--- /dev/null
+++ b/tests/test_encoding_consistency.py
@@ -0,0 +1,37 @@
+"""Regression guard: categorical encoding must not depend on row order or on the split.
+
+Added after an incident where switching to pandas.factorize made the training and evaluation
+frames receive different integer codes, silently degrading the model.
+"""
+
+import pandas as pd
+
+from churn.features import CATEGORICAL_COLUMNS, encode_categoricals
+
+
+def _sample_frame() -> pd.DataFrame:
+    return pd.DataFrame(
+        {
+            "contract": ["two-year", "month-to-month", "one-year", "month-to-month"],
+            "plan": ["premium", "basic", "standard", "basic"],
+            "payment_method": ["credit_card", "electronic_check", "bank_transfer", "credit_card"],
+            "region": ["west", "north", "east", "south"],
+        }
+    )
+
+
+def test_encoding_is_independent_of_row_order():
+    df = _sample_frame()
+    forward = encode_categoricals(df)
+    backward = encode_categoricals(df.iloc[::-1].reset_index(drop=True)).iloc[::-1].reset_index(drop=True)
+    for col in CATEGORICAL_COLUMNS:
+        assert forward[col].tolist() == backward[col].tolist(), f"{col} codes changed with row order"
+
+
+def test_encoding_is_consistent_across_splits():
+    df = _sample_frame()
+    full = encode_categoricals(df)
+    for i in range(len(df)):
+        single = encode_categoricals(df.iloc[[i]])
+        for col in CATEGORICAL_COLUMNS:
+            assert int(single[col].iloc[0]) == int(full[col].iloc[i]), f"{col} code differs when encoded alone"
```
