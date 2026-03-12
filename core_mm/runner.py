from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence

from core_mm.book_manager import BookManager
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
    ) -> None:
        self.mode = str(mode).upper()
        self.market_selector = market_selector
        self.book_manager = book_manager or BookManager()
        self.position_tracker = position_tracker or PositionTracker()
        self.user_feed = user_feed or UserFeedState(position_tracker=self.position_tracker)
        self.order_manager = order_manager or SmartOrderManager()
        self.risk_manager = risk_manager or RiskManager()
        self.broker = broker or (PaperBroker(book_manager=self.book_manager, position_tracker=self.position_tracker) if self.mode == "PAPER" else None)
        self.main_loop = TradingMainLoop(
            book_manager=self.book_manager,
            order_manager=self.order_manager,
            risk_manager=self.risk_manager,
            execution_adapter=self.broker if self.mode != "OBSERVE" else None,
            mode=self.mode,
        )
        self.current_market: Optional[MarketCandidate] = None

    def refresh_market_selection(self, events: Optional[Iterable[Dict[str, Any]]] = None) -> bool:
        candidates = self.market_selector.select_from_events(events) if events is not None else self.market_selector.select_markets()
        if not candidates:
            return False
        selected = candidates[0]
        changed = self.current_market is None or self.current_market.condition_id != selected.condition_id
        self.current_market = selected
        return changed

    def on_market_message(self, message: Dict[str, Any], recv_wall_ms: Optional[int] = None) -> int:
        return self.book_manager.process_message(message, recv_wall_ms=recv_wall_ms)

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
            min_order_size=float(self.current_market.min_incentive_size or 0.0),
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
        has_books = all(self.book_manager.get_book(token_id) is not None for token_id in token_ids)
        return RunnerStatus(
            mode=self.mode,
            market_id=(self.current_market.slug if self.current_market is not None else None),
            token_ids=token_ids,
            has_books=has_books,
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
