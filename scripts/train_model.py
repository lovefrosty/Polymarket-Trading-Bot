"""
⚠️ COMPATIBILITY SHIM — NOT PART OF RUNTIME SYSTEM ⚠️

This module exists solely to satisfy legacy test imports.
It must never be used in live trading, discovery, or replay logic.

If this module is invoked in production paths, that is a BUG.
"""

from __future__ import annotations

import os

if os.getenv("RUNTIME_MODE") == "live":
    raise RuntimeError("Legacy compatibility module imported in live mode")

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.model_fit import fit_ridge_logistic, predict_proba


FEATURE_ORDER = ["ret_60s", "ret_300s", "ret_900s", "ewma_vol_300s", "z_mom", "z_rev"]


def main() -> None:
    args = _parse_args()
    rows = _load_rows(Path(args.data))
    model = train_from_rows(rows, l2_lambda=args.l2, max_iter=args.max_iter, tol=args.tol, seed=args.seed)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(model, indent=2, sort_keys=True))


def train_from_rows(
    rows: List[Dict[str, Any]],
    l2_lambda: float,
    max_iter: int,
    tol: float,
    seed: int,
) -> Dict[str, Any]:
    rows = sorted(rows, key=lambda row: row.get("as_of_ts_ms", 0))
    train_rows, val_rows = _time_split(rows)
    X_train, y_train = _build_matrix(train_rows)
    X_val, y_val = _build_matrix(val_rows)

    w, b, train_metrics = fit_ridge_logistic(X_train, y_train, l2_lambda, max_iter, tol, seed)
    val_metrics = {}
    if len(y_val) > 0:
        p_val = predict_proba(X_val, w, b)
        val_metrics = _metrics(p_val, y_val)

    model = {
        "schema_version": "model_ridge_logit_v1",
        "feature_order": FEATURE_ORDER,
        "w": w.tolist(),
        "b": float(b),
        "l2_lambda": l2_lambda,
        "train_metrics": train_metrics,
        "val_metrics": val_metrics,
        "training_time_range": {
            "start_ms": train_rows[0]["as_of_ts_ms"] if train_rows else None,
            "end_ms": train_rows[-1]["as_of_ts_ms"] if train_rows else None,
        },
        "validation_time_range": {
            "start_ms": val_rows[0]["as_of_ts_ms"] if val_rows else None,
            "end_ms": val_rows[-1]["as_of_ts_ms"] if val_rows else None,
        },
        "leakage_guards": {"split": "time_order", "feature_asof": "strict_lt"},
    }
    return model


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _build_matrix(rows: List[Dict[str, Any]]) -> Tuple:
    import numpy as np

    X_list = []
    y_list = []
    for row in rows:
        as_of_ts = row.get("as_of_ts_ms")
        window_end = row.get("window_end_ts_ms")
        if as_of_ts is not None and window_end is not None:
            try:
                if int(as_of_ts) >= int(window_end):
                    continue
            except (TypeError, ValueError):
                continue
        features = row.get("features") if isinstance(row.get("features"), dict) else row
        values = []
        skip = False
        for key in FEATURE_ORDER:
            value = features.get(key) if isinstance(features, dict) else None
            if value is None:
                skip = True
                break
            values.append(float(value))
        if skip:
            continue
        label = row.get("label_up")
        if label is None:
            continue
        X_list.append(values)
        y_list.append(float(label))
    if not X_list:
        raise ValueError("no_training_rows")
    return np.asarray(X_list, dtype=float), np.asarray(y_list, dtype=float)


def _time_split(rows: List[Dict[str, Any]], train_frac: float = 0.7) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not rows:
        return [], []
    split_idx = max(1, int(len(rows) * train_frac))
    return rows[:split_idx], rows[split_idx:]


def _metrics(p, y) -> Dict[str, float]:
    import numpy as np

    eps = 1e-8
    p = np.clip(p, eps, 1.0 - eps)
    logloss = float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
    brier = float(((p - y) ** 2).mean())
    acc = float(((p >= 0.5) == (y >= 0.5)).mean())
    return {"logloss": logloss, "brier": brier, "accuracy": acc}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train placeholder ridge-logit model")
    parser.add_argument("--data", required=True, help="Path to reference window JSONL")
    parser.add_argument("--out", required=True, help="Output model JSON path")
    parser.add_argument("--l2", type=float, default=1.0, help="L2 regularization strength")
    parser.add_argument("--max-iter", type=int, default=1000, help="Max iterations (ignored)")
    parser.add_argument("--tol", type=float, default=1e-6, help="Gradient norm tolerance (ignored)")
    parser.add_argument("--seed", type=int, default=0, help="Deterministic seed (ignored)")
    return parser.parse_args()


if __name__ == "__main__":
    main()
