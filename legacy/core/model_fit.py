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

import math
from typing import Dict, Tuple


def _require_numpy():
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("numpy_required") from exc
    return np


def predict_proba(X, w, b):
    np = _require_numpy()
    X = np.asarray(X, dtype=float)
    w = np.asarray(w, dtype=float)
    logits = X @ w + float(b)
    logits = np.clip(logits, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-logits))


def fit_ridge_logistic(
    X,
    y,
    l2_lambda: float,
    max_iter: int,
    tol: float,
    seed: int,
) -> Tuple:
    """Return a deterministic placeholder model without iterative fitting."""
    np = _require_numpy()
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim != 2 or y.ndim != 1:
        raise ValueError("invalid_training_shapes")
    if X.shape[0] == 0:
        raise ValueError("no_training_rows")
    if X.shape[0] != y.shape[0]:
        raise ValueError("training_row_mismatch")

    mean = float(np.clip(y.mean(), 1e-6, 1.0 - 1e-6))
    b = math.log(mean / (1.0 - mean))
    w = np.zeros(X.shape[1], dtype=float)
    p = predict_proba(X, w, b)
    metrics = _metrics(p, y, w)
    metrics["iterations"] = 0
    return w, b, metrics


def _metrics(p, y, w) -> Dict[str, float]:
    np = _require_numpy()
    eps = 1e-8
    p = np.clip(p, eps, 1.0 - eps)
    logloss = float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
    brier = float(((p - y) ** 2).mean())
    acc = float(((p >= 0.5) == (y >= 0.5)).mean())
    w_norm = float(np.linalg.norm(w))
    return {
        "logloss": logloss,
        "brier": brier,
        "accuracy": acc,
        "w_norm": w_norm,
    }
