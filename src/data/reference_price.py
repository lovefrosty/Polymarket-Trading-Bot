from __future__ import annotations

from dataclasses import dataclass
import math
import warnings
from typing import Iterable, Optional, Set


@dataclass(frozen=True)
class PriceSource:
    name: str
    value: float
    ts: int


@dataclass(frozen=True)
class ValidatedPrice:
    value: float
    ts: int
    sources: Set[str]
    confidence: float


@dataclass(frozen=True)
class ReferencePriceResult:
    price: Optional[ValidatedPrice]
    freeze_reason: Optional[str]
    diff_bps: Optional[float] = None
    disagreement_multiplier: Optional[float] = None


class ReferencePriceValidator:
    def __init__(
        self,
        staleness_ms: int,
        disagreement_bps: float,
        min_confidence: float,
        disagreement_bps_soft: Optional[float] = None,
        disagreement_bps_hard: Optional[float] = None,
        disagreement_decay_k: float = 1.0,
        required_sources: Optional[Set[str]] = None,
        allow_override_required_sources: bool = False,
    ) -> None:
        default_sources = {"spot", "perp"}
        if required_sources is None:
            required_sources = default_sources
        if required_sources != default_sources and not allow_override_required_sources:
            raise ValueError("required_sources_override_not_allowed")
        if required_sources != default_sources and allow_override_required_sources:
            warnings.warn(
                f"required_sources_override:{','.join(sorted(required_sources))}",
                RuntimeWarning,
            )
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

    def validate(self, sources: Iterable[PriceSource], decision_ts: int) -> ReferencePriceResult:
        source_map = {source.name: source for source in sources}
        missing = self.required_sources - set(source_map.keys())
        if missing:
            return ReferencePriceResult(None, f"missing_sources:{','.join(sorted(missing))}")

        for name in self.required_sources:
            source = source_map[name]
            if source.ts >= decision_ts:
                return ReferencePriceResult(None, "reference_price_from_future")
            if decision_ts - source.ts > self.staleness_ms:
                return ReferencePriceResult(None, f"stale_source:{name}")

        if self.required_sources >= {"spot", "perp"}:
            spot = source_map["spot"]
            perp = source_map["perp"]
            mid = (spot.value + perp.value) / 2.0
            if mid <= 0:
                return ReferencePriceResult(None, "invalid_mid")
            diff_ratio = abs(spot.value - perp.value) / mid
            diff_bps = diff_ratio * 10000.0
            if diff_bps > self.disagreement_bps_hard:
                return ReferencePriceResult(
                    None,
                    "pstar_disagreement_extreme",
                    diff_bps=diff_bps,
                    disagreement_multiplier=0.0,
                )
            confidence = _disagreement_multiplier(
                diff_bps,
                self.disagreement_bps_soft,
                self.disagreement_bps_hard,
                self.disagreement_decay_k,
            )
            ts = max(spot.ts, perp.ts)
            return ReferencePriceResult(
                ValidatedPrice(mid, ts, {"spot", "perp"}, confidence),
                None,
                diff_bps=diff_bps,
                disagreement_multiplier=confidence,
            )

        # Fallback for required sources without spot/perp pairing.
        values = [source_map[name].value for name in self.required_sources]
        ts = max(source_map[name].ts for name in self.required_sources)
        avg = sum(values) / float(len(values))
        return ReferencePriceResult(
            ValidatedPrice(avg, ts, set(self.required_sources), 1.0),
            None,
        )


def _disagreement_multiplier(diff_bps: float, soft: float, hard: float, decay_k: float) -> float:
    if hard <= soft:
        return 1.0 if diff_bps <= soft else 0.0
    if diff_bps <= soft:
        return 1.0
    if diff_bps >= hard:
        return 0.0
    x = (diff_bps - soft) / (hard - soft)
    return float(math.exp(-decay_k * x))
