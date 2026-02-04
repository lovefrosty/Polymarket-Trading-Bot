from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class ReturnSample:
    values: List[float]
    event_ts: int


@dataclass(frozen=True)
class CovarianceResult:
    matrix: Optional[List[List[float]]]
    shrinkage_lambda: float
    condition_number: float
    min_eigenvalue: float
    healthy: bool
    sample_count: int
    dims: int
    diagonal_only: bool
    whitening_allowed: bool
    whitening_reason: Optional[str]
    size_multiplier: float
    skip: bool


def _clip(value: float, limit: float) -> float:
    if limit <= 0:
        return value
    return max(-limit, min(limit, value))


def _transpose(matrix: List[List[float]]) -> List[List[float]]:
    return [list(row) for row in zip(*matrix)] if matrix else []


def _mat_vec(matrix: List[List[float]], vec: Sequence[float]) -> List[float]:
    return [sum(a * b for a, b in zip(row, vec)) for row in matrix]


def _dot(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(vec_a, vec_b))


def _norm(vec: Sequence[float]) -> float:
    return _dot(vec, vec) ** 0.5


def _identity(size: int) -> List[List[float]]:
    return [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]


def _mat_add(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    return [[x + y for x, y in zip(row_a, row_b)] for row_a, row_b in zip(a, b)]


def _mat_scale(matrix: List[List[float]], scale: float) -> List[List[float]]:
    return [[value * scale for value in row] for row in matrix]


def _power_iteration(matrix: List[List[float]], iterations: int = 50) -> float:
    size = len(matrix)
    vec = [1.0 for _ in range(size)]
    for _ in range(iterations):
        next_vec = _mat_vec(matrix, vec)
        norm = _norm(next_vec)
        if norm == 0:
            return 0.0
        vec = [value / norm for value in next_vec]
    return _dot(vec, _mat_vec(matrix, vec))


def _solve_linear_system(matrix: List[List[float]], vec: List[float]) -> Optional[List[float]]:
    size = len(matrix)
    aug = [row[:] + [vec[i]] for i, row in enumerate(matrix)]
    for i in range(size):
        pivot = aug[i][i]
        if pivot == 0:
            return None
        for j in range(i, size + 1):
            aug[i][j] /= pivot
        for k in range(size):
            if k == i:
                continue
            factor = aug[k][i]
            for j in range(i, size + 1):
                aug[k][j] -= factor * aug[i][j]
    return [aug[i][-1] for i in range(size)]


def _inverse_power_iteration(matrix: List[List[float]], iterations: int = 50) -> float:
    size = len(matrix)
    vec = [1.0 for _ in range(size)]
    for _ in range(iterations):
        solved = _solve_linear_system(matrix, vec)
        if solved is None:
            return 0.0
        norm = _norm(solved)
        if norm == 0:
            return 0.0
        vec = [value / norm for value in solved]
    denom = _dot(vec, _mat_vec(matrix, vec))
    if denom == 0:
        return 0.0
    return denom


class CovarianceEstimator:
    def __init__(
        self,
        winsor_limit: float,
        shrinkage_lambda: float,
        adaptive_shrinkage_scale: float,
        min_eigenvalue: float,
        max_condition_number: float,
    ) -> None:
        self.winsor_limit = winsor_limit
        self.shrinkage_lambda = shrinkage_lambda
        self.adaptive_shrinkage_scale = adaptive_shrinkage_scale
        self.min_eigenvalue = min_eigenvalue
        self.max_condition_number = max_condition_number

    def estimate(self, samples: List[ReturnSample], as_of_ts: int) -> CovarianceResult:
        filtered = [s for s in samples if s.event_ts < as_of_ts]
        if not filtered:
            return CovarianceResult(
                None,
                self.shrinkage_lambda,
                float("inf"),
                0.0,
                False,
                0,
                0,
                False,
                False,
                "insufficient_samples",
                0.0,
                True,
            )

        dims = len(filtered[0].values)
        sample_count = len(filtered)
        clipped = [
            [_clip(value, self.winsor_limit) for value in sample.values]
            for sample in filtered
        ]
        mean = [0.0 for _ in range(dims)]
        for vec in clipped:
            for i, value in enumerate(vec):
                mean[i] += value
        count = float(len(clipped))
        mean = [value / count for value in mean]

        diagonal_only = sample_count < 5 * dims
        if len(clipped) < 2:
            cov = [[0.0 for _ in range(dims)] for _ in range(dims)]
        else:
            cov = [[0.0 for _ in range(dims)] for _ in range(dims)]
            for vec in clipped:
                diff = [value - mean[i] for i, value in enumerate(vec)]
                for i in range(dims):
                    for j in range(dims):
                        if diagonal_only and i != j:
                            continue
                        cov[i][j] += diff[i] * diff[j]
            scale = 1.0 / (len(clipped) - 1)
            cov = _mat_scale(cov, scale)

        diag = [cov[i][i] for i in range(dims)] if dims > 0 else [0.0]
        median_diag = sorted(diag)[len(diag) // 2] if diag else 0.0
        adaptive_lambda = max(self.shrinkage_lambda, self.adaptive_shrinkage_scale * median_diag)
        shrink = _mat_scale(_identity(dims), adaptive_lambda)
        cov = _mat_add(cov, shrink)

        max_eigen = _power_iteration(cov)
        min_eigen = _inverse_power_iteration(cov)
        if min_eigen <= 0:
            condition = float("inf")
        else:
            condition = max_eigen / min_eigen
        healthy = min_eigen >= self.min_eigenvalue and condition <= self.max_condition_number
        whitening_allowed = sample_count >= 10 * dims
        whitening_reason = None if whitening_allowed else "insufficient_samples_for_whitening"
        skip = not healthy
        size_multiplier = 0.0 if skip else 1.0
        return CovarianceResult(
            cov,
            adaptive_lambda,
            condition,
            min_eigen,
            healthy,
            sample_count,
            dims,
            diagonal_only,
            whitening_allowed,
            whitening_reason,
            size_multiplier,
            skip,
        )
