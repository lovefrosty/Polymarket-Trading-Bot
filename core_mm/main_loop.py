from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from core_mm.book_manager import BookManager
from core_mm.book_metrics import BookDiagnostic, MeaningfulBBO, classify_book_state, find_meaningful_bbo
from core_mm.execution import ExecutionAdapter, ExecutionResult
from core_mm.flow_filter import FlowFilterDecision, evaluate_volume_ratio
from core_mm.order_manager import DesiredQuote, OrderAction, RestingOrder, SmartOrderManager
from core_mm.quote_engine import QuotePlan, get_order_prices
from core_mm.risk_manager import RiskDecision, RiskManager
from core_mm.sizing import SizePlan, get_buy_sell_amount


@dataclass(frozen=True)
class MarketConfig:
    market_id: str
    token_ids: Sequence[str]
    tick_size: Optional[float] = None
    min_size: float = 100.0
    fallback_size: float = 20.0
    within_pct: float = 0.02
    trade_size: float = 50.0
    max_size: float = 100.0
    reverse_position_min_size: float = 20.0
    min_order_size: float = 0.0


@dataclass(frozen=True)
class TokenState:
    token_id: str
    position: float = 0.0
    avg_cost: float = 0.0
    reverse_position: float = 0.0
    usdc_balance: Optional[float] = None
    three_hour_volatility: float = 0.0


@dataclass(frozen=True)
class TokenCycleDecision:
    token_id: str
    book_diag: BookDiagnostic
    metrics: Optional[MeaningfulBBO]
    flow_filter: Optional[FlowFilterDecision]
    quote_plan: Optional[QuotePlan]
    size_plan: Optional[SizePlan]
    risk_decision: Optional[RiskDecision]
    desired_quotes: Sequence[DesiredQuote]


@dataclass(frozen=True)
class MarketCycleResult:
    market_id: str
    token_decisions: Sequence[TokenCycleDecision]
    desired_quotes: Dict[str, DesiredQuote]
    order_actions: Sequence[OrderAction]
    execution_results: Sequence[ExecutionResult]


class TradingMainLoop:
    def __init__(
        self,
        *,
        book_manager: BookManager,
        order_manager: SmartOrderManager,
        risk_manager: RiskManager,
        execution_adapter: Optional[ExecutionAdapter] = None,
        mode: str = "OBSERVE",
    ) -> None:
        self._book_manager = book_manager
        self._order_manager = order_manager
        self._risk_manager = risk_manager
        self._execution_adapter = execution_adapter
        self._mode = str(mode).upper()
        self._market_locks: Dict[str, asyncio.Lock] = {}

    def get_market_lock(self, market_id: str) -> asyncio.Lock:
        lock = self._market_locks.get(str(market_id))
        if lock is None:
            lock = asyncio.Lock()
            self._market_locks[str(market_id)] = lock
        return lock

    async def run_market_cycle(
        self,
        *,
        market: MarketConfig,
        token_states: Sequence[TokenState],
        existing_orders: Dict[str, RestingOrder],
        now_ms: int,
    ) -> MarketCycleResult:
        async with self.get_market_lock(market.market_id):
            token_decisions: List[TokenCycleDecision] = []
            desired_quotes: Dict[str, DesiredQuote] = {}

            for token_state in token_states:
                decision = self._evaluate_token(market=market, token_state=token_state, now_ms=now_ms)
                token_decisions.append(decision)
                for quote in decision.desired_quotes:
                    desired_quotes[quote.quote_key] = quote

            order_actions = self._order_manager.plan(
                desired_quotes=desired_quotes,
                existing_orders=existing_orders,
                now_ms=now_ms,
            )
            execution_results = self._apply_actions(order_actions)
            return MarketCycleResult(
                market_id=market.market_id,
                token_decisions=tuple(token_decisions),
                desired_quotes=desired_quotes,
                order_actions=tuple(order_actions),
                execution_results=tuple(execution_results),
            )

    def _evaluate_token(self, *, market: MarketConfig, token_state: TokenState, now_ms: int) -> TokenCycleDecision:
        book = self._book_manager.get_book(token_state.token_id)
        book_diag = classify_book_state(
            book,
            min_size=market.min_size,
            fallback_size=market.fallback_size,
            now_ms=now_ms,
        )
        if book_diag.state != "book_ok" or book is None:
            return TokenCycleDecision(token_state.token_id, book_diag, None, None, None, None, None, ())

        metrics = find_meaningful_bbo(
            book.bids,
            book.asks,
            min_size=market.min_size,
            fallback_size=market.fallback_size,
            within_pct=market.within_pct,
        )
        if metrics is None:
            return TokenCycleDecision(token_state.token_id, book_diag, None, None, None, None, None, ())

        flow = evaluate_volume_ratio(
            metrics.bid_sum_within_n_percent,
            metrics.ask_sum_within_n_percent,
        )
        quote_plan = get_order_prices(
            metrics,
            avg_cost=token_state.avg_cost,
            tick_size=market.tick_size,
            min_size=metrics.min_size_used,
        )
        size_plan = get_buy_sell_amount(
            position=token_state.position,
            max_size=market.max_size,
            trade_size=market.trade_size,
            avg_price=token_state.avg_cost,
            reverse_position=token_state.reverse_position,
            reverse_position_min_size=market.reverse_position_min_size,
            min_order_size=market.min_order_size,
            usdc_balance=token_state.usdc_balance,
            buy_price=quote_plan.bid_price,
        )
        spread_bps = None
        if metrics.best_bid is not None and metrics.best_ask is not None:
            mid = (metrics.best_bid + metrics.best_ask) / 2.0
            if mid > 0:
                spread_bps = ((metrics.best_ask - metrics.best_bid) / mid) * 10000.0
        risk = self._risk_manager.evaluate(
            market_id=market.market_id,
            now_ms=now_ms,
            position_size=token_state.position,
            avg_price=token_state.avg_cost,
            current_mid=book.mid_price,
            best_bid=book.best_bid,
            best_ask=book.best_ask,
            spread_bps=spread_bps,
            three_hour_volatility=token_state.three_hour_volatility,
        )

        desired: List[DesiredQuote] = []
        if flow.allow_buy and risk.allow_buy and quote_plan.bid_price is not None and size_plan.buy_amount > 0:
            desired.append(
                DesiredQuote(
                    quote_key=f"{market.market_id}:{token_state.token_id}:buy",
                    token_id=token_state.token_id,
                    side="buy",
                    price=quote_plan.bid_price,
                    size=size_plan.buy_amount,
                    metadata={
                        "market_id": market.market_id,
                        "quote_mode": quote_plan.bid_mode,
                        "risk_action": risk.action,
                        "risk_reasons": list(risk.reasons),
                        "position": float(token_state.position),
                        "reverse_position": float(token_state.reverse_position),
                        "best_bid": metrics.best_bid,
                        "best_ask": metrics.best_ask,
                        "mid": book.mid_price,
                        "top_bid": metrics.top_bid,
                        "top_ask": metrics.top_ask,
                        "spread_bps": spread_bps,
                        "bid_depth": metrics.bid_sum_within_n_percent,
                        "ask_depth": metrics.ask_sum_within_n_percent,
                    },
                )
            )
        if flow.allow_sell and risk.allow_sell and quote_plan.ask_price is not None and size_plan.sell_amount > 0:
            desired.append(
                DesiredQuote(
                    quote_key=f"{market.market_id}:{token_state.token_id}:sell",
                    token_id=token_state.token_id,
                    side="sell",
                    price=quote_plan.ask_price,
                    size=size_plan.sell_amount,
                    metadata={
                        "market_id": market.market_id,
                        "quote_mode": quote_plan.ask_mode,
                        "risk_action": risk.action,
                        "risk_reasons": list(risk.reasons),
                        "position": float(token_state.position),
                        "reverse_position": float(token_state.reverse_position),
                        "best_bid": metrics.best_bid,
                        "best_ask": metrics.best_ask,
                        "mid": book.mid_price,
                        "top_bid": metrics.top_bid,
                        "top_ask": metrics.top_ask,
                        "spread_bps": spread_bps,
                        "bid_depth": metrics.bid_sum_within_n_percent,
                        "ask_depth": metrics.ask_sum_within_n_percent,
                    },
                )
            )

        return TokenCycleDecision(
            token_id=token_state.token_id,
            book_diag=book_diag,
            metrics=metrics,
            flow_filter=flow,
            quote_plan=quote_plan,
            size_plan=size_plan,
            risk_decision=risk,
            desired_quotes=tuple(desired),
        )

    def _apply_actions(self, order_actions: Sequence[OrderAction]) -> List[ExecutionResult]:
        if self._mode == "OBSERVE" or self._execution_adapter is None:
            return []
        results: List[ExecutionResult] = []
        for action in order_actions:
            if action.action == "NOOP":
                continue
            if action.action == "CANCEL" and action.existing_order_id:
                results.append(self._execution_adapter.cancel_order(action.existing_order_id))
                continue
            if action.action == "PLACE" and action.desired_quote is not None:
                dq = action.desired_quote
                results.append(
                        self._execution_adapter.place_order(
                            token_id=dq.token_id,
                            side=dq.side,
                            price=dq.price,
                            size=dq.size,
                            client_order_id=dq.quote_key,
                            quote_group_id=dq.metadata.get("market_id") if isinstance(dq.metadata, dict) else None,
                            metadata=dq.metadata,
                        )
                    )
                continue
            if action.action == "CANCEL_AND_REPLACE":
                if action.existing_order_id:
                    results.append(self._execution_adapter.cancel_order(action.existing_order_id))
                if action.desired_quote is not None:
                    dq = action.desired_quote
                    results.append(
                        self._execution_adapter.place_order(
                            token_id=dq.token_id,
                            side=dq.side,
                            price=dq.price,
                            size=dq.size,
                            client_order_id=dq.quote_key,
                            quote_group_id=dq.metadata.get("market_id") if isinstance(dq.metadata, dict) else None,
                            metadata=dq.metadata,
                        )
                    )
        return results
