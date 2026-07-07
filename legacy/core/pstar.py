from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class SourceSnapshot:
    source: str
    symbol: str
    value: float
    ts_event_ms: int
    ts_recv_wall_ms: int


@dataclass(frozen=True)
class PStar:
    symbol: str
    value: Optional[float]
    ts_event_ms: Optional[int]
    sources_used: Set[str]
    confidence: float
    valid: bool
    diagnostics: Dict[str, object]
    ts_recv_ms: Optional[int] = None
    invalid_reason: Optional[str] = None
    state: str = "UNAVAILABLE"


class PStarBuilder:
    def __init__(
        self,
        max_age_ms: int,
        freeze_disagree_bps: float,
        degrade_disagree_bps: float = 10.0,
        allow_degraded_single_source: bool = True,
        required_sources: Optional[Set[str]] = None,
    ) -> None:
        self.max_age_ms = int(max_age_ms)
        self.freeze_disagree_bps = float(freeze_disagree_bps)
        self.degrade_disagree_bps = float(degrade_disagree_bps)
        self.allow_degraded_single_source = bool(allow_degraded_single_source)
        self.required_sources = required_sources or {"spot", "perp"}
        self._latest: Dict[str, Dict[str, SourceSnapshot]] = {}

    def ingest(self, source: str, symbol: str, value: float, ts_event_ms: int, ts_recv_wall_ms: int) -> None:
        by_source = self._latest.setdefault(symbol, {})
        snap = SourceSnapshot(
            source=str(source),
            symbol=str(symbol),
            value=float(value),
            ts_event_ms=int(ts_event_ms),
            ts_recv_wall_ms=int(ts_recv_wall_ms),
        )
        prev = by_source.get(snap.source)
        if prev is not None and snap.ts_event_ms < prev.ts_event_ms:
            return
        by_source[snap.source] = snap

    def reset_symbols(self, symbols: Set[str]) -> None:
        for symbol in {str(sym) for sym in symbols if sym}:
            self._latest.pop(symbol, None)

    def build(self, symbol: str, now_wall_ms: int) -> PStar:
        by_source = self._latest.get(symbol) or {}
        missing = sorted(self.required_sources - set(by_source.keys()))
        state = "UNAVAILABLE"
        diagnostics: Dict[str, object] = {
            "symbol": symbol,
            "missing_sources": missing,
            "freeze_reason": None,
            "degraded": False,
            "state": state,
            "age_ms": {},
            "disagreement_bps": None,
            "sources_available": sorted(by_source.keys()),
            "spot_px": None,
            "perp_px": None,
            "single_source": None,
            "pstar_recv_ts_ms": None,
            "pstar_sourceset": [],
            "invalid_reason": None,
        }

        def _age(source_name: str) -> Optional[int]:
            src = by_source.get(source_name)
            if src is None:
                return None
            return int(now_wall_ms - src.ts_event_ms)

        age_spot = _age("spot")
        age_perp = _age("perp")
        if age_spot is not None:
            diagnostics["age_ms"]["spot"] = age_spot
        if age_perp is not None:
            diagnostics["age_ms"]["perp"] = age_perp

        stale_sources = [
            src
            for src, age in [("spot", age_spot), ("perp", age_perp)]
            if age is not None and age > self.max_age_ms
        ]
        if stale_sources:
            state = "STALE"
            invalid_reason = f"stale_source:{','.join(sorted(stale_sources))}"
            diagnostics["freeze_reason"] = invalid_reason
            diagnostics["invalid_reason"] = invalid_reason
            diagnostics["state"] = state
            return PStar(
                symbol=symbol,
                value=None,
                ts_event_ms=None,
                sources_used=set(),
                confidence=0.0,
                valid=False,
                diagnostics=diagnostics,
                ts_recv_ms=None,
                invalid_reason=invalid_reason,
                state=state,
            )

        spot = by_source.get("spot")
        perp = by_source.get("perp")
        if spot is not None:
            diagnostics["spot_px"] = float(spot.value)
        if perp is not None:
            diagnostics["perp_px"] = float(perp.value)
        if spot is not None and perp is not None:
            mid = (spot.value + perp.value) / 2.0
            if mid <= 0:
                state = "DIVERGED"
                diagnostics["freeze_reason"] = "non_positive_reference"
                diagnostics["invalid_reason"] = "non_positive_reference"
                diagnostics["state"] = state
                return PStar(
                    symbol=symbol,
                    value=None,
                    ts_event_ms=None,
                    sources_used={"spot", "perp"},
                    confidence=0.0,
                    valid=False,
                    diagnostics=diagnostics,
                    ts_recv_ms=None,
                    invalid_reason="non_positive_reference",
                    state=state,
                )
            disagree_bps = abs(perp.value - spot.value) / mid * 10000.0
            diagnostics["disagreement_bps"] = disagree_bps
            if disagree_bps >= self.freeze_disagree_bps:
                state = "DIVERGED"
                diagnostics["freeze_reason"] = "pstar_disagreement_extreme"
                diagnostics["invalid_reason"] = "pstar_disagreement_extreme"
                diagnostics["state"] = state
                return PStar(
                    symbol=symbol,
                    value=None,
                    ts_event_ms=None,
                    sources_used={"spot", "perp"},
                    confidence=0.0,
                    valid=False,
                    diagnostics=diagnostics,
                    ts_recv_ms=None,
                    invalid_reason="pstar_disagreement_extreme",
                    state=state,
                )
            confidence = 1.0
            if disagree_bps > self.degrade_disagree_bps:
                width = max(1.0, self.freeze_disagree_bps - self.degrade_disagree_bps)
                x = min(1.0, (disagree_bps - self.degrade_disagree_bps) / width)
                confidence = max(0.2, 1.0 - x)
            state = "VALID"
            ts_event_ms = max(spot.ts_event_ms, perp.ts_event_ms)
            ts_recv_ms = max(spot.ts_recv_wall_ms, perp.ts_recv_wall_ms)
            diagnostics["pstar_recv_ts_ms"] = ts_recv_ms
            diagnostics["pstar_sourceset"] = ["perp", "spot"]
            diagnostics["state"] = state
            return PStar(
                symbol=symbol,
                value=mid,
                ts_event_ms=ts_event_ms,
                sources_used={"spot", "perp"},
                confidence=float(confidence),
                valid=True,
                diagnostics=diagnostics,
                ts_recv_ms=ts_recv_ms,
                invalid_reason=None,
                state=state,
            )

        if not self.allow_degraded_single_source:
            state = "UNAVAILABLE"
            diagnostics["freeze_reason"] = "missing_required_sources"
            diagnostics["invalid_reason"] = "missing_required_sources"
            diagnostics["state"] = state
            return PStar(
                symbol=symbol,
                value=None,
                ts_event_ms=None,
                sources_used=set(),
                confidence=0.0,
                valid=False,
                diagnostics=diagnostics,
                ts_recv_ms=None,
                invalid_reason="missing_required_sources",
                state=state,
            )

        freshest = _freshest(by_source)
        if freshest is None:
            state = "UNAVAILABLE"
            diagnostics["freeze_reason"] = "missing_required_sources"
            diagnostics["invalid_reason"] = "missing_required_sources"
            diagnostics["state"] = state
            return PStar(
                symbol=symbol,
                value=None,
                ts_event_ms=None,
                sources_used=set(),
                confidence=0.0,
                valid=False,
                diagnostics=diagnostics,
                ts_recv_ms=None,
                invalid_reason="missing_required_sources",
                state=state,
            )
        state = "WARMING"
        diagnostics["degraded"] = True
        diagnostics["freeze_reason"] = None
        diagnostics["single_source"] = freshest.source
        diagnostics["pstar_recv_ts_ms"] = int(freshest.ts_recv_wall_ms)
        diagnostics["pstar_sourceset"] = [str(freshest.source)]
        diagnostics["state"] = state
        return PStar(
            symbol=symbol,
            value=float(freshest.value),
            ts_event_ms=int(freshest.ts_event_ms),
            sources_used={freshest.source},
            confidence=0.4,
            valid=True,
            diagnostics=diagnostics,
            ts_recv_ms=int(freshest.ts_recv_wall_ms),
            invalid_reason=None,
            state=state,
        )


def _freshest(by_source: Dict[str, SourceSnapshot]) -> Optional[SourceSnapshot]:
    if not by_source:
        return None
    snapshots = sorted(by_source.values(), key=lambda s: (s.ts_event_ms, s.ts_recv_wall_ms), reverse=True)
    return snapshots[0]
