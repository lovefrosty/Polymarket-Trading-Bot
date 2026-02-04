from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class ReferenceQuote:
    source: str
    symbol: str
    value: float
    t_event_ms: Optional[int]
    t_recv_mono_ns: int
    t_recv_wall_iso: str
    t_recv_wall_ms: Optional[int] = None


@dataclass(frozen=True)
class ValidatedPrice:
    value: float
    ts_event_ms: Optional[int]
    t_recv_mono_ns: int
    t_recv_wall_ms: Optional[int]
    sources: List[str]
    confidence: float


@dataclass(frozen=True)
class ReferencePriceResult:
    price: Optional[ValidatedPrice]
    status: str
    reasons: List[str]
    warnings: List[str]
    pstar_asof_wall_ms: Optional[int]
    spot_px: Optional[float]
    spot_asof_wall_ms: Optional[int]
    perp_px: Optional[float]
    perp_asof_wall_ms: Optional[int]
    diff_bps: Optional[float] = None
    c_basis: Optional[float] = None
    c_stale: Optional[float] = None
    c_ref: Optional[float] = None

    @property
    def freeze_reason(self) -> Optional[str]:
        return None if self.status == "ok" else self.status


@dataclass(frozen=True)
class IngestResult:
    recv_out_of_order: bool
    event_time_regressed: bool
    symbol_mismatch: bool


class ReferencePriceAggregator:
    def __init__(
        self,
        required_sources: Set[str],
        staleness_ms: int,
        disagreement_bps: float,
        min_confidence: float,
        disagreement_bps_soft: Optional[float] = None,
        disagreement_bps_hard: Optional[float] = None,
        disagreement_decay_k: float = 1.0,
        allowed_symbols: Optional[Set[str]] = None,
    ) -> None:
        self.required_sources = required_sources
        self.staleness_ms = staleness_ms
        self.disagreement_bps = disagreement_bps
        self.min_confidence = min_confidence
        self.disagreement_bps_soft = (
            disagreement_bps_soft if disagreement_bps_soft is not None else disagreement_bps
        )
        self.disagreement_bps_hard = (
            disagreement_bps_hard if disagreement_bps_hard is not None else disagreement_bps
        )
        self.disagreement_decay_k = disagreement_decay_k
        self.allowed_symbols = allowed_symbols
        self._quotes: Dict[str, Dict[str, ReferenceQuote]] = {}

    def ingest(self, quote: ReferenceQuote) -> IngestResult:
        symbol_mismatch = False
        if self.allowed_symbols is not None and quote.symbol not in self.allowed_symbols:
            return IngestResult(False, False, True)

        by_source = self._quotes.setdefault(quote.symbol, {})
        prev = by_source.get(quote.source)
        recv_out_of_order = prev is not None and quote.t_recv_mono_ns < prev.t_recv_mono_ns
        event_time_regressed = False
        if prev is not None and quote.t_event_ms is not None and prev.t_event_ms is not None:
            event_time_regressed = quote.t_event_ms < prev.t_event_ms
        if recv_out_of_order:
            return IngestResult(True, event_time_regressed, symbol_mismatch)

        by_source[quote.source] = quote
        return IngestResult(False, event_time_regressed, symbol_mismatch)

    def validated_price(
        self,
        symbol: str,
        as_of_mono_ns: int,
        decision_wall_ms: Optional[int],
    ) -> ReferencePriceResult:
        warnings: List[str] = []
        if self.allowed_symbols is not None and symbol not in self.allowed_symbols:
            return ReferencePriceResult(
                None,
                "missing_source",
                ["symbol_mismatch"],
                warnings,
                None,
                None,
                None,
                None,
                None,
            )

        quotes = self._quotes.get(symbol, {})
        missing_sources = [source for source in self.required_sources if source not in quotes]
        if missing_sources:
            reasons = [f"missing_source:{name}" for name in sorted(missing_sources)]
            return ReferencePriceResult(
                None,
                "missing_source",
                reasons,
                warnings,
                None,
                None,
                None,
                None,
                None,
            )

        selected: Dict[str, ReferenceQuote] = {}
        asof_wall: Dict[str, int] = {}
        staleness_ms: Dict[str, int] = {}
        future_reasons: List[str] = []
        stale_reasons: List[str] = []
        for source in self.required_sources:
            quote = quotes.get(source)
            if quote is None:
                return ReferencePriceResult(
                    None,
                    "missing_source",
                    [f"missing_source:{source}"],
                    warnings,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            if quote.t_recv_mono_ns > as_of_mono_ns:
                future_reasons.append(f"future_leakage:{source}")
                continue
            src_asof_wall_ms = _asof_wall_ms(quote, as_of_mono_ns, decision_wall_ms)
            if src_asof_wall_ms is None:
                future_reasons.append(f"future_leakage:{source}")
                continue
            if decision_wall_ms is not None and src_asof_wall_ms >= decision_wall_ms:
                future_reasons.append(f"future_leakage:{source}")
            else:
                age_ms = int(decision_wall_ms - src_asof_wall_ms) if decision_wall_ms is not None else 0
                staleness_ms[source] = age_ms
                if age_ms > self.staleness_ms:
                    stale_reasons.append(f"stale:{source}")
                asof_wall[source] = src_asof_wall_ms
            selected[source] = quote

        if future_reasons:
            return ReferencePriceResult(
                None,
                "future_leakage",
                sorted(set(future_reasons)),
                warnings,
                None,
                None,
                None,
                None,
                None,
            )

        if self.required_sources >= {"spot", "perp"}:
            spot = selected.get("spot")
            perp = selected.get("perp")
            if spot is None or perp is None:
                return ReferencePriceResult(
                    None,
                    "missing_source",
                    ["missing_source:spot_or_perp"],
                    warnings,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            mid = (spot.value + perp.value) / 2.0
            if mid <= 0:
                return ReferencePriceResult(
                    None,
                    "non_positive_price",
                    ["non_positive_price"],
                    warnings,
                    None,
                    spot.value,
                    asof_wall.get("spot"),
                    perp.value,
                    asof_wall.get("perp"),
                )
            diff_bps = abs(spot.value - perp.value) / mid * 10000.0
            c_basis = _disagreement_multiplier(
                diff_bps,
                self.disagreement_bps_soft,
                self.disagreement_bps_hard,
                self.disagreement_decay_k,
            )
            pstar_asof_wall_ms = max(asof_wall.values()) if asof_wall else None
            max_stale = max(staleness_ms.get("spot", 0), staleness_ms.get("perp", 0))
            c_stale = _staleness_multiplier(max_stale, self.staleness_ms)
            c_ref = c_basis * c_stale
            reasons: List[str] = []
            status = "ok"
            if diff_bps >= self.disagreement_bps_hard:
                reasons.append("basis_extreme")
                status = "basis_extreme"
                c_basis = 0.0
                c_ref = c_basis * c_stale
            if stale_reasons:
                reasons.extend(stale_reasons)
                if status == "ok":
                    status = "stale"
            ts_event_ms = _max_event_ts([spot, perp])
            recv_mono_ns = max(spot.t_recv_mono_ns, perp.t_recv_mono_ns)
            price = ValidatedPrice(
                mid,
                ts_event_ms,
                recv_mono_ns,
                pstar_asof_wall_ms or 0,
                ["spot", "perp"],
                c_ref,
            )
            return ReferencePriceResult(
                price,
                status,
                reasons,
                warnings,
                pstar_asof_wall_ms,
                spot.value,
                asof_wall.get("spot"),
                perp.value,
                asof_wall.get("perp"),
                diff_bps=diff_bps,
                c_basis=c_basis,
                c_stale=c_stale,
                c_ref=c_ref,
            )

        values = [selected[source].value for source in self.required_sources]
        ts_event_ms = _max_event_ts(selected.values())
        recv_mono_ns = max(q.t_recv_mono_ns for q in selected.values())
        avg = sum(values) / float(len(values))
        return ReferencePriceResult(
            ValidatedPrice(avg, ts_event_ms, recv_mono_ns, _max_wall_ms(asof_wall), sorted(self.required_sources), 1.0),
            "ok",
            [],
            warnings,
            _max_wall_ms(asof_wall),
            None,
            None,
            None,
            None,
        )


def parse_reference_event(
    raw: object,
    t_recv_mono_ns: int,
    t_recv_wall_iso: str,
    t_recv_wall_ms: Optional[int] = None,
) -> Optional[ReferenceQuote]:
    if not isinstance(raw, dict):
        return None
    source = raw.get("source")
    symbol = raw.get("symbol")
    value = raw.get("value")
    if source is None or symbol is None or value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    t_event_ms = raw.get("t_event_ms")
    try:
        t_event_ms = int(t_event_ms) if t_event_ms is not None else None
    except (TypeError, ValueError):
        t_event_ms = None
    recv_wall_ms = int(t_recv_wall_ms) if t_recv_wall_ms is not None else _wall_ms_from_iso(t_recv_wall_iso)
    return ReferenceQuote(
        source=str(source),
        symbol=str(symbol),
        value=price,
        t_event_ms=t_event_ms,
        t_recv_mono_ns=t_recv_mono_ns,
        t_recv_wall_iso=t_recv_wall_iso,
        t_recv_wall_ms=recv_wall_ms,
    )


def _max_event_ts(quotes: List[ReferenceQuote]) -> Optional[int]:
    ts_values = [quote.t_event_ms for quote in quotes if quote.t_event_ms is not None]
    return max(ts_values) if ts_values else None


def _disagreement_multiplier(diff_bps: float, soft: float, hard: float, decay_k: float) -> float:
    if hard <= soft:
        return 1.0 if diff_bps <= soft else 0.0
    if diff_bps <= soft:
        return 1.0
    if diff_bps >= hard:
        return 0.0
    x = (diff_bps - soft) / (hard - soft)
    gamma = max(0.1, float(decay_k))
    return float((1.0 - x) ** gamma)


def _asof_wall_ms(
    quote: ReferenceQuote, as_of_mono_ns: int, decision_wall_ms: Optional[int]
) -> Optional[int]:
    if quote.t_recv_wall_ms is not None:
        return int(quote.t_recv_wall_ms)
    if decision_wall_ms is None:
        return None
    delta_ms = (as_of_mono_ns - quote.t_recv_mono_ns) / 1_000_000.0
    return int(decision_wall_ms - delta_ms)


def _staleness_multiplier(max_age_ms: int, threshold_ms: int) -> float:
    if threshold_ms <= 0:
        return 1.0 if max_age_ms <= 0 else 0.0
    ratio = max_age_ms / float(threshold_ms)
    return min(1.0, max(0.0, 1.0 - ratio))


def _max_wall_ms(values: Dict[str, int]) -> Optional[int]:
    if not values:
        return None
    return max(values.values())


def _wall_ms_from_iso(value: str) -> int:
    try:
        from datetime import datetime, timezone

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0
