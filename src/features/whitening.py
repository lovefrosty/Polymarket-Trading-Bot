from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.features.covariance import CovarianceResult


@dataclass(frozen=True)
class WhiteningResult:
    values: List[float]
    applied: bool
    reason: Optional[str]


def _identity(size: int) -> List[List[float]]:
    return [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]


def _mat_mul(a: List[List[float]], b: List[List[float]]) -> List[List[float]]:
    result = [[0.0 for _ in range(len(b[0]))] for _ in range(len(a))]
    for i in range(len(a)):
        for j in range(len(b[0])):
            result[i][j] = sum(a[i][k] * b[k][j] for k in range(len(b)))
    return result


def _mat_vec(mat: List[List[float]], vec: List[float]) -> List[float]:
    return [sum(a * b for a, b in zip(row, vec)) for row in mat]


def _transpose(mat: List[List[float]]) -> List[List[float]]:
    return [list(row) for row in zip(*mat)] if mat else []


def _jacobi_eigen_decomposition(
    matrix: List[List[float]],
    max_iter: int = 50,
    tol: float = 1e-10,
) -> Optional[tuple[List[float], List[List[float]]]]:
    size = len(matrix)
    if size == 0:
        return None

    a = [row[:] for row in matrix]
    v = _identity(size)

    for _ in range(max_iter):
        max_val = 0.0
        p = 0
        q = 1
        for i in range(size):
            for j in range(i + 1, size):
                if abs(a[i][j]) > abs(max_val):
                    max_val = a[i][j]
                    p, q = i, j
        if abs(max_val) < tol:
            break

        if a[p][p] == a[q][q]:
            theta = 0.25 * 3.141592653589793
        else:
            theta = 0.5 * _atan2(2 * a[p][q], a[q][q] - a[p][p])

        c = _cos(theta)
        s = _sin(theta)

        for i in range(size):
            if i != p and i != q:
                aip = a[i][p]
                aiq = a[i][q]
                a[i][p] = c * aip - s * aiq
                a[p][i] = a[i][p]
                a[i][q] = c * aiq + s * aip
                a[q][i] = a[i][q]

        app = c * c * a[p][p] - 2 * s * c * a[p][q] + s * s * a[q][q]
        aqq = s * s * a[p][p] + 2 * s * c * a[p][q] + c * c * a[q][q]
        a[p][p] = app
        a[q][q] = aqq
        a[p][q] = 0.0
        a[q][p] = 0.0

        for i in range(size):
            vip = v[i][p]
            viq = v[i][q]
            v[i][p] = c * vip - s * viq
            v[i][q] = s * vip + c * viq

    eigenvalues = [a[i][i] for i in range(size)]
    return eigenvalues, v


def _atan2(y: float, x: float) -> float:
    import math

    return math.atan2(y, x)


def _cos(x: float) -> float:
    import math

    return math.cos(x)


def _sin(x: float) -> float:
    import math

    return math.sin(x)


class Whitening:
    def apply(self, values: List[float], cov: CovarianceResult) -> WhiteningResult:
        if cov.matrix is None:
            return WhiteningResult(values, False, "missing_covariance")
        if not cov.whitening_allowed:
            return WhiteningResult(values, False, cov.whitening_reason or "whitening_not_allowed")
        if not cov.healthy:
            return WhiteningResult(values, False, "covariance_unhealthy")

        decomposition = _jacobi_eigen_decomposition(cov.matrix)
        if decomposition is None:
            return WhiteningResult(values, False, "whitening_failed")

        eigenvalues, eigenvectors = decomposition
        if any(val <= 0 for val in eigenvalues):
            return WhiteningResult(values, False, "non_positive_eigenvalue")

        inv_sqrt = [[0.0 for _ in eigenvalues] for _ in eigenvalues]
        for i, val in enumerate(eigenvalues):
            inv_sqrt[i][i] = val ** -0.5

        whiten_matrix = _mat_mul(_mat_mul(eigenvectors, inv_sqrt), _transpose(eigenvectors))
        whitened = _mat_vec(whiten_matrix, values)
        return WhiteningResult(whitened, True, None)
