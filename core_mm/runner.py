from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence

from core_mm.book_manager import BookManager
from core_mm.book_metrics import classify_book_state
from core_mm.execution import ExecutionAdapter
from core_mm.main_loop import MarketConfig, MarketCycleResult, TokenState, TradingMainLoop
from core_mm.market_selector import MarketCandidate, MarketSelector
from core_mm.order_manager import RestingOrder, SmartOrderManager
from core_mm.paper_broker import PaperBroker
from core_mm.positions import PositionTracker
from core_mm.risk_manager import RiskManager
from core_mm.user_feed import UserFeedState


@dataclass(frozen=True)
class RunnerStatus:
    mode: str
    market_id: Optional[str]
    token_ids: tuple[str, ...]
    has_books: bool
    book_diag: Dict[str, Any]


class CoreMMRunner:
    def __init__(
        self,
        *,
        market_selector: MarketSelector,
        book_manager: Optional[BookManager] = None,
        position_tracker: Optional[PositionTracker] = None,
        user_feed: Optional[UserFeedState] = None,
        order_manager: Optional[SmartOrderManager] = None,
        risk_manager: Optional[RiskManager] = None,
        broker: Optional[Any] = None,
        mode: str = "OBSERVE",
        min_size: float = 100.0,
        fallback_size: float = 20.0,
        within_pct: float = 0.02,
        trade_size: float = 50.0,
        max_size: float = 100.0,
        reverse_position_min_size: float = 20.0,
        min_order_size_override: Optional[float] = None,
        fee_bps: float = 25.0,
        fee_mode: str = "taker",
        market_dwell_ms: int = 0,
    ) -> None:
        self.mode = str(mode).upper()
        self.market_selector = market_selector
        self.book_manager = book_manager or BookManager()
        self.position_tracker = position_tracker or PositionTracker()
        self.user_feed = user_feed or UserFeedState(position_tracker=self.position_tracker)
        self.order_manager = order_manager or SmartOrderManager()
        self.risk_manager = risk_manager or RiskManager()
        self.broker = broker or (
            PaperBroker(
                book_manager=self.book_manager,
                position_tracker=self.position_tracker,
                fee_bps=fee_bps,
                fee_mode=fee_mode,
            )
            if self.mode == "PAPER"
            else None
        )
        self.main_loop = TradingMainLoop(
            book_manager=self.book_manager,
            order_manager=self.order_manager,
            risk_manager=self.risk_manager,
            execution_adapter=self.broker if self.mode != "OBSERVE" else None,
            mode=self.mode,
        )
        self.current_market: Optional[MarketCandidate] = None
        self._min_size = float(min_size)
        self._fallback_size = float(fallback_size)
        self._within_pct = float(within_pct)
        self._trade_size = float(trade_size)
        self._max_size = float(max_size)
        self._reverse_position_min_size = float(reverse_position_min_size)
        self._min_order_size_override = min_order_size_override
        self._market_dwell_ms = max(0, int(market_dwell_ms))
        self._market_selected_at_ms: Optional[int] = None

    def refresh_market_selection(
        self,
        events: Optional[Iterable[Dict[str, Any]]] = None,
        *,
        now_ms: Optional[int] = None,
    ) -> bool:
        active_now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        candidates = (
            self.market_selector.select_from_events(events, now_ts=int(active_now_ms / 1000))
            if events is not None
            else self.market_selector.select_markets(now_ts=int(active_now_ms / 1000))
        )
        if not candidates:
            changed = self.current_market is not None
            self.current_market = None
            if changed:
                self._market_selected_at_ms = None
            return changed
        selected = candidates[0]
        if self.current_market is not None:
            current_match = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.condition_id == self.current_market.condition_id
                ),
                None,
            )
            if current_match is not None:
                if (
                    selected.condition_id != self.current_market.condition_id
                    and self._market_selected_at_ms is not None
                    and self._market_dwell_ms > 0
                    and active_now_ms < (self._market_selected_at_ms + self._market_dwell_ms)
                ):
                    selected = current_match
                elif selected.condition_id == self.current_market.condition_id:
                    selected = current_match
        changed = self.current_market is None or self.current_market.condition_id != selected.condition_id
        self.current_market = selected
        if changed or self._market_selected_at_ms is None:
            self._market_selected_at_ms = active_now_ms
        return changed

    def on_market_message(self, message: Dict[str, Any], recv_wall_ms: Optional[int] = None) -> int:
        applied = self.book_manager.process_message(message, recv_wall_ms=recv_wall_ms)
        if applied > 0 and isinstance(self.broker, PaperBroker):
            self.broker.sweep_fills()
        return applied

    def on_user_message(self, message: Dict[str, Any]) -> Sequence[Any]:
        return self.user_feed.apply_message(message)

    async def run_cycle(
        self,
        *,
        now_ms: int,
        usdc_balance: Optional[float] = None,
        three_hour_volatility: float = 0.0,
    ) -> Optional[MarketCycleResult]:
        if self.current_market is None:
            return None
        market = MarketConfig(
            market_id=self.current_market.slug,
            token_ids=self.current_market.token_ids,
            tick_size=self.current_market.tick_size,
            min_size=self._min_size,
            fallback_size=self._fallback_size,
            within_pct=self._within_pct,
            trade_size=self._trade_size,
            max_size=self._max_size,
            reverse_position_min_size=self._reverse_position_min_size,
            min_order_size=(
                float(self._min_order_size_override)
                if self._min_order_size_override is not None
                else float(self.current_market.min_incentive_size or 0.0)
            ),
        )
        token_states = []
        token_ids = list(self.current_market.token_ids)
        for idx, token_id in enumerate(token_ids):
            reverse_id = token_ids[1 - idx] if len(token_ids) == 2 else None
            position = self.position_tracker.get_position(token_id)
            reverse_position = self.position_tracker.get_position(reverse_id).size if reverse_id else 0.0
            token_states.append(
                TokenState(
                    token_id=token_id,
                    position=position.size,
                    avg_cost=position.avg_price,
                    reverse_position=reverse_position,
                    usdc_balance=usdc_balance,
                    three_hour_volatility=three_hour_volatility,
                )
            )
        existing_orders = self._existing_orders_by_quote_key(market.market_id)
        return await self.main_loop.run_market_cycle(
            market=market,
            token_states=tuple(token_states),
            existing_orders=existing_orders,
            now_ms=now_ms,
        )

    def status(self) -> RunnerStatus:
        token_ids = tuple(self.current_market.token_ids) if self.current_market is not None else ()
        token_diag: Dict[str, Dict[str, Any]] = {}
        blocking_state_counts: Dict[str, int] = {}
        tokens_ok = 0
        for token_id in token_ids:
            diag = classify_book_state(
                self.book_manager.get_book(token_id),
                min_size=self._min_size,
                fallback_size=self._fallback_size,
            )
            token_diag[str(token_id)] = diag.as_dict()
            if diag.state == "book_ok":
                tokens_ok += 1
            else:
                blocking_state_counts[diag.state] = int(blocking_state_counts.get(diag.state, 0)) + 1
        has_books = bool(token_ids) and all(
            token_diag[token_id]["state"] not in {"book_absent", "book_empty"} for token_id in token_diag
        )
        return RunnerStatus(
            mode=self.mode,
            market_id=(self.current_market.slug if self.current_market is not None else None),
            token_ids=token_ids,
            has_books=has_books,
            book_diag={
                "per_token": token_diag,
                "tokens_ok": int(tokens_ok),
                "tokens_blocked": max(0, len(token_diag) - int(tokens_ok)),
                "blocking_state_counts": blocking_state_counts,
            },
        )

    def _existing_orders_by_quote_key(self, market_id: str) -> Dict[str, RestingOrder]:
        if self.broker is None or not hasattr(self.broker, "get_open_orders"):
            return {}
        snapshot = self.broker.get_open_orders()
        if not snapshot.success:
            return {}
        orders = snapshot.payload.get("orders") or []
        existing: Dict[str, RestingOrder] = {}
        for order in orders:
            if not isinstance(order, dict):
                continue
            token_id = str(order.get("token_id") or "")
            side = str(order.get("side") or "")
            order_id = str(order.get("order_id") or order.get("orderID") or "")
            if not token_id or not side or not order_id:
                continue
            quote_key = f"{market_id}:{token_id}:{side}"
            existing[quote_key] = RestingOrder(
                quote_key=quote_key,
                order_id=order_id,
                token_id=token_id,
                side=side,
                price=float(order.get("price") or 0.0),
                size=float(order.get("size") or 0.0),
                placed_at_ms=int(order.get("placed_at_ms") or order.get("placedAtMs") or 0),
            )
        return existing
