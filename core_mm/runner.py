from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core_mm.book_manager import BookManager
from core_mm.book_metrics import classify_book_state
from core_mm.complement_arb import ComplementArbConfig, ComplementArbScanner, ComplementArbSignal
from core_mm.execution import ExecutionAdapter
from core_mm.main_loop import MarketConfig, MarketCycleResult, TokenState, TradingMainLoop
from core_mm.market_selector import MarketCandidate, MarketSelector
from core_mm.order_manager import RestingOrder, SmartOrderManager
from core_mm.paper_broker import PaperBroker
from core_mm.positions import PositionTracker
from core_mm.risk_manager import RiskConfig, RiskManager
from core_mm.user_feed import UserFeedState


@dataclass(frozen=True)
class RunnerStatus:
    mode: str
    market_id: Optional[str]
    market_ids: tuple[str, ...]
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
        # Inventory skew parameters (wired into MarketConfig)
        stale_book_gate_ms: int = 5_000,
        max_skew_ticks: int = 3,
        inventory_skew_factor: float = 1.0,
        # Paper broker realism parameters
        paper_stale_book_ms: int = 5_000,
        paper_min_queue_wait_ms: int = 200,
        paper_queue_depth_fraction: float = 0.5,
        # EWMA flow filter span (cycles)
        flow_filter_ewma_span: int = 10,
        # Minimum position overlap to trigger merge
        min_merge_size: float = 20.0,
        # Risk configuration (sleep_hours, thresholds, etc.)
        risk_config: Optional[RiskConfig] = None,
        # Multi-market
        max_active_markets: int = 1,
        # Complement arbitrage
        complement_arb_config: Optional[ComplementArbConfig] = None,
    ) -> None:
        self.mode = str(mode).upper()
        self.market_selector = market_selector
        self.book_manager = book_manager or BookManager()
        self.position_tracker = position_tracker or PositionTracker()
        self.user_feed = user_feed or UserFeedState(position_tracker=self.position_tracker)
        self.order_manager = order_manager or SmartOrderManager()
        self.risk_manager = risk_manager or RiskManager(config=risk_config)
        if broker is not None:
            self.broker = broker
        elif self.mode == "PAPER":
            self.broker = PaperBroker(
                book_manager=self.book_manager,
                position_tracker=self.position_tracker,
                fee_bps=fee_bps,
                fee_mode=fee_mode,
                stale_book_ms=paper_stale_book_ms,
                min_queue_wait_ms=paper_min_queue_wait_ms,
                queue_depth_fraction=paper_queue_depth_fraction,
            )
        elif self.mode == "LIVE":
            raise ValueError("LiveBroker must be provided for LIVE mode (pass broker=)")
        else:
            self.broker = None
        self._flow_filter_ewma_span = int(flow_filter_ewma_span)
        self._min_merge_size = float(min_merge_size)
        self.main_loop = TradingMainLoop(
            book_manager=self.book_manager,
            order_manager=self.order_manager,
            risk_manager=self.risk_manager,
            execution_adapter=self.broker if self.mode != "OBSERVE" else None,
            mode=self.mode,
            flow_filter_ewma_span=self._flow_filter_ewma_span,
        )
        self._max_active_markets = max(1, int(max_active_markets))
        self.active_markets: List[MarketCandidate] = []
        self._market_dwell_at: Dict[str, int] = {}
        self._min_size = float(min_size)
        self._fallback_size = float(fallback_size)
        self._within_pct = float(within_pct)
        self._trade_size = float(trade_size)
        self._max_size = float(max_size)
        self._reverse_position_min_size = float(reverse_position_min_size)
        self._min_order_size_override = min_order_size_override
        self._market_dwell_ms = max(0, int(market_dwell_ms))
        self._stale_book_gate_ms = int(stale_book_gate_ms)
        self._max_skew_ticks = int(max_skew_ticks)
        self._inventory_skew_factor = float(inventory_skew_factor)
        # Complement arbitrage scanner
        self._complement_arb = ComplementArbScanner(config=complement_arb_config)
        # Phase 4: merge tracking
        self._merge_count: int = 0
        self._total_merged_amount: float = 0.0
        # Per-token quote generation counts for asymmetry diagnosis
        self._per_token_quote_counts: Dict[str, Dict[str, int]] = {}

    @property
    def current_market(self) -> Optional[MarketCandidate]:
        """Backward-compat: returns first active market or None."""
        return self.active_markets[0] if self.active_markets else None

    @property
    def all_token_ids(self) -> tuple[str, ...]:
        """Union of all token IDs across all active markets."""
        seen: List[str] = []
        for m in self.active_markets:
            for tid in m.token_ids:
                if tid not in seen:
                    seen.append(tid)
        return tuple(seen)

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
        old_ids = {m.condition_id for m in self.active_markets}
        if not candidates:
            changed = bool(self.active_markets)
            self.active_markets = []
            self._market_dwell_at.clear()
            return changed
        candidate_by_id = {c.condition_id: c for c in candidates}
        # Which currently active markets are still valid candidates?
        still_valid = {
            old_m.condition_id: candidate_by_id[old_m.condition_id]
            for old_m in self.active_markets
            if old_m.condition_id in candidate_by_id
        }
        # Which of those are dwell-protected (cannot be evicted)?
        dwell_protected_ids: set[str] = set()
        if self._market_dwell_ms > 0:
            for cid in still_valid:
                dwell_start = self._market_dwell_at.get(cid, 0)
                if active_now_ms < dwell_start + self._market_dwell_ms:
                    dwell_protected_ids.add(cid)
        # Start with dwell-protected markets (they reserve their slots)
        result_markets = [still_valid[cid] for cid in dwell_protected_ids]
        result_ids = set(dwell_protected_ids)
        # Fill remaining slots from top candidates
        remaining = max(0, self._max_active_markets - len(result_markets))
        for c in candidates:
            if remaining <= 0:
                break
            if c.condition_id not in result_ids:
                result_markets.append(c)
                result_ids.add(c.condition_id)
                remaining -= 1
        changed = result_ids != old_ids
        self.active_markets = result_markets
        # Track dwell for newly added markets
        for m in result_markets:
            if m.condition_id not in old_ids:
                self._market_dwell_at[m.condition_id] = active_now_ms
        # Clean up removed markets
        for cid in list(self._market_dwell_at):
            if cid not in result_ids:
                del self._market_dwell_at[cid]
        return changed

    def on_market_message(self, message: Dict[str, Any], recv_wall_ms: Optional[int] = None) -> int:
        applied = self.book_manager.process_message(message, recv_wall_ms=recv_wall_ms)
        if applied > 0 and isinstance(self.broker, PaperBroker):
            self.broker.sweep_fills()
        return applied

    def on_user_message(self, message: Dict[str, Any]) -> Sequence[Any]:
        return self.user_feed.apply_message(message)

    async def run_cycles(
        self,
        *,
        now_ms: int,
        usdc_balance: Optional[float] = None,
        three_hour_volatility: float = 0.0,
    ) -> List[MarketCycleResult]:
        """Run trading cycle for ALL active markets. Returns list of results."""
        if not self.active_markets:
            return []
        effective_balance = usdc_balance
        # Aggregate risk: block new buys if total position notional exceeded
        if self.risk_manager.config.max_total_position_notional > 0:
            total_notional = sum(
                pos.size * pos.avg_price
                for m in self.active_markets
                for tid in m.token_ids
                if (pos := self.position_tracker.get_position(tid)).size > 0
            )
            if total_notional > self.risk_manager.config.max_total_position_notional:
                effective_balance = 0.0
        # Aggregate risk: block new buys if too many markets hold inventory
        if self.risk_manager.config.max_markets_with_position > 0:
            markets_with_pos = sum(
                1 for m in self.active_markets
                if any(self.position_tracker.get_position(tid).size > 0 for tid in m.token_ids)
            )
            if markets_with_pos > self.risk_manager.config.max_markets_with_position:
                effective_balance = 0.0
        results: List[MarketCycleResult] = []
        for market in self.active_markets:
            result = await self._run_single_market_cycle(
                market=market, now_ms=now_ms,
                usdc_balance=effective_balance,
                three_hour_volatility=three_hour_volatility,
            )
            if result is not None:
                results.append(result)
        return results

    async def run_cycle(
        self,
        *,
        now_ms: int,
        usdc_balance: Optional[float] = None,
        three_hour_volatility: float = 0.0,
    ) -> Optional[MarketCycleResult]:
        """Backward-compat: run cycle for first active market only."""
        if not self.active_markets:
            return None
        return await self._run_single_market_cycle(
            market=self.active_markets[0], now_ms=now_ms,
            usdc_balance=usdc_balance, three_hour_volatility=three_hour_volatility,
        )

    async def _run_single_market_cycle(
        self,
        *,
        market: MarketCandidate,
        now_ms: int,
        usdc_balance: Optional[float] = None,
        three_hour_volatility: float = 0.0,
    ) -> Optional[MarketCycleResult]:
        # Merge opposing positions before evaluating to free capital
        token_ids_for_merge = list(market.token_ids)
        if len(token_ids_for_merge) == 2:
            merge_result = self.position_tracker.merge_positions(
                token_ids_for_merge[0], token_ids_for_merge[1],
                min_merge_size=self._min_merge_size,
            )
            if merge_result.executed:
                self._merge_count += 1
                self._total_merged_amount += float(merge_result.amount_to_merge)
            if merge_result.executed and isinstance(self.broker, PaperBroker):
                merge_ts = int(time.time() * 1000)
                self.broker.consume_fifo_for_merge(token_ids_for_merge[0], merge_result.amount_to_merge, merge_ts)
                self.broker.consume_fifo_for_merge(token_ids_for_merge[1], merge_result.amount_to_merge, merge_ts)
        # Complement arb: evaluate edge and adjust sizing if active
        arb_signal = ComplementArbSignal()
        effective_trade_size = self._trade_size
        if len(token_ids_for_merge) == 2:
            book_yes = self.book_manager.get_book(token_ids_for_merge[0])
            book_no = self.book_manager.get_book(token_ids_for_merge[1])
            arb_signal = self._complement_arb.evaluate(
                yes_bid=book_yes.best_bid if book_yes else None,
                yes_ask=book_yes.best_ask if book_yes else None,
                no_bid=book_no.best_bid if book_no else None,
                no_ask=book_no.best_ask if book_no else None,
            )
            if arb_signal.size_multiplier > 1.0:
                effective_trade_size = self._trade_size * arb_signal.size_multiplier

        market_config = MarketConfig(
            market_id=market.slug,
            token_ids=market.token_ids,
            tick_size=market.tick_size,
            min_size=self._min_size,
            fallback_size=self._fallback_size,
            within_pct=self._within_pct,
            trade_size=effective_trade_size,
            max_size=self._max_size,
            reverse_position_min_size=self._reverse_position_min_size,
            min_order_size=(
                float(self._min_order_size_override)
                if self._min_order_size_override is not None
                else float(market.min_incentive_size or 0.0)
            ),
            stale_book_gate_ms=self._stale_book_gate_ms,
            max_skew_ticks=self._max_skew_ticks,
            inventory_skew_factor=self._inventory_skew_factor,
        )
        token_states = []
        token_ids = list(market.token_ids)
        for idx, token_id in enumerate(token_ids):
            reverse_id = token_ids[1 - idx] if len(token_ids) == 2 else None
            position = self.position_tracker.get_position(token_id)
            reverse_position = self.position_tracker.get_position(reverse_id).size if reverse_id else 0.0
            net_position = position.size - reverse_position
            token_states.append(
                TokenState(
                    token_id=token_id,
                    position=position.size,
                    avg_cost=position.avg_price,
                    reverse_position=reverse_position,
                    net_position=net_position,
                    usdc_balance=usdc_balance,
                    three_hour_volatility=three_hour_volatility,
                )
            )
        # Complement arbitrage: feed both token mids into alpha overlays
        if len(token_ids) == 2:
            book_0 = self.book_manager.get_book(token_ids[0])
            book_1 = self.book_manager.get_book(token_ids[1])
            mid_0 = float(book_0.mid_price) if book_0 and book_0.mid_price else 0.0
            mid_1 = float(book_1.mid_price) if book_1 and book_1.mid_price else 0.0
            if mid_0 > 0 and mid_1 > 0:
                self.main_loop._get_alpha_overlay(token_ids[0]).update_complement(mid_0, mid_1)
                self.main_loop._get_alpha_overlay(token_ids[1]).update_complement(mid_0, mid_1)

        existing_orders = self._existing_orders_by_quote_key(market_config.market_id)
        result = await self.main_loop.run_market_cycle(
            market=market_config,
            token_states=tuple(token_states),
            existing_orders=existing_orders,
            now_ms=now_ms,
        )
        # Accumulate per-token quote counts for asymmetry diagnosis
        for td in result.token_decisions:
            tid = str(td.token_id)
            counts = self._per_token_quote_counts.setdefault(tid, {
                "buy_quotes": 0, "sell_quotes": 0,
                "skip_count": 0, "freeze_count": 0, "emergency_count": 0,
            })
            buy_q = sum(1 for q in td.desired_quotes if q.side == "buy")
            sell_q = sum(1 for q in td.desired_quotes if q.side == "sell")
            counts["buy_quotes"] += buy_q
            counts["sell_quotes"] += sell_q
            if td.book_diag.state != "book_ok":
                counts["freeze_count"] += 1
            elif buy_q == 0 and sell_q == 0:
                if td.flow_filter is None:
                    counts["emergency_count"] += 1
                else:
                    counts["skip_count"] += 1
        return result

    def status(self) -> RunnerStatus:
        token_ids = self.all_token_ids
        market_ids = tuple(m.slug for m in self.active_markets)
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
            market_id=(self.active_markets[0].slug if self.active_markets else None),
            market_ids=market_ids,
            token_ids=token_ids,
            has_books=has_books,
            book_diag={
                "per_token": token_diag,
                "tokens_ok": int(tokens_ok),
                "tokens_blocked": max(0, len(token_diag) - int(tokens_ok)),
                "blocking_state_counts": blocking_state_counts,
            },
        )

    @property
    def complement_arb_stats(self) -> Dict[str, Any]:
        return self._complement_arb.stats

    @property
    def merge_stats(self) -> Dict[str, Any]:
        return {
            "merge_count": self._merge_count,
            "total_merged_amount": self._total_merged_amount,
        }

    @property
    def per_token_quote_stats(self) -> Dict[str, Any]:
        return dict(self._per_token_quote_counts)

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
