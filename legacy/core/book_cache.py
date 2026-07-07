from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
from typing import Dict, List, Optional, Tuple

from core.order_book import OrderBook


Level = Tuple[float, float]


class BookHealthState(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    DOWN = "DOWN"


@dataclass(frozen=True)
class BookSnapshot:
    token_id: str
    bids: Tuple[Level, ...]
    asks: Tuple[Level, ...]
    ts_event_ms: Optional[int]
    ts_recv_mono_ns: int
    ts_recv_wall_ms: int
    book_asof_ts_ms: Optional[int] = None
    book_recv_ts_ms: Optional[int] = None
    book_seq: int = 0
    book_level_count: int = 0
    book_health_state: Optional[str] = None

    @classmethod
    def from_order_book(
        cls,
        token_id: str,
        book: OrderBook,
        ts_recv_wall_ms: int,
        book_seq: int = 0,
    ) -> "BookSnapshot":
        bids = tuple(sorted([(float(p), float(s)) for p, s in book.bids.items() if s > 0], key=lambda x: -x[0]))
        asks = tuple(sorted([(float(p), float(s)) for p, s in book.asks.items() if s > 0], key=lambda x: x[0]))
        asof_ts_ms = int(book.last_event_ts_ms if book.last_event_ts_ms is not None else ts_recv_wall_ms)
        recv_ts_ms = int(ts_recv_wall_ms)
        level_count = int(len(bids) + len(asks))
        age_ms = max(0, recv_ts_ms - asof_ts_ms)
        if age_ms > 120_000:
            health_state = BookHealthState.DOWN.value
        elif age_ms > 30_000:
            health_state = BookHealthState.STALE.value
        else:
            health_state = BookHealthState.FRESH.value
        return cls(
            token_id=token_id,
            bids=bids,
            asks=asks,
            ts_event_ms=book.last_event_ts_ms,
            ts_recv_mono_ns=book.last_recv_mono_ns,
            ts_recv_wall_ms=int(ts_recv_wall_ms),
            book_asof_ts_ms=asof_ts_ms,
            book_recv_ts_ms=recv_ts_ms,
            book_seq=int(book_seq),
            book_level_count=level_count,
            book_health_state=health_state,
        )

    def is_fresh(self, now_wall_ms: int, threshold_ms: int) -> bool:
        if self.ts_event_ms is None:
            return False
        return (now_wall_ms - self.ts_event_ms) <= threshold_ms

    def age_ms(self, now_wall_ms: int) -> Optional[int]:
        if self.ts_event_ms is None:
            return None
        return int(max(0, now_wall_ms - self.ts_event_ms))

    def health_state(
        self,
        now_wall_ms: int,
        stale_after_ms: int = 30_000,
        down_after_ms: int = 120_000,
    ) -> BookHealthState:
        age_ms = self.age_ms(now_wall_ms)
        if age_ms is None or age_ms > int(down_after_ms):
            return BookHealthState.DOWN
        if age_ms > int(stale_after_ms):
            return BookHealthState.STALE
        return BookHealthState.FRESH

    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    def mid(self) -> Optional[float]:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def spread_bps(self) -> Optional[float]:
        mid = self.mid()
        bid = self.best_bid()
        ask = self.best_ask()
        if mid is None or bid is None or ask is None or mid <= 0:
            return None
        return ((ask - bid) / mid) * 10000.0

    def depth_at_qty(self, side: str, qty: float) -> float:
        if qty <= 0:
            return 0.0
        levels = self._levels_for_side(side)
        filled = 0.0
        for _, size in levels:
            if filled >= qty:
                break
            filled += min(size, qty - filled)
        return filled

    def depth_at_notional(self, side: str, notional: float) -> float:
        if notional <= 0:
            return 0.0
        levels = self._levels_for_side(side)
        remaining = float(notional)
        filled_qty = 0.0
        for price, size in levels:
            if remaining <= 0:
                break
            if price <= 0:
                continue
            level_notional = float(price) * float(size)
            take_notional = min(level_notional, remaining)
            take_qty = take_notional / float(price)
            filled_qty += take_qty
            remaining -= take_notional
        return float(filled_qty)

    def vwap_to_fill(self, side: str, qty: float) -> Optional[float]:
        if qty <= 0:
            return None
        levels = self._levels_for_side(side)
        filled = 0.0
        notional = 0.0
        for price, size in levels:
            if filled >= qty:
                break
            take = min(size, qty - filled)
            notional += take * price
            filled += take
        if filled < qty:
            return None
        return notional / filled if filled > 0 else None

    def expected_slippage_bps(self, side: str, qty: float) -> Optional[float]:
        mid = self.mid()
        vwap = self.vwap_to_fill(side, qty)
        if mid is None or mid <= 0 or vwap is None:
            return None
        if side.lower() in {"buy", "bid"}:
            return ((vwap - mid) / mid) * 10000.0
        return ((mid - vwap) / mid) * 10000.0

    def effective_spread_bps(self, side: str, qty: float) -> Optional[float]:
        mid = self.mid()
        vwap = self.vwap_to_fill(side, qty)
        if mid is None or mid <= 0 or vwap is None:
            return None
        return abs(vwap - mid) / mid * 2.0 * 10000.0

    def to_l2_rows(self) -> List[Tuple[str, str, float, float, int]]:
        rows: List[Tuple[str, str, float, float, int]] = []
        event_ts = self.ts_event_ms or self.ts_recv_wall_ms
        for price, size in self.bids:
            rows.append((self.token_id, "buy", float(price), float(size), int(event_ts)))
        for price, size in self.asks:
            rows.append((self.token_id, "sell", float(price), float(size), int(event_ts)))
        return rows

    def _levels_for_side(self, side: str) -> Tuple[Level, ...]:
        side_norm = side.lower()
        if side_norm in {"buy", "bid"}:
            return self.asks
        return self.bids


class BookCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_token: Dict[str, BookSnapshot] = {}

    def update(self, snapshot: BookSnapshot) -> None:
        with self._lock:
            self._by_token[snapshot.token_id] = snapshot

    def get(self, token_id: str) -> Optional[BookSnapshot]:
        with self._lock:
            return self._by_token.get(token_id)

    def snapshot(self) -> Dict[str, BookSnapshot]:
        with self._lock:
            return dict(self._by_token)

    def is_token_stale(self, token_id: str, now_wall_ms: int, threshold_ms: int) -> bool:
        snap = self.get(token_id)
        if snap is None:
            return True
        return not snap.is_fresh(now_wall_ms, threshold_ms)

    def health_state(
        self,
        token_id: str,
        now_wall_ms: int,
        stale_after_ms: int = 30_000,
        down_after_ms: int = 120_000,
    ) -> BookHealthState:
        snap = self.get(token_id)
        if snap is None:
            return BookHealthState.DOWN
        return snap.health_state(
            now_wall_ms=now_wall_ms,
            stale_after_ms=stale_after_ms,
            down_after_ms=down_after_ms,
        )
