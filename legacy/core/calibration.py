from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from core.model_fit import _require_numpy


@dataclass(frozen=True)
class CalibrationReport:
    bins: List[Dict[str, float]]
    brier: float
    logloss: float


def fit_platt(p_raw, y, max_iter: int = 2000, tol: float = 1e-6) -> Tuple[float, float]:
    np = _require_numpy()
    p_raw = np.asarray(p_raw, dtype=float)
    y = np.asarray(y, dtype=float)
    p_raw = np.clip(p_raw, 1e-6, 1.0 - 1e-6)
    x = np.log(p_raw / (1.0 - p_raw))
    a = 1.0
    c = 0.0
    for i in range(max_iter):
        logits = a * x + c
        logits = np.clip(logits, -50.0, 50.0)
        p = 1.0 / (1.0 + np.exp(-logits))
        grad_a = float(((p - y) * x).mean())
        grad_c = float((p - y).mean())
        grad_norm = (grad_a**2 + grad_c**2) ** 0.5
        if grad_norm < tol:
            break
        step = 0.5 / (1.0 + 0.01 * i)
        a -= step * grad_a
        c -= step * grad_c
    return a, c


def apply_platt(p_raw, a: float, c: float):
    np = _require_numpy()
    p_raw = np.asarray(p_raw, dtype=float)
    p_raw = np.clip(p_raw, 1e-6, 1.0 - 1e-6)
    x = np.log(p_raw / (1.0 - p_raw))
    logits = a * x + c
    logits = np.clip(logits, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-logits))


def calibration_report(p, y, bins: int = 10) -> CalibrationReport:
    np = _require_numpy()
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    p = np.clip(p, 1e-8, 1.0 - 1e-8)
    brier = float(((p - y) ** 2).mean())
    logloss = float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())
    bins_list: List[Dict[str, float]] = []
    for i in range(bins):
        lo = i / bins
        hi = (i + 1) / bins
        mask = (p >= lo) & (p < hi) if i < bins - 1 else (p >= lo) & (p <= hi)
        if mask.sum() == 0:
            bins_list.append({"bin": i, "count": 0, "avg_p": 0.0, "empirical": 0.0})
            continue
        avg_p = float(p[mask].mean())
        emp = float(y[mask].mean())
        bins_list.append({"bin": i, "count": int(mask.sum()), "avg_p": avg_p, "empirical": emp})
    return CalibrationReport(bins=bins_list, brier=brier, logloss=logloss)
