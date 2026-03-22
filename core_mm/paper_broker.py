from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional

from core_mm.book_manager import BookManager
from core_mm.execution import ExecutionResult
from core_mm.positions import PositionTracker


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    placed_at_ms: int
    client_order_id: Optional[str] = None
    quote_group_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    status: str = "open"


class PaperBroker:
    def __init__(
        self,
        *,
        book_manager: BookManager,
        position_tracker: Optional[PositionTracker] = None,
        fee_bps: float = 25.0,
        fee_mode: str = "taker",
        # Realism parameters
        stale_book_ms: int = 5_000,
        min_queue_wait_ms: int = 200,
        queue_depth_fraction: float = 0.5,
    ) -> None:
        self._book_manager = book_manager
        self._positions = position_tracker or PositionTracker()
        self._orders: Dict[str, PaperOrder] = {}
        self._fills: List[Dict[str, Any]] = []
        self._fill_cursor = 0
        self._next_id = 1
        self._fee_bps = float(fee_bps)
        self._fee_mode = str(fee_mode).lower()
        # Realism controls
        self._stale_book_ms = int(stale_book_ms)
        self._min_queue_wait_ms = int(min_queue_wait_ms)
        # Fraction of the visible size at the fill level that we can assume
        # we are behind (0.5 = we are behind 50% of displayed depth on average).
        # Fills where our size > remaining_depth are capped to remaining_depth.
        self._queue_depth_fraction = float(max(0.0, min(1.0, queue_depth_fraction)))
        self._stats: Dict[str, float] = {
            "realized_gross_pnl": 0.0,
            "realized_net_pnl": 0.0,
            "cumulative_fees": 0.0,
            "turnover": 0.0,
            "win_count": 0.0,
            "loss_count": 0.0,
        }
        # Bankroll tracking: USDC spent on buys - received from sells
        self._bankroll_spent: float = 0.0
        self._bankroll_received: float = 0.0
        # Markout tracking: measure fill quality at 1s / 5s / 30s horizons
        self._pending_markouts: List[Dict[str, Any]] = []
        self._avg_markout_1s: float = 0.0
        self._markout_initialized: bool = False
        # FIFO inventory duration tracking
        self._fifo_entries: Dict[str, List[List[float]]] = defaultdict(list)
        self._duration_total_weighted_ms: float = 0.0
        self._duration_total_closed_qty: float = 0.0

    @property
    def position_tracker(self) -> PositionTracker:
        return self._positions

    def place_order(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        size: float,
        client_order_id: Optional[str] = None,
        quote_group_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        neg_risk: bool = False,
        time_in_force: Optional[str] = None,
    ) -> ExecutionResult:
        order_id = f"paper-{self._next_id}"
        self._next_id += 1
        placed_at_ms = _now_ms()
        order = PaperOrder(
            order_id=order_id,
            token_id=str(token_id),
            side=str(side).lower(),
            price=float(price),
            size=float(size),
            placed_at_ms=placed_at_ms,
            client_order_id=client_order_id,
            quote_group_id=quote_group_id,
            metadata=dict(metadata or {}),
        )
        self._orders[order_id] = order
        fill = self._try_fill(order)
        payload: Dict[str, Any] = {
            "orderID": order_id,
            "status": order.status,
            "neg_risk": bool(neg_risk),
            "client_order_id": client_order_id,
            "quote_group_id": quote_group_id,
        }
        if fill is not None:
            payload["fill"] = fill
        return ExecutionResult(True, payload)

    def cancel_order(self, order_id: str) -> ExecutionResult:
        order = self._orders.get(str(order_id))
        if order is None:
            return ExecutionResult(False, payload={}, error="order_not_found")
        self._orders[str(order_id)] = PaperOrder(**{**order.__dict__, "status": "canceled"})
        return ExecutionResult(True, {"canceled": [str(order_id)]})

    def cancel_all(self) -> ExecutionResult:
        for order_id, order in list(self._orders.items()):
            self._orders[order_id] = PaperOrder(**{**order.__dict__, "status": "canceled"})
        return ExecutionResult(True, {"canceled": list(self._orders.keys())})

    def get_open_orders(self) -> ExecutionResult:
        return ExecutionResult(
            True,
            {"orders": [order.__dict__.copy() for order in self._orders.values() if order.status == "open"]},
        )

    def get_positions(self) -> ExecutionResult:
        positions = [
            {"token_id": token_id, "size": position.size, "avg_price": position.avg_price}
            for token_id, position in self._positions.snapshot().items()
        ]
        return ExecutionResult(True, {"positions": positions})

    def fills(self) -> List[Dict[str, Any]]:
        return list(self._fills)

    def drain_new_fills(self) -> List[Dict[str, Any]]:
        if self._fill_cursor >= len(self._fills):
            return []
        out = self._fills[self._fill_cursor :]
        self._fill_cursor = len(self._fills)
        return list(out)

    def stats(self) -> Dict[str, float]:
        result = dict(self._stats)
        result["avg_duration_ms"] = self.avg_duration_ms
        result["bankroll_spent"] = self._bankroll_spent
        result["bankroll_received"] = self._bankroll_received
        result["bankroll_net_cash_flow"] = self._bankroll_received - self._bankroll_spent
        result["unrealized_pnl"] = self._compute_unrealized_pnl()
        return result

    def _compute_unrealized_pnl(self) -> float:
        """Estimate unrealized PnL from open positions at current mid prices."""
        total = 0.0
        for token_id, position in self._positions.snapshot().items():
            if position.size <= 0 or position.avg_price <= 0:
                continue
            book = self._book_manager.get_book(token_id)
            if book is None or book.mid_price is None:
                continue
            total += (float(book.mid_price) - position.avg_price) * position.size
        return total

    def _consume_fifo(self, token_id: str, qty: float, exit_ts_ms: int) -> None:
        remaining = float(qty)
        entries = self._fifo_entries.get(str(token_id), [])
        while remaining > 1e-9 and entries:
            entry_ts, entry_qty = float(entries[0][0]), float(entries[0][1])
            consume = min(remaining, entry_qty)
            duration_ms = max(0.0, float(exit_ts_ms) - entry_ts)
            self._duration_total_weighted_ms += duration_ms * consume
            self._duration_total_closed_qty += consume
            remaining -= consume
            entries[0][1] -= consume
            if entries[0][1] <= 1e-9:
                entries.pop(0)

    def consume_fifo_for_merge(self, token_id: str, qty: float, merge_ts_ms: int) -> None:
        self._consume_fifo(token_id, qty, merge_ts_ms)

    @property
    def avg_duration_ms(self) -> float:
        if self._duration_total_closed_qty <= 0:
            return 0.0
        return self._duration_total_weighted_ms / self._duration_total_closed_qty

    def sweep_fills(self, token_id: Optional[str] = None) -> List[Dict[str, Any]]:
        fills: List[Dict[str, Any]] = []
        for order in list(self._orders.values()):
            if order.status != "open":
                continue
            if token_id is not None and order.token_id != str(token_id):
                continue
            fill = self._try_fill(order, allow_touch_fill=True)
            if fill is not None:
                fills.append(fill)
        self.process_markouts()
        return fills

    def process_markouts(self, now_ms: Optional[int] = None) -> None:
        """Evaluate pending fill markouts and update the rolling EWMA average."""
        if not self._pending_markouts:
            return
        ts = int(now_ms) if now_ms is not None else _now_ms()
        still_pending: List[Dict[str, Any]] = []
        for record in self._pending_markouts:
            book = self._book_manager.get_book(record["token_id"])
            if book is None or book.mid_price is None:
                still_pending.append(record)
                continue
            mid_ref = record["mid_at_fill"]
            if mid_ref <= 0:
                still_pending.append(record)
                continue
            elapsed = ts - record["fill_ts_ms"]
            raw_move = float(book.mid_price) - mid_ref
            # Sign convention: +bps = price moved in favour of the fill
            signed_bps = (raw_move / mid_ref * 10_000.0) if record["side"] == "buy" else (-raw_move / mid_ref * 10_000.0)
            if elapsed >= 1_000 and record["markout_1s_bps"] is None:
                record["markout_1s_bps"] = signed_bps
                if not self._markout_initialized:
                    self._avg_markout_1s = signed_bps
                    self._markout_initialized = True
                else:
                    self._avg_markout_1s = 0.9 * self._avg_markout_1s + 0.1 * signed_bps
            if elapsed >= 5_000 and record["markout_5s_bps"] is None:
                record["markout_5s_bps"] = signed_bps
            if elapsed >= 30_000 and record["markout_30s_bps"] is None:
                record["markout_30s_bps"] = signed_bps
            if record["markout_30s_bps"] is None:
                still_pending.append(record)
        self._pending_markouts = still_pending

    @property
    def avg_markout_1s_bps(self) -> float:
        """Rolling EWMA of 1-second markout across all fills (span ≈ 20)."""
        return self._avg_markout_1s

    def _try_fill(self, order: PaperOrder, *, allow_touch_fill: bool = False) -> Optional[Dict[str, Any]]:
        book = self._book_manager.get_book(order.token_id)
        if book is None:
            return None

        # Realism gate 1: refuse to fill on stale book data.
        # In live trading, a stale feed means we have no idea where the market
        # actually is — filling at stale prices is unrealistically optimistic.
        now_ms = _now_ms()
        if self._stale_book_ms > 0 and book.last_update_ms is not None and (now_ms - book.last_update_ms) > self._stale_book_ms:
            return None

        fill_price: Optional[float] = None
        fill_trigger = "cross"
        liquidity_mode = "taker"

        if order.side == "buy" and book.best_ask is not None and float(order.price) >= float(book.best_ask):
            fill_price = float(book.best_ask)
        elif order.side == "sell" and book.best_bid is not None and float(order.price) <= float(book.best_bid):
            fill_price = float(book.best_bid)
        elif allow_touch_fill:
            # Realism gate 2: resting orders need a minimum age before they can
            # be touch-filled. This simulates queue position — a freshly placed
            # order must wait for at least min_queue_wait_ms to represent the
            # time it takes to queue behind existing liquidity.
            age_ms = now_ms - order.placed_at_ms
            if age_ms < self._min_queue_wait_ms:
                return None

            if order.side == "buy" and book.best_bid is not None and float(order.price) >= float(book.best_bid):
                fill_price = float(order.price)
                fill_trigger = "touch"
                liquidity_mode = "maker" if self._fee_mode == "maker" else "simulated_touch"
            elif order.side == "sell" and book.best_ask is not None and float(order.price) <= float(book.best_ask):
                fill_price = float(order.price)
                fill_trigger = "touch"
                liquidity_mode = "maker" if self._fee_mode == "maker" else "simulated_touch"

        if fill_price is None:
            return None

        # Realism gate 3: partial fill based on visible depth and queue position.
        # We model being behind queue_depth_fraction of the displayed size at the
        # fill level. Our effective available size = level_size * (1 - queue_fraction).
        # Cap the fill to that amount.
        fill_size = float(order.size)
        if fill_trigger in ("touch", "cross"):
            level_size = _level_size_at_price(book, side=order.side, price=fill_price)
            if level_size is not None and level_size > 0.0:
                available = level_size * (1.0 - self._queue_depth_fraction)
                fill_size = min(fill_size, max(0.0, available))
        if fill_size <= 0.0:
            return None

        prior_position = self._positions.get_position(order.token_id)
        closed_qty = 0.0
        realized_gross_pnl = 0.0
        if order.side == "sell" and prior_position.size > 0:
            closed_qty = min(fill_size, float(prior_position.size))
            realized_gross_pnl = (float(fill_price) - float(prior_position.avg_price)) * closed_qty
        gross_notional = fill_size * float(fill_price)
        # Maker fills (touch) get zero fees on Polymarket; taker fills pay fee_bps
        if liquidity_mode == "maker" or (fill_trigger == "touch" and self._fee_mode != "always"):
            fee_usdc = 0.0
        else:
            fee_usdc = gross_notional * self._fee_bps / 10_000.0
        realized_net_pnl = realized_gross_pnl - fee_usdc
        updated_position = self._positions.apply_fill(token_id=order.token_id, side=order.side, size=fill_size, price=fill_price)
        fill_ts_ms = _now_ms()

        # Mark the order as partially or fully filled
        is_partial = fill_size < float(order.size) - 1e-9
        new_status = "partial" if is_partial else "filled"

        fill = {
            "order_id": order.order_id,
            "token_id": order.token_id,
            "side": order.side,
            "size": fill_size,
            "price": fill_price,
            "ts_ms": fill_ts_ms,
            "placed_at_ms": order.placed_at_ms,
            "client_order_id": order.client_order_id,
            "quote_group_id": order.quote_group_id,
            "gross_notional": gross_notional,
            "fee_bps": self._fee_bps,
            "fee_usdc": fee_usdc,
            "net_notional": gross_notional - fee_usdc,
            "liquidity_mode": liquidity_mode,
            "fill_trigger": fill_trigger,
            "realized_gross_pnl_delta": realized_gross_pnl,
            "realized_net_pnl_delta": realized_net_pnl,
            "is_partial": is_partial,
            "inventory_after_fill": {
                "size": updated_position.size,
                "avg_price": updated_position.avg_price,
            },
            "placement_metadata": dict(order.metadata or {}),
        }
        self._stats["realized_gross_pnl"] += realized_gross_pnl
        self._stats["realized_net_pnl"] += realized_net_pnl
        self._stats["cumulative_fees"] += fee_usdc
        self._stats["turnover"] += gross_notional
        if realized_net_pnl > 0:
            self._stats["win_count"] += 1
        elif realized_net_pnl < 0:
            self._stats["loss_count"] += 1
        # Bankroll tracking
        if order.side == "buy":
            self._bankroll_spent += gross_notional + fee_usdc
        else:
            self._bankroll_received += gross_notional - fee_usdc
        self._fills.append(fill)
        self._orders[order.order_id] = PaperOrder(**{**order.__dict__, "status": new_status})
        # Record pending markout for fill-quality measurement
        mid_at_fill = float(book.mid_price) if book.mid_price is not None else fill_price
        self._pending_markouts.append({
            "token_id": order.token_id,
            "side": order.side,
            "mid_at_fill": mid_at_fill,
            "fill_ts_ms": fill_ts_ms,
            "markout_1s_bps": None,
            "markout_5s_bps": None,
            "markout_30s_bps": None,
        })
        # FIFO duration tracking
        if order.side == "buy":
            self._fifo_entries[order.token_id].append([float(fill_ts_ms), float(fill_size)])
        elif order.side == "sell":
            self._consume_fifo(order.token_id, float(fill_size), fill_ts_ms)
        return fill


def _level_size_at_price(book: Any, *, side: str, price: float) -> Optional[float]:
    """Return the displayed size at the given price level on the given side."""
    if side == "buy":
        # For a buy fill, we cross the ask side — find size at that ask price
        for level_price, level_size in book.asks:
            if abs(float(level_price) - float(price)) < 1e-7:
                return float(level_size)
    else:
        # For a sell fill, we cross the bid side — find size at that bid price
        for level_price, level_size in book.bids:
            if abs(float(level_price) - float(price)) < 1e-7:
                return float(level_size)
    return None


def _now_ms() -> int:
    return int(time.time() * 1000)
