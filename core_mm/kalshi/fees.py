from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional


_QUADRATIC_COEFFICIENT = 0.07
_QUADRATIC_MAKER_COEFFICIENT = 0.0175
_FLAT_COEFFICIENT = 0.035


@dataclass(frozen=True)
class KalshiFeeSpec:
    fee_type: str = "quadratic"
    fee_multiplier: float = 1.0


@dataclass(frozen=True)
class KalshiFeeResult:
    fee_usdc: float
    fee_bps: float
    fee_source: str
    fee_type: str
    fee_multiplier: float


def infer_fee_spec(raw: Optional[Mapping[str, Any]]) -> KalshiFeeSpec:
    payload = dict(raw or {})
    fee_type = str(
        payload.get("fee_type")
        or payload.get("series_fee_type")
        or payload.get("market_fee_type")
        or _default_fee_type(payload)
    ).strip().lower()
    if fee_type not in {"quadratic", "quadratic_with_maker_fees", "flat"}:
        fee_type = _default_fee_type(payload)
    fee_multiplier = _coerce_float(
        payload.get("fee_multiplier")
        or payload.get("series_fee_multiplier")
        or payload.get("market_fee_multiplier")
        or 1.0
    )
    return KalshiFeeSpec(
        fee_type=fee_type,
        fee_multiplier=max(0.0, float(fee_multiplier or 1.0)),
    )


def calculate_kalshi_fee(
    *,
    price: float,
    contracts: float,
    fee_spec: Optional[KalshiFeeSpec] = None,
    is_taker: bool,
    fee_source: str = "model_fallback",
) -> KalshiFeeResult:
    spec = fee_spec or KalshiFeeSpec()
    normalized_price = max(0.0, min(1.0, float(price or 0.0)))
    contract_count = max(0.0, float(contracts or 0.0))
    gross_notional = normalized_price * contract_count
    if gross_notional <= 0.0 or contract_count <= 0.0:
        return KalshiFeeResult(
            fee_usdc=0.0,
            fee_bps=0.0,
            fee_source=str(fee_source),
            fee_type=spec.fee_type,
            fee_multiplier=spec.fee_multiplier,
        )
    coefficient = _coefficient(spec.fee_type, is_taker=is_taker)
    if coefficient <= 0.0:
        fee_usdc = 0.0
    else:
        raw_fee = coefficient * float(spec.fee_multiplier) * contract_count * normalized_price * (1.0 - normalized_price)
        fee_usdc = math.ceil(raw_fee * 100.0 - 1e-12) / 100.0
    fee_bps = (fee_usdc / gross_notional) * 10_000.0 if gross_notional > 0.0 else 0.0
    return KalshiFeeResult(
        fee_usdc=float(fee_usdc),
        fee_bps=float(fee_bps),
        fee_source=str(fee_source),
        fee_type=spec.fee_type,
        fee_multiplier=float(spec.fee_multiplier),
    )


def reported_kalshi_fee(fill_payload: Mapping[str, Any], *, price: float, contracts: float) -> Optional[KalshiFeeResult]:
    fee_value = fill_payload.get("fee_usdc")
    if fee_value in (None, ""):
        fee_value = fill_payload.get("fee_cost")
    if fee_value in (None, ""):
        raw = fill_payload.get("raw_kalshi")
        if isinstance(raw, Mapping):
            fee_value = raw.get("fee_cost")
    fee_usdc = _coerce_float(fee_value)
    if fee_usdc is None:
        return None
    gross_notional = max(0.0, float(price or 0.0) * float(contracts or 0.0))
    fee_bps = (fee_usdc / gross_notional) * 10_000.0 if gross_notional > 0.0 else 0.0
    spec = infer_fee_spec(fill_payload if isinstance(fill_payload, Mapping) else {})
    return KalshiFeeResult(
        fee_usdc=float(fee_usdc),
        fee_bps=float(fee_bps),
        fee_source="exchange_reported",
        fee_type=spec.fee_type,
        fee_multiplier=float(spec.fee_multiplier),
    )


def _coefficient(fee_type: str, *, is_taker: bool) -> float:
    normalized = str(fee_type or "").strip().lower()
    if normalized == "quadratic_with_maker_fees":
        return _QUADRATIC_COEFFICIENT if is_taker else _QUADRATIC_MAKER_COEFFICIENT
    if normalized == "flat":
        return _FLAT_COEFFICIENT if is_taker else 0.0
    if normalized == "quadratic":
        return _QUADRATIC_COEFFICIENT if is_taker else 0.0
    return _QUADRATIC_COEFFICIENT if is_taker else 0.0


def _default_fee_type(raw: Mapping[str, Any]) -> str:
    series_ticker = str(
        raw.get("series_ticker")
        or raw.get("seriesTicker")
        or raw.get("event_ticker")
        or raw.get("eventTicker")
        or raw.get("ticker")
        or ""
    ).upper()
    if series_ticker.startswith("KXINX") or series_ticker.startswith("NASDAQ100") or series_ticker.startswith("KXNDX"):
        return "flat"
    return "quadratic"


def _coerce_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
