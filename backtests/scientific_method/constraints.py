from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class PortfolioConstraints:
    max_gross_delta: float
    max_net_delta: float
    max_position_fraction: float
    max_asset_fraction: float
    max_drawdown_pct: float
    drawdown_lookback_days: int
    min_liquidity_ratio: float
    max_open_positions: int


def load_portfolio_constraints(path: Path) -> PortfolioConstraints:
    data = _parse_simple_yaml(path)
    required = [
        "max_gross_delta",
        "max_net_delta",
        "max_position_fraction",
        "max_asset_fraction",
        "max_drawdown_pct",
        "drawdown_lookback_days",
        "min_liquidity_ratio",
        "max_open_positions",
    ]
    for key in required:
        if key not in data:
            raise ValueError(f"missing_portfolio_setting:{key}")
    return PortfolioConstraints(
        max_gross_delta=float(data["max_gross_delta"]),
        max_net_delta=float(data["max_net_delta"]),
        max_position_fraction=float(data["max_position_fraction"]),
        max_asset_fraction=float(data["max_asset_fraction"]),
        max_drawdown_pct=float(data["max_drawdown_pct"]),
        drawdown_lookback_days=int(data["drawdown_lookback_days"]),
        min_liquidity_ratio=float(data["min_liquidity_ratio"]),
        max_open_positions=int(data["max_open_positions"]),
    )


def _parse_simple_yaml(path: Path) -> Dict[str, float]:
    data: Dict[str, float] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        data[key.strip()] = _coerce_value(value.strip())
    return data


def _coerce_value(value: str):
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
