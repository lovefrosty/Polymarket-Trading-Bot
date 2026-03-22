from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from core_mm.alpha_overlay import AlphaOverlayManager, AlphaSignal
from core_mm.book_manager import BookManager
from core_mm.book_metrics import BookDiagnostic, MeaningfulBBO, classify_book_state, find_meaningful_bbo
from core_mm.execution import ExecutionAdapter, ExecutionResult
from core_mm.flow_filter import FlowFilter, FlowFilterDecision, evaluate_volume_ratio
from core_mm.order_manager import DesiredQuote, OrderAction, RestingOrder, SmartOrderManager
from core_mm.quote_engine import QuotePlan, compute_inventory_skew_ticks, get_order_prices
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
    # Staleness gate: refuse to quote if the book hasn't updated within this window.
    # 0 = disabled (original behaviour).
    stale_book_gate_ms: int = 5_000
    # Inventory skew: max number of ticks to shift quote center toward neutrality.
    # 0 = disabled (no skew). Recommended default: 3.
    max_skew_ticks: int = 3
    # Inventory skew factor for sizing: 1.0 = linear scale from full buy at
    # flat to zero buy at max_size. 0.0 = disabled.
    inventory_skew_factor: float = 1.0
    # Kelly criterion sizing: fraction of Kelly to use. 0.0 = disabled (fixed trade_size).
    kelly_fraction: float = 0.0


@dataclass(frozen=True)
class TokenState:
    token_id: str
    position: float = 0.0
    avg_cost: float = 0.0
    reverse_position: float = 0.0
    usdc_balance: Optional[float] = None
    three_hour_volatility: float = 0.0
    net_position: float = 0.0


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
    alpha_signal: Optional[AlphaSignal] = None


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
        flow_filter_ewma_span: int = 10,
    ) -> None:
        self._book_manager = book_manager
        self._order_manager = order_manager
        self._risk_manager = risk_manager
        self._execution_adapter = execution_adapter
        self._mode = str(mode).upper()
        self._market_locks: Dict[str, asyncio.Lock] = {}
        self._flow_filter_ewma_span = int(flow_filter_ewma_span)
        self._flow_filters: Dict[str, FlowFilter] = {}
        self._alpha_overlays: Dict[str, AlphaOverlayManager] = {}
        self._emergency_cancel_count: int = 0
        self._rush_fill_count: int = 0

    @property
    def flow_stats(self) -> Dict[str, Any]:
        total_emergency = sum(
            ff.total_emergency_triggers for ff in self._flow_filters.values()
        )
        return {
            "emergency_cancel_count": self._emergency_cancel_count,
            "rush_fill_count": self._rush_fill_count,
            "total_emergency_triggers": total_emergency,
        }

    def get_market_lock(self, market_id: str) -> asyncio.Lock:
        lock = self._market_locks.get(str(market_id))
        if lock is None:
            lock = asyncio.Lock()
            self._market_locks[str(market_id)] = lock
        return lock

    def _get_flow_filter(self, token_id: str) -> FlowFilter:
        if token_id not in self._flow_filters:
            self._flow_filters[token_id] = FlowFilter(ewma_span=self._flow_filter_ewma_span)
        return self._flow_filters[token_id]

    def _get_alpha_overlay(self, token_id: str) -> AlphaOverlayManager:
        if token_id not in self._alpha_overlays:
            self._alpha_overlays[token_id] = AlphaOverlayManager()
        return self._alpha_overlays[token_id]

    def record_fill_for_alpha(self, token_id: str, side: str, price: float, mid_at_fill: float) -> None:
        """Feed a fill into the alpha overlay's fill asymmetry tracker."""
        overlay = self._get_alpha_overlay(token_id)
        overlay.record_fill(side, price, mid_at_fill)

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

        # Graduated staleness gate:
        #   age > stale_gate_ms           → STALE: stop quoting entirely
        #   age > 0.6 * stale_gate_ms     → CAUTION: widen spread 2x
        #   otherwise                     → FRESH: normal
        spread_multiplier = 1.0
        if market.stale_book_gate_ms > 0 and book.last_update_ms is not None:
            age_ms = now_ms - book.last_update_ms
            stale_caution_ms = int(market.stale_book_gate_ms * 0.6)
            if age_ms > market.stale_book_gate_ms:
                stale_diag = BookDiagnostic(
                    state="book_stale",
                    bid_levels=book_diag.bid_levels,
                    ask_levels=book_diag.ask_levels,
                    best_bid=book_diag.best_bid,
                    best_ask=book_diag.best_ask,
                    best_bid_size=book_diag.best_bid_size,
                    best_ask_size=book_diag.best_ask_size,
                    min_size=book_diag.min_size,
                    fallback_size=book_diag.fallback_size,
                    last_update_ms=book.last_update_ms,
                    book_age_ms=age_ms,
                )
                return TokenCycleDecision(token_state.token_id, stale_diag, None, None, None, None, None, ())
            elif age_ms > stale_caution_ms:
                book_diag = BookDiagnostic(
                    state="book_caution",
                    bid_levels=book_diag.bid_levels,
                    ask_levels=book_diag.ask_levels,
                    best_bid=book_diag.best_bid,
                    best_ask=book_diag.best_ask,
                    best_bid_size=book_diag.best_bid_size,
                    best_ask_size=book_diag.best_ask_size,
                    min_size=book_diag.min_size,
                    fallback_size=book_diag.fallback_size,
                    last_update_ms=book.last_update_ms,
                    book_age_ms=age_ms,
                )
                spread_multiplier = 2.0

        metrics = find_meaningful_bbo(
            book.bids,
            book.asks,
            min_size=market.min_size,
            fallback_size=market.fallback_size,
            within_pct=market.within_pct,
        )
        if metrics is None:
            return TokenCycleDecision(token_state.token_id, book_diag, None, None, None, None, None, ())

        flow_obj = self._get_flow_filter(token_state.token_id)
        flow = flow_obj.update(
            metrics.bid_sum_within_n_percent,
            metrics.ask_sum_within_n_percent,
        )

        # Emergency cancel/cooldown: fire only on large delta that lands in an
        # extreme regime.  threshold_bps=5000 requires a 50% swing across the
        # ±10000 bps scale; min_magnitude_bps=3000 means we don't cancel when
        # flow is simply reverting to neutral (harmless).  cooldown_cycles=2
        # (≈1s at current cycle rate) is enough to flush stale orders.
        emergency_cancel = flow_obj.check_reversal(
            threshold_bps=5000.0, cooldown_cycles=2, min_magnitude_bps=3000.0
        )
        if emergency_cancel or flow_obj.in_emergency_cooldown:
            self._emergency_cancel_count += 1
            return TokenCycleDecision(
                token_state.token_id, book_diag, metrics, flow, None, None, None, (),
            )

        # Alpha overlay: book imbalance, fill asymmetry, volatility regime.
        alpha_mgr = self._get_alpha_overlay(token_state.token_id)
        alpha_mgr.update_book(
            metrics.bid_sum_within_n_percent,
            metrics.ask_sum_within_n_percent,
        )
        mid_price = float(book.mid_price) if book.mid_price is not None else 0.0
        if mid_price > 0:
            alpha_mgr.update_mid(mid_price)
        alpha_signal = alpha_mgr.get_signal()

        # Compute inventory skew: number of ticks to shift the quote center
        # toward neutrality.  Positive → long → shift prices down.
        inventory_skew_ticks = compute_inventory_skew_ticks(
            position=token_state.net_position,
            max_size=market.max_size,
            max_skew_ticks=market.max_skew_ticks,
            avg_cost=token_state.avg_cost,
            mid_price=mid_price,
        )
        # Apply alpha overlay: add extra skew from book imbalance signal.
        inventory_skew_ticks += alpha_signal.extra_skew_ticks

        # Apply alpha overlay spread multiplier (fill asymmetry + vol regime).
        effective_spread_mult = spread_multiplier * alpha_signal.spread_multiplier

        quote_plan = get_order_prices(
            metrics,
            avg_cost=token_state.avg_cost,
            tick_size=market.tick_size,
            min_size=metrics.min_size_used,
            inventory_skew_ticks=inventory_skew_ticks,
            spread_multiplier=effective_spread_mult,
        )
        # Rush fill: tighten quote by 1 tick when flow favors inventory reduction
        rush_fill = False
        if abs(token_state.net_position) > market.max_size * 0.5:
            tick = quote_plan.tick_size
            if token_state.net_position > 0 and flow.ewma_imbalance_bps > 3000:
                # Long + buy pressure → tighten ask to sell faster
                if quote_plan.ask_price is not None:
                    new_ask = max(0.01, quote_plan.ask_price - tick)
                    if quote_plan.bid_price is None or new_ask > quote_plan.bid_price:
                        quote_plan = QuotePlan(
                            bid_price=quote_plan.bid_price, ask_price=new_ask,
                            bid_mode=quote_plan.bid_mode, ask_mode=f"{quote_plan.ask_mode}_rush",
                            tick_size=tick,
                        )
                        rush_fill = True
                        self._rush_fill_count += 1
            elif token_state.net_position < 0 and flow.ewma_imbalance_bps < -3000:
                # Short + sell pressure → tighten bid to buy faster
                if quote_plan.bid_price is not None:
                    new_bid = min(0.99, quote_plan.bid_price + tick)
                    if quote_plan.ask_price is None or new_bid < quote_plan.ask_price:
                        quote_plan = QuotePlan(
                            bid_price=new_bid, ask_price=quote_plan.ask_price,
                            bid_mode=f"{quote_plan.bid_mode}_rush", ask_mode=quote_plan.ask_mode,
                            tick_size=tick,
                        )
                        rush_fill = True
                        self._rush_fill_count += 1

        # Compute p_fair for Kelly criterion sizing: mid_price + imbalance tilt
        p_fair: Optional[float] = None
        if market.kelly_fraction > 0.0 and mid_price > 0.0:
            tilt = alpha_signal.imbalance_alpha_bps / 10000.0
            p_fair = max(0.01, min(0.99, mid_price + tilt))

        size_plan = get_buy_sell_amount(
            position=token_state.position,
            max_size=market.max_size,
            trade_size=market.trade_size,
            avg_price=token_state.avg_cost,
            reverse_position=token_state.reverse_position,
            reverse_position_min_size=market.reverse_position_min_size,
            net_position=token_state.net_position,
            min_order_size=market.min_order_size,
            usdc_balance=token_state.usdc_balance,
            buy_price=quote_plan.bid_price,
            sell_price=quote_plan.ask_price,
            inventory_skew_factor=market.inventory_skew_factor,
            p_fair=p_fair,
            kelly_fraction=market.kelly_fraction,
            bankroll=token_state.usdc_balance,
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
                        "net_position": float(token_state.net_position),
                        "reverse_position": float(token_state.reverse_position),
                        "inventory_skew_ticks": inventory_skew_ticks,
                        "best_bid": metrics.best_bid,
                        "best_ask": metrics.best_ask,
                        "mid": book.mid_price,
                        "top_bid": metrics.top_bid,
                        "top_ask": metrics.top_ask,
                        "spread_bps": spread_bps,
                        "bid_depth": metrics.bid_sum_within_n_percent,
                        "ask_depth": metrics.ask_sum_within_n_percent,
                        "book_state": book_diag.state,
                        "spread_multiplier": effective_spread_mult,
                        "imbalance_bps": flow.imbalance_bps,
                        "ewma_imbalance_bps": flow.ewma_imbalance_bps,
                        "rush_fill": rush_fill,
                        "alpha_extra_skew": alpha_signal.extra_skew_ticks,
                        "alpha_spread_mult": alpha_signal.spread_multiplier,
                        "alpha_vol_regime": alpha_signal.vol_regime,
                        "alpha_adversity": alpha_signal.fill_adversity_ratio,
                        "alpha_complement_bps": alpha_signal.complement_skew_bps,
                        "alpha_depth_change": alpha_signal.depth_change_signal,
                        "p_fair": p_fair,
                        "kelly_fraction": market.kelly_fraction,
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
                        "net_position": float(token_state.net_position),
                        "reverse_position": float(token_state.reverse_position),
                        "inventory_skew_ticks": inventory_skew_ticks,
                        "best_bid": metrics.best_bid,
                        "best_ask": metrics.best_ask,
                        "mid": book.mid_price,
                        "top_bid": metrics.top_bid,
                        "top_ask": metrics.top_ask,
                        "spread_bps": spread_bps,
                        "bid_depth": metrics.bid_sum_within_n_percent,
                        "ask_depth": metrics.ask_sum_within_n_percent,
                        "book_state": book_diag.state,
                        "spread_multiplier": effective_spread_mult,
                        "imbalance_bps": flow.imbalance_bps,
                        "ewma_imbalance_bps": flow.ewma_imbalance_bps,
                        "rush_fill": rush_fill,
                        "alpha_extra_skew": alpha_signal.extra_skew_ticks,
                        "alpha_spread_mult": alpha_signal.spread_multiplier,
                        "alpha_vol_regime": alpha_signal.vol_regime,
                        "alpha_adversity": alpha_signal.fill_adversity_ratio,
                        "alpha_complement_bps": alpha_signal.complement_skew_bps,
                        "alpha_depth_change": alpha_signal.depth_change_signal,
                        "p_fair": p_fair,
                        "kelly_fraction": market.kelly_fraction,
                        "alpha_depth_change": alpha_signal.depth_change_signal,
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
            alpha_signal=alpha_signal,
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
