from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from core_mm.adverse_selection import evaluate_tail_adverse_selection
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
    event_id: Optional[str] = None
    exchange: Optional[str] = None
    fee_model_exchange: Optional[str] = None
    fee_type: Optional[str] = None
    fee_multiplier: Optional[float] = None
    tick_size: Optional[float] = None
    min_size: float = 100.0
    fallback_size: float = 20.0
    within_pct: float = 0.02
    trade_size: float = 50.0
    max_size: float = 100.0
    min_order_size: float = 0.0
    # Boundary guard: when a token is at the far tails, do not open new risk.
    # Buys that reduce reverse-token exposure remain allowed; sells remain
    # allowed so inventory can still be exited.
    boundary_no_new_risk_min_price: float = 0.10
    boundary_no_new_risk_max_price: float = 0.90
    boundary_guard_mode: str = "adaptive"
    boundary_adverse_selection_threshold: float = 0.50
    boundary_exit_cost_multiplier: float = 1.25
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
    end_ts_ms: Optional[int] = None
    market_duration_ms: Optional[int] = None
    stop_open_before_expiry_ms: int = 0
    force_flat_before_expiry_ms: int = 0
    stale_position_after_ms: int = 0
    manual_force_flat: bool = False
    base_spread_multiplier: float = 1.0


@dataclass(frozen=True)
class TokenState:
    token_id: str
    position: float = 0.0
    avg_cost: float = 0.0
    reverse_position: float = 0.0
    usdc_balance: Optional[float] = None
    three_hour_volatility: float = 0.0
    net_position: float = 0.0
    current_equity: Optional[float] = None
    reference_equity: Optional[float] = None
    market_position_notional: float = 0.0
    event_position_notional: float = 0.0
    market_unrealized_pnl: float = 0.0
    event_unrealized_pnl: float = 0.0
    portfolio_total_pnl: float = 0.0
    hedge_action: str = "NONE"
    hedge_cluster_id: Optional[str] = None
    control_state: str = "NORMAL"
    hedge_action_reason: Optional[str] = None
    hedge_market_id: Optional[str] = None
    hedge_target_token_id: Optional[str] = None
    hedge_target_side: Optional[str] = None
    hedge_preferred_side: Optional[str] = None
    hedge_ratio: Optional[float] = None
    hedge_extra_skew_ticks: int = 0
    hedge_block_buy: bool = False
    hedge_block_sell: bool = False
    hedge_reduce_only: bool = False
    hedge_quality_score: Optional[float] = None
    hedge_execution_quality_score: Optional[float] = None
    hedge_covariance: Optional[float] = None
    hedge_correlation: Optional[float] = None
    hedge_beta_raw: Optional[float] = None
    hedge_beta: Optional[float] = None
    hedge_beta_shrunk: Optional[float] = None
    hedge_beta_clipped: Optional[float] = None
    hedge_covariance_sample_count: Optional[int] = None
    hedge_covariance_state: Optional[str] = None
    hedge_covariance_confidence: Optional[str] = None
    hedge_pair_score: Optional[float] = None
    hedgeability_tier: Optional[str] = None
    hedge_structural_score: Optional[float] = None
    hedge_covariance_score: Optional[float] = None
    hedge_beta_stability_score: Optional[float] = None
    hedge_execution_availability_score: Optional[float] = None
    hedge_realized_outcome_score: Optional[float] = None
    hedge_relation_confidence_state: Optional[str] = None
    hedge_permission_state: Optional[str] = None
    hedge_rejection_reason: Optional[str] = None
    hedge_model_state: Optional[str] = None
    hedge_realized_improvement_state: Optional[str] = None
    hedge_success_window_ms: Optional[int] = None
    hedge_failed_cooldown_until_ms: Optional[int] = None
    hedge_rejection_reasons: Sequence[str] = ()


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
        post_fill_reentry_cooldown_ms: int = 0,
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
        self._post_fill_reentry_cooldown_ms = max(0, int(post_fill_reentry_cooldown_ms))
        self._buy_reentry_block_until: Dict[str, int] = {}

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
        """Backward-compatible alias for record_fill()."""
        self.record_fill(token_id=token_id, side=side, price=price, mid_at_fill=mid_at_fill)

    def record_fill(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        mid_at_fill: float,
        ts_ms: Optional[int] = None,
        cooldown_ms: Optional[int] = None,
    ) -> None:
        """Feed a fill into alpha and arm buy re-entry cooldowns after exits."""
        overlay = self._get_alpha_overlay(token_id)
        overlay.record_fill(side, price, mid_at_fill)
        if hasattr(self._risk_manager, "record_fill"):
            self._risk_manager.record_fill(token_id=token_id, side=side, ts_ms=ts_ms)
        active_cooldown_ms = self._post_fill_reentry_cooldown_ms
        if cooldown_ms is not None:
            active_cooldown_ms = max(0, int(cooldown_ms))
        if active_cooldown_ms <= 0:
            return
        if str(side).lower() != "sell":
            return
        active_ts_ms = int(ts_ms or 0)
        self._buy_reentry_block_until[str(token_id)] = active_ts_ms + active_cooldown_ms

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
        block_until_ms = int(self._buy_reentry_block_until.get(str(token_state.token_id), 0) or 0)
        buy_reentry_blocked = block_until_ms > int(now_ms)
        if block_until_ms and not buy_reentry_blocked:
            self._buy_reentry_block_until.pop(str(token_state.token_id), None)

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
        inventory_skew_ticks += int(token_state.hedge_extra_skew_ticks or 0)
        # Apply alpha overlay: add extra skew from book imbalance signal.
        inventory_skew_ticks += alpha_signal.extra_skew_ticks

        # Apply alpha overlay spread multiplier (fill asymmetry + vol regime).
        effective_spread_mult = spread_multiplier * alpha_signal.spread_multiplier * max(0.1, float(market.base_spread_multiplier or 1.0))

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

        inferred_reference_equity = max(
            0.0,
            float(token_state.reference_equity or token_state.current_equity or token_state.usdc_balance or 0.0),
        )
        risk_per_trade_budget = (
            inferred_reference_equity * float(self._risk_manager.config.per_trade_loss_pct)
            if bool(self._risk_manager.config.risk_based_share_sizing) and inferred_reference_equity > 0.0
            else None
        )
        size_plan = get_buy_sell_amount(
            position=token_state.position,
            max_size=market.max_size,
            trade_size=market.trade_size,
            avg_price=token_state.avg_cost,
            reverse_position=token_state.reverse_position,
            net_position=token_state.net_position,
            min_order_size=market.min_order_size,
            usdc_balance=token_state.usdc_balance,
            buy_price=quote_plan.bid_price,
            sell_price=quote_plan.ask_price,
            inventory_skew_factor=market.inventory_skew_factor,
            p_fair=p_fair,
            kelly_fraction=market.kelly_fraction,
            bankroll=token_state.reference_equity,
            risk_per_trade_budget=risk_per_trade_budget,
            risk_based_share_sizing=bool(self._risk_manager.config.risk_based_share_sizing),
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
            token_id=token_state.token_id,
            event_id=market.event_id,
            current_equity=token_state.current_equity,
            reference_equity=token_state.reference_equity,
            planned_buy_price=quote_plan.bid_price,
            market_position_notional=token_state.market_position_notional,
            event_position_notional=token_state.event_position_notional,
            market_unrealized_pnl=token_state.market_unrealized_pnl,
            event_unrealized_pnl=token_state.event_unrealized_pnl,
            portfolio_total_pnl=token_state.portfolio_total_pnl,
            market_duration_ms=market.market_duration_ms,
            time_to_expiry_ms=(
                0
                if market.manual_force_flat
                else (
                    max(0, int(market.end_ts_ms) - int(now_ms))
                    if market.end_ts_ms is not None
                    else None
                )
            ),
        )

        boundary_decision = evaluate_tail_adverse_selection(
            mode=market.boundary_guard_mode,
            mid_price=mid_price,
            quote_bid_price=quote_plan.bid_price,
            best_bid=metrics.best_bid,
            best_ask=metrics.best_ask,
            bid_depth=metrics.bid_sum_within_n_percent,
            ask_depth=metrics.ask_sum_within_n_percent,
            trade_size=market.trade_size,
            net_position=token_state.net_position,
            static_min_price=market.boundary_no_new_risk_min_price,
            static_max_price=market.boundary_no_new_risk_max_price,
            threshold=market.boundary_adverse_selection_threshold,
            exit_cost_multiplier=market.boundary_exit_cost_multiplier,
            spread_bps=spread_bps,
            ewma_imbalance_bps=flow.ewma_imbalance_bps,
            fill_adversity_ratio=alpha_signal.fill_adversity_ratio,
            realized_vol_bps=alpha_signal.realized_vol_bps,
            three_hour_volatility=token_state.three_hour_volatility,
            book_age_ms=book_diag.book_age_ms,
            stale_book_gate_ms=market.stale_book_gate_ms,
            time_to_expiry_ms=risk.time_to_expiry_ms,
            market_duration_ms=market.market_duration_ms,
        )
        boundary_reason: Optional[str] = boundary_decision.reason
        boundary_active = boundary_decision.active
        boundary_buy_blocked = boundary_decision.buy_blocked

        # Risk-manager exit actions override the normal sell quote. This is
        # where drawdown/lifecycle controls such as stop loss, stale unwind,
        # force flat, event de-risk, or day-loss cap turn inventory into an
        # exit quote. It does not pause the whole bot; runner-level trading
        # enable/kill-switch logic handles that before this loop runs.
        if risk.action in {"STOP_LOSS", "TAKE_PROFIT", "STALE_UNWIND", "FORCE_FLAT", "EVENT_DE_RISK", "DAY_LOSS_CAP"} and risk.exit_price is not None and risk.exit_size > 0:
            quote_plan = QuotePlan(
                bid_price=quote_plan.bid_price,
                ask_price=float(risk.exit_price),
                bid_mode=quote_plan.bid_mode,
                ask_mode=(
                    f"risk_exit_{str(risk.action).lower()}_{str(risk.exit_mode or 'maker').lower()}"
                ),
                tick_size=quote_plan.tick_size,
            )

        desired: List[DesiredQuote] = []
        buy_amount = float(size_plan.buy_amount)
        if buy_reentry_blocked:
            buy_amount = 0.0
        if boundary_buy_blocked:
            buy_amount = 0.0
        if token_state.hedge_reduce_only or token_state.hedge_block_buy:
            buy_amount = 0.0
        if token_state.hedge_action == "HEDGE" and token_state.hedge_target_side == "buy":
            buy_amount *= max(0.0, min(1.0, float(token_state.hedge_ratio or 0.0)))
        if risk.max_buy_size is not None:
            buy_amount = min(buy_amount, max(0.0, float(risk.max_buy_size)))
        min_effective_order_size = max(1.0, float(market.min_order_size or 0.0))
        if buy_amount < min_effective_order_size:
            buy_amount = 0.0

        sell_amount = float(size_plan.sell_amount)
        if token_state.hedge_block_sell:
            sell_amount = 0.0
        if risk.action in {"STOP_LOSS", "TAKE_PROFIT", "STALE_UNWIND", "FORCE_FLAT", "EVENT_DE_RISK", "DAY_LOSS_CAP"} and risk.exit_size > 0:
            sell_amount = min(float(token_state.position), float(risk.exit_size))

        if flow.allow_buy and risk.allow_buy and quote_plan.bid_price is not None and buy_amount > 0:
            desired.append(
                DesiredQuote(
                    quote_key=f"{market.market_id}:{token_state.token_id}:buy",
                    token_id=token_state.token_id,
                    side="buy",
                    price=quote_plan.bid_price,
                    size=buy_amount,
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
                        "price_boundary_active": boundary_active,
                        "price_boundary_buy_blocked": boundary_buy_blocked,
                        "price_boundary_reason": boundary_reason,
                        "price_boundary_mode": boundary_decision.mode,
                        "price_boundary_score": boundary_decision.score,
                        "price_boundary_threshold": boundary_decision.threshold,
                        "price_boundary_effective_min_price": boundary_decision.effective_min_price,
                        "price_boundary_effective_max_price": boundary_decision.effective_max_price,
                        "price_boundary_components": dict(boundary_decision.components),
                        "boundary_no_new_risk_min_price": market.boundary_no_new_risk_min_price,
                        "boundary_no_new_risk_max_price": market.boundary_no_new_risk_max_price,
                        "boundary_adverse_selection_threshold": market.boundary_adverse_selection_threshold,
                        "boundary_exit_cost_multiplier": market.boundary_exit_cost_multiplier,
                        "buy_reentry_blocked": buy_reentry_blocked,
                        "buy_reentry_block_until_ms": block_until_ms if buy_reentry_blocked else None,
                        "event_id": market.event_id,
                        "exchange": market.exchange,
                        "fee_model_exchange": market.fee_model_exchange,
                        "fee_type": market.fee_type,
                        "fee_multiplier": market.fee_multiplier,
                        "hedge_action": token_state.hedge_action,
                        "hedge_cluster_id": token_state.hedge_cluster_id,
                        "control_state": token_state.control_state,
                        "hedge_action_reason": token_state.hedge_action_reason,
                        "hedge_market_id": token_state.hedge_market_id,
                        "hedge_target_token_id": token_state.hedge_target_token_id,
                        "hedge_target_side": token_state.hedge_target_side,
                        "hedge_preferred_side": token_state.hedge_preferred_side,
                        "hedge_ratio": token_state.hedge_ratio,
                        "hedge_quality_score": token_state.hedge_quality_score,
                        "hedge_execution_quality_score": token_state.hedge_execution_quality_score,
                        "hedge_covariance": token_state.hedge_covariance,
                        "hedge_correlation": token_state.hedge_correlation,
                        "hedge_beta_raw": token_state.hedge_beta_raw,
                        "hedge_beta": token_state.hedge_beta,
                        "hedge_beta_shrunk": token_state.hedge_beta_shrunk,
                        "hedge_beta_clipped": token_state.hedge_beta_clipped,
                        "hedge_covariance_sample_count": token_state.hedge_covariance_sample_count,
                        "hedge_covariance_state": token_state.hedge_covariance_state,
                        "hedge_covariance_confidence": token_state.hedge_covariance_confidence,
                        "hedge_pair_score": token_state.hedge_pair_score,
                        "hedgeability_tier": token_state.hedgeability_tier,
                        "hedge_structural_score": token_state.hedge_structural_score,
                        "hedge_covariance_score": token_state.hedge_covariance_score,
                        "hedge_beta_stability_score": token_state.hedge_beta_stability_score,
                        "hedge_execution_availability_score": token_state.hedge_execution_availability_score,
                        "hedge_realized_outcome_score": token_state.hedge_realized_outcome_score,
                        "hedge_relation_confidence_state": token_state.hedge_relation_confidence_state,
                        "hedge_permission_state": token_state.hedge_permission_state,
                        "hedge_rejection_reason": token_state.hedge_rejection_reason,
                        "hedge_model_state": token_state.hedge_model_state,
                        "hedge_realized_improvement_state": token_state.hedge_realized_improvement_state,
                        "hedge_success_window_ms": token_state.hedge_success_window_ms,
                        "hedge_failed_cooldown_until_ms": token_state.hedge_failed_cooldown_until_ms,
                        "hedge_rejection_reasons": list(token_state.hedge_rejection_reasons or ()),
                        "current_equity": risk.current_equity,
                        "reference_equity": risk.reference_equity,
                        "risk_state": risk.risk_state,
                        "stale_state": risk.stale_state,
                        "exit_mode": risk.exit_mode,
                        "exit_escalation_reason": risk.exit_escalation_reason,
                        "stop_open_triggered": risk.stop_open_triggered,
                        "force_flat_triggered": risk.force_flat_triggered,
                        "cross_armed": risk.cross_armed,
                        "maker_exit_deadline_ms": risk.maker_exit_deadline_ms,
                        "flatten_only_triggered": risk.flatten_only_triggered,
                        "market_exposure_notional": risk.market_exposure_notional,
                        "event_exposure_notional": risk.event_exposure_notional,
                        "market_unrealized_pnl": risk.market_unrealized_pnl,
                        "event_unrealized_pnl": risk.event_unrealized_pnl,
                        "portfolio_total_pnl": risk.portfolio_total_pnl,
                        "time_to_expiry_ms": risk.time_to_expiry_ms,
                        "stale_after_ms": risk.stale_after_ms,
                        "per_trade_loss_budget": risk.per_trade_loss_budget,
                        "max_buy_size": risk.max_buy_size,
                    },
                )
            )
        if flow.allow_sell and risk.allow_sell and quote_plan.ask_price is not None and sell_amount > 0:
            desired.append(
                DesiredQuote(
                    quote_key=f"{market.market_id}:{token_state.token_id}:sell",
                    token_id=token_state.token_id,
                    side="sell",
                    price=quote_plan.ask_price,
                    size=sell_amount,
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
                        "price_boundary_active": boundary_active,
                        "price_boundary_buy_blocked": boundary_buy_blocked,
                        "price_boundary_reason": boundary_reason,
                        "price_boundary_mode": boundary_decision.mode,
                        "price_boundary_score": boundary_decision.score,
                        "price_boundary_threshold": boundary_decision.threshold,
                        "price_boundary_effective_min_price": boundary_decision.effective_min_price,
                        "price_boundary_effective_max_price": boundary_decision.effective_max_price,
                        "price_boundary_components": dict(boundary_decision.components),
                        "boundary_no_new_risk_min_price": market.boundary_no_new_risk_min_price,
                        "boundary_no_new_risk_max_price": market.boundary_no_new_risk_max_price,
                        "boundary_adverse_selection_threshold": market.boundary_adverse_selection_threshold,
                        "boundary_exit_cost_multiplier": market.boundary_exit_cost_multiplier,
                        "buy_reentry_blocked": buy_reentry_blocked,
                        "buy_reentry_block_until_ms": block_until_ms if buy_reentry_blocked else None,
                        "event_id": market.event_id,
                        "exchange": market.exchange,
                        "fee_model_exchange": market.fee_model_exchange,
                        "fee_type": market.fee_type,
                        "fee_multiplier": market.fee_multiplier,
                        "hedge_action": token_state.hedge_action,
                        "hedge_cluster_id": token_state.hedge_cluster_id,
                        "control_state": token_state.control_state,
                        "hedge_action_reason": token_state.hedge_action_reason,
                        "hedge_market_id": token_state.hedge_market_id,
                        "hedge_target_token_id": token_state.hedge_target_token_id,
                        "hedge_target_side": token_state.hedge_target_side,
                        "hedge_preferred_side": token_state.hedge_preferred_side,
                        "hedge_ratio": token_state.hedge_ratio,
                        "hedge_quality_score": token_state.hedge_quality_score,
                        "hedge_execution_quality_score": token_state.hedge_execution_quality_score,
                        "hedge_covariance": token_state.hedge_covariance,
                        "hedge_correlation": token_state.hedge_correlation,
                        "hedge_beta_raw": token_state.hedge_beta_raw,
                        "hedge_beta": token_state.hedge_beta,
                        "hedge_beta_shrunk": token_state.hedge_beta_shrunk,
                        "hedge_beta_clipped": token_state.hedge_beta_clipped,
                        "hedge_covariance_sample_count": token_state.hedge_covariance_sample_count,
                        "hedge_covariance_state": token_state.hedge_covariance_state,
                        "hedge_covariance_confidence": token_state.hedge_covariance_confidence,
                        "hedge_pair_score": token_state.hedge_pair_score,
                        "hedgeability_tier": token_state.hedgeability_tier,
                        "hedge_structural_score": token_state.hedge_structural_score,
                        "hedge_covariance_score": token_state.hedge_covariance_score,
                        "hedge_beta_stability_score": token_state.hedge_beta_stability_score,
                        "hedge_execution_availability_score": token_state.hedge_execution_availability_score,
                        "hedge_realized_outcome_score": token_state.hedge_realized_outcome_score,
                        "hedge_relation_confidence_state": token_state.hedge_relation_confidence_state,
                        "hedge_permission_state": token_state.hedge_permission_state,
                        "hedge_rejection_reason": token_state.hedge_rejection_reason,
                        "hedge_model_state": token_state.hedge_model_state,
                        "hedge_realized_improvement_state": token_state.hedge_realized_improvement_state,
                        "hedge_success_window_ms": token_state.hedge_success_window_ms,
                        "hedge_failed_cooldown_until_ms": token_state.hedge_failed_cooldown_until_ms,
                        "hedge_rejection_reasons": list(token_state.hedge_rejection_reasons or ()),
                        "current_equity": risk.current_equity,
                        "reference_equity": risk.reference_equity,
                        "risk_state": risk.risk_state,
                        "stale_state": risk.stale_state,
                        "exit_mode": risk.exit_mode,
                        "exit_escalation_reason": risk.exit_escalation_reason,
                        "stop_open_triggered": risk.stop_open_triggered,
                        "force_flat_triggered": risk.force_flat_triggered,
                        "cross_armed": risk.cross_armed,
                        "maker_exit_deadline_ms": risk.maker_exit_deadline_ms,
                        "flatten_only_triggered": risk.flatten_only_triggered,
                        "market_exposure_notional": risk.market_exposure_notional,
                        "event_exposure_notional": risk.event_exposure_notional,
                        "market_unrealized_pnl": risk.market_unrealized_pnl,
                        "event_unrealized_pnl": risk.event_unrealized_pnl,
                        "portfolio_total_pnl": risk.portfolio_total_pnl,
                        "time_to_expiry_ms": risk.time_to_expiry_ms,
                        "stale_after_ms": risk.stale_after_ms,
                        "per_trade_loss_budget": risk.per_trade_loss_budget,
                        "max_buy_size": risk.max_buy_size,
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
