from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.model_fit_offset import fit_ridge_logistic_offset, predict_proba_offset


FEATURE_ORDER = ["z_mom", "z_rev", "ewma_vol"]


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
    rows = sorted(_filter_trainable_rows(rows), key=lambda row: row["as_of_ts_ms"])
    if not rows:
        raise ValueError("no_training_rows")
    train_rows, val_rows = _time_split(rows)
    X_train, y_train, offset_train = _build_matrix(train_rows)
    X_val, y_val, offset_val = _build_matrix(val_rows)

    w, b, train_metrics = fit_ridge_logistic_offset(
        X_train, y_train, offset_train, l2_lambda, max_iter, tol, seed
    )
    p_val = predict_proba_offset(X_val, offset_val, w, b)
    val_metrics = _metrics(p_val, y_val)

    model = {
        "schema_version": "model_ridge_logit_offset_v1",
        "feature_order": FEATURE_ORDER,
        "w": w.tolist(),
        "b": float(b),
        "offset_mode": "logit_p_market_exec_buy",
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
    rows = []
    for line in path.read_text().splitlines():
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _build_matrix(rows: List[Dict[str, Any]]) -> Tuple:
    import numpy as np

    X_list = []
    y_list = []
    offset_list = []
    for row in rows:
        as_of_ts = row.get("as_of_ts_ms")
        window_end = row.get("window_end_ts_ms")
        if as_of_ts is None or window_end is None:
            continue
        try:
            if int(as_of_ts) >= int(window_end):
                continue
        except (TypeError, ValueError):
            continue
        values = []
        skip = False
        for key in FEATURE_ORDER:
            value = row.get(key)
            if value is None:
                skip = True
                break
            values.append(float(value))
        if skip:
            continue
        label = row.get("label_up")
        if label is None:
            continue
        p_market = row.get("p_market_exec_buy")
        if p_market is None:
            continue
        p_market = float(p_market)
        if p_market <= 0 or p_market >= 1:
            continue
        offset = _logit(p_market)
        X_list.append(values)
        y_list.append(float(label))
        offset_list.append(offset)
    if not X_list:
        raise ValueError("no_training_rows")
    return (
        np.asarray(X_list, dtype=float),
        np.asarray(y_list, dtype=float),
        np.asarray(offset_list, dtype=float),
    )


def _filter_trainable_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    usable: List[Dict[str, Any]] = []
    for row in rows:
        as_of_ts = row.get("as_of_ts_ms")
        window_end = row.get("window_end_ts_ms")
        if as_of_ts is None or window_end is None:
            continue
        try:
            if int(as_of_ts) >= int(window_end):
                continue
        except (TypeError, ValueError):
            continue
        if any(row.get(key) is None for key in FEATURE_ORDER):
            continue
        label = row.get("label_up")
        p_market = row.get("p_market_exec_buy")
        if label is None or p_market is None:
            continue
        try:
            p_market = float(p_market)
        except (TypeError, ValueError):
            continue
        if not (0.0 < p_market < 1.0):
            continue
        usable.append(row)
    return usable


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


def _logit(p: float) -> float:
    import math

    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train ridge-logit offset model on micro dataset")
    parser.add_argument("--data", required=True, help="Path to micro_decisions JSONL")
    parser.add_argument("--out", required=True, help="Output model JSON path")
    parser.add_argument("--l2", type=float, default=1.0, help="L2 regularization strength")
    parser.add_argument("--max-iter", type=int, default=1000, help="Max iterations")
    parser.add_argument("--tol", type=float, default=1e-6, help="Gradient norm tolerance")
    parser.add_argument("--seed", type=int, default=0, help="Deterministic seed")
    return parser.parse_args()


if __name__ == "__main__":
    main()
