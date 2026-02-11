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

    def build(self, symbol: str, now_wall_ms: int) -> PStar:
        by_source = self._latest.get(symbol) or {}
        missing = sorted(self.required_sources - set(by_source.keys()))
        diagnostics: Dict[str, object] = {
            "symbol": symbol,
            "missing_sources": missing,
            "freeze_reason": None,
            "degraded": False,
            "age_ms": {},
            "disagreement_bps": None,
            "sources_available": sorted(by_source.keys()),
            "spot_px": None,
            "perp_px": None,
            "single_source": None,
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
            diagnostics["freeze_reason"] = f"stale_source:{','.join(sorted(stale_sources))}"
            return PStar(
                symbol=symbol,
                value=None,
                ts_event_ms=None,
                sources_used=set(),
                confidence=0.0,
                valid=False,
                diagnostics=diagnostics,
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
                diagnostics["freeze_reason"] = "non_positive_reference"
                return PStar(
                    symbol=symbol,
                    value=None,
                    ts_event_ms=None,
                    sources_used={"spot", "perp"},
                    confidence=0.0,
                    valid=False,
                    diagnostics=diagnostics,
                )
            disagree_bps = abs(perp.value - spot.value) / mid * 10000.0
            diagnostics["disagreement_bps"] = disagree_bps
            if disagree_bps >= self.freeze_disagree_bps:
                diagnostics["freeze_reason"] = "pstar_disagreement_extreme"
                return PStar(
                    symbol=symbol,
                    value=None,
                    ts_event_ms=None,
                    sources_used={"spot", "perp"},
                    confidence=0.0,
                    valid=False,
                    diagnostics=diagnostics,
                )
            confidence = 1.0
            if disagree_bps > self.degrade_disagree_bps:
                width = max(1.0, self.freeze_disagree_bps - self.degrade_disagree_bps)
                x = min(1.0, (disagree_bps - self.degrade_disagree_bps) / width)
                confidence = max(0.2, 1.0 - x)
            ts_event_ms = max(spot.ts_event_ms, perp.ts_event_ms)
            return PStar(
                symbol=symbol,
                value=mid,
                ts_event_ms=ts_event_ms,
                sources_used={"spot", "perp"},
                confidence=float(confidence),
                valid=True,
                diagnostics=diagnostics,
            )

        if not self.allow_degraded_single_source:
            diagnostics["freeze_reason"] = "missing_required_sources"
            return PStar(
                symbol=symbol,
                value=None,
                ts_event_ms=None,
                sources_used=set(),
                confidence=0.0,
                valid=False,
                diagnostics=diagnostics,
            )

        freshest = _freshest(by_source)
        if freshest is None:
            diagnostics["freeze_reason"] = "missing_required_sources"
            return PStar(
                symbol=symbol,
                value=None,
                ts_event_ms=None,
                sources_used=set(),
                confidence=0.0,
                valid=False,
                diagnostics=diagnostics,
            )
        diagnostics["degraded"] = True
        diagnostics["freeze_reason"] = None
        diagnostics["single_source"] = freshest.source
        return PStar(
            symbol=symbol,
            value=float(freshest.value),
            ts_event_ms=int(freshest.ts_event_ms),
            sources_used={freshest.source},
            confidence=0.4,
            valid=True,
            diagnostics=diagnostics,
        )


def _freshest(by_source: Dict[str, SourceSnapshot]) -> Optional[SourceSnapshot]:
    if not by_source:
        return None
    snapshots = sorted(by_source.values(), key=lambda s: (s.ts_event_ms, s.ts_recv_wall_ms), reverse=True)
    return snapshots[0]
