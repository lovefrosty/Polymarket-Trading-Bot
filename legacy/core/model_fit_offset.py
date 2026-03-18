from __future__ import annotations

from typing import Dict, Tuple

from core.model_fit import _require_numpy


def predict_proba_offset(X, offset, w, b):
    np = _require_numpy()
    X = np.asarray(X, dtype=float)
    offset = np.asarray(offset, dtype=float)
    logits = offset + X @ w + b
    logits = np.clip(logits, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-logits))


def fit_ridge_logistic_offset(
    X,
    y,
    offset,
    l2_lambda: float,
    max_iter: int,
    tol: float,
    seed: int,
) -> Tuple:
    np = _require_numpy()
    np.random.seed(seed)
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    offset = np.asarray(offset, dtype=float)
    n, d = X.shape
    w = np.zeros(d, dtype=float)
    b = 0.0

    for i in range(max_iter):
        logits = offset + X @ w + b
        logits = np.clip(logits, -50.0, 50.0)
        p = 1.0 / (1.0 + np.exp(-logits))
        grad_w = (X.T @ (p - y)) / n + l2_lambda * w
        grad_b = float((p - y).mean())
        grad_norm = float(np.sqrt((grad_w**2).sum() + grad_b**2))
        if grad_norm < tol:
            break
        step = 0.5 / (1.0 + 0.01 * i)
        w -= step * grad_w
        b -= step * grad_b

    metrics = _metrics(predict_proba_offset(X, offset, w, b), y, w)
    metrics["iterations"] = i + 1
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
