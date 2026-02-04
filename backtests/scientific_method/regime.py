from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class HMMParams:
    A: List[List[float]]
    mu: List[List[float]]
    sigma: List[List[float]]
    pi0: List[float]

    @property
    def k(self) -> int:
        return len(self.pi0)


def load_hmm_params(path: Path) -> HMMParams:
    data = json.loads(path.read_text())
    for key in ("A", "mu", "sigma", "pi0"):
        if key not in data:
            raise ValueError(f"hmm_missing_key:{key}")
    return HMMParams(
        A=data["A"],
        mu=data["mu"],
        sigma=data["sigma"],
        pi0=data["pi0"],
    )


@dataclass
class HMMFilter:
    params: HMMParams
    last_pi: Optional[List[float]] = None

    def update(self, obs: List[float]) -> List[float]:
        pi_prev = self.last_pi or list(self.params.pi0)
        pi_pred = _matmul_vec(pi_prev, self.params.A)
        log_pred = [math.log(max(val, 1e-12)) for val in pi_pred]
        log_emit = _log_emission(obs, self.params.mu, self.params.sigma)
        log_alpha = [lp + le for lp, le in zip(log_pred, log_emit)]
        pi = _softmax(log_alpha)
        self.last_pi = pi
        return pi


def _matmul_vec(vec: List[float], mat: List[List[float]]) -> List[float]:
    out = []
    for j in range(len(mat[0])):
        s = 0.0
        for i, v in enumerate(vec):
            s += v * mat[i][j]
        out.append(s)
    return out


def _log_emission(obs: List[float], mu: List[List[float]], sigma: List[List[float]]) -> List[float]:
    logs: List[float] = []
    for k in range(len(mu)):
        total = 0.0
        for x, m, s in zip(obs, mu[k], sigma[k]):
            s_val = max(float(s), 1e-6)
            total += -0.5 * math.log(2.0 * math.pi * s_val * s_val)
            total += -0.5 * ((x - m) ** 2) / (s_val * s_val)
        logs.append(total)
    return logs


def _softmax(values: List[float]) -> List[float]:
    m = max(values)
    exps = [math.exp(v - m) for v in values]
    total = sum(exps)
    if total <= 0:
        return [1.0 / len(values)] * len(values)
    return [v / total for v in exps]
