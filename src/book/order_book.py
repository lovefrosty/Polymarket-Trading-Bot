from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Iterable, List, Tuple


@dataclass(frozen=True)
class DepthMetrics:
    filled_qty: float
    avg_price: float
    depth_ok: bool
    effective_spread_bps: float
    slippage_to_mid_bps: float


class OrderBook:
    def __init__(self) -> None:
        self.bids: List[Tuple[float, float]] = []
        self.asks: List[Tuple[float, float]] = []
        self.last_update_ts: int = 0
        self._logger = logging.getLogger(__name__)

    def set_snapshot(
        self,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        ts: int,
    ) -> None:
        self.bids = sorted([(p, s) for p, s in bids if s > 0], key=lambda x: -x[0])
        self.asks = sorted([(p, s) for p, s in asks if s > 0], key=lambda x: x[0])
        self.last_update_ts = ts

    def update_l2(self, bid_updates: Iterable, ask_updates: Iterable, ts: int) -> None:
        if ts < self.last_update_ts:
            self._logger.warning("order_book_out_of_order_ts current=%s update=%s", self.last_update_ts, ts)
        self._apply_updates(self.bids, bid_updates, descending=True)
        self._apply_updates(self.asks, ask_updates, descending=False)
        self.last_update_ts = max(self.last_update_ts, ts)

    def is_stale(self, as_of_ts: int, max_age_ms: int) -> bool:
        return as_of_ts - self.last_update_ts > max_age_ms

    def has_book(self) -> bool:
        return bool(self.bids) and bool(self.asks)

    def best_bid(self) -> float:
        return self.bids[0][0]

    def best_ask(self) -> float:
        return self.asks[0][0]

    def depth_metrics(self, side: str, qty: float) -> DepthMetrics:
        if qty <= 0:
            raise ValueError("qty must be positive")
        if not self.has_book():
            return DepthMetrics(0.0, 0.0, False, float("inf"), float("inf"))

        levels = self.asks if side == "buy" else self.bids
        filled = 0.0
        cost = 0.0
        for price, size in levels:
            if filled >= qty:
                break
            take = min(size, qty - filled)
            cost += take * price
            filled += take

        avg_price = cost / filled if filled > 0 else 0.0
        depth_ok = filled >= qty
        mid = (self.best_bid() + self.best_ask()) / 2.0
        spread_bps = ((self.best_ask() - self.best_bid()) / mid) * 10000.0
        if side == "buy":
            slippage_bps = ((avg_price - mid) / mid) * 10000.0
        else:
            slippage_bps = ((mid - avg_price) / mid) * 10000.0
        return DepthMetrics(filled, avg_price, depth_ok, spread_bps, slippage_bps)

    def _apply_updates(self, book: List[Tuple[float, float]], updates: Iterable, descending: bool) -> None:
        price_map = {price: size for price, size in book}
        for update in updates:
            price = float(update.price)
            size = float(update.size)
            if size <= 0:
                price_map.pop(price, None)
            else:
                price_map[price] = size
        new_book = [(p, s) for p, s in price_map.items() if s > 0]
        book[:] = sorted(new_book, key=lambda x: -x[0] if descending else x[0])
