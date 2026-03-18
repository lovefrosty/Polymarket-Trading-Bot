from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class UpdateResult:
    recv_out_of_order: bool
    event_time_regressed: bool


@dataclass(frozen=True)
class ExecutionDiagnostics:
    vwap_price: Optional[float]
    depth_at_qty: float
    slippage_bps: Optional[float]
    spread_bps: Optional[float]
    book_age_ms: Optional[float]


@dataclass
class OrderBook:
    asset_id: str
    bids: Dict[float, float]
    asks: Dict[float, float]
    last_event_ts_ms: Optional[int] = None
    last_recv_mono_ns: int = 0
    last_hash: Optional[str] = None

    def apply_snapshot(
        self,
        bids: Iterable[Tuple[float, float]],
        asks: Iterable[Tuple[float, float]],
        event_ts_ms: Optional[int],
        recv_mono_ns: int,
        last_hash: Optional[str] = None,
    ) -> UpdateResult:
        recv_out_of_order = recv_mono_ns < self.last_recv_mono_ns
        event_time_regressed = self._event_time_regressed(event_ts_ms)
        if recv_out_of_order:
            return UpdateResult(recv_out_of_order=True, event_time_regressed=event_time_regressed)
        self.bids = {float(p): float(s) for p, s in bids if float(s) > 0}
        self.asks = {float(p): float(s) for p, s in asks if float(s) > 0}
        self._update_timestamps(event_ts_ms, recv_mono_ns)
        if last_hash is not None:
            self.last_hash = last_hash
        return UpdateResult(recv_out_of_order=False, event_time_regressed=event_time_regressed)

    def apply_update(
        self,
        side: str,
        price: float,
        size: float,
        event_ts_ms: Optional[int],
        recv_mono_ns: int,
        last_hash: Optional[str] = None,
    ) -> UpdateResult:
        recv_out_of_order = recv_mono_ns < self.last_recv_mono_ns
        event_time_regressed = self._event_time_regressed(event_ts_ms)
        if recv_out_of_order:
            return UpdateResult(recv_out_of_order=True, event_time_regressed=event_time_regressed)
        book = self.bids if side == "buy" else self.asks
        if size <= 0:
            book.pop(float(price), None)
        else:
            book[float(price)] = float(size)
        self._update_timestamps(event_ts_ms, recv_mono_ns)
        if last_hash is not None:
            self.last_hash = last_hash
        return UpdateResult(recv_out_of_order=False, event_time_regressed=event_time_regressed)

    def best_bid(self) -> Optional[float]:
        return max(self.bids.keys()) if self.bids else None

    def best_ask(self) -> Optional[float]:
        return min(self.asks.keys()) if self.asks else None

    def mid(self) -> Optional[float]:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def spread_abs(self) -> Optional[float]:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return ask - bid

    def spread_bps(self) -> Optional[float]:
        mid = self.mid()
        spread = self.spread_abs()
        if mid is None or spread is None or mid == 0:
            return None
        return (spread / mid) * 10000.0

    def depth_at_qty(self, side: str, qty: float) -> float:
        if qty <= 0:
            return 0.0
        levels = self._sorted_levels(side)
        filled = 0.0
        for _, size in levels:
            if filled >= qty:
                break
            take = min(size, qty - filled)
            filled += take
        return filled

    def depth_within_ticks_bid(self, ticks: int, tick_size: float) -> float:
        if ticks < 0 or tick_size <= 0:
            return 0.0
        best = self.best_bid()
        if best is None:
            return 0.0
        threshold = best - ticks * tick_size
        return sum(size for price, size in self.bids.items() if price >= threshold)

    def depth_within_ticks_ask(self, ticks: int, tick_size: float) -> float:
        if ticks < 0 or tick_size <= 0:
            return 0.0
        best = self.best_ask()
        if best is None:
            return 0.0
        threshold = best + ticks * tick_size
        return sum(size for price, size in self.asks.items() if price <= threshold)

    def vwap_to_fill(self, side: str, qty: float) -> Optional[float]:
        if qty <= 0:
            return None
        levels = self._sorted_levels(side)
        filled = 0.0
        cost = 0.0
        for price, size in levels:
            if filled >= qty:
                break
            take = min(size, qty - filled)
            cost += take * price
            filled += take
        if filled < qty:
            return None
        return cost / filled

    def executable_price(self, side: str, qty: float) -> Optional[float]:
        return self.vwap_to_fill(side, qty)

    def expected_slippage_to_fill(self, side: str, qty: float) -> float:
        if qty <= 0:
            return math.inf
        mid = self.mid()
        if mid is None or mid <= 0:
            return math.inf
        levels = self._sorted_levels(side)
        filled = 0.0
        cost = 0.0
        for price, size in levels:
            if filled >= qty:
                break
            take = min(size, qty - filled)
            cost += take * price
            filled += take
        if filled < qty:
            return math.inf
        vwap = cost / filled
        if side == "buy":
            slippage = (vwap - mid) / mid
        else:
            slippage = (mid - vwap) / mid
        return slippage * 10000.0

    def execution_diagnostics(self, side: str, qty: float, decision_mono_ns: int) -> ExecutionDiagnostics:
        vwap = self.vwap_to_fill(side, qty)
        depth = self.depth_at_qty(side, qty)
        slippage_bps = None
        if vwap is not None:
            slippage = self.expected_slippage_to_fill(side, qty)
            slippage_bps = slippage if math.isfinite(slippage) else None
        spread_bps = self.spread_bps()
        book_age_ms = None
        if self.last_recv_mono_ns > 0:
            book_age_ms = (decision_mono_ns - self.last_recv_mono_ns) / 1_000_000.0
        return ExecutionDiagnostics(
            vwap_price=vwap,
            depth_at_qty=depth,
            slippage_bps=slippage_bps,
            spread_bps=spread_bps,
            book_age_ms=book_age_ms,
        )

    def book_is_stale(self, now_mono_ns: int, staleness_ms: int) -> bool:
        if self.last_recv_mono_ns == 0:
            return True
        age_ms = (now_mono_ns - self.last_recv_mono_ns) / 1_000_000.0
        return age_ms > staleness_ms

    def _sorted_levels(self, side: str) -> List[Tuple[float, float]]:
        if side == "buy":
            return sorted(self.asks.items(), key=lambda x: x[0])
        return sorted(self.bids.items(), key=lambda x: -x[0])

    def _event_time_regressed(self, event_ts_ms: Optional[int]) -> bool:
        if event_ts_ms is None or self.last_event_ts_ms is None:
            return False
        return event_ts_ms < self.last_event_ts_ms

    def _update_timestamps(self, event_ts_ms: Optional[int], recv_mono_ns: int) -> None:
        if event_ts_ms is not None:
            if self.last_event_ts_ms is None:
                self.last_event_ts_ms = event_ts_ms
            else:
                self.last_event_ts_ms = max(self.last_event_ts_ms, event_ts_ms)
        self.last_recv_mono_ns = max(self.last_recv_mono_ns, recv_mono_ns)
