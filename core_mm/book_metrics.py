from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


Level = Tuple[float, float]


@dataclass(frozen=True)
class MeaningfulBBO:
    best_bid: Optional[float]
    best_bid_size: float
    second_bid: Optional[float]
    best_ask: Optional[float]
    best_ask_size: float
    second_ask: Optional[float]
    top_bid: Optional[float]
    top_ask: Optional[float]
    bid_sum_within_n_percent: float
    ask_sum_within_n_percent: float
    min_size_used: float

    def as_dict(self) -> dict:
        return {
            "best_bid": self.best_bid,
            "best_bid_size": self.best_bid_size,
            "second_bid": self.second_bid,
            "best_ask": self.best_ask,
            "best_ask_size": self.best_ask_size,
            "second_ask": self.second_ask,
            "top_bid": self.top_bid,
            "top_ask": self.top_ask,
            "bid_sum_within_n_percent": self.bid_sum_within_n_percent,
            "ask_sum_within_n_percent": self.ask_sum_within_n_percent,
            "min_size_used": self.min_size_used,
        }


def find_meaningful_bbo(
    bids: Iterable[Level],
    asks: Iterable[Level],
    min_size: float = 100.0,
    fallback_size: float = 20.0,
    within_pct: float = 0.02,
) -> Optional[MeaningfulBBO]:
    bid_levels = _normalize_levels(bids, reverse=True)
    ask_levels = _normalize_levels(asks, reverse=False)
    if not bid_levels or not ask_levels:
        return None

    top_bid = bid_levels[0][0]
    top_ask = ask_levels[0][0]
    mid = (top_bid + top_ask) / 2.0 if top_bid is not None and top_ask is not None else None

    threshold = float(min_size)
    best_bid_idx = _find_meaningful_index(bid_levels, threshold)
    best_ask_idx = _find_meaningful_index(ask_levels, threshold)
    if best_bid_idx is None or best_ask_idx is None:
        threshold = float(fallback_size)
        best_bid_idx = _find_meaningful_index(bid_levels, threshold)
        best_ask_idx = _find_meaningful_index(ask_levels, threshold)
    if best_bid_idx is None or best_ask_idx is None:
        return None

    best_bid, best_bid_size = bid_levels[best_bid_idx]
    best_ask, best_ask_size = ask_levels[best_ask_idx]
    second_bid = bid_levels[best_bid_idx + 1][0] if best_bid_idx + 1 < len(bid_levels) else None
    second_ask = ask_levels[best_ask_idx + 1][0] if best_ask_idx + 1 < len(ask_levels) else None
    bid_sum, ask_sum = _sum_near_mid(bid_levels, ask_levels, mid, within_pct)

    return MeaningfulBBO(
        best_bid=best_bid,
        best_bid_size=best_bid_size,
        second_bid=second_bid,
        best_ask=best_ask,
        best_ask_size=best_ask_size,
        second_ask=second_ask,
        top_bid=top_bid,
        top_ask=top_ask,
        bid_sum_within_n_percent=bid_sum,
        ask_sum_within_n_percent=ask_sum,
        min_size_used=threshold,
    )


def _normalize_levels(levels: Iterable[Level], reverse: bool) -> List[Level]:
    normalized = [(float(price), float(size)) for price, size in levels if float(size) > 0]
    return sorted(normalized, key=lambda item: item[0], reverse=reverse)


def _find_meaningful_index(levels: Sequence[Level], min_size: float) -> Optional[int]:
    for index, (_, size) in enumerate(levels):
        if float(size) >= float(min_size):
            return index
    return None


def _sum_near_mid(
    bid_levels: Sequence[Level],
    ask_levels: Sequence[Level],
    mid: Optional[float],
    within_pct: float,
) -> Tuple[float, float]:
    if mid is None or mid <= 0 or within_pct < 0:
        return 0.0, 0.0
    lower = mid * (1.0 - float(within_pct))
    upper = mid * (1.0 + float(within_pct))
    bid_sum = sum(size for price, size in bid_levels if price >= lower)
    ask_sum = sum(size for price, size in ask_levels if price <= upper)
    return float(bid_sum), float(ask_sum)
