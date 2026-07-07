from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core_mm.book_manager import BookManager
from core_mm.book_metrics import classify_book_state
from core_mm.complement_arb import ComplementArbConfig, ComplementArbScanner, ComplementArbSignal
from core_mm.execution import ExecutionAdapter
from core_mm.hedge_engine import HedgeEngine
from core_mm.kalshi.fees import infer_fee_spec
from core_mm.main_loop import MarketConfig, MarketCycleResult, TokenState, TradingMainLoop
from core_mm.market_selector import MarketCandidate, MarketSelector
from core_mm.order_manager import RestingOrder, SmartOrderManager
from core_mm.paper_broker import PaperBroker
from core_mm.positions import PositionTracker
from core_mm.risk_manager import RiskConfig, RiskManager
from core_mm.user_feed import UserFeedState


SAFE_RISK_PROFILES: Dict[str, Dict[str, float]] = {
    "200": {
        "allocated_equity": 200.0,
        "trade_size": 4.0,
        "max_size": 8.0,
        "hard_position_cap": 12.0,
        "min_size": 2.0,
        "fallback_size": 2.0,
        "per_event_loss_pct": 0.04,
        "per_day_loss_pct": 0.06,
    },
    "500": {
        "allocated_equity": 500.0,
        "trade_size": 8.0,
        "max_size": 20.0,
        "hard_position_cap": 30.0,
        "min_size": 2.0,
        "fallback_size": 2.0,
        "per_event_loss_pct": 0.04,
        "per_day_loss_pct": 0.06,
    },
    "1000": {
        "allocated_equity": 1000.0,
        "trade_size": 10.0,
        "max_size": 35.0,
        "hard_position_cap": 50.0,
        "min_size": 3.0,
        "fallback_size": 2.0,
        "per_event_loss_pct": 0.05,
        "per_day_loss_pct": 0.08,
    },
}


def resolve_safe_risk_profile_name(strategy_allocated_equity: Optional[float], requested_profile: str = "auto") -> str:
    requested = str(requested_profile or "auto").strip().lower()
    if requested in SAFE_RISK_PROFILES:
        return requested
    if requested == "custom":
        return "custom"
    equity = float(strategy_allocated_equity or 0.0)
    if equity <= 350.0:
        return "200"
    if equity <= 750.0:
        return "500"
    return "1000"


@dataclass(frozen=True)
class RunnerStatus:
    mode: str
    market_id: Optional[str]
    market_ids: tuple[str, ...]
    token_ids: tuple[str, ...]
    has_books: bool
    book_diag: Dict[str, Any]
    selection: Dict[str, Any]
    active_market_health: Dict[str, Any]
    cluster_exposure: Dict[str, Any]
    cluster_hedge: Dict[str, Any]
    control_state: Dict[str, Any]


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
        min_order_size_override: Optional[float] = None,
        fee_bps: float = 25.0,
        fee_mode: str = "taker",
        market_dwell_ms: int = 0,
        # Inventory skew parameters (wired into MarketConfig)
        stale_book_gate_ms: int = 5_000,
        boundary_no_new_risk_min_price: float = 0.10,
        boundary_no_new_risk_max_price: float = 0.90,
        boundary_guard_mode: str = "adaptive",
        boundary_adverse_selection_threshold: float = 0.50,
        boundary_exit_cost_multiplier: float = 1.25,
        max_skew_ticks: int = 3,
        inventory_skew_factor: float = 1.0,
        # Kelly criterion sizing
        kelly_fraction: float = 0.0,
        # Paper broker realism parameters
        paper_stale_book_ms: int = 5_000,
        paper_min_queue_wait_ms: int = 200,
        paper_queue_depth_fraction: float = 0.5,
        # EWMA flow filter span (cycles)
        flow_filter_ewma_span: int = 10,
        # Cooldown after a sell fill before we allow re-entry buys again.
        post_fill_reentry_cooldown_ms: int = 0,
        # Minimum position overlap to trigger merge
        min_merge_size: float = 20.0,
        # Risk configuration (sleep_hours, thresholds, etc.)
        risk_config: Optional[RiskConfig] = None,
        strategy_allocated_equity: Optional[float] = None,
        use_allocated_equity_for_risk: bool = True,
        risk_based_share_sizing: bool = True,
        safe_risk_profile: str = "auto",
        # Multi-market
        max_active_markets: int = 1,
        skew_threshold_fraction: float = 0.25,
        hedge_threshold_fraction: float = 0.60,
        hedge_requires_stale_inventory: bool = True,
        hedge_quality_must_beat_inventory_market: bool = True,
        hedge_min_quality_score: float = 10_000.0,
        hedge_max_temp_gross_increase_fraction: float = 0.10,
        hedge_failure_cooldown_scale: float = 1.0,
        hedge_search_profile: str = "production",
        proof_only_bucket_distance: int = 2,
        proof_only_expiry_slack_ms: int = 60_000,
        hedge_covariance_enabled: bool = True,
        hedge_covariance_window_secs: float = 600.0,
        hedge_covariance_min_samples: int = 5,
        hedge_covariance_min_correlation: float = 0.25,
        hedge_covariance_min_abs_beta: float = 0.05,
        hedge_covariance_beta_clip: float = 1.0,
        hedge_covariance_gate_required: bool = True,
        hedge_covariance_beta_shrinkage: float = 0.35,
        hedge_covariance_max_sample_age_ms: int = 30_000,
        hedge_covariance_max_update_gap_ms: int = 2_000,
        hedge_covariance_boundary_buffer: float = 0.08,
        hedge_covariance_boundary_max_fraction: float = 0.50,
        hedge_covariance_strong_correlation: float = 0.60,
        hedge_covariance_strong_min_samples: int = 8,
        hedge_covariance_stability_ratio_max: float = 3.0,
        observe_pause_interval_secs: float = 1_200.0,
        observe_pause_duration_secs: float = 10.0,
        negative_pnl_reduce_only_enabled: bool = True,
        negative_pnl_unwind_requires_worsening: bool = True,
        negative_pnl_unwind_requires_stale_or_worsening: bool = True,
        cycle_hint_ms: int = 1_000,
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
            post_fill_reentry_cooldown_ms=post_fill_reentry_cooldown_ms,
        )
        self._max_active_markets = max(1, int(max_active_markets))
        self.active_markets: List[MarketCandidate] = []
        self._market_dwell_at: Dict[str, int] = {}
        self._min_size = float(min_size)
        self._fallback_size = float(fallback_size)
        self._within_pct = float(within_pct)
        self._trade_size = float(trade_size)
        self._max_size = float(max_size)
        self._min_order_size_override = min_order_size_override
        self._market_dwell_ms = max(0, int(market_dwell_ms))
        self._stale_book_gate_ms = int(stale_book_gate_ms)
        boundary_min = min(1.0, max(0.0, float(boundary_no_new_risk_min_price)))
        boundary_max = min(1.0, max(0.0, float(boundary_no_new_risk_max_price)))
        if boundary_min >= boundary_max:
            boundary_min = 0.0
            boundary_max = 1.0
        self._boundary_no_new_risk_min_price = boundary_min
        self._boundary_no_new_risk_max_price = boundary_max
        self._boundary_guard_mode = self._normalize_boundary_guard_mode(boundary_guard_mode)
        self._boundary_adverse_selection_threshold = max(0.01, float(boundary_adverse_selection_threshold))
        self._boundary_exit_cost_multiplier = max(1.0, float(boundary_exit_cost_multiplier))
        self._max_skew_ticks = int(max_skew_ticks)
        self._inventory_skew_factor = float(inventory_skew_factor)
        self._kelly_fraction = float(kelly_fraction)
        self._strategy_allocated_equity = float(strategy_allocated_equity) if strategy_allocated_equity not in (None, "") else None
        self._use_allocated_equity_for_risk = bool(use_allocated_equity_for_risk)
        self._risk_based_share_sizing = bool(risk_based_share_sizing)
        self._safe_risk_profile = resolve_safe_risk_profile_name(self._strategy_allocated_equity, safe_risk_profile)
        self._skew_threshold_fraction = max(0.0, float(skew_threshold_fraction))
        self._hedge_threshold_fraction = max(0.0, float(hedge_threshold_fraction))
        self._hedge_requires_stale_inventory = bool(hedge_requires_stale_inventory)
        self._hedge_quality_must_beat_inventory_market = bool(hedge_quality_must_beat_inventory_market)
        self._hedge_min_quality_score = max(0.0, float(hedge_min_quality_score))
        self._hedge_max_temp_gross_increase_fraction = max(0.0, float(hedge_max_temp_gross_increase_fraction))
        self._hedge_failure_cooldown_scale = max(0.1, float(hedge_failure_cooldown_scale))
        self._hedge_search_profile = str(hedge_search_profile or "production").strip().lower()
        self._proof_only_bucket_distance = max(1, int(proof_only_bucket_distance))
        self._proof_only_expiry_slack_ms = max(0, int(proof_only_expiry_slack_ms))
        self._hedge_covariance_enabled = bool(hedge_covariance_enabled)
        self._hedge_covariance_window_secs = max(1.0, float(hedge_covariance_window_secs))
        self._hedge_covariance_min_samples = max(2, int(hedge_covariance_min_samples))
        self._hedge_covariance_min_correlation = max(0.0, float(hedge_covariance_min_correlation))
        self._hedge_covariance_min_abs_beta = max(0.0, float(hedge_covariance_min_abs_beta))
        self._hedge_covariance_beta_clip = max(0.0, float(hedge_covariance_beta_clip))
        self._hedge_covariance_gate_required = bool(hedge_covariance_gate_required)
        self._hedge_covariance_beta_shrinkage = min(0.95, max(0.0, float(hedge_covariance_beta_shrinkage)))
        self._hedge_covariance_max_sample_age_ms = max(0, int(hedge_covariance_max_sample_age_ms))
        self._hedge_covariance_max_update_gap_ms = max(0, int(hedge_covariance_max_update_gap_ms))
        self._hedge_covariance_boundary_buffer = min(0.49, max(0.0, float(hedge_covariance_boundary_buffer)))
        self._hedge_covariance_boundary_max_fraction = min(1.0, max(0.0, float(hedge_covariance_boundary_max_fraction)))
        self._hedge_covariance_strong_correlation = max(
            self._hedge_covariance_min_correlation,
            float(hedge_covariance_strong_correlation),
        )
        self._hedge_covariance_strong_min_samples = max(
            self._hedge_covariance_min_samples,
            int(hedge_covariance_strong_min_samples),
        )
        self._hedge_covariance_stability_ratio_max = max(1.0, float(hedge_covariance_stability_ratio_max))
        if self.mode != "PAPER" and self._hedge_search_profile == "proof-only":
            self._hedge_search_profile = "production"
        self._observe_pause_interval_ms = int(max(0.0, float(observe_pause_interval_secs)) * 1000.0)
        self._observe_pause_duration_ms = int(max(0.0, float(observe_pause_duration_secs)) * 1000.0)
        self._negative_pnl_reduce_only_enabled = bool(negative_pnl_reduce_only_enabled)
        self._negative_pnl_unwind_requires_worsening = bool(negative_pnl_unwind_requires_worsening)
        self._negative_pnl_unwind_requires_stale_or_worsening = bool(negative_pnl_unwind_requires_stale_or_worsening)
        self._cycle_hint_ms = max(100, int(cycle_hint_ms))
        object.__setattr__(self.risk_manager.config, "strategy_allocated_equity", self._strategy_allocated_equity)
        object.__setattr__(self.risk_manager.config, "use_allocated_equity_for_risk", self._use_allocated_equity_for_risk)
        object.__setattr__(self.risk_manager.config, "risk_based_share_sizing", self._risk_based_share_sizing)
        object.__setattr__(self.risk_manager.config, "negative_pnl_reduce_only_enabled", self._negative_pnl_reduce_only_enabled)
        object.__setattr__(self.risk_manager.config, "negative_pnl_unwind_requires_worsening", self._negative_pnl_unwind_requires_worsening)
        object.__setattr__(self.risk_manager.config, "negative_pnl_unwind_requires_stale_or_worsening", self._negative_pnl_unwind_requires_stale_or_worsening)
        # Complement arbitrage scanner
        self._complement_arb = ComplementArbScanner(config=complement_arb_config)
        self._hedge_engine = HedgeEngine(
            skew_threshold_fraction=self._skew_threshold_fraction,
            hedge_threshold_fraction=self._hedge_threshold_fraction,
            hedge_requires_stale_inventory=self._hedge_requires_stale_inventory,
            hedge_quality_must_beat_inventory_market=self._hedge_quality_must_beat_inventory_market,
            min_quality_score=self._hedge_min_quality_score,
            hedge_max_temp_gross_increase_fraction=self._hedge_max_temp_gross_increase_fraction,
            hedge_failure_cooldown_scale=self._hedge_failure_cooldown_scale,
            hedge_success_window_ms=min(5_000, self._cycle_hint_ms * 5),
            negative_pnl_reduce_only_enabled=self._negative_pnl_reduce_only_enabled,
            negative_pnl_unwind_requires_worsening=self._negative_pnl_unwind_requires_worsening,
            negative_pnl_unwind_requires_stale_or_worsening=self._negative_pnl_unwind_requires_stale_or_worsening,
            hedge_search_profile=self._hedge_search_profile,
            proof_only_bucket_distance=self._proof_only_bucket_distance,
            proof_only_expiry_slack_ms=self._proof_only_expiry_slack_ms,
            hedge_covariance_enabled=self._hedge_covariance_enabled,
            hedge_covariance_window_secs=self._hedge_covariance_window_secs,
            hedge_covariance_min_samples=self._hedge_covariance_min_samples,
            hedge_covariance_min_correlation=self._hedge_covariance_min_correlation,
            hedge_covariance_min_abs_beta=self._hedge_covariance_min_abs_beta,
            hedge_covariance_beta_clip=self._hedge_covariance_beta_clip,
            hedge_covariance_gate_required=self._hedge_covariance_gate_required,
            hedge_covariance_beta_shrinkage=self._hedge_covariance_beta_shrinkage,
            hedge_covariance_max_sample_age_ms=self._hedge_covariance_max_sample_age_ms,
            hedge_covariance_max_update_gap_ms=self._hedge_covariance_max_update_gap_ms,
            hedge_covariance_boundary_buffer=self._hedge_covariance_boundary_buffer,
            hedge_covariance_boundary_max_fraction=self._hedge_covariance_boundary_max_fraction,
            hedge_covariance_strong_correlation=self._hedge_covariance_strong_correlation,
            hedge_covariance_strong_min_samples=self._hedge_covariance_strong_min_samples,
            hedge_covariance_stability_ratio_max=self._hedge_covariance_stability_ratio_max,
        )
        # Phase 4: merge tracking
        self._merge_count: int = 0
        self._total_merged_amount: float = 0.0
        # Per-token quote generation counts for asymmetry diagnosis
        self._per_token_quote_counts: Dict[str, Dict[str, int]] = {}
        self._recent_book_states = deque(maxlen=200)
        self._last_selection_report: Dict[str, Any] = {}
        self._last_cluster_hedge_state: Dict[str, Any] = {"enabled": False, "paper_only": True, "clusters": []}
        self._known_markets_by_id: Dict[str, MarketCandidate] = {}
        self._known_markets_by_token_id: Dict[str, MarketCandidate] = {}
        self._trading_enabled: bool = True
        self._kill_switch: bool = False
        self._quote_spread_multiplier: float = 1.0
        self._forced_flat_events: set[str] = set()
        self._forced_flat_markets: set[str] = set()
        self._last_control_command: Dict[str, Any] = {}
        self._flatten_only_mode: bool = False
        self._halt_after_flatten: bool = False
        self._risk_warning_triggered: bool = False
        self._observe_pause_until_ms: int = 0
        self._observe_pause_next_at_ms: Optional[int] = None
        self._observe_pause_last_started_ms: int = 0
        self._last_cycle_now_ms: int = 0
        if self._safe_risk_profile != "custom":
            self.apply_safe_risk_profile(self._safe_risk_profile, allocated_equity=self._strategy_allocated_equity)
        self._safe_profile_defaults: Dict[str, Any] = {
            "safe_risk_profile": self._safe_risk_profile,
            "strategy_allocated_equity": self._strategy_allocated_equity,
            "use_allocated_equity_for_risk": self._use_allocated_equity_for_risk,
            "risk_based_share_sizing": self._risk_based_share_sizing,
            "quote_spread_multiplier": 1.0,
            "trade_size": self._trade_size,
            "max_size": self._max_size,
            "min_size": self._min_size,
            "fallback_size": self._fallback_size,
            "min_order_size": self._min_order_size_override,
            "within_pct": self._within_pct,
            "market_dwell_secs": self._market_dwell_ms / 1000.0,
            "post_fill_reentry_cooldown_secs": self.main_loop._post_fill_reentry_cooldown_ms / 1000.0,
            "boundary_no_new_risk_min_price": self._boundary_no_new_risk_min_price,
            "boundary_no_new_risk_max_price": self._boundary_no_new_risk_max_price,
            "boundary_guard_mode": self._boundary_guard_mode,
            "boundary_adverse_selection_threshold": self._boundary_adverse_selection_threshold,
            "boundary_exit_cost_multiplier": self._boundary_exit_cost_multiplier,
            "hard_position_cap": self.risk_manager.config.hard_position_cap,
            "per_trade_loss_pct": self.risk_manager.config.per_trade_loss_pct,
            "per_event_loss_pct": self.risk_manager.config.per_event_loss_pct,
            "per_day_loss_pct": self.risk_manager.config.per_day_loss_pct,
            "max_order_notional_pct": self.risk_manager.config.max_order_notional_pct,
            "max_market_exposure_pct": self.risk_manager.config.max_market_exposure_pct,
            "max_event_exposure_pct": self.risk_manager.config.max_event_exposure_pct,
            "stale_duration_scale": self.risk_manager.config.stale_duration_scale,
            "maker_exit_grace_secs": self.risk_manager.config.maker_exit_grace_secs,
            "cross_escalation_drawdown_pct": self.risk_manager.config.cross_escalation_drawdown_pct,
            "stop_open_before_expiry_secs": self.risk_manager.config.stop_open_before_expiry_secs,
            "force_flat_before_expiry_secs": self.risk_manager.config.force_flat_before_expiry_secs,
            "reentry_cooldown_scale": self.risk_manager.config.reentry_cooldown_scale,
            "pre_kill_warning_fraction": self.risk_manager.config.pre_kill_warning_fraction,
            "skew_threshold_fraction": self._skew_threshold_fraction,
            "hedge_threshold_fraction": self._hedge_threshold_fraction,
            "hedge_requires_stale_inventory": self._hedge_requires_stale_inventory,
            "hedge_quality_must_beat_inventory_market": self._hedge_quality_must_beat_inventory_market,
            "hedge_min_quality_score": self._hedge_min_quality_score,
            "hedge_max_temp_gross_increase_fraction": self._hedge_max_temp_gross_increase_fraction,
            "hedge_failure_cooldown_scale": self._hedge_failure_cooldown_scale,
            "hedge_search_profile": self._hedge_search_profile,
            "proof_only_bucket_distance": self._proof_only_bucket_distance,
            "proof_only_expiry_slack_ms": self._proof_only_expiry_slack_ms,
            "observe_pause_interval_secs": self._observe_pause_interval_ms / 1000.0,
            "observe_pause_duration_secs": self._observe_pause_duration_ms / 1000.0,
            "negative_pnl_reduce_only_enabled": bool(self._negative_pnl_reduce_only_enabled),
            "negative_pnl_unwind_requires_worsening": bool(self._negative_pnl_unwind_requires_worsening),
            "negative_pnl_unwind_requires_stale_or_worsening": bool(self._negative_pnl_unwind_requires_stale_or_worsening),
        }

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

    @property
    def hedge_search_markets(self) -> tuple[MarketCandidate, ...]:
        """Markets the hedge engine may inspect without widening quote selection."""
        markets_by_id: Dict[str, MarketCandidate] = {}
        for market in self.active_markets:
            markets_by_id[str(market.condition_id)] = market

        active_kalshi_btc_clusters = {
            self._event_id_for_market(market)
            for market in self.active_markets
            if self._is_kalshi_btc_market(market)
        }
        if not active_kalshi_btc_clusters:
            return tuple(markets_by_id.values())

        for market in self._known_markets_by_id.values():
            market_id = str(market.condition_id or "")
            if not market_id or market_id in markets_by_id:
                continue
            if not self._is_kalshi_btc_market(market):
                continue
            if self._event_id_for_market(market) not in active_kalshi_btc_clusters:
                continue
            if market.closed is True or market.active is False or market.accepting_orders is False:
                continue
            markets_by_id[market_id] = market
        return tuple(markets_by_id.values())

    @property
    def hedge_search_token_ids(self) -> tuple[str, ...]:
        """Union of token IDs across the hedge-search universe."""
        seen: List[str] = []
        for market in self.hedge_search_markets:
            for token_id in market.token_ids:
                if token_id not in seen:
                    seen.append(token_id)
        return tuple(seen)

    @staticmethod
    def _normalize_boundary_guard_mode(value: Any) -> str:
        mode = str(value or "adaptive").strip().lower()
        return mode if mode in {"off", "static", "adaptive"} else "adaptive"

    def _event_id_for_market(self, market: MarketCandidate) -> str:
        raw = market.raw if isinstance(market.raw, dict) else {}
        for key in ("event_ticker", "eventTicker", "series_ticker", "seriesTicker"):
            value = raw.get(key)
            if value not in (None, ""):
                return str(value)
        slug = str(market.slug or market.condition_id or "")
        kalshi_bucket = re.match(r"^(.*?)-B\d+(?:\.\d+)?$", slug)
        if kalshi_bucket:
            return str(kalshi_bucket.group(1))
        return slug or str(market.condition_id)

    def _is_kalshi_btc_market(self, market: MarketCandidate) -> bool:
        if str(market.reference_symbol or "").strip().upper() != "BTC":
            return False
        cluster_id = self._event_id_for_market(market).upper()
        slug = str(market.slug or "").upper()
        return cluster_id.startswith("KXBTC-") or slug.startswith("KXBTC-")

    def _is_kalshi_market_candidate(self, market: MarketCandidate) -> bool:
        slug = str(market.slug or "").upper()
        raw = market.raw if isinstance(market.raw, dict) else {}
        series_ticker = str(raw.get("series_ticker") or raw.get("seriesTicker") or "").upper()
        event_ticker = str(raw.get("event_ticker") or raw.get("eventTicker") or "").upper()
        return slug.startswith("KX") or series_ticker.startswith("KX") or event_ticker.startswith("KX")

    def _remember_markets(self, markets: Sequence[MarketCandidate]) -> None:
        for market in markets:
            market_id = str(market.condition_id or "")
            if market_id:
                self._known_markets_by_id[market_id] = market
            for token_id in market.token_ids:
                token_key = str(token_id or "")
                if token_key:
                    self._known_markets_by_token_id[token_key] = market

    def _portfolio_markets(self) -> List[MarketCandidate]:
        market_map: Dict[str, MarketCandidate] = {}
        for market in self.active_markets:
            market_map[str(market.condition_id)] = market
        for token_id, position in self.position_tracker.snapshot().items():
            if position.size <= 0:
                continue
            market = self._known_markets_by_token_id.get(str(token_id))
            if market is not None:
                market_map[str(market.condition_id)] = market
        return list(market_map.values())

    def _token_outcome_side(self, market: MarketCandidate, token_id: str) -> str:
        token_key = str(token_id or "").strip().lower()
        if (
            token_key == "yes"
            or token_key.startswith("yes_")
            or token_key.startswith("yes-")
            or token_key.endswith(":yes")
            or token_key.endswith("_yes")
            or token_key.endswith("-yes")
        ):
            return "yes"
        if (
            token_key == "no"
            or token_key.startswith("no_")
            or token_key.startswith("no-")
            or token_key.endswith(":no")
            or token_key.endswith("_no")
            or token_key.endswith("-no")
        ):
            return "no"
        try:
            token_index = list(market.token_ids).index(str(token_id))
        except ValueError:
            token_index = -1
        if 0 <= token_index < len(market.outcomes):
            outcome = str(market.outcomes[token_index] or "").strip().lower()
            if outcome in {"yes", "true", "up", "above", "long"}:
                return "yes"
            if outcome in {"no", "false", "down", "below", "short"}:
                return "no"
        return "unknown"

    def _bucket_anchor_for_market(self, market: MarketCandidate) -> Optional[float]:
        raw = market.raw if isinstance(market.raw, dict) else {}
        for key in ("bucket_start", "bucketStart", "floor_strike", "floorStrike", "floor", "strike_floor"):
            value = raw.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        slug = str(market.slug or market.condition_id or "")
        match = re.search(r"-B(\d+(?:\.\d+)?)", slug)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                return None
        return None

    def _portfolio_candidate_decision(
        self,
        *,
        candidate: MarketCandidate,
        selected_markets: Sequence[MarketCandidate],
    ) -> Dict[str, Any]:
        cluster_id = self._event_id_for_market(candidate)
        bucket_anchor = self._bucket_anchor_for_market(candidate)
        if not selected_markets:
            return {
                "market_id": candidate.slug,
                "cluster_id": cluster_id,
                "bucket_anchor": bucket_anchor,
                "allowed": True,
                "reason": "first_market",
                "diversity_score": 1.0,
                "bucket_gap": None,
            }
        selected_clusters = {self._event_id_for_market(market) for market in selected_markets}
        if cluster_id not in selected_clusters:
            return {
                "market_id": candidate.slug,
                "cluster_id": cluster_id,
                "bucket_anchor": bucket_anchor,
                "allowed": True,
                "reason": "different_cluster",
                "diversity_score": 2.0,
                "bucket_gap": None,
            }
        same_cluster_markets = [
            market for market in selected_markets
            if self._event_id_for_market(market) == cluster_id
        ]
        if not same_cluster_markets:
            return {
                "market_id": candidate.slug,
                "cluster_id": cluster_id,
                "bucket_anchor": bucket_anchor,
                "allowed": True,
                "reason": "same_cluster_without_overlap",
                "diversity_score": 1.0,
                "bucket_gap": None,
            }
        gaps = []
        for market in same_cluster_markets:
            anchor = self._bucket_anchor_for_market(market)
            if anchor is None or bucket_anchor is None:
                continue
            gaps.append(abs(float(bucket_anchor) - float(anchor)))
        min_gap = min(gaps) if gaps else None
        if min_gap is None:
            return {
                "market_id": candidate.slug,
                "cluster_id": cluster_id,
                "bucket_anchor": bucket_anchor,
                "allowed": True,
                "reason": "same_cluster_without_bucket_signal",
                "diversity_score": 0.5,
                "bucket_gap": None,
            }
        if min_gap <= 100.0:
            return {
                "market_id": candidate.slug,
                "cluster_id": cluster_id,
                "bucket_anchor": bucket_anchor,
                "allowed": False,
                "reason": "same_cluster_adjacent_bucket_blocked",
                "diversity_score": -1.0,
                "bucket_gap": float(min_gap),
            }
        return {
            "market_id": candidate.slug,
            "cluster_id": cluster_id,
            "bucket_anchor": bucket_anchor,
            "allowed": True,
            "reason": "same_cluster_diverse_bucket",
            "diversity_score": min(1.5, 0.5 + float(min_gap) / 200.0),
            "bucket_gap": float(min_gap),
        }

    def _build_cluster_exposure_state(
        self,
        *,
        now_ms: int,
        portfolio_risk: Dict[str, Any],
    ) -> Dict[str, Any]:
        current_equity = float(portfolio_risk.get("current_equity") or 0.0)
        reference_equity = float(portfolio_risk.get("reference_equity") or 0.0)
        cluster_cap_notional = (
            current_equity * float(self.risk_manager.config.max_event_exposure_pct)
            if current_equity > 0.0 and float(self.risk_manager.config.max_event_exposure_pct) > 0.0
            else None
        )
        active_market_ids = {str(m.condition_id) for m in self.active_markets}
        clusters_by_id: Dict[str, Dict[str, Any]] = {}
        market_index: Dict[str, Dict[str, Any]] = {}

        for market in self._portfolio_markets():
            market_id = str(market.condition_id)
            cluster_id = self._event_id_for_market(market)
            time_to_expiry_ms = (
                max(0, int(market.end_ts_ms) - int(now_ms))
                if market.end_ts_ms is not None
                else None
            )
            cluster = clusters_by_id.setdefault(
                cluster_id,
                {
                    "cluster_id": cluster_id,
                    "event_id": cluster_id,
                    "market_ids": [],
                    "active_market_ids": [],
                    "token_ids": [],
                    "market_count": 0,
                    "active_market_count": 0,
                    "yes_exposure_notional": 0.0,
                    "no_exposure_notional": 0.0,
                    "unknown_exposure_notional": 0.0,
                    "stale_exposure_notional": 0.0,
                    "stale_market_count": 0,
                    "has_stale_inventory": False,
                    "stale_dominant_inventory": False,
                    "maker_exit_failed": False,
                    "net_yes_exposure_notional": 0.0,
                    "gross_exposure": 0.0,
                    "unrealized_pnl": 0.0,
                    "time_to_expiry_ms": None,
                    "stop_open_window_ms": None,
                    "force_flat_window_ms": None,
                    "max_event_exposure_notional": cluster_cap_notional,
                    "remaining_event_exposure_notional": None,
                    "stale_after_ms": None,
                    "dominant_inventory_market_id": None,
                    "dominant_inventory_market_quality_score": None,
                    "dominant_inventory_market_unrealized_pnl": None,
                    "negative_dominant_inventory": False,
                    "negative_dominant_inventory_worsening": False,
                    "explicit_forced_reduction": bool(cluster_id in self._forced_flat_events),
                    "markets": [],
                },
            )
            market_notional = 0.0
            market_unrealized = 0.0
            market_yes_notional = 0.0
            market_no_notional = 0.0
            market_unknown_notional = 0.0
            market_stale_notional = 0.0
            market_has_stale_inventory = False
            market_maker_exit_failed = False
            market_duration_ms = self._market_duration_ms(market)
            market_yes_quality_score = self._market_side_quality_score(market, "yes")
            market_no_quality_score = self._market_side_quality_score(market, "no")
            for token_id in market.token_ids:
                position = self.position_tracker.get_position(token_id)
                if position.size <= 0:
                    continue
                mark = self._token_mark_price(token_id, position.avg_price)
                token_notional = float(position.size) * float(mark)
                outcome_side = self._token_outcome_side(market, token_id)
                inventory_state = self.risk_manager.token_inventory_state(
                    token_id=str(token_id),
                    now_ms=now_ms,
                    market_duration_ms=market_duration_ms,
                )
                market_notional += token_notional
                if position.avg_price > 0:
                    market_unrealized += (float(mark) - float(position.avg_price)) * float(position.size)
                if bool(inventory_state.get("stale")):
                    market_has_stale_inventory = True
                    market_stale_notional += token_notional
                if bool(inventory_state.get("maker_exit_failed")):
                    market_maker_exit_failed = True
                if outcome_side == "yes":
                    market_yes_notional += token_notional
                elif outcome_side == "no":
                    market_no_notional += token_notional
                else:
                    market_unknown_notional += token_notional
                if str(token_id) not in cluster["token_ids"]:
                    cluster["token_ids"].append(str(token_id))
            cluster["yes_exposure_notional"] += market_yes_notional
            cluster["no_exposure_notional"] += market_no_notional
            cluster["unknown_exposure_notional"] += market_unknown_notional
            cluster["stale_exposure_notional"] += market_stale_notional
            cluster["gross_exposure"] += market_notional
            cluster["unrealized_pnl"] += market_unrealized
            if market_has_stale_inventory:
                cluster["stale_market_count"] += 1
                cluster["has_stale_inventory"] = True
            if market_maker_exit_failed:
                cluster["maker_exit_failed"] = True
            if market.slug not in cluster["market_ids"]:
                cluster["market_ids"].append(market.slug)
            if market_id in active_market_ids and market.slug not in cluster["active_market_ids"]:
                cluster["active_market_ids"].append(market.slug)
            if str(market.slug) in self._forced_flat_markets:
                cluster["explicit_forced_reduction"] = True
            if time_to_expiry_ms is not None:
                prior_time = cluster.get("time_to_expiry_ms")
                cluster["time_to_expiry_ms"] = (
                    time_to_expiry_ms
                    if prior_time is None
                    else min(int(prior_time), int(time_to_expiry_ms))
                )
            stop_open_window_ms = self.risk_manager.expiry_window_ms(
                market_duration_ms,
                self.risk_manager.config.stop_open_before_expiry_secs,
            )
            force_flat_window_ms = self.risk_manager.expiry_window_ms(
                market_duration_ms,
                self.risk_manager.config.force_flat_before_expiry_secs,
            )
            prior_stop_open = cluster.get("stop_open_window_ms")
            cluster["stop_open_window_ms"] = (
                int(stop_open_window_ms)
                if prior_stop_open is None
                else min(int(prior_stop_open), int(stop_open_window_ms))
            )
            prior_force_flat = cluster.get("force_flat_window_ms")
            cluster["force_flat_window_ms"] = (
                int(force_flat_window_ms)
                if prior_force_flat is None
                else min(int(prior_force_flat), int(force_flat_window_ms))
            )
            cluster["markets"].append(
                {
                    "market_id": market.slug,
                    "condition_id": market_id,
                    "token_ids": list(market.token_ids),
                    "market_position_notional": market_notional,
                    "market_unrealized_pnl": market_unrealized,
                    "yes_exposure_notional": market_yes_notional,
                    "no_exposure_notional": market_no_notional,
                    "unknown_exposure_notional": market_unknown_notional,
                    "stale_exposure_notional": market_stale_notional,
                    "has_stale_inventory": market_has_stale_inventory,
                    "maker_exit_failed": market_maker_exit_failed,
                    "market_duration_ms": market_duration_ms,
                    "yes_quality_score": market_yes_quality_score,
                    "no_quality_score": market_no_quality_score,
                    "time_to_expiry_ms": time_to_expiry_ms,
                    "active": market_id in active_market_ids,
                }
            )
            market_index[market_id] = {
                "cluster_id": cluster_id,
                "event_id": cluster_id,
                "market_position_notional": market_notional,
                "market_unrealized_pnl": market_unrealized,
                "market_duration_ms": market_duration_ms,
                "time_to_expiry_ms": time_to_expiry_ms,
                "has_stale_inventory": market_has_stale_inventory,
                "maker_exit_failed": market_maker_exit_failed,
            }

        clusters: List[Dict[str, Any]] = []
        for cluster_id in sorted(clusters_by_id):
            cluster = clusters_by_id[cluster_id]
            cluster["market_count"] = len(cluster["market_ids"])
            cluster["active_market_count"] = len(cluster["active_market_ids"])
            cluster["net_yes_exposure_notional"] = (
                float(cluster["yes_exposure_notional"]) - float(cluster["no_exposure_notional"])
            )
            if cluster_cap_notional is not None:
                cluster["remaining_event_exposure_notional"] = max(
                    0.0,
                    float(cluster_cap_notional) - float(cluster["gross_exposure"]),
                )
            if cluster.get("markets"):
                dominant_side = "yes" if float(cluster["yes_exposure_notional"]) >= float(cluster["no_exposure_notional"]) else "no"
                dominant_market = max(
                    list(cluster["markets"]),
                    key=lambda item: float(item.get(f"{dominant_side}_exposure_notional") or 0.0),
                )
                cluster["dominant_inventory_market_id"] = dominant_market.get("market_id")
                cluster["dominant_inventory_market_quality_score"] = dominant_market.get(f"{dominant_side}_quality_score")
                cluster["dominant_inventory_market_unrealized_pnl"] = dominant_market.get("market_unrealized_pnl")
                cluster["stale_dominant_inventory"] = bool(
                    dominant_market.get("has_stale_inventory")
                    and float(dominant_market.get(f"{dominant_side}_exposure_notional") or 0.0) > 0.0
                )
                cluster["negative_dominant_inventory"] = bool(
                    float(dominant_market.get(f"{dominant_side}_exposure_notional") or 0.0) > 0.0
                    and float(dominant_market.get("market_unrealized_pnl") or 0.0) < 0.0
                )
                cluster["maker_exit_failed"] = bool(
                    cluster.get("maker_exit_failed")
                    or (
                        dominant_market.get("maker_exit_failed")
                        and float(dominant_market.get(f"{dominant_side}_exposure_notional") or 0.0) > 0.0
                    )
                )
                cluster["stale_after_ms"] = dominant_market.get("market_duration_ms")
                if cluster["stale_after_ms"] not in (None, ""):
                    cluster["stale_after_ms"] = self.risk_manager.stale_duration_ms(
                        int(cluster["stale_after_ms"])
                    )
            clusters.append(cluster)

        return {
            "payload": {
                "clusters": clusters,
                "cluster_count": len(clusters),
                "active_cluster_count": sum(1 for cluster in clusters if int(cluster.get("active_market_count") or 0) > 0),
                "gross_exposure": sum(float(cluster.get("gross_exposure") or 0.0) for cluster in clusters),
                "unrealized_pnl": sum(float(cluster.get("unrealized_pnl") or 0.0) for cluster in clusters),
                "current_equity": current_equity,
                "reference_equity": reference_equity,
            },
            "market_index": market_index,
            "clusters_by_id": clusters_by_id,
        }

    def _market_side_quality_score(self, market: MarketCandidate, side: str) -> Optional[float]:
        token_id = None
        for candidate_token_id in market.token_ids:
            if self._token_outcome_side(market, candidate_token_id) == side:
                token_id = candidate_token_id
                break
        if token_id is None:
            return None
        book = self.book_manager.get_book(str(token_id))
        if book is None or book.best_bid is None or book.best_ask is None:
            return None
        spread = max(0.0, float(book.best_ask) - float(book.best_bid))
        depth = float(book.best_bid_size or 0.0) + float(book.best_ask_size or 0.0)
        if spread <= 0.0 or depth <= 0.0:
            return None
        return depth / max(spread, 0.01)

    def _enrich_cluster_exposure_with_policy(
        self,
        *,
        cluster_exposure: Dict[str, Any],
        cluster_hedge: Dict[str, Any],
        observe_pause_active: bool,
    ) -> Dict[str, Any]:
        payload = dict(cluster_exposure or {})
        clusters = [dict(cluster) for cluster in list(payload.get("clusters") or [])]
        hedge_by_cluster = {
            str(cluster.get("cluster_id") or ""): dict(cluster)
            for cluster in list((cluster_hedge or {}).get("clusters") or [])
            if str(cluster.get("cluster_id") or "")
        }
        for cluster in clusters:
            cluster_id = str(cluster.get("cluster_id") or "")
            hedge_cluster = hedge_by_cluster.get(cluster_id, {})
            cluster["control_state"] = (
                "OBSERVE_PAUSE"
                if observe_pause_active
                else str(hedge_cluster.get("control_state") or cluster.get("control_state") or "NORMAL")
            )
            cluster["hedge_action"] = str(hedge_cluster.get("action") or cluster.get("hedge_action") or "NONE")
            cluster["hedge_action_reason"] = hedge_cluster.get("action_reason") or cluster.get("hedge_action_reason")
            cluster["hedge_ratio"] = hedge_cluster.get("hedge_ratio") if hedge_cluster.get("hedge_ratio") is not None else cluster.get("hedge_ratio")
            cluster["hedge_target_market"] = hedge_cluster.get("hedge_market_id") or cluster.get("hedge_target_market")
            cluster["hedge_target_token"] = hedge_cluster.get("hedge_target_token_id") or cluster.get("hedge_target_token")
            cluster["hedge_target_side"] = hedge_cluster.get("hedge_target_side") or cluster.get("hedge_target_side")
            cluster["hedge_covariance"] = hedge_cluster.get("hedge_covariance") if hedge_cluster.get("hedge_covariance") is not None else cluster.get("hedge_covariance")
            cluster["hedge_correlation"] = hedge_cluster.get("hedge_correlation") if hedge_cluster.get("hedge_correlation") is not None else cluster.get("hedge_correlation")
            cluster["hedge_beta_raw"] = hedge_cluster.get("hedge_beta_raw") if hedge_cluster.get("hedge_beta_raw") is not None else cluster.get("hedge_beta_raw")
            cluster["hedge_beta"] = hedge_cluster.get("hedge_beta") if hedge_cluster.get("hedge_beta") is not None else cluster.get("hedge_beta")
            cluster["hedge_beta_shrunk"] = hedge_cluster.get("hedge_beta_shrunk") if hedge_cluster.get("hedge_beta_shrunk") is not None else cluster.get("hedge_beta_shrunk")
            cluster["hedge_beta_clipped"] = hedge_cluster.get("hedge_beta_clipped") if hedge_cluster.get("hedge_beta_clipped") is not None else cluster.get("hedge_beta_clipped")
            cluster["hedge_covariance_sample_count"] = hedge_cluster.get("hedge_covariance_sample_count") if hedge_cluster.get("hedge_covariance_sample_count") is not None else cluster.get("hedge_covariance_sample_count")
            cluster["hedge_covariance_state"] = hedge_cluster.get("hedge_covariance_state") or cluster.get("hedge_covariance_state")
            cluster["hedge_covariance_confidence"] = hedge_cluster.get("hedge_covariance_confidence") or cluster.get("hedge_covariance_confidence")
            cluster["hedge_execution_quality_score"] = hedge_cluster.get("hedge_execution_quality_score") if hedge_cluster.get("hedge_execution_quality_score") is not None else cluster.get("hedge_execution_quality_score")
            cluster["hedge_permission_state"] = hedge_cluster.get("hedge_permission_state") or cluster.get("hedge_permission_state")
            cluster["hedge_rejection_reason"] = hedge_cluster.get("hedge_rejection_reason") or cluster.get("hedge_rejection_reason")
            cluster["hedge_model_state"] = hedge_cluster.get("hedge_model_state") or cluster.get("hedge_model_state")
            cluster["hedge_realized_improvement_state"] = hedge_cluster.get("hedge_realized_improvement_state") or cluster.get("hedge_realized_improvement_state")
            cluster["hedge_success_window_ms"] = hedge_cluster.get("hedge_success_window_ms") or cluster.get("hedge_success_window_ms")
            cluster["hedge_failed_cooldown_until_ms"] = hedge_cluster.get("hedge_failed_cooldown_until_ms") or cluster.get("hedge_failed_cooldown_until_ms")
            suppress_reason = None
            control_state = str(cluster["control_state"] or "")
            hedge_action = str(cluster["hedge_action"] or "")
            if control_state == "UNWIND_ONLY":
                suppress_reason = (
                    "correlated_market_stale"
                    if int(cluster.get("stale_market_count") or 0) > 0 and int(cluster.get("active_market_count") or 0) > 1
                    else "cluster_unwind_only"
                )
            elif (
                int(cluster.get("active_market_count") or 0) > 1
                and int(cluster.get("stale_market_count") or 0) > 0
                and bool(cluster.get("maker_exit_failed"))
                and hedge_action != "HEDGE"
            ):
                suppress_reason = "correlated_market_stale"
            cluster["new_entries_suppressed"] = bool(suppress_reason)
            cluster["new_entry_block_reason"] = suppress_reason
            market_action = cluster["hedge_action"]
            market_reason = cluster["hedge_action_reason"]
            market_ratio = cluster.get("hedge_ratio")
            market_target_market = cluster.get("hedge_target_market")
            market_target_token = cluster.get("hedge_target_token")
            market_target_side = cluster.get("hedge_target_side")
            market_covariance = cluster.get("hedge_covariance")
            market_correlation = cluster.get("hedge_correlation")
            market_beta_raw = cluster.get("hedge_beta_raw")
            market_beta = cluster.get("hedge_beta")
            market_beta_shrunk = cluster.get("hedge_beta_shrunk")
            market_beta_clipped = cluster.get("hedge_beta_clipped")
            market_covariance_sample_count = cluster.get("hedge_covariance_sample_count")
            market_covariance_state = cluster.get("hedge_covariance_state")
            market_covariance_confidence = cluster.get("hedge_covariance_confidence")
            market_execution_quality_score = cluster.get("hedge_execution_quality_score")
            market_permission_state = cluster.get("hedge_permission_state")
            market_rejection_reason = cluster.get("hedge_rejection_reason")
            market_model_state = cluster.get("hedge_model_state")
            market_realized_improvement_state = cluster.get("hedge_realized_improvement_state")
            for market in list(cluster.get("markets") or []):
                market["hedge_action"] = market.get("hedge_action") or market_action
                market["hedge_action_reason"] = market.get("hedge_action_reason") or market_reason
                market["hedge_ratio"] = market.get("hedge_ratio") if market.get("hedge_ratio") is not None else market_ratio
                market["hedge_target_market"] = market.get("hedge_target_market") or market_target_market
                market["hedge_target_token"] = market.get("hedge_target_token") or market_target_token
                market["hedge_target_side"] = market.get("hedge_target_side") or market_target_side
                market["hedge_covariance"] = market.get("hedge_covariance") if market.get("hedge_covariance") is not None else market_covariance
                market["hedge_correlation"] = market.get("hedge_correlation") if market.get("hedge_correlation") is not None else market_correlation
                market["hedge_beta_raw"] = market.get("hedge_beta_raw") if market.get("hedge_beta_raw") is not None else market_beta_raw
                market["hedge_beta"] = market.get("hedge_beta") if market.get("hedge_beta") is not None else market_beta
                market["hedge_beta_shrunk"] = market.get("hedge_beta_shrunk") if market.get("hedge_beta_shrunk") is not None else market_beta_shrunk
                market["hedge_beta_clipped"] = market.get("hedge_beta_clipped") if market.get("hedge_beta_clipped") is not None else market_beta_clipped
                market["hedge_covariance_sample_count"] = market.get("hedge_covariance_sample_count") if market.get("hedge_covariance_sample_count") is not None else market_covariance_sample_count
                market["hedge_covariance_state"] = market.get("hedge_covariance_state") or market_covariance_state
                market["hedge_covariance_confidence"] = market.get("hedge_covariance_confidence") or market_covariance_confidence
                market["hedge_execution_quality_score"] = market.get("hedge_execution_quality_score") if market.get("hedge_execution_quality_score") is not None else market_execution_quality_score
                market["hedge_permission_state"] = market.get("hedge_permission_state") or market_permission_state
                market["hedge_rejection_reason"] = market.get("hedge_rejection_reason") or market_rejection_reason
                market["hedge_model_state"] = market.get("hedge_model_state") or market_model_state
                market["hedge_realized_improvement_state"] = market.get("hedge_realized_improvement_state") or market_realized_improvement_state
                market["new_entries_suppressed"] = bool(cluster["new_entries_suppressed"])
                market["new_entry_block_reason"] = cluster["new_entry_block_reason"]
        payload["clusters"] = clusters
        return payload

    def _maybe_enter_observe_pause(self, now_ms: int) -> bool:
        if self._observe_pause_interval_ms <= 0 or self._observe_pause_duration_ms <= 0:
            return False
        if self._observe_pause_next_at_ms is None:
            self._observe_pause_next_at_ms = int(now_ms) + self._observe_pause_interval_ms
            return False
        if int(now_ms) < int(self._observe_pause_until_ms or 0):
            return True
        if int(now_ms) < int(self._observe_pause_next_at_ms):
            return False
        self._observe_pause_last_started_ms = int(now_ms)
        self._observe_pause_until_ms = int(now_ms) + self._observe_pause_duration_ms
        self._observe_pause_next_at_ms = self._observe_pause_until_ms + self._observe_pause_interval_ms
        self.cancel_all_quotes()
        return True

    def _market_duration_ms(self, market: MarketCandidate) -> int:
        raw = market.raw if isinstance(market.raw, dict) else {}
        open_ts_ms = None
        for key in ("open_time", "openTime", "open_date", "openDate"):
            value = raw.get(key)
            if value in (None, ""):
                continue
            try:
                parsed = float(value)
                open_ts_ms = int(parsed * 1000) if parsed < 1e12 else int(parsed)
                break
            except (TypeError, ValueError):
                continue
        if open_ts_ms is not None and market.end_ts_ms is not None and market.end_ts_ms > open_ts_ms:
            return int(market.end_ts_ms - open_ts_ms)

        slug = str(market.slug or market.condition_id or "").lower()
        match = re.search(r"-(\d+)([mh])(?:-|$)", slug)
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            if unit == "h":
                return int(value * 3600 * 1000)
            return int(value * 60 * 1000)
        # BTC/ETH/SOL Kalshi range markets are typically hourly buckets.
        return 3_600_000

    def _token_mark_price(self, token_id: str, avg_price: float = 0.5) -> float:
        book = self.book_manager.get_book(str(token_id))
        if book is not None and book.mid_price is not None:
            return float(book.mid_price)
        if book is not None and book.best_bid is not None and book.best_ask is not None:
            return (float(book.best_bid) + float(book.best_ask)) / 2.0
        return max(0.01, min(0.99, float(avg_price or 0.5)))

    def _set_risk_config_attr(self, key: str, value: Any) -> None:
        object.__setattr__(self.risk_manager.config, key, value)

    def apply_safe_risk_profile(self, profile_name: str, *, allocated_equity: Optional[float] = None) -> Dict[str, Any]:
        resolved = resolve_safe_risk_profile_name(allocated_equity or self._strategy_allocated_equity, profile_name)
        if resolved == "custom":
            self._safe_risk_profile = "custom"
            return {"safe_risk_profile": self._safe_risk_profile}
        profile = dict(SAFE_RISK_PROFILES.get(resolved) or {})
        if not profile:
            return {}
        if allocated_equity not in (None, ""):
            self._strategy_allocated_equity = float(allocated_equity)
        elif self._strategy_allocated_equity is None:
            self._strategy_allocated_equity = float(profile.get("allocated_equity") or 0.0)
        self._safe_risk_profile = resolved
        self._trade_size = float(profile["trade_size"])
        self._max_size = float(profile["max_size"])
        self._min_size = float(profile["min_size"])
        self._fallback_size = float(profile["fallback_size"])
        self._set_risk_config_attr("hard_position_cap", float(profile["hard_position_cap"]))
        self._set_risk_config_attr("per_event_loss_pct", float(profile["per_event_loss_pct"]))
        self._set_risk_config_attr("per_day_loss_pct", float(profile["per_day_loss_pct"]))
        self._set_risk_config_attr("strategy_allocated_equity", self._strategy_allocated_equity)
        return {
            "safe_risk_profile": self._safe_risk_profile,
            "strategy_allocated_equity": self._strategy_allocated_equity,
            "trade_size": self._trade_size,
            "max_size": self._max_size,
            "min_size": self._min_size,
            "fallback_size": self._fallback_size,
            "hard_position_cap": self.risk_manager.config.hard_position_cap,
            "per_event_loss_pct": self.risk_manager.config.per_event_loss_pct,
            "per_day_loss_pct": self.risk_manager.config.per_day_loss_pct,
        }

    def _portfolio_risk_snapshot(self, *, usdc_balance: Optional[float]) -> Dict[str, Any]:
        realized_net = 0.0
        if hasattr(self.broker, "stats"):
            realized_net = float(self.broker.stats().get("realized_net_pnl") or 0.0)
        gross_exposure = 0.0
        unrealized_pnl = 0.0
        active_positions = 0
        for token_id, position in self.position_tracker.snapshot().items():
            if position.size <= 0:
                continue
            active_positions += 1
            mark = self._token_mark_price(token_id, position.avg_price)
            gross_exposure += float(position.size) * float(mark)
            if position.avg_price > 0:
                unrealized_pnl += (float(mark) - float(position.avg_price)) * float(position.size)
        portfolio_total_pnl = realized_net + unrealized_pnl
        balance_base = float(usdc_balance or 0.0)
        risk_base = balance_base
        if self._use_allocated_equity_for_risk and self._strategy_allocated_equity is not None:
            risk_base = max(0.0, float(self._strategy_allocated_equity))
        current_equity = max(0.0, risk_base + portfolio_total_pnl) if risk_base > 0.0 else max(0.0, portfolio_total_pnl)
        reference_equity = max(risk_base, current_equity)
        return {
            "gross_exposure": gross_exposure,
            "unrealized_pnl": unrealized_pnl,
            "portfolio_total_pnl": portfolio_total_pnl,
            "current_equity": current_equity,
            "reference_equity": reference_equity,
            "active_positions": active_positions,
        }

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
        self._remember_markets(candidates)
        self._last_selection_report = dict(getattr(self.market_selector, "last_selection_report", {}) or {})
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
        portfolio_candidate_decisions: List[Dict[str, Any]] = []
        for market in result_markets:
            portfolio_candidate_decisions.append(
                {
                    "market_id": market.slug,
                    "cluster_id": self._event_id_for_market(market),
                    "bucket_anchor": self._bucket_anchor_for_market(market),
                    "allowed": True,
                    "reason": "dwell_protected",
                    "diversity_score": 1.0,
                    "bucket_gap": None,
                }
            )
        # Fill remaining slots from top candidates
        remaining = max(0, self._max_active_markets - len(result_markets))
        for c in candidates:
            if remaining <= 0:
                break
            if c.condition_id in result_ids:
                continue
            decision = self._portfolio_candidate_decision(candidate=c, selected_markets=result_markets)
            portfolio_candidate_decisions.append(decision)
            if not decision["allowed"]:
                continue
            result_markets.append(c)
            result_ids.add(c.condition_id)
            remaining -= 1
        changed = result_ids != old_ids
        self.active_markets = result_markets
        selection_report = dict(self._last_selection_report)
        selection_report["portfolio_selection"] = {
            "mode": self.mode,
            "max_active_markets": self._max_active_markets,
            "launch_scope": "single_market" if self._max_active_markets == 1 else "multi_market",
            "selected_market_ids": [market.slug for market in result_markets],
            "selected_cluster_ids": sorted({self._event_id_for_market(market) for market in result_markets}),
            "candidate_decisions": portfolio_candidate_decisions,
        }
        self._last_selection_report = selection_report
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

    def set_trading_enabled(self, enabled: bool, *, reason: str = "") -> Dict[str, Any]:
        self._trading_enabled = bool(enabled)
        if enabled:
            self._flatten_only_mode = False
            self._halt_after_flatten = False
            self._risk_warning_triggered = False
        result = {"trading_enabled": self._trading_enabled, "reason": str(reason or "")}
        self._last_control_command = {"action": "resume_trading" if enabled else "pause_trading", **result}
        return result

    def set_kill_switch(self, enabled: bool, *, reason: str = "") -> Dict[str, Any]:
        self._kill_switch = bool(enabled)
        if enabled:
            self._trading_enabled = False
        else:
            self._flatten_only_mode = False
            self._halt_after_flatten = False
            self._risk_warning_triggered = False
        result = {
            "kill_switch_enabled": self._kill_switch,
            "trading_enabled": self._trading_enabled,
            "reason": str(reason or ""),
        }
        self._last_control_command = {"action": "kill_switch_on" if enabled else "kill_switch_off", **result}
        return result

    def cancel_all_quotes(self) -> Dict[str, Any]:
        if self.broker is None or not hasattr(self.broker, "cancel_all"):
            result = {"success": False, "reason": "broker_cancel_all_unavailable"}
            self._last_control_command = {"action": "cancel_all_quotes", **result}
            return result
        response = self.broker.cancel_all()
        payload = dict(response.payload or {})
        result = {"success": bool(response.success), "canceled": payload.get("canceled") or [], "error": response.error}
        self._last_control_command = {"action": "cancel_all_quotes", **result}
        return result

    def request_flatten_event(self, event_id: str) -> Dict[str, Any]:
        value = str(event_id or "")
        if not value:
            return {"success": False, "reason": "missing_event_id"}
        self._forced_flat_events.add(value)
        result = {"success": True, "event_id": value}
        self._last_control_command = {"action": "flatten_event_inventory", **result}
        return result

    def request_flatten_market(self, market_id: str) -> Dict[str, Any]:
        value = str(market_id or "")
        if not value:
            return {"success": False, "reason": "missing_market_id"}
        self._forced_flat_markets.add(value)
        result = {"success": True, "market_id": value}
        self._last_control_command = {"action": "flatten_market_inventory", **result}
        return result

    def restore_safe_profile(self) -> Dict[str, Any]:
        return self.apply_config_patch(dict(self._safe_profile_defaults))

    def apply_config_patch(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        patch = dict(patch or {})
        applied: Dict[str, Any] = {}

        def _to_float(value: Any) -> Optional[float]:
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def _to_bool(value: Any) -> Optional[bool]:
            if value in (None, ""):
                return None
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
            return None

        def _clamp_probability(value: float) -> float:
            return min(1.0, max(0.0, float(value)))

        boundary_keys = {"boundary_no_new_risk_min_price", "boundary_no_new_risk_max_price"}
        boundary_min_value = (
            _to_float(patch.get("boundary_no_new_risk_min_price"))
            if "boundary_no_new_risk_min_price" in patch
            else None
        )
        boundary_max_value = (
            _to_float(patch.get("boundary_no_new_risk_max_price"))
            if "boundary_no_new_risk_max_price" in patch
            else None
        )
        if boundary_min_value is not None or boundary_max_value is not None:
            candidate_min = (
                self._boundary_no_new_risk_min_price
                if boundary_min_value is None
                else _clamp_probability(boundary_min_value)
            )
            candidate_max = (
                self._boundary_no_new_risk_max_price
                if boundary_max_value is None
                else _clamp_probability(boundary_max_value)
            )
            if candidate_min < candidate_max:
                self._boundary_no_new_risk_min_price = candidate_min
                self._boundary_no_new_risk_max_price = candidate_max
                if boundary_min_value is not None:
                    applied["boundary_no_new_risk_min_price"] = self._boundary_no_new_risk_min_price
                if boundary_max_value is not None:
                    applied["boundary_no_new_risk_max_price"] = self._boundary_no_new_risk_max_price

        for key, raw_value in patch.items():
            if key in boundary_keys:
                continue
            value = _to_float(raw_value) if key != "min_order_size" else (_to_float(raw_value) if raw_value not in ("", None) else None)
            if key == "safe_risk_profile":
                applied.update(self.apply_safe_risk_profile(str(raw_value or ""), allocated_equity=self._strategy_allocated_equity))
            elif key == "strategy_allocated_equity" and value is not None:
                self._strategy_allocated_equity = max(0.0, float(value))
                self._set_risk_config_attr("strategy_allocated_equity", self._strategy_allocated_equity)
                applied[key] = self._strategy_allocated_equity
            elif key == "use_allocated_equity_for_risk":
                bool_value = _to_bool(raw_value)
                if bool_value is not None:
                    self._use_allocated_equity_for_risk = bool_value
                    self._set_risk_config_attr("use_allocated_equity_for_risk", self._use_allocated_equity_for_risk)
                    applied[key] = self._use_allocated_equity_for_risk
            elif key == "risk_based_share_sizing":
                bool_value = _to_bool(raw_value)
                if bool_value is not None:
                    self._risk_based_share_sizing = bool_value
                    self._set_risk_config_attr("risk_based_share_sizing", self._risk_based_share_sizing)
                    applied[key] = self._risk_based_share_sizing
            elif key == "quote_spread_multiplier" and value is not None:
                self._quote_spread_multiplier = max(0.1, float(value))
                applied[key] = self._quote_spread_multiplier
            elif key == "boundary_guard_mode":
                self._boundary_guard_mode = self._normalize_boundary_guard_mode(raw_value)
                applied[key] = self._boundary_guard_mode
            elif key == "boundary_adverse_selection_threshold" and value is not None:
                self._boundary_adverse_selection_threshold = max(0.01, float(value))
                applied[key] = self._boundary_adverse_selection_threshold
            elif key == "boundary_exit_cost_multiplier" and value is not None:
                self._boundary_exit_cost_multiplier = max(1.0, float(value))
                applied[key] = self._boundary_exit_cost_multiplier
            elif key == "skew_threshold_fraction" and value is not None:
                self._skew_threshold_fraction = max(0.0, float(value))
                self._hedge_engine._skew_threshold_fraction = self._skew_threshold_fraction
                applied[key] = self._skew_threshold_fraction
            elif key == "hedge_threshold_fraction" and value is not None:
                self._hedge_threshold_fraction = max(0.0, float(value))
                self._hedge_engine._hedge_threshold_fraction = self._hedge_threshold_fraction
                applied[key] = self._hedge_threshold_fraction
            elif key == "hedge_covariance_enabled":
                bool_value = _to_bool(raw_value)
                if bool_value is not None:
                    self._hedge_covariance_enabled = bool_value
                    self._hedge_engine._hedge_covariance_enabled = self._hedge_covariance_enabled
                    applied[key] = self._hedge_covariance_enabled
            elif key == "hedge_covariance_window_secs" and value is not None:
                self._hedge_covariance_window_secs = max(1.0, float(value))
                self._hedge_engine._hedge_covariance_window_ms = int(self._hedge_covariance_window_secs * 1000.0)
                applied[key] = self._hedge_covariance_window_secs
            elif key == "hedge_covariance_min_samples" and value is not None:
                self._hedge_covariance_min_samples = max(2, int(value))
                self._hedge_engine._hedge_covariance_min_samples = self._hedge_covariance_min_samples
                applied[key] = self._hedge_covariance_min_samples
            elif key == "hedge_covariance_min_correlation" and value is not None:
                self._hedge_covariance_min_correlation = max(0.0, float(value))
                self._hedge_engine._hedge_covariance_min_correlation = self._hedge_covariance_min_correlation
                applied[key] = self._hedge_covariance_min_correlation
            elif key == "hedge_covariance_min_abs_beta" and value is not None:
                self._hedge_covariance_min_abs_beta = max(0.0, float(value))
                self._hedge_engine._hedge_covariance_min_abs_beta = self._hedge_covariance_min_abs_beta
                applied[key] = self._hedge_covariance_min_abs_beta
            elif key == "hedge_covariance_beta_clip" and value is not None:
                self._hedge_covariance_beta_clip = max(0.0, float(value))
                self._hedge_engine._hedge_covariance_beta_clip = self._hedge_covariance_beta_clip
                applied[key] = self._hedge_covariance_beta_clip
            elif key == "hedge_covariance_beta_shrinkage" and value is not None:
                self._hedge_covariance_beta_shrinkage = min(0.95, max(0.0, float(value)))
                self._hedge_engine._hedge_covariance_beta_shrinkage = self._hedge_covariance_beta_shrinkage
                applied[key] = self._hedge_covariance_beta_shrinkage
            elif key == "hedge_covariance_max_sample_age_ms" and value is not None:
                self._hedge_covariance_max_sample_age_ms = max(0, int(value))
                self._hedge_engine._hedge_covariance_max_sample_age_ms = self._hedge_covariance_max_sample_age_ms
                applied[key] = self._hedge_covariance_max_sample_age_ms
            elif key == "hedge_covariance_max_update_gap_ms" and value is not None:
                self._hedge_covariance_max_update_gap_ms = max(0, int(value))
                self._hedge_engine._hedge_covariance_max_update_gap_ms = self._hedge_covariance_max_update_gap_ms
                applied[key] = self._hedge_covariance_max_update_gap_ms
            elif key == "hedge_covariance_boundary_buffer" and value is not None:
                self._hedge_covariance_boundary_buffer = min(0.49, max(0.0, float(value)))
                self._hedge_engine._hedge_covariance_boundary_buffer = self._hedge_covariance_boundary_buffer
                applied[key] = self._hedge_covariance_boundary_buffer
            elif key == "hedge_covariance_boundary_max_fraction" and value is not None:
                self._hedge_covariance_boundary_max_fraction = min(1.0, max(0.0, float(value)))
                self._hedge_engine._hedge_covariance_boundary_max_fraction = self._hedge_covariance_boundary_max_fraction
                applied[key] = self._hedge_covariance_boundary_max_fraction
            elif key == "hedge_covariance_strong_correlation" and value is not None:
                self._hedge_covariance_strong_correlation = max(self._hedge_covariance_min_correlation, float(value))
                self._hedge_engine._hedge_covariance_strong_correlation = self._hedge_covariance_strong_correlation
                applied[key] = self._hedge_covariance_strong_correlation
            elif key == "hedge_covariance_strong_min_samples" and value is not None:
                self._hedge_covariance_strong_min_samples = max(self._hedge_covariance_min_samples, int(value))
                self._hedge_engine._hedge_covariance_strong_min_samples = self._hedge_covariance_strong_min_samples
                applied[key] = self._hedge_covariance_strong_min_samples
            elif key == "hedge_covariance_stability_ratio_max" and value is not None:
                self._hedge_covariance_stability_ratio_max = max(1.0, float(value))
                self._hedge_engine._hedge_covariance_stability_ratio_max = self._hedge_covariance_stability_ratio_max
                applied[key] = self._hedge_covariance_stability_ratio_max
            elif key == "hedge_covariance_gate_required":
                bool_value = _to_bool(raw_value)
                if bool_value is not None:
                    self._hedge_covariance_gate_required = bool_value
                    self._hedge_engine._hedge_covariance_gate_required = self._hedge_covariance_gate_required
                    applied[key] = self._hedge_covariance_gate_required
            elif key == "hedge_requires_stale_inventory":
                bool_value = _to_bool(raw_value)
                if bool_value is not None:
                    self._hedge_requires_stale_inventory = bool_value
                    self._hedge_engine._hedge_requires_stale_inventory = self._hedge_requires_stale_inventory
                    applied[key] = self._hedge_requires_stale_inventory
            elif key == "hedge_quality_must_beat_inventory_market":
                bool_value = _to_bool(raw_value)
                if bool_value is not None:
                    self._hedge_quality_must_beat_inventory_market = bool_value
                    self._hedge_engine._hedge_quality_must_beat_inventory_market = self._hedge_quality_must_beat_inventory_market
                    applied[key] = self._hedge_quality_must_beat_inventory_market
            elif key == "hedge_min_quality_score" and value is not None:
                self._hedge_min_quality_score = max(0.0, float(value))
                self._hedge_engine._min_quality_score = self._hedge_min_quality_score
                applied[key] = self._hedge_min_quality_score
            elif key == "hedge_max_temp_gross_increase_fraction" and value is not None:
                self._hedge_max_temp_gross_increase_fraction = max(0.0, float(value))
                self._hedge_engine._hedge_max_temp_gross_increase_fraction = self._hedge_max_temp_gross_increase_fraction
                applied[key] = self._hedge_max_temp_gross_increase_fraction
            elif key == "hedge_failure_cooldown_scale" and value is not None:
                self._hedge_failure_cooldown_scale = max(0.1, float(value))
                self._hedge_engine._hedge_failure_cooldown_scale = self._hedge_failure_cooldown_scale
                applied[key] = self._hedge_failure_cooldown_scale
            elif key == "hedge_search_profile":
                profile = str(raw_value or "").strip().lower()
                if profile:
                    self._hedge_search_profile = profile
                    self._hedge_engine._hedge_search_profile = self._hedge_search_profile
                    applied[key] = self._hedge_search_profile
            elif key == "proof_only_bucket_distance" and value is not None:
                self._proof_only_bucket_distance = max(1, int(value))
                self._hedge_engine._proof_only_bucket_distance = self._proof_only_bucket_distance
                applied[key] = self._proof_only_bucket_distance
            elif key == "proof_only_expiry_slack_ms" and value is not None:
                self._proof_only_expiry_slack_ms = max(0, int(value))
                self._hedge_engine._proof_only_expiry_slack_ms = self._proof_only_expiry_slack_ms
                applied[key] = self._proof_only_expiry_slack_ms
            elif key == "observe_pause_interval_secs" and value is not None:
                self._observe_pause_interval_ms = int(max(0.0, float(value)) * 1000.0)
                applied[key] = self._observe_pause_interval_ms / 1000.0
            elif key == "observe_pause_duration_secs" and value is not None:
                self._observe_pause_duration_ms = int(max(0.0, float(value)) * 1000.0)
                applied[key] = self._observe_pause_duration_ms / 1000.0
            elif key == "negative_pnl_reduce_only_enabled":
                bool_value = _to_bool(raw_value)
                if bool_value is not None:
                    self._negative_pnl_reduce_only_enabled = bool_value
                    self._hedge_engine._negative_pnl_reduce_only_enabled = self._negative_pnl_reduce_only_enabled
                    self._set_risk_config_attr("negative_pnl_reduce_only_enabled", self._negative_pnl_reduce_only_enabled)
                    applied[key] = self._negative_pnl_reduce_only_enabled
            elif key == "negative_pnl_unwind_requires_worsening":
                bool_value = _to_bool(raw_value)
                if bool_value is not None:
                    self._negative_pnl_unwind_requires_worsening = bool_value
                    self._hedge_engine._negative_pnl_unwind_requires_worsening = self._negative_pnl_unwind_requires_worsening
                    self._set_risk_config_attr("negative_pnl_unwind_requires_worsening", self._negative_pnl_unwind_requires_worsening)
                    applied[key] = self._negative_pnl_unwind_requires_worsening
            elif key == "negative_pnl_unwind_requires_stale_or_worsening":
                bool_value = _to_bool(raw_value)
                if bool_value is not None:
                    self._negative_pnl_unwind_requires_stale_or_worsening = bool_value
                    self._hedge_engine._negative_pnl_unwind_requires_stale_or_worsening = self._negative_pnl_unwind_requires_stale_or_worsening
                    self._set_risk_config_attr("negative_pnl_unwind_requires_stale_or_worsening", self._negative_pnl_unwind_requires_stale_or_worsening)
                    applied[key] = self._negative_pnl_unwind_requires_stale_or_worsening
            elif key == "trade_size" and value is not None:
                self._trade_size = max(0.0, float(value))
                applied[key] = self._trade_size
            elif key == "max_size" and value is not None:
                self._max_size = max(0.0, float(value))
                applied[key] = self._max_size
            elif key == "min_size" and value is not None:
                self._min_size = max(0.0, float(value))
                applied[key] = self._min_size
            elif key == "fallback_size" and value is not None:
                self._fallback_size = max(0.0, float(value))
                applied[key] = self._fallback_size
            elif key == "min_order_size":
                self._min_order_size_override = None if value is None else max(0.0, float(value))
                applied[key] = self._min_order_size_override
            elif key == "within_pct" and value is not None:
                self._within_pct = max(0.0, float(value))
                applied[key] = self._within_pct
            elif key == "market_dwell_secs" and value is not None:
                self._market_dwell_ms = int(max(0.0, float(value)) * 1000.0)
                applied[key] = self._market_dwell_ms / 1000.0
            elif key == "post_fill_reentry_cooldown_secs" and value is not None:
                self.main_loop._post_fill_reentry_cooldown_ms = int(max(0.0, float(value)) * 1000.0)
                applied[key] = self.main_loop._post_fill_reentry_cooldown_ms / 1000.0
            elif key == "hard_position_cap" and value is not None:
                self._set_risk_config_attr("hard_position_cap", max(0.0, float(value)))
                applied[key] = self.risk_manager.config.hard_position_cap
            elif hasattr(self.risk_manager.config, key) and value is not None:
                self._set_risk_config_attr(key, float(value))
                applied[key] = getattr(self.risk_manager.config, key)

        self._last_control_command = {"action": "apply_config_patch", "applied": applied}
        return applied

    def control_state(self) -> Dict[str, Any]:
        reference_now_ms = int(self._last_cycle_now_ms or time.time() * 1000)
        return {
            "trading_enabled": bool(self._trading_enabled),
            "kill_switch_enabled": bool(self._kill_switch),
            "flatten_only_mode": bool(self._flatten_only_mode),
            "halt_after_flatten": bool(self._halt_after_flatten),
            "risk_warning_triggered": bool(self._risk_warning_triggered),
            "quote_spread_multiplier": float(self._quote_spread_multiplier),
            "boundary_no_new_risk_min_price": float(self._boundary_no_new_risk_min_price),
            "boundary_no_new_risk_max_price": float(self._boundary_no_new_risk_max_price),
            "boundary_guard_mode": self._boundary_guard_mode,
            "boundary_adverse_selection_threshold": float(self._boundary_adverse_selection_threshold),
            "boundary_exit_cost_multiplier": float(self._boundary_exit_cost_multiplier),
            "strategy_allocated_equity": self._strategy_allocated_equity,
            "use_allocated_equity_for_risk": bool(self._use_allocated_equity_for_risk),
            "risk_based_share_sizing": bool(self._risk_based_share_sizing),
            "safe_risk_profile": self._safe_risk_profile,
            "skew_threshold_fraction": self._skew_threshold_fraction,
            "hedge_threshold_fraction": self._hedge_threshold_fraction,
            "hedge_requires_stale_inventory": bool(self._hedge_requires_stale_inventory),
            "hedge_quality_must_beat_inventory_market": bool(self._hedge_quality_must_beat_inventory_market),
            "hedge_min_quality_score": float(self._hedge_min_quality_score),
            "hedge_max_temp_gross_increase_fraction": float(self._hedge_max_temp_gross_increase_fraction),
            "hedge_failure_cooldown_scale": float(self._hedge_failure_cooldown_scale),
            "hedge_search_profile": self._hedge_search_profile,
            "proof_only_bucket_distance": int(self._proof_only_bucket_distance),
            "proof_only_expiry_slack_ms": int(self._proof_only_expiry_slack_ms),
            "hedge_covariance_enabled": bool(self._hedge_covariance_enabled),
            "hedge_covariance_window_secs": float(self._hedge_covariance_window_secs),
            "hedge_covariance_min_samples": int(self._hedge_covariance_min_samples),
            "hedge_covariance_min_correlation": float(self._hedge_covariance_min_correlation),
            "hedge_covariance_min_abs_beta": float(self._hedge_covariance_min_abs_beta),
            "hedge_covariance_beta_clip": float(self._hedge_covariance_beta_clip),
            "hedge_covariance_beta_shrinkage": float(self._hedge_covariance_beta_shrinkage),
            "hedge_covariance_max_sample_age_ms": int(self._hedge_covariance_max_sample_age_ms),
            "hedge_covariance_max_update_gap_ms": int(self._hedge_covariance_max_update_gap_ms),
            "hedge_covariance_boundary_buffer": float(self._hedge_covariance_boundary_buffer),
            "hedge_covariance_boundary_max_fraction": float(self._hedge_covariance_boundary_max_fraction),
            "hedge_covariance_strong_correlation": float(self._hedge_covariance_strong_correlation),
            "hedge_covariance_strong_min_samples": int(self._hedge_covariance_strong_min_samples),
            "hedge_covariance_stability_ratio_max": float(self._hedge_covariance_stability_ratio_max),
            "hedge_covariance_gate_required": bool(self._hedge_covariance_gate_required),
            "observe_pause_interval_secs": self._observe_pause_interval_ms / 1000.0,
            "observe_pause_duration_secs": self._observe_pause_duration_ms / 1000.0,
            "negative_pnl_reduce_only_enabled": bool(self._negative_pnl_reduce_only_enabled),
            "negative_pnl_unwind_requires_worsening": bool(self._negative_pnl_unwind_requires_worsening),
            "negative_pnl_unwind_requires_stale_or_worsening": bool(self._negative_pnl_unwind_requires_stale_or_worsening),
            "observe_pause_active": bool(self._observe_pause_until_ms and self._observe_pause_until_ms > reference_now_ms),
            "observe_pause_until_ms": int(self._observe_pause_until_ms or 0),
            "observe_pause_next_at_ms": int(self._observe_pause_next_at_ms or 0),
            "forced_flat_events": sorted(self._forced_flat_events),
            "forced_flat_markets": sorted(self._forced_flat_markets),
            "last_control_command": dict(self._last_control_command),
        }

    async def run_cycles(
        self,
        *,
        now_ms: int,
        usdc_balance: Optional[float] = None,
        three_hour_volatility: float = 0.0,
    ) -> List[MarketCycleResult]:
        """Run trading cycle for ALL active markets. Returns list of results."""
        self._last_cycle_now_ms = int(now_ms)
        if not self.active_markets:
            return []
        if self._kill_switch or not self._trading_enabled:
            return []
        if self._maybe_enter_observe_pause(int(now_ms)):
            return []
        portfolio_risk = self._portfolio_risk_snapshot(usdc_balance=usdc_balance)
        cluster_state = self._build_cluster_exposure_state(now_ms=now_ms, portfolio_risk=portfolio_risk)
        cluster_hedge_state = self._hedge_engine.plan(
            mode=self.mode,
            now_ms=now_ms,
            cluster_payload=cluster_state["payload"],
            active_markets=self.active_markets,
            hedge_search_markets=self.hedge_search_markets,
            book_manager=self.book_manager,
        )
        self._last_cluster_hedge_state = dict(cluster_hedge_state["payload"])
        cluster_state["payload"] = self._enrich_cluster_exposure_with_policy(
            cluster_exposure=cluster_state["payload"],
            cluster_hedge=self._last_cluster_hedge_state,
            observe_pause_active=False,
        )
        cluster_policy_by_id = {
            str(cluster.get("cluster_id") or ""): dict(cluster)
            for cluster in list(cluster_state["payload"].get("clusters") or [])
            if str(cluster.get("cluster_id") or "")
        }
        current_equity = float(portfolio_risk.get("current_equity") or 0.0)
        reference_equity = float(portfolio_risk.get("reference_equity") or 0.0)
        portfolio_total_pnl = float(portfolio_risk.get("portfolio_total_pnl") or 0.0)
        effective_balance = float(usdc_balance) if usdc_balance is not None else usdc_balance

        day_loss_budget = (
            reference_equity * float(self.risk_manager.config.per_day_loss_pct)
            if reference_equity > 0.0 and float(self.risk_manager.config.per_day_loss_pct) > 0.0
            else None
        )
        pre_kill_budget = (
            day_loss_budget * float(self.risk_manager.config.pre_kill_warning_fraction)
            if day_loss_budget is not None and float(self.risk_manager.config.pre_kill_warning_fraction) > 0.0
            else None
        )
        self._risk_warning_triggered = bool(pre_kill_budget is not None and portfolio_total_pnl <= -float(pre_kill_budget))
        if self._risk_warning_triggered:
            self._flatten_only_mode = True
        if day_loss_budget is not None and portfolio_total_pnl <= -float(day_loss_budget):
            self._flatten_only_mode = True
            self._halt_after_flatten = True

        market_context: Dict[str, Dict[str, Any]] = {}
        for market in self.active_markets:
            market_state = cluster_state["market_index"].get(str(market.condition_id), {})
            event_id = str(market_state.get("cluster_id") or self._event_id_for_market(market))
            cluster_info = cluster_policy_by_id.get(event_id, {})
            hedge_market_ctx = cluster_hedge_state["directives_by_market"].get(str(market.slug), {})
            context = {
                "event_id": event_id,
                "market_position_notional": float(market_state.get("market_position_notional") or 0.0),
                "market_unrealized_pnl": float(market_state.get("market_unrealized_pnl") or 0.0),
                "market_duration_ms": int(market_state.get("market_duration_ms") or self._market_duration_ms(market)),
                "event_position_notional": float(cluster_info.get("gross_exposure") or 0.0),
                "event_unrealized_pnl": float(cluster_info.get("unrealized_pnl") or 0.0),
                "new_entries_suppressed": bool(cluster_info.get("new_entries_suppressed")),
                "new_entry_block_reason": cluster_info.get("new_entry_block_reason"),
                "manual_force_flat": bool(
                    event_id in self._forced_flat_events
                    or str(market.slug) in self._forced_flat_markets
                    or hedge_market_ctx.get("manual_force_flat")
                ),
            }
            market_context[str(market.condition_id)] = context

        if hasattr(self.broker, "configure_dynamic_risk_limits"):
            self.broker.configure_dynamic_risk_limits(
                current_equity=current_equity,
                reference_equity=reference_equity,
                risk_config=self.risk_manager.config,
            )
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
        if self._flatten_only_mode:
            effective_balance = 0.0
        results: List[MarketCycleResult] = []
        for market in self.active_markets:
            market_ctx = market_context.get(str(market.condition_id), {})
            market_effective_balance = effective_balance
            if bool(market_ctx.get("new_entries_suppressed")):
                market_effective_balance = 0.0
            result = await self._run_single_market_cycle(
                market=market, now_ms=now_ms,
                usdc_balance=market_effective_balance,
                three_hour_volatility=three_hour_volatility,
                current_equity=current_equity,
                reference_equity=reference_equity,
                portfolio_total_pnl=portfolio_total_pnl,
                market_position_notional=float(market_ctx.get("market_position_notional") or 0.0),
                event_position_notional=float(market_ctx.get("event_position_notional") or 0.0),
                market_unrealized_pnl=float(market_ctx.get("market_unrealized_pnl") or 0.0),
                event_unrealized_pnl=float(market_ctx.get("event_unrealized_pnl") or 0.0),
                event_id=str(market_ctx.get("event_id") or self._event_id_for_market(market)),
                market_duration_ms=int(market_ctx.get("market_duration_ms") or self._market_duration_ms(market)),
                manual_force_flat=bool(market_ctx.get("manual_force_flat")) or (
                    self._flatten_only_mode and float(market_ctx.get("market_position_notional") or 0.0) > 0.0
                ),
                hedge_directives_by_token=cluster_hedge_state["directives_by_token"],
            )
            if result is not None:
                results.append(result)
                if bool(market_ctx.get("manual_force_flat")) and float(market_ctx.get("market_position_notional") or 0.0) <= 1e-9:
                    self._forced_flat_markets.discard(str(market.slug))
                    self._forced_flat_events.discard(str(market_ctx.get("event_id") or ""))
        if self._halt_after_flatten:
            post_cycle_snapshot = self._portfolio_risk_snapshot(usdc_balance=usdc_balance)
            if int(post_cycle_snapshot.get("active_positions") or 0) <= 0:
                self.cancel_all_quotes()
                self.set_kill_switch(True, reason="day_loss_cap_flattened")
        return results

    async def run_cycle(
        self,
        *,
        now_ms: int,
        usdc_balance: Optional[float] = None,
        three_hour_volatility: float = 0.0,
    ) -> Optional[MarketCycleResult]:
        """Backward-compat: run cycle for first active market only."""
        results = await self.run_cycles(
            now_ms=now_ms,
            usdc_balance=usdc_balance,
            three_hour_volatility=three_hour_volatility,
        )
        return results[0] if results else None

    async def _run_single_market_cycle(
        self,
        *,
        market: MarketCandidate,
        now_ms: int,
        usdc_balance: Optional[float] = None,
        three_hour_volatility: float = 0.0,
        current_equity: Optional[float] = None,
        reference_equity: Optional[float] = None,
        portfolio_total_pnl: float = 0.0,
        market_position_notional: float = 0.0,
        event_position_notional: float = 0.0,
        market_unrealized_pnl: float = 0.0,
        event_unrealized_pnl: float = 0.0,
        event_id: Optional[str] = None,
        market_duration_ms: Optional[int] = None,
        manual_force_flat: bool = False,
        hedge_directives_by_token: Optional[Dict[str, Any]] = None,
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

        market_duration_ms = int(market_duration_ms or self._market_duration_ms(market))
        kalshi_fee_spec = infer_fee_spec(market.raw) if self._is_kalshi_market_candidate(market) else None
        market_config = MarketConfig(
            market_id=market.slug,
            token_ids=market.token_ids,
            event_id=event_id or self._event_id_for_market(market),
            exchange="kalshi" if kalshi_fee_spec is not None else None,
            fee_model_exchange="kalshi" if kalshi_fee_spec is not None else None,
            fee_type=kalshi_fee_spec.fee_type if kalshi_fee_spec is not None else None,
            fee_multiplier=kalshi_fee_spec.fee_multiplier if kalshi_fee_spec is not None else None,
            tick_size=market.tick_size,
            min_size=self._min_size,
            fallback_size=self._fallback_size,
            within_pct=self._within_pct,
            trade_size=effective_trade_size,
            max_size=self._max_size,
            min_order_size=(
                float(self._min_order_size_override)
                if self._min_order_size_override is not None
                else float(market.min_incentive_size or 0.0)
            ),
            stale_book_gate_ms=self._stale_book_gate_ms,
            boundary_no_new_risk_min_price=self._boundary_no_new_risk_min_price,
            boundary_no_new_risk_max_price=self._boundary_no_new_risk_max_price,
            boundary_guard_mode=self._boundary_guard_mode,
            boundary_adverse_selection_threshold=self._boundary_adverse_selection_threshold,
            boundary_exit_cost_multiplier=self._boundary_exit_cost_multiplier,
            max_skew_ticks=self._max_skew_ticks,
            inventory_skew_factor=self._inventory_skew_factor,
            kelly_fraction=self._kelly_fraction,
            end_ts_ms=market.end_ts_ms,
            market_duration_ms=market_duration_ms,
            stop_open_before_expiry_ms=self.risk_manager.expiry_window_ms(
                market_duration_ms,
                self.risk_manager.config.stop_open_before_expiry_secs,
            ),
            force_flat_before_expiry_ms=self.risk_manager.expiry_window_ms(
                market_duration_ms,
                self.risk_manager.config.force_flat_before_expiry_secs,
            ),
            stale_position_after_ms=self.risk_manager.stale_duration_ms(market_duration_ms),
            manual_force_flat=bool(manual_force_flat),
            base_spread_multiplier=self._quote_spread_multiplier,
        )
        token_states = []
        token_ids = list(market.token_ids)
        for idx, token_id in enumerate(token_ids):
            reverse_id = token_ids[1 - idx] if len(token_ids) == 2 else None
            position = self.position_tracker.get_position(token_id)
            reverse_position = self.position_tracker.get_position(reverse_id).size if reverse_id else 0.0
            net_position = position.size - reverse_position
            hedge_directive = dict((hedge_directives_by_token or {}).get(str(token_id)).__dict__) if (hedge_directives_by_token or {}).get(str(token_id)) is not None else {}
            token_states.append(
                TokenState(
                    token_id=token_id,
                    position=position.size,
                    avg_cost=position.avg_price,
                    reverse_position=reverse_position,
                    net_position=net_position,
                    usdc_balance=usdc_balance,
                    three_hour_volatility=three_hour_volatility,
                    current_equity=current_equity,
                    reference_equity=reference_equity,
                    market_position_notional=market_position_notional,
                    event_position_notional=event_position_notional,
                    market_unrealized_pnl=market_unrealized_pnl,
                    event_unrealized_pnl=event_unrealized_pnl,
                    portfolio_total_pnl=portfolio_total_pnl,
                    hedge_action=str(hedge_directive.get("action") or "NONE"),
                    hedge_cluster_id=str(hedge_directive.get("cluster_id") or event_id or ""),
                    control_state=str(hedge_directive.get("control_state") or "NORMAL"),
                    hedge_action_reason=(str(hedge_directive.get("action_reason")) if hedge_directive.get("action_reason") else None),
                    hedge_market_id=(str(hedge_directive.get("hedge_market_id")) if hedge_directive.get("hedge_market_id") else None),
                    hedge_target_token_id=(str(hedge_directive.get("hedge_target_token_id")) if hedge_directive.get("hedge_target_token_id") else None),
                    hedge_target_side=(str(hedge_directive.get("hedge_target_side")) if hedge_directive.get("hedge_target_side") else None),
                    hedge_preferred_side=(str(hedge_directive.get("preferred_side")) if hedge_directive.get("preferred_side") else None),
                    hedge_ratio=(
                        float(hedge_directive.get("hedge_ratio"))
                        if hedge_directive.get("hedge_ratio") not in (None, "")
                        else None
                    ),
                    hedge_extra_skew_ticks=int(hedge_directive.get("extra_skew_ticks") or 0),
                    hedge_block_buy=bool(hedge_directive.get("block_buy")),
                    hedge_block_sell=bool(hedge_directive.get("block_sell")),
                    hedge_reduce_only=bool(hedge_directive.get("reduce_only")),
                    hedge_quality_score=(
                        float(hedge_directive.get("hedge_quality_score"))
                        if hedge_directive.get("hedge_quality_score") not in (None, "")
                        else None
                    ),
                    hedge_execution_quality_score=(
                        float(hedge_directive.get("hedge_execution_quality_score"))
                        if hedge_directive.get("hedge_execution_quality_score") not in (None, "")
                        else None
                    ),
                    hedge_covariance=(
                        float(hedge_directive.get("hedge_covariance"))
                        if hedge_directive.get("hedge_covariance") not in (None, "")
                        else None
                    ),
                    hedge_correlation=(
                        float(hedge_directive.get("hedge_correlation"))
                        if hedge_directive.get("hedge_correlation") not in (None, "")
                        else None
                    ),
                    hedge_beta_raw=(
                        float(hedge_directive.get("hedge_beta_raw"))
                        if hedge_directive.get("hedge_beta_raw") not in (None, "")
                        else None
                    ),
                    hedge_beta=(
                        float(hedge_directive.get("hedge_beta"))
                        if hedge_directive.get("hedge_beta") not in (None, "")
                        else None
                    ),
                    hedge_beta_shrunk=(
                        float(hedge_directive.get("hedge_beta_shrunk"))
                        if hedge_directive.get("hedge_beta_shrunk") not in (None, "")
                        else None
                    ),
                    hedge_beta_clipped=(
                        float(hedge_directive.get("hedge_beta_clipped"))
                        if hedge_directive.get("hedge_beta_clipped") not in (None, "")
                        else None
                    ),
                    hedge_covariance_sample_count=(
                        int(hedge_directive.get("hedge_covariance_sample_count"))
                        if hedge_directive.get("hedge_covariance_sample_count") not in (None, "")
                        else None
                    ),
                    hedge_covariance_state=(
                        str(hedge_directive.get("hedge_covariance_state"))
                        if hedge_directive.get("hedge_covariance_state") not in (None, "")
                        else None
                    ),
                    hedge_covariance_confidence=(
                        str(hedge_directive.get("hedge_covariance_confidence"))
                        if hedge_directive.get("hedge_covariance_confidence") not in (None, "")
                        else None
                    ),
                    hedge_pair_score=(
                        float(hedge_directive.get("hedge_pair_score"))
                        if hedge_directive.get("hedge_pair_score") not in (None, "")
                        else None
                    ),
                    hedgeability_tier=(
                        str(hedge_directive.get("hedgeability_tier"))
                        if hedge_directive.get("hedgeability_tier") not in (None, "")
                        else None
                    ),
                    hedge_structural_score=(
                        float(hedge_directive.get("hedge_structural_score"))
                        if hedge_directive.get("hedge_structural_score") not in (None, "")
                        else None
                    ),
                    hedge_covariance_score=(
                        float(hedge_directive.get("hedge_covariance_score"))
                        if hedge_directive.get("hedge_covariance_score") not in (None, "")
                        else None
                    ),
                    hedge_beta_stability_score=(
                        float(hedge_directive.get("hedge_beta_stability_score"))
                        if hedge_directive.get("hedge_beta_stability_score") not in (None, "")
                        else None
                    ),
                    hedge_execution_availability_score=(
                        float(hedge_directive.get("hedge_execution_availability_score"))
                        if hedge_directive.get("hedge_execution_availability_score") not in (None, "")
                        else None
                    ),
                    hedge_realized_outcome_score=(
                        float(hedge_directive.get("hedge_realized_outcome_score"))
                        if hedge_directive.get("hedge_realized_outcome_score") not in (None, "")
                        else None
                    ),
                    hedge_relation_confidence_state=(
                        str(hedge_directive.get("hedge_relation_confidence_state"))
                        if hedge_directive.get("hedge_relation_confidence_state") not in (None, "")
                        else None
                    ),
                    hedge_permission_state=(
                        str(hedge_directive.get("hedge_permission_state"))
                        if hedge_directive.get("hedge_permission_state") not in (None, "")
                        else None
                    ),
                    hedge_rejection_reason=(
                        str(hedge_directive.get("hedge_rejection_reason"))
                        if hedge_directive.get("hedge_rejection_reason") not in (None, "")
                        else None
                    ),
                    hedge_model_state=(
                        str(hedge_directive.get("hedge_model_state"))
                        if hedge_directive.get("hedge_model_state") not in (None, "")
                        else None
                    ),
                    hedge_realized_improvement_state=(
                        str(hedge_directive.get("hedge_realized_improvement_state"))
                        if hedge_directive.get("hedge_realized_improvement_state") not in (None, "")
                        else None
                    ),
                    hedge_success_window_ms=(
                        int(hedge_directive.get("hedge_success_window_ms"))
                        if hedge_directive.get("hedge_success_window_ms") not in (None, "")
                        else None
                    ),
                    hedge_failed_cooldown_until_ms=(
                        int(hedge_directive.get("hedge_failed_cooldown_until_ms"))
                        if hedge_directive.get("hedge_failed_cooldown_until_ms") not in (None, "")
                        else None
                    ),
                    hedge_rejection_reasons=tuple(str(reason) for reason in (hedge_directive.get("rejection_reasons") or ())),
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
            self._recent_book_states.append(str(td.book_diag.state))
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
        selection_report = dict(self._last_selection_report)
        if self.current_market is not None and not isinstance(selection_report.get("selected_market"), dict):
            selection_report["selected_market"] = {
                "slug": self.current_market.slug,
                "condition_id": self.current_market.condition_id,
                "reference_symbol": self.current_market.reference_symbol,
                "score": self.current_market.score,
                "spread": self.current_market.spread,
                "mid_price": self.current_market.mid_price,
                "accepted": True,
                "reason": "current_market",
            }
        if self.current_market is not None and not selection_report.get("selected_reason"):
            selection_report["selected_reason"] = "current_market"
        if self.current_market is not None and "accepted_candidates" not in selection_report:
            selection_report["accepted_candidates"] = []
        if self.current_market is not None and "rejected_candidates" not in selection_report:
            selection_report["rejected_candidates"] = []
        if self.current_market is not None and "fetch_attempts" not in selection_report:
            selection_report["fetch_attempts"] = []
        if not selection_report and self.current_market is not None:
            selection_report = {
                "selected_market": {
                    "slug": self.current_market.slug,
                    "condition_id": self.current_market.condition_id,
                    "reference_symbol": self.current_market.reference_symbol,
                    "score": self.current_market.score,
                    "spread": self.current_market.spread,
                    "mid_price": self.current_market.mid_price,
                    "accepted": True,
                    "reason": "current_market",
                },
                "selected_reason": "current_market",
                "accepted_candidates": [],
                "rejected_candidates": [],
                "fetch_attempts": [],
                "portfolio_selection": {
                    "mode": self.mode,
                    "max_active_markets": self._max_active_markets,
                    "launch_scope": "single_market" if self._max_active_markets == 1 else "multi_market",
                    "selected_market_ids": [market.slug for market in self.active_markets],
                    "selected_cluster_ids": sorted({self._event_id_for_market(market) for market in self.active_markets}),
                    "candidate_decisions": [],
                },
            }
        active_market_health = self._active_market_health(token_diag=token_diag, blocking_state_counts=blocking_state_counts)
        portfolio_risk = self._portfolio_risk_snapshot(usdc_balance=None)
        active_now_ms = int(time.time() * 1000)
        cluster_exposure = self._build_cluster_exposure_state(
            now_ms=active_now_ms,
            portfolio_risk=portfolio_risk,
        )["payload"]
        cluster_exposure = self._enrich_cluster_exposure_with_policy(
            cluster_exposure=cluster_exposure,
            cluster_hedge=dict(self._last_cluster_hedge_state),
            observe_pause_active=bool(self._observe_pause_until_ms and self._observe_pause_until_ms > active_now_ms),
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
            selection=selection_report,
            active_market_health=active_market_health,
            cluster_exposure=cluster_exposure,
            cluster_hedge=dict(self._last_cluster_hedge_state),
            control_state=self.control_state(),
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

    def _active_market_health(
        self,
        *,
        token_diag: Dict[str, Dict[str, Any]],
        blocking_state_counts: Dict[str, int],
    ) -> Dict[str, Any]:
        market = self.current_market
        token_ids = tuple(market.token_ids) if market is not None else tuple()
        per_token = dict(token_diag)
        tokens_ok = sum(1 for diag in per_token.values() if diag.get("state") == "book_ok")
        book_valid_both_sides = bool(token_ids) and tokens_ok == len(token_ids)
        if not token_ids:
            quoteability_state = "no_active_market"
        elif book_valid_both_sides:
            quoteability_state = "quoteable"
        elif any(state in {"book_absent", "book_empty"} for state in blocking_state_counts):
            quoteability_state = "book_unavailable"
        else:
            quoteability_state = "book_blocked"

        recent_state_counts = Counter(state for state in self._recent_book_states if state and state != "book_ok")
        broker_stats = self.broker.stats() if hasattr(self.broker, "stats") else {}
        portfolio_risk = self._portfolio_risk_snapshot(usdc_balance=None)
        fills = len(self.broker.fills()) if hasattr(self.broker, "fills") else 0
        open_orders = 0
        if hasattr(self.broker, "get_open_orders"):
            try:
                snapshot = self.broker.get_open_orders()
                orders = snapshot.payload.get("orders") if getattr(snapshot, "success", False) else []
                open_orders = len(orders or [])
            except Exception:
                open_orders = 0
        fill_rate_snapshot = float(fills) / float(max(fills + open_orders, 1))
        return {
            "market_id": market.slug if market is not None else None,
            "market_ids": tuple(m.slug for m in self.active_markets),
            "event_id": self._event_id_for_market(market) if market is not None else None,
            "token_ids": token_ids,
            "book_valid_both_sides": bool(book_valid_both_sides),
            "quoteability_state": quoteability_state,
            "per_token": per_token,
            "blocking_state_counts": dict(blocking_state_counts),
            "freeze_reason_breakdown": dict(recent_state_counts),
            "order_actions_snapshot": {
                "open_orders": open_orders,
                "fills": fills,
                "fill_rate_snapshot": fill_rate_snapshot,
            },
            "broker_stats": broker_stats,
            "portfolio_risk": portfolio_risk,
            "market_duration_ms": (self._market_duration_ms(market) if market is not None else None),
            "time_to_expiry_ms": (
                max(0, int(market.end_ts_ms) - int(time.time() * 1000))
                if market is not None and market.end_ts_ms is not None
                else None
            ),
        }

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
