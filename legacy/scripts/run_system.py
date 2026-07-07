from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import traceback
import sys
import time
import uuid
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import load_markets, load_settings, validate_markets_config
from core.book_cache import BookCache, BookHealthState, BookSnapshot
from core.broker_base import BrokerEvent, BrokerSnapshot, OrderIntent
from core.brokers.cli_broker import CLIBroker, CLIBrokerConfig
from core.broker_polymarket import (
    EXPECTED_CLOB_CLIENT_VERSION,
    BrokerContractError,
    PolymarketBroker,
    PolymarketBrokerConfig,
)
from core.broker_sim import SimBroker, SimBrokerConfig
from core.decision_tape import DecisionRecord, DecisionTape, TimeMapper
from core.event_tape import EventTape
from core.execution_fsm import ExecutionFSM, ExecutionState
from core.market_discovery import (
    GAMMA_BASE_URL,
    GammaFetchError,
    NoActiveMarketError,
    deterministic_market_selection_key_str,
    resolve_markets,
)
from core.market_rollover import MarketRolloverConfig, MarketRolloverManager, MarketState, market_state_from_resolved
from core.metrics import Metrics
from core.order_book import OrderBook
from core.policy_gate import PolicyContext, PolicyThresholds, PolicyVerdict, evaluate_policy
from core.pstar import PStar, PStarBuilder
from core.reference_feed import ReferenceFeed, ReferenceFeedConfig
from core.reference_ws import ReferenceWSClient, ReferenceWSConfig
from core.sqlite_store import SQLiteStore
from core.trade_tape import TradeTape
from core.validators import OrderConstraints
from data.polymarket_ws import MarketWSClient, ResubscribeResult, WSConfig

ALERT_STATE_OK = "OK"
ALERT_STATE_DEGRADED = "DEGRADED"
ALERT_STATE_FROZEN = "FROZEN"
FEED_READINESS_BOOTING = "BOOTING"
FEED_READINESS_PARTIAL = "PARTIAL"
FEED_READINESS_READY = "READY"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _utc_iso_from_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _round_down(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return (int(price / tick)) * tick


def _round_up(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return (int((price + tick - 1e-12) / tick)) * tick


def _quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(max(0, min(len(ordered) - 1, round((len(ordered) - 1) * q))))
    return float(ordered[idx])


@dataclass
class OpenQuote:
    order_id: str
    client_order_id: str
    side: str
    price: float
    qty: float
    post_only: bool
    quote_group_id: str
    idempotency_key: str
    updated_ms: int


@dataclass
class ExecutionAttributionPending:
    event_id: str
    token_id: str
    order_id: str
    side: str
    fill_ts_ms: int
    fill_price: float
    fill_qty: float
    fee_bps: float
    mid_at_send: Optional[float]
    mid_at_ack: Optional[float]
    mid_at_fill: Optional[float]
    due_ts_ms: int


@dataclass
class RolloverHealthGate:
    abort_threshold: int = 3
    abort_window_ms: int = 10 * 60_000
    cooldown_ms: int = 10 * 60_000
    abort_ts_ms: Deque[int] = field(default_factory=deque)
    frozen_until_ts_ms: Optional[int] = None

    def is_frozen(self, now_ms: int) -> bool:
        now = int(now_ms)
        if self.frozen_until_ts_ms is None:
            return False
        if now >= int(self.frozen_until_ts_ms):
            self.frozen_until_ts_ms = None
            self._prune(now)
            return False
        return True

    def note_abort(self, now_ms: int) -> Optional[Dict[str, Any]]:
        now = int(now_ms)
        self.abort_ts_ms.append(now)
        self._prune(now)
        threshold = max(1, int(self.abort_threshold))
        if len(self.abort_ts_ms) < threshold:
            return None
        cooldown = max(0, int(self.cooldown_ms))
        self.frozen_until_ts_ms = now + cooldown if cooldown > 0 else now
        oldest = int(self.abort_ts_ms[0]) if self.abort_ts_ms else now
        return {
            "abort_count_window": int(len(self.abort_ts_ms)),
            "abort_threshold": int(threshold),
            "abort_window_ms": int(self.abort_window_ms),
            "window_start_ts_ms": int(oldest),
            "window_end_ts_ms": int(now),
            "frozen_until_ts_ms": int(self.frozen_until_ts_ms),
        }

    def _prune(self, now_ms: int) -> None:
        now = int(now_ms)
        window = max(1, int(self.abort_window_ms))
        while self.abort_ts_ms and now - int(self.abort_ts_ms[0]) > window:
            self.abort_ts_ms.popleft()


@dataclass(frozen=True)
class MarketReadinessConfig:
    book_max_age_ms: int = 5_000
    book_max_spread_bps: float = 200.0
    depth_target_qty: float = 1.0
    pstar_max_age_ms: int = 5_000


@dataclass(frozen=True)
class MarketReadinessResult:
    ready: bool
    reason_codes: List[str]
    details: Dict[str, Any]


@dataclass(frozen=True)
class RolloverCommitDecision:
    action: str
    force_observe_only: bool
    reason: str


class RuntimeEngine:
    def __init__(
        self,
        mode: str,
        db: SQLiteStore,
        decision_tape: DecisionTape,
        trade_tape: TradeTape,
        books: Dict[str, OrderBook],
        constraints: Dict[str, OrderConstraints],
        market_meta: Dict[str, Dict[str, Any]],
        pstar_builder: PStarBuilder,
        policy_thresholds: PolicyThresholds,
        constitution: Dict[str, Any],
        time_mapper: TimeMapper,
        broker: Optional[Any],
        run_epoch_ms: int,
        run_id: Optional[str] = None,
        readiness_config: Optional[MarketReadinessConfig] = None,
        reference_poll_secs: Optional[float] = None,
    ) -> None:
        self.mode = mode
        self.db = db
        self.decision_tape = decision_tape
        self.trade_tape = trade_tape
        self.books = books
        self.constraints = constraints
        self.market_meta = market_meta
        self.pstar_builder = pstar_builder
        self.policy_thresholds = policy_thresholds
        self.constitution = constitution
        self.time_mapper = time_mapper
        self.broker = broker
        self.run_epoch_ms = int(run_epoch_ms)
        self.run_id = str(run_id or f"run-{self.run_epoch_ms}")
        self.book_cache = BookCache()
        self.fsms: Dict[str, ExecutionFSM] = {
            token: ExecutionFSM(rebalance_timeout_ms=self.policy_thresholds.hedge_timeout_ms)
            for token in books.keys()
        }
        self._last_fsm_state_by_token: Dict[str, str] = {
            token: ExecutionState.QUOTING_BOTH.value for token in books.keys()
        }
        self.inventory_yes: Dict[str, float] = defaultdict(float)
        self.inventory_no: Dict[str, float] = defaultdict(float)
        self.open_quotes: Dict[str, Dict[str, OpenQuote]] = defaultdict(dict)
        self.last_pstar_value: Dict[str, float] = {}
        self.last_pstar_ts: Dict[str, int] = {}
        self.send_ts_by_order: Dict[str, int] = {}
        self.ack_ts_by_order: Dict[str, int] = {}
        self.send_ack_samples: Deque[float] = deque(maxlen=2000)
        self.ack_fill_samples: Deque[float] = deque(maxlen=2000)
        self.signal_age_samples: Deque[float] = deque(maxlen=2000)
        self.ws_lag_samples: Deque[float] = deque(maxlen=2000)
        self.pending_freeze: Dict[str, List[str]] = defaultdict(list)
        self._trade_tape_parent_event_ids: Dict[str, int] = {}
        self._unwind_state: Dict[str, Dict[str, Any]] = {}
        self._decision_seq = 0
        self._quote_revision: Dict[Tuple[str, str], int] = defaultdict(int)
        self._post_only_attempts_by_token: Dict[str, int] = defaultdict(int)
        self._post_only_rejects_by_token: Dict[str, int] = defaultdict(int)
        self._last_q_by_token: Dict[str, float] = defaultdict(lambda: 0.5)
        self._book_stale_after_ms = int(self.constitution.get("policy", {}).get("book_stale_after_ms", 30_000))
        self._book_down_after_ms = int(self.constitution.get("policy", {}).get("book_down_after_ms", 120_000))
        self._cap_state: Dict[str, Dict[str, float]] = self._build_caps(
            trading_cfg=self.constitution.get("trading", {}),
            execution_cfg=self.constitution.get("execution", {}),
        )
        recon_cfg = self.constitution.get("reconciliation", {}) if isinstance(self.constitution, dict) else {}
        trading_cfg = self.constitution.get("trading", {}) if isinstance(self.constitution, dict) else {}
        self._reconcile_period_ms = int(
            trading_cfg.get(
                "reconcile_period_ms",
                recon_cfg.get("reconcile_period_ms", 5_000),
            )
        )
        self._mismatch_tolerance_qty = float(
            trading_cfg.get(
                "mismatch_tolerance_qty",
                recon_cfg.get("mismatch_tolerance_qty", 0.01),
            )
        )
        self._mismatch_tolerance_usdc = float(
            trading_cfg.get(
                "mismatch_tolerance_usdc",
                recon_cfg.get("mismatch_tolerance_usdc", 1.0),
            )
        )
        self._mismatch_freeze_cycles = int(
            trading_cfg.get(
                "mismatch_freeze_cycles",
                recon_cfg.get("mismatch_freeze_cycles", 3),
            )
        )
        self._onchain_disagree_freeze_cycles = int(
            trading_cfg.get(
                "onchain_disagree_freeze_cycles",
                recon_cfg.get("onchain_disagree_freeze_cycles", 6),
            )
        )
        self._reconcile_clean_unfreeze_cycles = int(
            trading_cfg.get(
                "reconcile_clean_unfreeze_cycles",
                recon_cfg.get("reconcile_clean_unfreeze_cycles", 3),
            )
        )
        self._single_level_quoting = bool(
            trading_cfg.get(
                "single_level_quoting",
                recon_cfg.get("single_level_quoting", True),
            )
        )
        self._startup_allow_exact_duplicate_cleanup = bool(
            trading_cfg.get(
                "startup_allow_exact_duplicate_cleanup",
                recon_cfg.get("startup_allow_exact_duplicate_cleanup", False),
            )
        )
        self._qty_scale = int(
            trading_cfg.get(
                "qty_scale",
                recon_cfg.get("qty_scale", 1_000_000),
            )
        )
        self._usdc_scale = int(
            trading_cfg.get(
                "usdc_scale",
                recon_cfg.get("usdc_scale", 1_000_000),
            )
        )
        self._max_orders_per_min = int(trading_cfg.get("max_orders_per_min", 30))
        self._max_cancels_per_min = int(trading_cfg.get("max_cancels_per_min", 60))
        self._max_daily_loss_usdc = float(trading_cfg.get("max_daily_loss_usdc", 100.0))
        self._max_daily_notional_usdc = float(trading_cfg.get("max_daily_notional_usdc", 2000.0))
        self._clock_drift_max_ms = float(trading_cfg.get("clock_drift_max_ms", 250.0))
        self._ws_starvation_max_ms = float(trading_cfg.get("ws_starvation_max_ms", 5000.0))
        self._econ_min_net_edge_p50_bps = float(trading_cfg.get("econ_min_net_edge_p50_bps", 0.0))
        self._econ_max_adverse_markout_5s_p95_bps = float(
            trading_cfg.get("econ_max_adverse_markout_5s_p95_bps", 20.0)
        )
        self._next_reconcile_due_ms: int = 0
        self._consecutive_mismatch_cycles: int = 0
        self._consecutive_onchain_disagree_cycles: int = 0
        self._consecutive_clean_cycles: int = 0
        self._reconciliation_frozen: bool = False
        self._reconciliation_freeze_reason: str = ""
        self._seen_reconcile_fill_ids: Set[str] = set()
        self._startup_unknown_order_quarantine: List[Dict[str, Any]] = []
        self._liveness_freeze_active: bool = False
        self._liveness_reason_codes: List[str] = []
        self._book_seq_by_token: Dict[str, int] = defaultdict(int)
        self._book_update_count_by_token: Dict[str, int] = defaultdict(int)
        self._last_book_recv_mono_by_token: Dict[str, int] = defaultdict(int)
        self._book_recv_ts_ms_by_token: Dict[str, Deque[int]] = defaultdict(lambda: deque(maxlen=5000))
        self._book_age_samples_by_token: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=2000))
        self._mid_history_by_token: Dict[str, Deque[Tuple[int, float]]] = defaultdict(lambda: deque(maxlen=20000))
        self._latest_book_snapshot_by_token: Dict[str, BookSnapshot] = {}
        self._latest_pstar_by_symbol: Dict[str, PStar] = {}
        self._last_pstar_stats_ts_by_symbol: Dict[str, int] = {}
        self.pstar_age_samples: Deque[float] = deque(maxlen=2000)
        self._pending_execution_quality: Dict[str, ExecutionAttributionPending] = {}
        self._order_submit_wall_by_order: Dict[str, int] = {}
        self._order_submit_price_by_order: Dict[str, float] = {}
        self._order_submit_qty_by_order: Dict[str, float] = {}
        self._order_submit_token_by_order: Dict[str, str] = {}
        self._first_fill_seen_by_order: Set[str] = set()
        self._time_to_first_fill_samples_by_token: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=4000))
        self._partial_fill_ts_by_token: Dict[str, Deque[int]] = defaultdict(lambda: deque(maxlen=4000))
        self._submit_event_ts: Deque[int] = deque(maxlen=20000)
        self._cancel_event_ts: Deque[int] = deque(maxlen=20000)
        self._fill_event_ts: Deque[int] = deque(maxlen=20000)
        self._submit_event_ts_by_token: Dict[str, Deque[int]] = defaultdict(lambda: deque(maxlen=5000))
        self._cancel_event_ts_by_token: Dict[str, Deque[int]] = defaultdict(lambda: deque(maxlen=5000))
        self._fill_event_ts_by_token: Dict[str, Deque[int]] = defaultdict(lambda: deque(maxlen=5000))
        self._daily_bucket_utc: Optional[str] = None
        self._daily_notional_usdc: float = 0.0
        self._daily_loss_usdc: float = 0.0
        self.readiness_config = readiness_config or MarketReadinessConfig()
        self._rollover_guard_token_ids: Set[str] = set()
        self._rollover_guard_quiet_until_ms: int = 0
        self._rollover_guard_require_readiness: bool = False
        self._rollover_guard_last_result: MarketReadinessResult = MarketReadinessResult(
            ready=True,
            reason_codes=[],
            details={},
        )
        self._rollover_guard_last_checked_ms: Optional[int] = None
        self._observe_causality_freeze_after = int(
            max(
                1,
                self.constitution.get("trading", {}).get("observe_causality_freeze_after", 3),
            )
        )
        self._observe_causality_streak_by_token: Dict[str, int] = defaultdict(int)
        self._startup_liveness_grace_ms = int(
            max(
                0,
                self.constitution.get("trading", {}).get("startup_liveness_grace_ms", 0),
            )
        )
        self._startup_readiness_state: str = FEED_READINESS_BOOTING
        self._startup_readiness_reason_codes: List[str] = []
        self._startup_readiness_payload: Dict[str, Any] = {}
        self._alert_emit_cooldown_ms = int(
            max(
                500,
                self.constitution.get("trading", {}).get("alert_emit_cooldown_ms", 10_000),
            )
        )
        self._last_alert_emit_ts_by_key: Dict[str, int] = {}
        self._reference_poll_secs: float = float(reference_poll_secs if reference_poll_secs is not None else 1.0)
        self._pstar_state_by_symbol: Dict[str, str] = {}
        self._pstar_transition_counts: Dict[str, int] = defaultdict(int)

    def on_reference(self, source: str, symbol: str, value: float, ts_event_ms: Optional[int], ts_recv_ms: int) -> None:
        event_ts = int(ts_event_ms if ts_event_ms is not None else ts_recv_ms)
        self.pstar_builder.ingest(source=source, symbol=symbol, value=value, ts_event_ms=event_ts, ts_recv_wall_ms=ts_recv_ms)

    def evaluate_market_readiness(
        self,
        token_ids: List[str],
        now_ms: int,
        books_override: Optional[Dict[str, OrderBook]] = None,
        market_meta_override: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> MarketReadinessResult:
        cfg = self.readiness_config
        books = books_override if books_override is not None else self.books
        market_meta = market_meta_override if market_meta_override is not None else self.market_meta
        reasons: List[str] = []
        details: Dict[str, Any] = {"tokens": {}, "pstar": {}}
        symbols_seen: Set[str] = set()
        required_qty = max(0.0, float(cfg.depth_target_qty))

        for token_id in [str(token) for token in token_ids if token]:
            book = books.get(token_id)
            token_meta = market_meta.get(token_id) or {}
            symbol = str(token_meta.get("reference_symbol") or "")
            if symbol:
                symbols_seen.add(symbol)
            if book is None:
                reasons.append("C_BOOK_DOWN")
                details["tokens"][token_id] = {
                    "book_present": False,
                    "book_health_state": BookHealthState.DOWN.value,
                    "book_age_ms": None,
                    "spread_bps": None,
                    "depth_at_qty_buy": 0.0,
                    "depth_at_qty_sell": 0.0,
                    "depth_target_qty": required_qty,
                    "symbol": symbol or None,
                }
                continue

            snap = BookSnapshot.from_order_book(token_id=token_id, book=book, ts_recv_wall_ms=int(now_ms))
            health = snap.health_state(
                now_wall_ms=int(now_ms),
                stale_after_ms=self._book_stale_after_ms,
                down_after_ms=self._book_down_after_ms,
            ).value
            age_ms = _maybe_int(snap.age_ms(int(now_ms)))
            spread_bps = _maybe_float(snap.spread_bps())
            depth_buy = float(snap.depth_at_qty("buy", required_qty))
            depth_sell = float(snap.depth_at_qty("sell", required_qty))
            min_depth = min(depth_buy, depth_sell)

            if health == BookHealthState.DOWN.value:
                reasons.append("C_BOOK_DOWN")
            elif health != BookHealthState.FRESH.value:
                reasons.append("C_BOOK_STALE")
            if age_ms is None or age_ms > int(cfg.book_max_age_ms):
                reasons.append("C_BOOK_STALE")
            if spread_bps is None or spread_bps > float(cfg.book_max_spread_bps):
                reasons.append("C_SPREAD_TOO_WIDE")
            if min_depth < required_qty:
                reasons.append("C_DEPTH_TOO_THIN")

            details["tokens"][token_id] = {
                "book_present": True,
                "book_health_state": health,
                "book_age_ms": age_ms,
                "spread_bps": spread_bps,
                "depth_at_qty_buy": depth_buy,
                "depth_at_qty_sell": depth_sell,
                "depth_target_qty": required_qty,
                "symbol": symbol or None,
            }

        selected_symbol = sorted(symbols_seen)[0] if symbols_seen else ""
        if not selected_symbol:
            reasons.append("A_PSTAR_INVALID")
            details["pstar"] = {
                "symbol": None,
                "state": "UNAVAILABLE",
                "valid": False,
                "value": None,
                "pstar_age_ms": None,
                "confidence": 0.0,
                "sources_used": [],
                "invalid_reason": "missing_symbol",
            }
        else:
            pstar = self.pstar_builder.build(selected_symbol, int(now_ms))
            self._latest_pstar_by_symbol[selected_symbol] = pstar
            pstar_age_ms = _maybe_int(int(now_ms) - int(pstar.ts_event_ms)) if pstar.ts_event_ms is not None else None
            pstar_state = self._pstar_state(pstar, int(now_ms))
            if not pstar.valid or pstar.value is None or pstar.ts_event_ms is None:
                reasons.append("A_PSTAR_INVALID")
            elif pstar_age_ms is None or pstar_age_ms > int(cfg.pstar_max_age_ms):
                reasons.append("A_PSTAR_STALE")
            details["pstar"] = {
                "symbol": selected_symbol,
                "state": str(pstar_state),
                "valid": bool(pstar.valid),
                "value": _maybe_float(pstar.value),
                "pstar_age_ms": pstar_age_ms,
                "confidence": _maybe_float(pstar.confidence),
                "sources_used": sorted(pstar.sources_used),
                "invalid_reason": str(pstar.invalid_reason or ""),
            }

        reason_codes = sorted(set(str(code) for code in reasons if code))
        ready = len(reason_codes) == 0
        details["ready"] = bool(ready)
        details["as_of_ts_ms"] = int(now_ms)
        return MarketReadinessResult(
            ready=bool(ready),
            reason_codes=reason_codes,
            details=details,
        )

    def activate_rollover_guard(
        self,
        token_ids: List[str],
        quiet_until_ms: int,
        require_readiness: bool,
    ) -> None:
        self._rollover_guard_token_ids = {str(token) for token in token_ids if token}
        self._rollover_guard_quiet_until_ms = int(max(0, quiet_until_ms))
        self._rollover_guard_require_readiness = bool(require_readiness)
        self._rollover_guard_last_checked_ms = None
        self._rollover_guard_last_result = MarketReadinessResult(
            ready=False,
            reason_codes=["ROLLOVER_QUIET_WINDOW"] if self._rollover_guard_token_ids else [],
            details={"as_of_ts_ms": int(_now_ms())},
        )

    def quote_guard_reasons(self, token_id: str, now_ms: int) -> List[str]:
        if token_id not in self._rollover_guard_token_ids:
            return []
        reasons: List[str] = []
        if int(now_ms) < int(self._rollover_guard_quiet_until_ms):
            reasons.append("ROLLOVER_QUIET_WINDOW")
        readiness_result = self.evaluate_market_readiness(
            token_ids=sorted(self._rollover_guard_token_ids),
            now_ms=int(now_ms),
        )
        self._rollover_guard_last_result = readiness_result
        self._rollover_guard_last_checked_ms = int(now_ms)
        if self._rollover_guard_require_readiness and not readiness_result.ready:
            reasons.append("ROLLOVER_READINESS_BLOCK")
            reasons.extend(readiness_result.reason_codes)
        if not reasons and (readiness_result.ready or not self._rollover_guard_require_readiness):
            self._rollover_guard_token_ids = set()
            self._rollover_guard_quiet_until_ms = 0
            self._rollover_guard_require_readiness = False
        return sorted(set(reasons))

    def rollover_guard_status(self, now_ms: int) -> Dict[str, Any]:
        if self._rollover_guard_token_ids:
            self.quote_guard_reasons(next(iter(sorted(self._rollover_guard_token_ids))), int(now_ms))
        return {
            "active": bool(self._rollover_guard_token_ids),
            "token_ids": sorted(self._rollover_guard_token_ids),
            "quiet_until_ts_ms": int(self._rollover_guard_quiet_until_ms),
            "requires_readiness": bool(self._rollover_guard_require_readiness),
            "last_ready": bool(self._rollover_guard_last_result.ready),
            "last_reason_codes": list(self._rollover_guard_last_result.reason_codes),
            "last_checked_ts_ms": _maybe_int(self._rollover_guard_last_checked_ms),
            "last_details": dict(self._rollover_guard_last_result.details),
        }

    async def run_quote_cycle(self, now_ms: int) -> None:
        target_size = float(self.constitution["execution"].get("maker_quote_size", 1.0))
        half_spread_bps = float(self.constitution["execution"].get("maker_half_spread_bps", 40.0))
        inventory_skew_per_unit = float(self.constitution["execution"].get("inventory_skew_per_unit", 0.0025))
        risk_padding_bps = float(self.constitution["execution"].get("risk_padding_bps", 5.0))
        is_frozen_any = False
        freeze_reasons: List[str] = []
        degraded_reasons: List[str] = []
        degraded_by_market: Dict[str, List[str]] = {}

        for token_id, book in self.books.items():
            meta = self.market_meta.get(token_id, {})
            symbol = str(meta.get("reference_symbol") or "")
            constraint = self.constraints[token_id]
            snap = self._snapshot_book(token_id=token_id, book=book, now_ms=now_ms)
            self.book_cache.update(snap)
            self._record_book_snapshot(snap)
            book_health = snap.health_state(
                now_wall_ms=now_ms,
                stale_after_ms=self._book_stale_after_ms,
                down_after_ms=self._book_down_after_ms,
            )

            ws_lag_ms = None
            if snap.ts_event_ms is not None:
                ws_lag_ms = float(now_ms - snap.ts_event_ms)
                self.ws_lag_samples.append(ws_lag_ms)

            pstar = self.pstar_builder.build(symbol, now_ms) if symbol else PStar(symbol="", value=None, ts_event_ms=None, sources_used=set(), confidence=0.0, valid=False, diagnostics={"freeze_reason": "missing_symbol"})
            self._record_pstar(now_ms, symbol, pstar)
            pstar_age_ms = None
            if pstar.ts_event_ms is not None:
                pstar_age_ms = float(max(0, now_ms - int(pstar.ts_event_ms)))
                self.pstar_age_samples.append(pstar_age_ms)

            q = self._fair_probability(token_id=token_id, symbol=symbol, snap=snap, pstar=pstar, now_ms=now_ms)
            self._last_q_by_token[token_id] = float(q)
            depth_buy = snap.depth_at_qty("buy", target_size)
            depth_sell = snap.depth_at_qty("sell", target_size)
            slip_buy = snap.expected_slippage_bps("buy", target_size)
            slip_sell = snap.expected_slippage_bps("sell", target_size)
            eff_buy = snap.effective_spread_bps("buy", target_size)
            eff_sell = snap.effective_spread_bps("sell", target_size)

            fsm = self.fsms[token_id]
            if fsm.on_rebalance_tick(now_ms) or fsm.status().state == ExecutionState.UNWINDING:
                await self._emergency_unwind(token_id, now_ms, constraint)

            fsm_state = fsm.status().state.value
            raw_book_asof_ts = _maybe_int(snap.ts_event_ms)
            raw_pstar_asof_ts = _maybe_int(pstar.ts_event_ms)
            # Guard against wall-clock and event-clock ms precision ties/skew.
            decision_ts_event_ms = int(
                max(
                    int(now_ms),
                    int(snap.ts_event_ms or 0) + 1,
                    int(pstar.ts_event_ms or 0) + 1,
                )
            )
            pre_decision_cap = int(decision_ts_event_ms - 1)
            clamped_book_asof_ts = min(int(raw_book_asof_ts), pre_decision_cap) if raw_book_asof_ts is not None else None
            clamped_pstar_asof_ts = min(int(raw_pstar_asof_ts), pre_decision_cap) if raw_pstar_asof_ts is not None else None
            feature_candidates = [int(ts) for ts in [clamped_book_asof_ts, clamped_pstar_asof_ts] if ts is not None]
            feature_max_ts = max(feature_candidates) if feature_candidates else 0
            signal_age_ms = (
                int(max(0, int(decision_ts_event_ms) - int(feature_max_ts)))
                if feature_max_ts
                else int(max(10_000, self.policy_thresholds.max_signal_age_ms * 5))
            )
            self.signal_age_samples.append(float(signal_age_ms))
            ack_p95 = _quantile(list(self.send_ack_samples), 0.95)
            causality_reasons = self._causality_violations(
                decision_ts_event_ms=decision_ts_event_ms,
                feature_max_ts_ms=feature_max_ts,
                book_asof_ts_ms=raw_book_asof_ts,
                pstar_asof_ts_ms=raw_pstar_asof_ts,
            )
            if causality_reasons:
                self._observe_causality_streak_by_token[token_id] = int(
                    self._observe_causality_streak_by_token.get(token_id, 0) + 1
                )
            else:
                self._observe_causality_streak_by_token[token_id] = 0
            causality_diag = {
                "decision_ts_ms": int(decision_ts_event_ms),
                "feature_max_ts_ms": int(feature_max_ts),
                "book_asof_ts_raw_ms": _maybe_int(raw_book_asof_ts),
                "book_asof_ts_clamped_ms": _maybe_int(clamped_book_asof_ts),
                "pstar_asof_ts_raw_ms": _maybe_int(raw_pstar_asof_ts),
                "pstar_asof_ts_clamped_ms": _maybe_int(clamped_pstar_asof_ts),
                "book_delta_ms": _maybe_int(
                    int(raw_book_asof_ts) - int(decision_ts_event_ms)
                    if raw_book_asof_ts is not None
                    else None
                ),
                "pstar_delta_ms": _maybe_int(
                    int(raw_pstar_asof_ts) - int(decision_ts_event_ms)
                    if raw_pstar_asof_ts is not None
                    else None
                ),
            }
            if causality_reasons:
                offending: List[Dict[str, Any]] = []
                if raw_book_asof_ts is not None and int(raw_book_asof_ts) >= int(decision_ts_event_ms):
                    offending.append(
                        {
                            "feature": "book_asof_ts_ms",
                            "feature_ts_ms": int(raw_book_asof_ts),
                            "decision_ts_ms": int(decision_ts_event_ms),
                            "delta_ms": int(raw_book_asof_ts) - int(decision_ts_event_ms),
                        }
                    )
                if raw_pstar_asof_ts is not None and int(raw_pstar_asof_ts) >= int(decision_ts_event_ms):
                    offending.append(
                        {
                            "feature": "pstar_asof_ts_ms",
                            "feature_ts_ms": int(raw_pstar_asof_ts),
                            "decision_ts_ms": int(decision_ts_event_ms),
                            "delta_ms": int(raw_pstar_asof_ts) - int(decision_ts_event_ms),
                        }
                    )
                if int(feature_max_ts) >= int(decision_ts_event_ms):
                    offending.append(
                        {
                            "feature": "max_feature_ts_ms",
                            "feature_ts_ms": int(feature_max_ts),
                            "decision_ts_ms": int(decision_ts_event_ms),
                            "delta_ms": int(feature_max_ts) - int(decision_ts_event_ms),
                        }
                    )
                causality_diag["offending"] = offending
                causality_is_hard = bool(
                    self.mode in {"PAPER", "TRADE"}
                    or int(self._observe_causality_streak_by_token.get(token_id, 0)) >= int(self._observe_causality_freeze_after)
                )
                self._emit_rate_limited_alert(
                    now_ms,
                    severity="critical" if causality_is_hard else "warning",
                    code="B_CAUSALITY_VIOLATION",
                    message=f"causality_violation token={token_id} reasons={','.join(causality_reasons)}",
                    payload={
                        "token_id": str(token_id),
                        "state": ALERT_STATE_FROZEN if causality_is_hard else ALERT_STATE_DEGRADED,
                        "reason_codes": list(causality_reasons),
                        "offending": offending,
                        "sustain_threshold": int(self._observe_causality_freeze_after),
                        "streak": int(self._observe_causality_streak_by_token.get(token_id, 0)),
                    },
                    dedupe_key=f"B_CAUSALITY_VIOLATION:{token_id}",
                )

            buy_ctx = PolicyContext(
                market=str(meta.get("slug") or token_id),
                token_id=token_id,
                now_ms=now_ms,
                decision_ts_event_ms=decision_ts_event_ms,
                feature_max_ts_ms=feature_max_ts,
                book=snap,
                pstar=pstar,
                quote_side="buy",
                quote_qty=target_size,
                signal_age_ms=signal_age_ms,
                ack_p95_ms=ack_p95,
                ws_lag_ms=ws_lag_ms,
                one_leg_age_ms=_one_leg_age_ms(fsm.status().one_leg_since_ms, now_ms),
                fsm_state=fsm_state,
                expected_slippage_bps=slip_buy,
                depth_at_qty=depth_buy,
                book_health_state=book_health.value,
                reconciliation_mismatch_critical=bool(self._reconciliation_frozen and self.mode == "TRADE"),
                risk_throttle_critical=False,
                liveness_critical=bool(self._liveness_freeze_active),
                unknown_order_quarantine=bool(self._startup_unknown_order_quarantine and self.mode in {"PAPER", "TRADE"}),
                daily_loss_critical=False,
            )
            sell_ctx = PolicyContext(
                market=str(meta.get("slug") or token_id),
                token_id=token_id,
                now_ms=now_ms,
                decision_ts_event_ms=decision_ts_event_ms,
                feature_max_ts_ms=feature_max_ts,
                book=snap,
                pstar=pstar,
                quote_side="sell",
                quote_qty=target_size,
                signal_age_ms=signal_age_ms,
                ack_p95_ms=ack_p95,
                ws_lag_ms=ws_lag_ms,
                one_leg_age_ms=_one_leg_age_ms(fsm.status().one_leg_since_ms, now_ms),
                fsm_state=fsm_state,
                expected_slippage_bps=slip_sell,
                depth_at_qty=depth_sell,
                book_health_state=book_health.value,
                reconciliation_mismatch_critical=bool(self._reconciliation_frozen and self.mode == "TRADE"),
                risk_throttle_critical=False,
                liveness_critical=bool(self._liveness_freeze_active),
                unknown_order_quarantine=bool(self._startup_unknown_order_quarantine and self.mode in {"PAPER", "TRADE"}),
                daily_loss_critical=False,
            )
            risk_reasons = self._risk_budget_reasons(now_ms)
            risk_critical = bool(risk_reasons)
            daily_loss_critical = "RISK_DAILY_LOSS_KILLSWITCH" in risk_reasons
            if risk_critical:
                buy_ctx = PolicyContext(
                    **{**buy_ctx.__dict__, "risk_throttle_critical": True, "daily_loss_critical": bool(daily_loss_critical)}
                )
                sell_ctx = PolicyContext(
                    **{**sell_ctx.__dict__, "risk_throttle_critical": True, "daily_loss_critical": bool(daily_loss_critical)}
                )
            buy_verdict = evaluate_policy(buy_ctx, self.policy_thresholds)
            sell_verdict = evaluate_policy(sell_ctx, self.policy_thresholds)

            reasons = sorted(set(buy_verdict.reason_codes + sell_verdict.reason_codes + causality_reasons))
            if risk_reasons:
                reasons = sorted(set(reasons + list(risk_reasons)))
            if self._liveness_reason_codes:
                reasons = sorted(set(reasons + list(self._liveness_reason_codes)))
            if (
                self.mode == "OBSERVE"
                and any(code.startswith("B_") for code in reasons)
                and int(self._observe_causality_streak_by_token.get(token_id, 0))
                >= int(self._observe_causality_freeze_after)
            ):
                reasons = sorted(set(reasons + ["B_CAUSALITY_SUSTAINED"]))
            guard_reasons = self.quote_guard_reasons(token_id=token_id, now_ms=now_ms)
            if guard_reasons:
                reasons = sorted(set(reasons + guard_reasons))
            cap_diag = self._inventory_cap_diagnostics(token_id=token_id, q=q)
            if bool(cap_diag.get("hard_breach", False)):
                reasons.append("RISK_CAP_BREACH")
            elif bool(cap_diag.get("soft_breach", False)):
                reasons.append("RISK_CAP_SOFT")
            reasons = sorted(set(reasons))
            buy_verdict = self._normalize_verdict_for_mode(
                PolicyVerdict(
                    allow=buy_verdict.allow,
                    action=buy_verdict.action,
                    reason_codes=reasons,
                    diagnostics={**buy_verdict.diagnostics, "cap_diag": cap_diag, "causality": causality_diag},
                ),
                reasons,
                token_id,
            )
            sell_verdict = self._normalize_verdict_for_mode(
                PolicyVerdict(
                    allow=sell_verdict.allow,
                    action=sell_verdict.action,
                    reason_codes=reasons,
                    diagnostics={**sell_verdict.diagnostics, "cap_diag": cap_diag, "causality": causality_diag},
                ),
                reasons,
                token_id,
            )
            pstar_state = self._pstar_state(pstar, now_ms)
            if "A_PSTAR_INVALID" in reasons or "A_PSTAR_STALE" in reasons:
                pstar_reason = "A_PSTAR_INVALID" if "A_PSTAR_INVALID" in reasons else "A_PSTAR_STALE"
                pstar_severity = "critical" if self.mode in {"PAPER", "TRADE"} else "warning"
                self._emit_rate_limited_alert(
                    now_ms,
                    severity=pstar_severity,
                    code=pstar_reason,
                    message=f"pstar_state={pstar_state} token={token_id}",
                    payload={
                        "token_id": str(token_id),
                        "state": str(pstar_state),
                        "age_ms": _maybe_float(pstar_age_ms),
                        "threshold_ms": int(self.pstar_builder.max_age_ms),
                        "provider": ",".join(sorted(pstar.sources_used)),
                        "invalid_reason": str(pstar.invalid_reason or ""),
                    },
                    dedupe_key=f"{token_id}:{pstar_reason}",
                )
            if "E_SIGNAL_AGE_HIGH" in reasons:
                signal_severity = "critical" if self.mode in {"PAPER", "TRADE"} else "warning"
                self._emit_rate_limited_alert(
                    now_ms,
                    severity=signal_severity,
                    code="E_SIGNAL_AGE_HIGH",
                    message=f"signal_age_high token={token_id}",
                    payload={
                        "token_id": str(token_id),
                        "state": str(ALERT_STATE_FROZEN if signal_severity == "critical" else ALERT_STATE_DEGRADED),
                        "age_ms": float(signal_age_ms),
                        "threshold_ms": int(self.policy_thresholds.max_signal_age_ms),
                        "provider": ",".join(sorted(pstar.sources_used)),
                    },
                    dedupe_key=f"{token_id}:E_SIGNAL_AGE_HIGH",
                )
            force_freeze = bool(
                book_health == BookHealthState.DOWN
                or bool(cap_diag.get("hard_breach", False))
                or bool(self._reconciliation_frozen and self.mode == "TRADE")
                or bool(self._liveness_freeze_active and self.mode in {"PAPER", "TRADE"})
                or bool(self._startup_unknown_order_quarantine and self.mode in {"PAPER", "TRADE"})
                or bool(risk_critical and self.mode in {"PAPER", "TRADE"})
                or bool(self.mode == "OBSERVE" and any(self._observe_reason_is_hard(code, token_id) for code in reasons))
            )
            if force_freeze:
                buy_verdict = PolicyVerdict(
                    allow=False,
                    action="FREEZE",
                    reason_codes=reasons,
                    diagnostics={**buy_verdict.diagnostics, "cap_diag": cap_diag},
                )
                sell_verdict = PolicyVerdict(
                    allow=False,
                    action="FREEZE",
                    reason_codes=reasons,
                    diagnostics={**sell_verdict.diagnostics, "cap_diag": cap_diag},
                )
            if guard_reasons and not force_freeze:
                buy_verdict = PolicyVerdict(
                    allow=False,
                    action="HOLD",
                    reason_codes=reasons,
                    diagnostics={**buy_verdict.diagnostics, "guard_reasons": guard_reasons, "cap_diag": cap_diag},
                )
                sell_verdict = PolicyVerdict(
                    allow=False,
                    action="HOLD",
                    reason_codes=reasons,
                    diagnostics={**sell_verdict.diagnostics, "guard_reasons": guard_reasons, "cap_diag": cap_diag},
                )
            prev_fsm_state = fsm.status().state.value
            if buy_verdict.action == "FREEZE" or sell_verdict.action == "FREEZE":
                fsm.freeze("policy_freeze")
                is_frozen_any = True
                freeze_reasons.extend(reasons)
                self.pending_freeze[token_id] = reasons
            else:
                self.pending_freeze.pop(token_id, None)
                if reasons:
                    degraded_reasons.extend(reasons)
                    degraded_by_market[token_id] = list(reasons)
                if fsm.status().state == ExecutionState.FROZEN and not reasons:
                    fsm.unfreeze()
            self._record_fsm_transition(
                token_id=token_id,
                prev_state=prev_fsm_state,
                new_state=fsm.status().state.value,
                ts_ms=now_ms,
                reason="policy_verdict",
            )

            spread_mult = 2.0 if book_health == BookHealthState.STALE else 1.0
            if bool(cap_diag.get("soft_breach", False)):
                spread_mult = max(spread_mult, 1.5)
            bid_px, ask_px = self._compute_quotes(
                q=q,
                mid=snap.mid(),
                constraint=constraint,
                half_spread_bps=half_spread_bps * spread_mult,
                inventory_skew=self.inventory_yes[token_id] * inventory_skew_per_unit,
                risk_padding_bps=risk_padding_bps,
            )

            self._decision_seq += 1
            decision_id = f"{self.run_epoch_ms}:{token_id}:{self._decision_seq}"
            self._record_decision(
                now_ms=now_ms,
                decision_id=decision_id,
                token_id=token_id,
                market=str(meta.get("slug") or token_id),
                action="FREEZE"
                if ("C_BOOK_DOWN" in reasons or buy_verdict.action == "FREEZE" or sell_verdict.action == "FREEZE")
                else ("QUOTE" if (buy_verdict.allow or sell_verdict.allow) else "SKIP"),
                reason_codes=reasons,
                p_hat=q,
                expected_edge=0.0,
                expected_cost=0.0,
                decision_ts_event_ms=decision_ts_event_ms,
                book_asof_ts_ms=clamped_book_asof_ts,
                pstar_asof_ts_ms=clamped_pstar_asof_ts,
                max_feature_ts_ms=feature_max_ts,
                policy_json={
                    "buy": buy_verdict.diagnostics,
                    "sell": sell_verdict.diagnostics,
                    "book_health_state": book_health.value,
                    "cap_diag": cap_diag,
                    "codes": reasons,
                    "guard_reasons": guard_reasons,
                    "causality": causality_diag,
                },
                fsm_state=fsm.status().state.value,
                pstar_diag=pstar.diagnostics,
            )
            self._record_decision_tick(
                now_ms=now_ms,
                token_id=token_id,
                decision_id=decision_id,
                decision_ts_ms=decision_ts_event_ms,
                max_feature_ts_ms=feature_max_ts,
                snap=snap,
                pstar=pstar,
                book_asof_ts_ms=clamped_book_asof_ts,
                pstar_asof_ts_ms=clamped_pstar_asof_ts,
                ws_lag_ms=ws_lag_ms,
                pstar_age_ms=pstar_age_ms,
                signal_age_ms=float(signal_age_ms),
                allow_action=bool(buy_verdict.allow or sell_verdict.allow),
                block_reason_codes=reasons,
                payload={
                    "buy_action": buy_verdict.action,
                    "sell_action": sell_verdict.action,
                    "book_health_state": book_health.value,
                    "guard_reasons": guard_reasons,
                    "causality": causality_diag,
                },
            )
            self._record_microstructure(
                now_ms=now_ms,
                token_id=token_id,
                book_health=book_health.value,
                spread_bps=snap.spread_bps(),
                depth_at_qty_buy=depth_buy,
                depth_at_qty_sell=depth_sell,
                slippage_bps_buy=slip_buy,
                slippage_bps_sell=slip_sell,
                effective_spread_bps_buy=eff_buy,
                effective_spread_bps_sell=eff_sell,
            )

            if book_health != BookHealthState.DOWN and not guard_reasons:
                await self._apply_side(token_id, "buy", bid_px, target_size, constraint, buy_verdict, now_ms, decision_id)
                await self._apply_side(token_id, "sell", ask_px, target_size, constraint, sell_verdict, now_ms, decision_id)
            else:
                if book_health == BookHealthState.DOWN:
                    self.db.append_alert(now_ms, "critical", "BOOK_DOWN_FREEZE", f"{token_id}:health={book_health.value}")
            self._record_inventory(now_ms, token_id)

        freeze_reason_codes = sorted(set(freeze_reasons))
        degraded_reason_codes = sorted(set(degraded_reasons))
        alert_state = ALERT_STATE_OK
        state_reason_codes: List[str] = []
        if is_frozen_any:
            alert_state = ALERT_STATE_FROZEN
            state_reason_codes = freeze_reason_codes
        elif degraded_reason_codes:
            alert_state = ALERT_STATE_DEGRADED
            state_reason_codes = degraded_reason_codes
        readiness_payload = {
            "readiness_state": str(self._startup_readiness_state),
            "readiness_reason_codes": list(self._startup_readiness_reason_codes),
            "readiness_payload": dict(self._startup_readiness_payload),
        }
        a_pipeline_diag = self._a_pipeline_diag(now_ms=int(now_ms))
        policy_cfg = self.constitution.get("policy", {}) if isinstance(self.constitution, dict) else {}
        execution_cfg = self.constitution.get("execution", {}) if isinstance(self.constitution, dict) else {}
        trading_cfg = self.constitution.get("trading", {}) if isinstance(self.constitution, dict) else {}
        self.db.upsert_system_state(
            as_of_ts=now_ms,
            is_frozen=is_frozen_any,
            reasons=",".join(state_reason_codes),
            mode=self.mode,
            payload={
                "alert_state": str(alert_state),
                "freeze_by_market": self.pending_freeze,
                "degraded_by_market": degraded_by_market,
                "freeze_reasons": freeze_reason_codes,
                "degraded_reasons": degraded_reason_codes,
                "a_pipeline_diag": a_pipeline_diag,
                "active_policy": {
                    "max_spread_bps": float(policy_cfg.get("max_spread_bps", self.policy_thresholds.max_spread_bps)),
                    "max_slippage_bps": float(policy_cfg.get("max_slippage_bps", self.policy_thresholds.max_slippage_bps)),
                    "min_depth_at_qty": float(policy_cfg.get("min_depth_at_qty", self.policy_thresholds.min_depth_at_qty)),
                },
                "active_execution": {
                    "maker_half_spread_bps": float(execution_cfg.get("maker_half_spread_bps", 40.0)),
                    "maker_quote_size": float(execution_cfg.get("maker_quote_size", 1.0)),
                },
                "active_trading": {
                    "paper_experiment_profile": str(trading_cfg.get("paper_experiment_profile") or ""),
                },
                **readiness_payload,
            },
        )

    async def _apply_side(
        self,
        token_id: str,
        side: str,
        price: float,
        qty: float,
        constraint: OrderConstraints,
        verdict,
        now_ms: int,
        decision_id: str,
    ) -> None:
        current = self.open_quotes[token_id].get(side)
        if not verdict.allow:
            if current is not None and self.mode in {"PAPER", "TRADE"} and self.broker is not None:
                for event in await self._broker_cancel(current.order_id):
                    self._handle_broker_event(token_id, side, event, decision_id)
                self.open_quotes[token_id].pop(side, None)
            if verdict.action == "FREEZE":
                freeze_severity = "critical"
                freeze_code = "POLICY_FREEZE"
                if self.mode == "OBSERVE":
                    freeze_severity = "warning"
                    freeze_code = "POLICY_FREEZE_OBSERVE"
                self._emit_rate_limited_alert(
                    now_ms,
                    severity=freeze_severity,
                    code=freeze_code,
                    message=f"{token_id}:{side}:{','.join(verdict.reason_codes)}",
                    payload={
                        "token_id": str(token_id),
                        "side": str(side),
                        "state": ALERT_STATE_FROZEN if verdict.action == "FREEZE" else ALERT_STATE_DEGRADED,
                        "reason_codes": list(verdict.reason_codes),
                    },
                    dedupe_key=f"policy_freeze:{token_id}:{side}",
                )
            return

        if self.mode == "OBSERVE" or self.broker is None:
            return

        risk_reasons = self._risk_budget_reasons(now_ms)
        if risk_reasons:
            self.db.append_alert(
                int(now_ms),
                "critical",
                "RISK_THROTTLE_CRITICAL",
                f"{token_id}:{side}:{','.join(sorted(set(risk_reasons)))}",
                payload={
                    "risk_reasons": sorted(set(risk_reasons)),
                    "orders_per_min": int(self._count_recent(self._submit_event_ts, now_ms)),
                    "cancels_per_min": int(self._count_recent(self._cancel_event_ts, now_ms)),
                    "daily_notional_usdc": float(self._daily_notional_usdc),
                    "daily_loss_usdc": float(self._daily_loss_usdc),
                },
            )
            return

        needs_replace = current is not None and (abs(current.price - price) >= max(constraint.min_tick, 1e-6) or abs(current.qty - qty) > 1e-9)
        needs_submit = current is None
        if not (needs_submit or needs_replace):
            return

        revision_key = (token_id, side)
        self._quote_revision[revision_key] += 1
        revision = self._quote_revision[revision_key]
        order_id = f"{self.run_epoch_ms}:{token_id}:{side}:{self._decision_seq}:{revision}"
        client_order_id = f"{order_id}:client"
        quote_group_id = f"{self.run_epoch_ms}:{token_id}:{self._decision_seq}"
        idempotency_key = f"{self.run_epoch_ms}:{token_id}:{side}:{self._decision_seq}:{revision}"
        intent = OrderIntent(
            order_id=order_id,
            client_order_id=client_order_id,
            asset_id=token_id,
            side=side,
            size=float(qty),
            price=float(price),
            mode="MAKE",
            t_decision_wall_ms=int(now_ms),
            as_of_ts_ms=int(now_ms),
            decision_id=decision_id,
            reason="quote_update",
            post_only=True,
            time_in_force="GTC",
            reduce_only=False,
            quote_group_id=quote_group_id,
            idempotency_key=idempotency_key,
        )

        events: List[BrokerEvent] = []
        if needs_submit:
            events = await self._broker_submit(intent)
        elif needs_replace:
            events = await self._broker_replace(current.order_id, intent)
        self._post_only_attempts_by_token[token_id] += 1
        if not events:
            return

        rejected_post_only = False
        has_submit = False
        has_ack = False
        has_reject = False
        for event in events:
            self._handle_broker_event(token_id, side, event, decision_id)
            if event.event_type == "order_submit":
                has_submit = True
            elif event.event_type == "order_ack":
                has_ack = True
            elif event.event_type in {"order_reject", "broker_error"}:
                has_reject = True
            if event.event_type == "order_reject":
                reason = str(event.payload.get("reason") or "")
                code = str(event.payload.get("error_code") or "")
                if "POST_ONLY" in reason.upper() or "POST_ONLY" in code.upper():
                    rejected_post_only = True
                    self._post_only_rejects_by_token[token_id] += 1

        if rejected_post_only:
            retried = await self._retry_post_only(token_id, side, intent, constraint, decision_id)
            if retried is not None:
                self.open_quotes[token_id][side] = OpenQuote(
                    order_id=intent.order_id,
                    client_order_id=intent.client_order_id,
                    side=side,
                    price=float(retried),
                    qty=float(qty),
                    post_only=True,
                    quote_group_id=quote_group_id,
                    idempotency_key=idempotency_key,
                    updated_ms=now_ms,
                )
                return
            self.open_quotes[token_id].pop(side, None)
            return

        if has_reject and not (has_submit or has_ack):
            self.open_quotes[token_id].pop(side, None)
            return

        if has_submit or has_ack:
            self.open_quotes[token_id][side] = OpenQuote(
                order_id=intent.order_id,
                client_order_id=intent.client_order_id,
                side=side,
                price=float(price),
                qty=float(qty),
                post_only=True,
                quote_group_id=quote_group_id,
                idempotency_key=idempotency_key,
                updated_ms=now_ms,
            )
        else:
            self.open_quotes[token_id].pop(side, None)

    async def _retry_post_only(
        self,
        token_id: str,
        side: str,
        intent: OrderIntent,
        constraint: OrderConstraints,
        decision_id: str,
    ) -> Optional[float]:
        tick = max(constraint.min_tick, 1e-6)
        new_price = intent.price - tick if side == "buy" else intent.price + tick
        new_price = _clamp(new_price, constraint.min_price, constraint.max_price)
        retry_intent = OrderIntent(
            order_id=intent.order_id,
            client_order_id=intent.client_order_id,
            asset_id=intent.asset_id,
            side=intent.side,
            size=intent.size,
            price=float(new_price),
            mode=intent.mode,
            t_decision_wall_ms=intent.t_decision_wall_ms,
            as_of_ts_ms=intent.as_of_ts_ms,
            decision_id=decision_id,
            reason="post_only_reprice",
            post_only=True,
            time_in_force=intent.time_in_force,
            reduce_only=intent.reduce_only,
            quote_group_id=intent.quote_group_id,
            idempotency_key=f"{decision_id}:{side}:retry",
        )
        self._post_only_attempts_by_token[token_id] += 1
        events = await self._broker_submit(retry_intent)
        for event in events:
            self._handle_broker_event(token_id, side, event, decision_id)
        any_reject = any(event.event_type in {"order_reject", "broker_error"} for event in events)
        return None if any_reject else float(new_price)

    async def _emergency_unwind(self, token_id: str, now_ms: int, constraint: OrderConstraints) -> None:
        fsm = self.fsms[token_id]
        status = fsm.status()
        qty = abs(status.net_qty)
        if qty <= 0:
            self._unwind_state.pop(token_id, None)
            fsm.reset_if_flat()
            return
        if self.mode == "OBSERVE" or self.broker is None:
            prev_state = fsm.status().state.value
            fsm.freeze("unwind_required_observe_mode")
            self._record_fsm_transition(
                token_id=token_id,
                prev_state=prev_state,
                new_state=fsm.status().state.value,
                ts_ms=now_ms,
                reason="unwind_required_observe_mode",
            )
            self.db.append_alert(now_ms, "critical", "UNWIND_REQUIRED", f"{token_id}:observe_mode")
            return

        unwind_state = self._unwind_state.setdefault(
            token_id,
            {
                "started_ms": int(now_ms),
                "maker_order_id": None,
                "last_taker_ms": None,
            },
        )
        side = "sell" if status.net_qty > 0 else "buy"
        maker_order_id = unwind_state.get("maker_order_id")
        if not maker_order_id:
            maker_price = self._maker_unwind_price(token_id, side, constraint)
            maker_order_id = f"{token_id}:unwind:maker:{uuid.uuid4().hex[:10]}"
            maker_intent = OrderIntent(
                order_id=maker_order_id,
                client_order_id=f"{maker_order_id}:client",
                asset_id=token_id,
                side=side,
                size=float(qty),
                price=float(maker_price),
                mode="MAKE",
                t_decision_wall_ms=now_ms,
                as_of_ts_ms=now_ms,
                decision_id=f"unwind-maker:{now_ms}",
                reason="hedge_timeout_maker_attempt",
                post_only=True,
                time_in_force="GTC",
                reduce_only=True,
                quote_group_id=f"{token_id}:unwind",
                idempotency_key=f"{token_id}:unwind:maker:{now_ms}",
            )
            events = await self._broker_submit(maker_intent)
            acked = False
            for event in events:
                self._handle_broker_event(token_id, side, event, maker_intent.decision_id or "")
                if event.event_type in {"order_submit", "order_ack"}:
                    acked = True
            if acked:
                unwind_state["maker_order_id"] = maker_order_id
            self.db.append_alert(now_ms, "warning", "UNWIND_MAKER_ATTEMPT", f"{token_id}:{side}:{qty}")
            return

        trading_cfg = self.constitution.get("trading", {}) if isinstance(self.constitution, dict) else {}
        emergency_taker_enabled = bool(trading_cfg.get("emergency_taker_enabled", True))
        taker_after_ms = int(trading_cfg.get("emergency_taker_after_ms", 1000))
        elapsed_ms = int(now_ms - int(unwind_state.get("started_ms", now_ms)))
        if not emergency_taker_enabled:
            if elapsed_ms >= taker_after_ms:
                prev_state = fsm.status().state.value
                fsm.freeze("unwind_timeout_taker_disabled")
                self._record_fsm_transition(
                    token_id=token_id,
                    prev_state=prev_state,
                    new_state=fsm.status().state.value,
                    ts_ms=now_ms,
                    reason="unwind_timeout_taker_disabled",
                )
                self.db.append_alert(now_ms, "critical", "UNWIND_STUCK", f"{token_id}:taker_disabled")
            return
        if elapsed_ms < taker_after_ms:
            return
        last_taker_ms = unwind_state.get("last_taker_ms")
        if isinstance(last_taker_ms, int) and now_ms - last_taker_ms < taker_after_ms:
            return

        maker_order_id = unwind_state.get("maker_order_id")
        if isinstance(maker_order_id, str) and maker_order_id:
            for event in await self._broker_cancel(maker_order_id):
                self._handle_broker_event(token_id, side, event, f"unwind-cancel:{now_ms}")
            unwind_state["maker_order_id"] = None

        taker_price = constraint.min_price if side == "sell" else constraint.max_price
        taker_order_id = f"{token_id}:unwind:taker:{uuid.uuid4().hex[:10]}"
        taker_intent = OrderIntent(
            order_id=taker_order_id,
            client_order_id=f"{taker_order_id}:client",
            asset_id=token_id,
            side=side,
            size=float(qty),
            price=float(taker_price),
            mode="TAKE",
            t_decision_wall_ms=now_ms,
            as_of_ts_ms=now_ms,
            decision_id=f"unwind-taker:{now_ms}",
            reason="hedge_timeout_taker",
            post_only=False,
            time_in_force="IOC",
            reduce_only=True,
            quote_group_id=f"{token_id}:unwind",
            idempotency_key=f"{token_id}:unwind:taker:{now_ms}",
        )
        for event in await self._broker_submit(taker_intent):
            self._handle_broker_event(token_id, side, event, taker_intent.decision_id or "")
        unwind_state["last_taker_ms"] = int(now_ms)
        self.db.append_alert(now_ms, "critical", "EMERGENCY_UNWIND", f"{token_id}:{side}:{qty}")

    def _maker_unwind_price(self, token_id: str, side: str, constraint: OrderConstraints) -> float:
        tick = max(constraint.min_tick, 1e-6)
        snap = self.book_cache.get(token_id)
        if side == "sell":
            if snap is not None and snap.best_ask() is not None:
                return _clamp(_round_up(float(snap.best_ask()), tick), constraint.min_price, constraint.max_price)
            if snap is not None and snap.mid() is not None:
                return _clamp(_round_up(float(snap.mid()) + tick, tick), constraint.min_price, constraint.max_price)
            return _clamp(constraint.max_price - tick, constraint.min_price, constraint.max_price)
        if snap is not None and snap.best_bid() is not None:
            return _clamp(_round_down(float(snap.best_bid()), tick), constraint.min_price, constraint.max_price)
        if snap is not None and snap.mid() is not None:
            return _clamp(_round_down(float(snap.mid()) - tick, tick), constraint.min_price, constraint.max_price)
        return _clamp(constraint.min_price + tick, constraint.min_price, constraint.max_price)

    async def _broker_submit(self, intent: OrderIntent) -> List[BrokerEvent]:
        return await asyncio.to_thread(self.broker.submit, intent)

    async def _broker_cancel(self, order_id: str) -> List[BrokerEvent]:
        return await asyncio.to_thread(self.broker.cancel, order_id)

    async def _broker_replace(self, order_id: str, new_intent: OrderIntent) -> List[BrokerEvent]:
        return await asyncio.to_thread(self.broker.replace, order_id, new_intent)

    def _handle_broker_event(self, token_id: str, side: str, event: BrokerEvent, decision_id: str) -> None:
        ts_ms = int(event.payload.get("t_event_wall_ms") or _now_ms())
        event_id = uuid.uuid4().hex
        status = str(event.payload.get("status") or event.event_type)
        self.db.insert(
            "orders",
            {
                "ts_ms": ts_ms,
                "event_id": event_id,
                "order_id": event.order_id,
                "client_order_id": str(event.payload.get("client_order_id") or ""),
                "token_id": token_id,
                "side": side,
                "price": float(event.payload.get("fill_price") or event.payload.get("price") or 0.0),
                "qty": float(event.payload.get("fill_size") or event.payload.get("size") or 0.0),
                "post_only": 1 if bool(event.payload.get("post_only", True)) else 0,
                "tif": str(event.payload.get("time_in_force") or "GTC"),
                "status": status,
                "reason": str(event.payload.get("reason") or ""),
                "quote_group_id": str(event.payload.get("quote_group_id") or ""),
                "idempotency_key": str(event.payload.get("idempotency_key") or ""),
                "fsm_state": self.fsms[token_id].status().state.value,
                "payload_json": json.dumps(event.payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )
        self._write_trade_tape_event(ts_ms, event_id, event)

        if event.event_type == "order_submit":
            send_ts = int(event.payload.get("t_send_wall_ms") or ts_ms)
            self.send_ts_by_order[event.order_id] = send_ts
            self._order_submit_wall_by_order[event.order_id] = int(send_ts)
            self._order_submit_price_by_order[event.order_id] = float(event.payload.get("price") or 0.0)
            self._order_submit_qty_by_order[event.order_id] = float(event.payload.get("size") or 0.0)
            self._order_submit_token_by_order[event.order_id] = str(token_id)
            self._submit_event_ts.append(int(ts_ms))
            self._submit_event_ts_by_token[token_id].append(int(ts_ms))
        elif event.event_type == "order_ack":
            ack_ts = int(event.payload.get("t_ack_wall_ms") or ts_ms)
            self.ack_ts_by_order[event.order_id] = ack_ts
            send_ts = self.send_ts_by_order.get(event.order_id)
            if send_ts is not None:
                self.send_ack_samples.append(float(max(0, ack_ts - send_ts)))
                self.db.insert(
                    "exec_latency",
                    {
                        "ts_ms": ts_ms,
                        "event_id": uuid.uuid4().hex,
                        "decision_id": decision_id,
                        "token_id": token_id,
                        "signal_age_ms": None,
                        "send_ack_ms": float(max(0, ack_ts - send_ts)),
                        "ack_fill_ms": None,
                        "ws_lag_ms": None,
                        "payload_json": "{}",
                    },
                )
        elif event.event_type == "order_cancel":
            self._cancel_event_ts.append(int(ts_ms))
            self._cancel_event_ts_by_token[token_id].append(int(ts_ms))
        elif event.event_type == "order_fill":
            fill_ts = int(event.payload.get("t_fill_wall_ms") or ts_ms)
            fill_qty = float(event.payload.get("fill_size") or 0.0)
            fill_price = float(event.payload.get("fill_price") or 0.0)
            fee_bps = float(event.payload.get("fees_bps") or 0.0)
            fill_key = str(event.payload.get("fill_event_id") or f"{event.order_id}:{fill_ts}:{fill_qty:.8f}:{side}")
            if fill_key in self._seen_reconcile_fill_ids:
                self.db.insert(
                    "recovery_events",
                    {
                        "ts_ms": int(fill_ts),
                        "event_id": uuid.uuid4().hex,
                        "run_id": self.run_id,
                        "mode": self.mode,
                        "recovery_action": "MISSED_FILL_DUPLICATE_SKIPPED",
                        "token_id": str(token_id),
                        "side": str(side),
                        "order_id": str(event.order_id),
                        "price": _maybe_float(fill_price),
                        "size": _maybe_float(fill_qty),
                        "adopted_order_count": None,
                        "payload_json": json.dumps(
                            {"fill_event_key": fill_key, "reason": "in_memory_seen"},
                            separators=(",", ":"),
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    },
                )
                return
            marked = self.db.mark_fill_event_seen(
                fill_event_key=str(fill_key),
                first_seen_ts_ms=int(fill_ts),
                source="broker_event",
                payload={
                    "order_id": str(event.order_id),
                    "token_id": str(token_id),
                    "side": str(side),
                    "fill_qty": float(fill_qty),
                    "fill_price": float(fill_price),
                },
            )
            if not marked:
                self._seen_reconcile_fill_ids.add(fill_key)
                self.db.insert(
                    "recovery_events",
                    {
                        "ts_ms": int(fill_ts),
                        "event_id": uuid.uuid4().hex,
                        "run_id": self.run_id,
                        "mode": self.mode,
                        "recovery_action": "MISSED_FILL_DUPLICATE_SKIPPED",
                        "token_id": str(token_id),
                        "side": str(side),
                        "order_id": str(event.order_id),
                        "price": _maybe_float(fill_price),
                        "size": _maybe_float(fill_qty),
                        "adopted_order_count": None,
                        "payload_json": json.dumps(
                            {"fill_event_key": fill_key, "reason": "persistent_seen"},
                            separators=(",", ":"),
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    },
                )
                return
            self._seen_reconcile_fill_ids.add(fill_key)
            fill_event_id = str(event.payload.get("fill_event_id") or uuid.uuid4().hex)
            self.db.insert(
                "fills",
                {
                    "ts_ms": fill_ts,
                    "event_id": fill_event_id,
                    "order_id": event.order_id,
                    "token_id": token_id,
                    "side": side,
                    "fill_price": fill_price,
                    "fill_qty": fill_qty,
                    "fee": fee_bps,
                    "liquidity": "maker" if str(event.payload.get("mode") or "").upper() == "MAKE" else "taker",
                    "payload_json": json.dumps(event.payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
                },
            )
            self._fill_event_ts.append(int(fill_ts))
            self._fill_event_ts_by_token[token_id].append(int(fill_ts))
            submit_qty = float(self._order_submit_qty_by_order.get(event.order_id, fill_qty))
            if submit_qty > 0 and fill_qty < submit_qty - 1e-9:
                self._partial_fill_ts_by_token[token_id].append(int(fill_ts))
            if event.order_id not in self._first_fill_seen_by_order:
                submit_ts = self._order_submit_wall_by_order.get(event.order_id)
                if submit_ts is not None:
                    time_to_first_fill_s = max(0.0, float(fill_ts - int(submit_ts)) / 1000.0)
                    self._time_to_first_fill_samples_by_token[token_id].append(float(time_to_first_fill_s))
                self._first_fill_seen_by_order.add(event.order_id)
            self._queue_execution_quality(
                token_id=token_id,
                side=side,
                order_id=event.order_id,
                fill_ts_ms=fill_ts,
                fill_price=fill_price,
                fill_qty=fill_qty,
                fee_bps=fee_bps,
            )
            fill_notional = abs(float(fill_price) * float(fill_qty))
            self._reset_daily_counters_if_needed(fill_ts)
            self._daily_notional_usdc += float(fill_notional)
            mid_ref = self._mid_nearest(token_id, fill_ts)
            signed_edge_bps = self._signed_edge_bps(side, fill_price, mid_ref)
            if signed_edge_bps is not None and signed_edge_bps < 0:
                self._daily_loss_usdc += abs(fill_notional * (float(signed_edge_bps) / 10_000.0))
            ack_ts = self.ack_ts_by_order.get(event.order_id)
            if ack_ts is not None:
                ack_fill_ms = float(max(0, fill_ts - ack_ts))
                self.ack_fill_samples.append(ack_fill_ms)
                self.db.insert(
                    "exec_latency",
                    {
                        "ts_ms": fill_ts,
                        "event_id": uuid.uuid4().hex,
                        "decision_id": decision_id,
                        "token_id": token_id,
                        "signal_age_ms": None,
                        "send_ack_ms": None,
                        "ack_fill_ms": ack_fill_ms,
                        "ws_lag_ms": None,
                        "payload_json": "{}",
                    },
                )
            if side == "buy":
                self.inventory_yes[token_id] += fill_qty
            else:
                self.inventory_yes[token_id] -= fill_qty
            prev_state = self.fsms[token_id].status().state.value
            self.fsms[token_id].on_fill(side=side, qty=fill_qty, ts_ms=fill_ts)
            self.fsms[token_id].reset_if_flat()
            self._record_fsm_transition(
                token_id=token_id,
                prev_state=prev_state,
                new_state=self.fsms[token_id].status().state.value,
                ts_ms=fill_ts,
                reason="order_fill",
            )
        elif event.event_type in {"order_reject", "broker_error"}:
            self.db.append_alert(ts_ms, "warning", "ORDER_REJECT", f"{token_id}:{side}:{event.payload}")

    def _write_trade_tape_event(self, ts_ms: int, event_id: str, event: BrokerEvent) -> None:
        event_type = event.event_type
        if event_type not in {"order_submit", "order_ack", "order_fill", "order_cancel", "order_reject", "broker_error"}:
            return
        if event.order_id not in self._trade_tape_parent_event_ids:
            intent_event_id = self.trade_tape.next_event_id()
            self.trade_tape.write(
                {
                    "schema_version": "trade_v1",
                    "run_id": self.trade_tape.run_id,
                    "event_id": intent_event_id,
                    "parent_event_id": None,
                    "event_type": "order_intent",
                    "order_id": event.order_id,
                    "client_order_id": str(event.payload.get("client_order_id") or f"{event.order_id}:client"),
                    "asset_id": str(event.payload.get("asset_id") or ""),
                    "side": str(event.payload.get("side") or ""),
                    "size": float(event.payload.get("size") or 0.0),
                    "price": float(event.payload.get("price") or 0.0),
                    "mode": str(event.payload.get("mode") or "MAKE"),
                    "t_decision_wall_ms": ts_ms,
                    "t_event_wall_ms": ts_ms,
                    "t_event_mono_ns": int(self.time_mapper.mono_ns_from_wall_ms(ts_ms)),
                    "as_of_ts_ms": ts_ms,
                }
            )
            self._trade_tape_parent_event_ids[event.order_id] = int(intent_event_id)

        parent_event_id = self._trade_tape_parent_event_ids.get(event.order_id)
        if parent_event_id is None:
            return
        payload: Dict[str, Any] = {
            "schema_version": "trade_v1",
            "run_id": self.trade_tape.run_id,
            "event_id": self.trade_tape.next_event_id(),
            "parent_event_id": parent_event_id,
            "event_type": event_type,
            "order_id": event.order_id,
            "t_event_wall_ms": ts_ms,
            "t_event_mono_ns": int(self.time_mapper.mono_ns_from_wall_ms(ts_ms)),
            "as_of_ts_ms": ts_ms,
        }
        if event_type == "order_submit":
            payload.update(
                {
                    "broker": str(event.payload.get("broker") or "polymarket"),
                    "status": str(event.payload.get("status") or "submitted"),
                    "t_send_wall_ms": int(event.payload.get("t_send_wall_ms") or ts_ms),
                }
            )
        elif event_type == "order_ack":
            payload.update(
                {
                    "broker": str(event.payload.get("broker") or "polymarket"),
                    "status": str(event.payload.get("status") or "accepted"),
                    "t_ack_wall_ms": int(event.payload.get("t_ack_wall_ms") or ts_ms),
                }
            )
        elif event_type == "order_fill":
            payload.update(
                {
                    "fill_price": float(event.payload.get("fill_price") or 0.0),
                    "fill_size": float(event.payload.get("fill_size") or 0.0),
                    "fees_bps": float(event.payload.get("fees_bps") or 0.0),
                    "t_fill_wall_ms": int(event.payload.get("t_fill_wall_ms") or ts_ms),
                }
            )
        elif event_type == "order_cancel":
            payload.update({"reason": str(event.payload.get("reason") or "CANCELED")})
        elif event_type == "order_reject":
            payload.update({"reason": str(event.payload.get("reason") or "REJECTED")})
        elif event_type == "broker_error":
            payload.update({"error_code": str(event.payload.get("error_code") or "BROKER_ERROR")})
        try:
            self.trade_tape.write(payload)
        except Exception:
            # Keep runtime resilient even if tape schema rejects auxiliary rows.
            pass

    def _record_book_snapshot(self, snap: BookSnapshot) -> None:
        rows = [
            {
                "ts_ms": int(snap.ts_event_ms or snap.ts_recv_wall_ms),
                "token_id": token,
                "side": side,
                "price": price,
                "size": size,
                "source": "ws",
            }
            for token, side, price, size, _ in snap.to_l2_rows()
        ]
        if rows:
            self.db.insert_many("market_data_book", rows)

    def _record_pstar(self, now_ms: int, symbol: str, pstar: PStar) -> None:
        if not symbol:
            return
        sources = ",".join(sorted(pstar.sources_used))
        self._latest_pstar_by_symbol[symbol] = pstar
        state_now = str(self._pstar_state(pstar, int(now_ms)))
        state_prev = self._pstar_state_by_symbol.get(symbol)
        if state_prev and state_prev != state_now:
            transition_key = f"{state_prev}->{state_now}"
            self._pstar_transition_counts[transition_key] = int(self._pstar_transition_counts.get(transition_key, 0)) + 1
        self._pstar_state_by_symbol[symbol] = state_now
        self.db.insert(
            "pstar",
            {
                "ts_ms": now_ms,
                "symbol": symbol or "",
                "value": pstar.value,
                "ts_event_ms": pstar.ts_event_ms,
                "pstar_recv_ts_ms": _maybe_int(pstar.ts_recv_ms),
                "confidence": float(pstar.confidence),
                "valid": 1 if pstar.valid else 0,
                "invalid_reason": str(pstar.invalid_reason or ""),
                "sources_used": sources,
                "diagnostics_json": json.dumps(
                    {**(pstar.diagnostics or {}), "state": state_now},
                    separators=(",", ":"),
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            },
        )
        if int(self._last_pstar_stats_ts_by_symbol.get(symbol, -1)) == int(now_ms):
            return
        self._last_pstar_stats_ts_by_symbol[symbol] = int(now_ms)
        diag = pstar.diagnostics or {}
        age_ms = diag.get("age_ms") if isinstance(diag.get("age_ms"), dict) else {}
        self.db.insert(
            "pstar_stats",
            {
                "ts_ms": now_ms,
                "symbol": symbol or "",
                "p_spot": _maybe_float(diag.get("spot_px")),
                "age_spot_ms": _maybe_int(age_ms.get("spot") if isinstance(age_ms, dict) else None),
                "p_perp": _maybe_float(diag.get("perp_px")),
                "age_perp_ms": _maybe_int(age_ms.get("perp") if isinstance(age_ms, dict) else None),
                "disagreement_bps": _maybe_float(diag.get("disagreement_bps")),
                "confidence": float(pstar.confidence),
                "valid": 1 if pstar.valid else 0,
            },
        )

    def _record_decision(
        self,
        now_ms: int,
        decision_id: str,
        token_id: str,
        market: str,
        action: str,
        reason_codes: List[str],
        p_hat: Optional[float],
        expected_edge: float,
        expected_cost: float,
        decision_ts_event_ms: int,
        book_asof_ts_ms: Optional[int],
        pstar_asof_ts_ms: Optional[int],
        max_feature_ts_ms: int,
        policy_json: Dict[str, Any],
        fsm_state: str,
        pstar_diag: Dict[str, Any],
    ) -> None:
        self.db.insert(
            "decisions",
            {
                "ts_ms": now_ms,
                "decision_id": decision_id,
                "market": market,
                "token_id": token_id,
                "action": action,
                "reason_codes": ",".join(reason_codes),
                "p_hat": p_hat,
                "expected_edge": expected_edge,
                "expected_cost": expected_cost,
                "decision_ts_event_ms": int(decision_ts_event_ms),
                "book_asof_ts_ms": _maybe_int(book_asof_ts_ms),
                "pstar_asof_ts_ms": _maybe_int(pstar_asof_ts_ms),
                "max_feature_ts_ms": int(max_feature_ts_ms),
                "policy_json": json.dumps(policy_json, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )
        # Compatibility decision tape row for existing audit/replay tooling.
        record = DecisionRecord(
            schema_version="decision_v4_system",
            engine_version="run_system_v1",
            run_id=self.decision_tape.run_id,
            t_decision_wall_iso=_utc_iso_from_ms(now_ms),
            t_decision_wall_ms=now_ms,
            t_decision_mono_ns=int(self.time_mapper.mono_ns_from_wall_ms(now_ms)),
            asset_id=token_id,
            market_slug=market,
            condition_id=None,
            token_id=token_id,
            outcome=None,
            outcome_by_token=None,
            book={},
            p_market_mid=None,
            p_market_exec_buy=None,
            p_market_exec_sell=None,
            p_market=None,
            p_fair=p_hat,
            edge_net_buy=None,
            edge_net_sell=None,
            p_star={
                "value": pstar_diag.get("value"),
                "confidence": pstar_diag.get("confidence"),
                "diagnostics": pstar_diag,
            },
            labels=None,
            features_raw=None,
            features_ortho=None,
            whitening=None,
            gates={"allow": action == "QUOTE", "reasons": reason_codes},
            exec_cost={},
            notes={
                "policy": policy_json,
                "action": action,
            },
            as_of_ts_ms=now_ms,
            pstar_diag=pstar_diag,
            policy_codes=reason_codes,
            latency={},
            fsm_state=fsm_state,
        )
        self.decision_tape.write(record)

    def _record_decision_tick(
        self,
        now_ms: int,
        token_id: str,
        decision_id: str,
        decision_ts_ms: int,
        max_feature_ts_ms: int,
        snap: BookSnapshot,
        pstar: PStar,
        book_asof_ts_ms: Optional[int],
        pstar_asof_ts_ms: Optional[int],
        ws_lag_ms: Optional[float],
        pstar_age_ms: Optional[float],
        signal_age_ms: float,
        allow_action: bool,
        block_reason_codes: List[str],
        payload: Dict[str, Any],
    ) -> None:
        self.db.insert(
            "decision_ticks",
            {
                "ts_ms": int(now_ms),
                "event_id": uuid.uuid4().hex,
                "decision_ts_ms": int(decision_ts_ms),
                "token_id": str(token_id),
                "decision_id": str(decision_id),
                "book_asof_ts_ms": _maybe_int(book_asof_ts_ms),
                "book_recv_ts_ms": _maybe_int(snap.book_recv_ts_ms),
                "book_seq": int(snap.book_seq),
                "book_level_count": int(snap.book_level_count),
                "book_health_state": str(snap.book_health_state or ""),
                "pstar_value": _maybe_float(pstar.value),
                "pstar_asof_ts_ms": _maybe_int(pstar_asof_ts_ms),
                "pstar_recv_ts_ms": _maybe_int(pstar.ts_recv_ms),
                "pstar_sourceset": json.dumps(sorted(pstar.sources_used), separators=(",", ":"), ensure_ascii=True),
                "pstar_confidence": float(pstar.confidence),
                "pstar_valid": 1 if pstar.valid else 0,
                "invalid_reason": str(pstar.invalid_reason or ""),
                "max_feature_ts_ms": int(max_feature_ts_ms),
                "ws_lag_ms": _maybe_float(ws_lag_ms),
                "pstar_age_ms": _maybe_float(pstar_age_ms),
                "signal_age_ms": _maybe_float(signal_age_ms),
                "allow_action": 1 if allow_action else 0,
                "block_reason_codes": ",".join(sorted(set(block_reason_codes))),
                "payload_json": json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )

    def _snapshot_book(self, token_id: str, book: OrderBook, now_ms: int) -> BookSnapshot:
        last_recv = int(book.last_recv_mono_ns or 0)
        prev_recv = int(self._last_book_recv_mono_by_token.get(token_id, 0))
        if last_recv > 0 and last_recv != prev_recv:
            self._book_seq_by_token[token_id] += 1
            self._book_update_count_by_token[token_id] += 1
            self._last_book_recv_mono_by_token[token_id] = last_recv
            self._book_recv_ts_ms_by_token[token_id].append(int(now_ms))
        snap = BookSnapshot.from_order_book(
            token_id=token_id,
            book=book,
            ts_recv_wall_ms=now_ms,
            book_seq=int(self._book_seq_by_token[token_id]),
        )
        book_health = snap.health_state(
            now_wall_ms=now_ms,
            stale_after_ms=self._book_stale_after_ms,
            down_after_ms=self._book_down_after_ms,
        ).value
        snap = BookSnapshot(
            token_id=snap.token_id,
            bids=snap.bids,
            asks=snap.asks,
            ts_event_ms=snap.ts_event_ms,
            ts_recv_mono_ns=snap.ts_recv_mono_ns,
            ts_recv_wall_ms=snap.ts_recv_wall_ms,
            book_asof_ts_ms=_maybe_int(snap.book_asof_ts_ms),
            book_recv_ts_ms=_maybe_int(snap.book_recv_ts_ms),
            book_seq=snap.book_seq,
            book_level_count=snap.book_level_count,
            book_health_state=book_health,
        )
        age_ms = snap.age_ms(now_ms)
        if age_ms is not None:
            self._book_age_samples_by_token[token_id].append(float(age_ms))
        mid = snap.mid()
        if mid is not None:
            self._mid_history_by_token[token_id].append((int(now_ms), float(mid)))
        self._latest_book_snapshot_by_token[token_id] = snap
        return snap

    def _record_inventory(self, now_ms: int, token_id: str) -> None:
        self.db.insert(
            "inventory",
            {
                "ts_ms": now_ms,
                "token_id": token_id,
                "yes_qty": float(self.inventory_yes[token_id]),
                "no_qty": float(self.inventory_no[token_id]),
                "usdc": None,
                "source": "offchain",
                "payload_json": "{}",
            },
        )

    def _record_fsm_transition(
        self,
        token_id: str,
        prev_state: str,
        new_state: str,
        ts_ms: int,
        reason: str,
    ) -> None:
        self._last_fsm_state_by_token[token_id] = str(new_state)
        if prev_state == new_state:
            return
        self.db.insert(
            "recovery_events",
            {
                "ts_ms": int(ts_ms),
                "event_id": uuid.uuid4().hex,
                "run_id": self.run_id,
                "mode": self.mode,
                "recovery_action": "FSM_TRANSITION",
                "token_id": token_id,
                "side": None,
                "order_id": None,
                "price": None,
                "size": None,
                "adopted_order_count": None,
                "payload_json": json.dumps(
                    {
                        "prev_state": prev_state,
                        "new_state": new_state,
                        "reason": reason,
                    },
                    separators=(",", ":"),
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            },
        )

    def _record_microstructure(
        self,
        now_ms: int,
        token_id: str,
        book_health: str,
        spread_bps: Optional[float],
        depth_at_qty_buy: float,
        depth_at_qty_sell: float,
        slippage_bps_buy: Optional[float],
        slippage_bps_sell: Optional[float],
        effective_spread_bps_buy: Optional[float],
        effective_spread_bps_sell: Optional[float],
    ) -> None:
        attempts = max(0, int(self._post_only_attempts_by_token.get(token_id, 0)))
        rejects = max(0, int(self._post_only_rejects_by_token.get(token_id, 0)))
        reject_rate = float(rejects / attempts) if attempts > 0 else 0.0
        self.db.insert(
            "microstructure_stats",
            {
                "ts_ms": int(now_ms),
                "event_id": uuid.uuid4().hex,
                "token_id": token_id,
                "book_health": str(book_health),
                "spread_bps": _maybe_float(spread_bps),
                "depth_at_qty_buy": float(depth_at_qty_buy),
                "depth_at_qty_sell": float(depth_at_qty_sell),
                "slippage_bps_buy": _maybe_float(slippage_bps_buy),
                "slippage_bps_sell": _maybe_float(slippage_bps_sell),
                "effective_spread_bps_buy": _maybe_float(effective_spread_bps_buy),
                "effective_spread_bps_sell": _maybe_float(effective_spread_bps_sell),
                "post_only_reject_rate": reject_rate,
            },
        )

    @staticmethod
    def _prune_recent_ts(values: Deque[int], now_ms: int, window_ms: int = 60_000) -> None:
        cutoff = int(now_ms - int(window_ms))
        while values and int(values[0]) < cutoff:
            values.popleft()

    @staticmethod
    def _count_recent(values: Deque[int], now_ms: int, window_ms: int = 60_000) -> int:
        RuntimeEngine._prune_recent_ts(values, now_ms=now_ms, window_ms=window_ms)
        return int(len(values))

    @staticmethod
    def _signed_edge_bps(side: str, fill_price: float, ref_price: Optional[float]) -> Optional[float]:
        if ref_price is None:
            return None
        if fill_price <= 0:
            return None
        side_norm = str(side or "").lower()
        if side_norm == "buy":
            return float((float(ref_price) - float(fill_price)) / float(fill_price) * 10_000.0)
        return float((float(fill_price) - float(ref_price)) / float(fill_price) * 10_000.0)

    def _mid_nearest(self, token_id: str, target_ts_ms: int) -> Optional[float]:
        history = self._mid_history_by_token.get(token_id)
        if not history:
            return None
        target = int(target_ts_ms)
        best_mid: Optional[float] = None
        best_dist: Optional[int] = None
        for ts_ms, mid in history:
            dist = abs(int(ts_ms) - target)
            if best_dist is None or dist < best_dist or (dist == best_dist and int(ts_ms) < target):
                best_mid = float(mid)
                best_dist = dist
        return best_mid

    def _queue_execution_quality(
        self,
        *,
        token_id: str,
        side: str,
        order_id: str,
        fill_ts_ms: int,
        fill_price: float,
        fill_qty: float,
        fee_bps: float,
    ) -> None:
        send_ts = _maybe_int(self.send_ts_by_order.get(order_id))
        ack_ts = _maybe_int(self.ack_ts_by_order.get(order_id))
        pending = ExecutionAttributionPending(
            event_id=uuid.uuid4().hex,
            token_id=str(token_id),
            order_id=str(order_id),
            side=str(side),
            fill_ts_ms=int(fill_ts_ms),
            fill_price=float(fill_price),
            fill_qty=float(fill_qty),
            fee_bps=float(fee_bps),
            mid_at_send=self._mid_nearest(str(token_id), int(send_ts)) if send_ts is not None else None,
            mid_at_ack=self._mid_nearest(str(token_id), int(ack_ts)) if ack_ts is not None else None,
            mid_at_fill=self._mid_nearest(str(token_id), int(fill_ts_ms)),
            due_ts_ms=int(fill_ts_ms + 30_000),
        )
        self._pending_execution_quality[pending.event_id] = pending

    def _flush_execution_quality(self, now_ms: int) -> int:
        due_ids = [
            event_id
            for event_id, pending in self._pending_execution_quality.items()
            if int(pending.due_ts_ms) <= int(now_ms)
        ]
        if not due_ids:
            return 0
        inserted = 0
        for event_id in sorted(
            due_ids,
            key=lambda item: (
                int(self._pending_execution_quality[item].fill_ts_ms),
                str(item),
            ),
        ):
            pending = self._pending_execution_quality.pop(event_id, None)
            if pending is None:
                continue
            mid_1s = self._mid_nearest(pending.token_id, int(pending.fill_ts_ms + 1_000))
            mid_5s = self._mid_nearest(pending.token_id, int(pending.fill_ts_ms + 5_000))
            mid_30s = self._mid_nearest(pending.token_id, int(pending.fill_ts_ms + 30_000))
            realized_ref = pending.mid_at_ack if pending.mid_at_ack is not None else pending.mid_at_fill
            realized_spread_bps = self._signed_edge_bps(
                pending.side,
                pending.fill_price,
                realized_ref,
            )
            markout_1s = self._signed_edge_bps(pending.side, pending.fill_price, mid_1s)
            markout_5s = self._signed_edge_bps(pending.side, pending.fill_price, mid_5s)
            markout_30s = self._signed_edge_bps(pending.side, pending.fill_price, mid_30s)
            net_edge_bps = None
            if realized_spread_bps is not None:
                net_edge_bps = float(realized_spread_bps - float(pending.fee_bps))
            payload = {
                "pending_due_ts_ms": int(pending.due_ts_ms),
                "mid_missing": {
                    "mid_at_send": pending.mid_at_send is None,
                    "mid_at_ack": pending.mid_at_ack is None,
                    "mid_at_fill": pending.mid_at_fill is None,
                    "mid_1s": mid_1s is None,
                    "mid_5s": mid_5s is None,
                    "mid_30s": mid_30s is None,
                },
            }
            self.db.insert(
                "execution_quality",
                {
                    "ts_ms": int(now_ms),
                    "event_id": str(pending.event_id),
                    "run_id": self.run_id,
                    "mode": self.mode,
                    "token_id": pending.token_id,
                    "order_id": pending.order_id,
                    "side": pending.side,
                    "fill_ts_ms": int(pending.fill_ts_ms),
                    "fill_price": float(pending.fill_price),
                    "fill_qty": float(pending.fill_qty),
                    "fee_bps": float(pending.fee_bps),
                    "mid_at_send": _maybe_float(pending.mid_at_send),
                    "mid_at_ack": _maybe_float(pending.mid_at_ack),
                    "mid_at_fill": _maybe_float(pending.mid_at_fill),
                    "mid_1s": _maybe_float(mid_1s),
                    "mid_5s": _maybe_float(mid_5s),
                    "mid_30s": _maybe_float(mid_30s),
                    "realized_spread_bps": _maybe_float(realized_spread_bps),
                    "markout_1s_bps": _maybe_float(markout_1s),
                    "markout_5s_bps": _maybe_float(markout_5s),
                    "markout_30s_bps": _maybe_float(markout_30s),
                    "net_edge_bps": _maybe_float(net_edge_bps),
                    "payload_json": json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
                },
            )
            inserted += 1
        return inserted

    def _record_execution_quality_stats(self, now_ms: int) -> None:
        rows = self.db.query(
            """
            SELECT token_id, realized_spread_bps, markout_1s_bps, markout_5s_bps, markout_30s_bps, net_edge_bps
            FROM execution_quality
            WHERE ts_ms >= ?
            ORDER BY ts_ms
            """,
            [int(now_ms - 3_600_000)],
        )
        grouped: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: {
                "realized_spread_bps": [],
                "markout_1s_bps": [],
                "markout_5s_bps": [],
                "markout_30s_bps": [],
                "net_edge_bps": [],
            }
        )
        for token_id, realized, m1, m5, m30, net in rows:
            token_key = str(token_id or "__all__")
            for key, value in (
                ("realized_spread_bps", realized),
                ("markout_1s_bps", m1),
                ("markout_5s_bps", m5),
                ("markout_30s_bps", m30),
                ("net_edge_bps", net),
            ):
                if value is None:
                    continue
                grouped[token_key][key].append(float(value))
                grouped["__all__"][key].append(float(value))

        for token_key in sorted(grouped.keys()):
            series = grouped[token_key]
            sample_count = max(len(series["net_edge_bps"]), len(series["realized_spread_bps"]))
            self.db.insert(
                "execution_quality_stats",
                {
                    "ts_ms": int(now_ms),
                    "event_id": uuid.uuid4().hex,
                    "token_id": str(token_key),
                    "sample_count": int(sample_count),
                    "p50_realized_spread_bps": _maybe_float(_quantile(series["realized_spread_bps"], 0.50)),
                    "p95_realized_spread_bps": _maybe_float(_quantile(series["realized_spread_bps"], 0.95)),
                    "p50_markout_1s_bps": _maybe_float(_quantile(series["markout_1s_bps"], 0.50)),
                    "p95_markout_1s_bps": _maybe_float(_quantile(series["markout_1s_bps"], 0.95)),
                    "p50_markout_5s_bps": _maybe_float(_quantile(series["markout_5s_bps"], 0.50)),
                    "p95_markout_5s_bps": _maybe_float(_quantile(series["markout_5s_bps"], 0.95)),
                    "p50_markout_30s_bps": _maybe_float(_quantile(series["markout_30s_bps"], 0.50)),
                    "p95_markout_30s_bps": _maybe_float(_quantile(series["markout_30s_bps"], 0.95)),
                    "p50_net_edge_bps": _maybe_float(_quantile(series["net_edge_bps"], 0.50)),
                    "p95_net_edge_bps": _maybe_float(_quantile(series["net_edge_bps"], 0.95)),
                },
            )

    def _record_queue_quality_stats(self, now_ms: int) -> None:
        for token_id in sorted(self.books.keys()):
            submits = self._submit_event_ts_by_token[token_id]
            cancels = self._cancel_event_ts_by_token[token_id]
            fills = self._fill_event_ts_by_token[token_id]
            self._prune_recent_ts(submits, now_ms)
            self._prune_recent_ts(cancels, now_ms)
            self._prune_recent_ts(fills, now_ms)
            fill_count = int(len(fills))
            cancel_count = int(len(cancels))
            submit_count = int(len(submits))
            cancel_to_fill_ratio = float(cancel_count / fill_count) if fill_count > 0 else None
            ttf = list(self._time_to_first_fill_samples_by_token[token_id])
            partials = self._partial_fill_ts_by_token[token_id]
            self._prune_recent_ts(partials, now_ms)
            attempts = max(0, int(self._post_only_attempts_by_token.get(token_id, 0)))
            rejects = max(0, int(self._post_only_rejects_by_token.get(token_id, 0)))
            reject_rate = float(rejects / attempts) if attempts > 0 else 0.0
            self.db.insert(
                "queue_quality_stats",
                {
                    "ts_ms": int(now_ms),
                    "event_id": uuid.uuid4().hex,
                    "token_id": str(token_id),
                    "post_only_reject_rate": reject_rate,
                    "cancel_to_fill_ratio": _maybe_float(cancel_to_fill_ratio),
                    "time_to_first_fill_p50_s": _maybe_float(_quantile(ttf, 0.50)),
                    "time_to_first_fill_p95_s": _maybe_float(_quantile(ttf, 0.95)),
                    "partial_fill_count": int(len(partials)),
                    "orders_per_min": float(submit_count),
                    "cancels_per_min": float(cancel_count),
                    "fills_per_min": float(fill_count),
                    "payload_json": json.dumps(
                        {
                            "submit_count": int(submit_count),
                            "cancel_count": int(cancel_count),
                            "fill_count": int(fill_count),
                        },
                        separators=(",", ":"),
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                },
            )

    def _reset_daily_counters_if_needed(self, now_ms: int) -> None:
        day_bucket = datetime.fromtimestamp(int(now_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        if self._daily_bucket_utc == day_bucket:
            return
        self._daily_bucket_utc = day_bucket
        self._daily_notional_usdc = 0.0
        self._daily_loss_usdc = 0.0

    def _risk_budget_reasons(self, now_ms: int) -> List[str]:
        self._reset_daily_counters_if_needed(now_ms)
        reasons: List[str] = []
        orders_per_min = self._count_recent(self._submit_event_ts, now_ms)
        cancels_per_min = self._count_recent(self._cancel_event_ts, now_ms)
        if orders_per_min >= max(1, int(self._max_orders_per_min)):
            reasons.append("RISK_ORDER_RATE_LIMIT")
        if cancels_per_min >= max(1, int(self._max_cancels_per_min)):
            reasons.append("RISK_CANCEL_RATE_LIMIT")
        if float(self._daily_notional_usdc) >= float(max(1.0, self._max_daily_notional_usdc)):
            reasons.append("RISK_DAILY_NOTIONAL_LIMIT")
        if float(self._daily_loss_usdc) >= float(max(0.0, self._max_daily_loss_usdc)):
            reasons.append("RISK_DAILY_LOSS_KILLSWITCH")
        return sorted(set(reasons))

    def _emit_rate_limited_alert(
        self,
        now_ms: int,
        *,
        severity: str,
        code: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
        cooldown_ms: Optional[int] = None,
    ) -> bool:
        key = str(dedupe_key or f"{code}")
        cool_ms = int(max(1, cooldown_ms if cooldown_ms is not None else self._alert_emit_cooldown_ms))
        prev = self._last_alert_emit_ts_by_key.get(key)
        if prev is not None and int(now_ms - int(prev)) < cool_ms:
            return False
        self._last_alert_emit_ts_by_key[key] = int(now_ms)
        self.db.append_alert(
            int(now_ms),
            str(severity),
            str(code),
            str(message),
            payload=payload or {},
        )
        return True

    def _observe_reason_is_hard(self, reason: str, token_id: str) -> bool:
        code = str(reason or "")
        if code in {
            "C_BOOK_DOWN",
            "D_HEDGE_TIMEOUT",
            "RECONCILIATION_MISMATCH_CRITICAL",
            "RISK_THROTTLE_CRITICAL",
            "RISK_DAILY_LOSS_KILLSWITCH",
            "E_LIVENESS_CRITICAL",
            "RECON_UNKNOWN_ORDER_QUARANTINE",
            "RISK_CAP_BREACH",
            "B_CAUSALITY_SUSTAINED",
        }:
            return True
        if code.startswith("B_"):
            streak = int(self._observe_causality_streak_by_token.get(str(token_id), 0))
            return streak >= int(self._observe_causality_freeze_after)
        if code.startswith("D_"):
            return code == "D_HEDGE_TIMEOUT"
        if code.startswith("C_"):
            return code == "C_BOOK_DOWN"
        return False

    def _normalize_verdict_for_mode(
        self,
        verdict: PolicyVerdict,
        reason_codes: List[str],
        token_id: str,
    ) -> PolicyVerdict:
        if self.mode != "OBSERVE":
            return verdict
        if verdict.action != "FREEZE":
            return verdict
        has_hard = any(self._observe_reason_is_hard(code, token_id) for code in reason_codes)
        if has_hard:
            return verdict
        diagnostics = dict(verdict.diagnostics)
        diagnostics["observe_downgraded"] = True
        diagnostics["observe_freeze_reason_codes"] = sorted(set(reason_codes))
        return PolicyVerdict(
            allow=False,
            action="SKIP",
            reason_codes=sorted(set(reason_codes)),
            diagnostics=diagnostics,
        )

    def _pstar_state(self, pstar: PStar, now_ms: int) -> str:
        state = str(getattr(pstar, "state", "") or pstar.diagnostics.get("state") or "")
        if state:
            return state
        if not pstar.valid:
            reason = str(pstar.invalid_reason or "")
            if reason.startswith("stale_source"):
                return "STALE"
            if "disagreement" in reason:
                return "DIVERGED"
            return "UNAVAILABLE"
        if pstar.ts_event_ms is None:
            return "UNAVAILABLE"
        age = max(0, int(now_ms - int(pstar.ts_event_ms)))
        if age > int(self.pstar_builder.max_age_ms):
            return "STALE"
        if len(pstar.sources_used) < 2:
            return "WARMING"
        return "VALID"

    def _a_pipeline_diag(self, now_ms: int) -> Dict[str, Any]:
        state_by_symbol: Dict[str, str] = {}
        for symbol in sorted(self._latest_pstar_by_symbol.keys()):
            pstar = self._latest_pstar_by_symbol[symbol]
            state_by_symbol[str(symbol)] = str(self._pstar_state(pstar, int(now_ms)))
        for symbol, state in self._pstar_state_by_symbol.items():
            if symbol not in state_by_symbol:
                state_by_symbol[str(symbol)] = str(state)
        transition_counts = {
            key: int(self._pstar_transition_counts.get(key, 0))
            for key in sorted(self._pstar_transition_counts.keys())
        }
        return {
            "reference_poll_secs": float(self._reference_poll_secs),
            "pstar_max_age_ms": int(self.pstar_builder.max_age_ms),
            "pstar_state_by_symbol": state_by_symbol,
            "pstar_transition_counts": transition_counts,
        }

    def _causality_violations(
        self,
        decision_ts_event_ms: int,
        feature_max_ts_ms: int,
        book_asof_ts_ms: Optional[int],
        pstar_asof_ts_ms: Optional[int],
    ) -> List[str]:
        reasons: List[str] = []
        if int(feature_max_ts_ms) >= int(decision_ts_event_ms):
            reasons.append("B_FEATURE_TIME_LEAK")
        if book_asof_ts_ms is not None and int(book_asof_ts_ms) >= int(decision_ts_event_ms):
            reasons.append("B_BOOK_TIME_LEAK")
        if pstar_asof_ts_ms is not None and int(pstar_asof_ts_ms) >= int(decision_ts_event_ms):
            reasons.append("B_PSTAR_TIME_LEAK")
        return reasons

    def _build_caps(self, trading_cfg: Dict[str, Any], execution_cfg: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        bankroll_usd = float(trading_cfg.get("bankroll_usd", 10_000.0))
        per_market_abs = float(trading_cfg.get("cap_gross_usd", 500.0))
        per_market_frac = float(trading_cfg.get("cap_gross_bankroll_frac", 0.005))
        per_market_gross = min(per_market_abs, bankroll_usd * per_market_frac)
        per_market_soft = float(trading_cfg.get("cap_soft_ratio", 0.80)) * per_market_gross
        per_market_net = float(trading_cfg.get("cap_net_ratio", 0.20)) * per_market_gross

        portfolio_abs = float(trading_cfg.get("cap_total_gross_usd", 2000.0))
        portfolio_frac = float(trading_cfg.get("cap_total_gross_bankroll_frac", 0.02))
        portfolio_gross = min(portfolio_abs, bankroll_usd * portfolio_frac)
        portfolio_net = float(trading_cfg.get("cap_total_net_ratio", 0.25)) * portfolio_gross

        return {
            "per_market": {
                "gross": float(max(1.0, per_market_gross)),
                "soft": float(max(1.0, per_market_soft)),
                "net": float(max(0.1, per_market_net)),
            },
            "portfolio": {
                "gross": float(max(1.0, portfolio_gross)),
                "net": float(max(0.1, portfolio_net)),
            },
        }

    def _inventory_cap_diagnostics(self, token_id: str, q: float) -> Dict[str, Any]:
        yes_qty = float(self.inventory_yes.get(token_id, 0.0))
        no_qty = float(self.inventory_no.get(token_id, 0.0))
        gross_qty = abs(yes_qty) + abs(no_qty)
        net_qty = yes_qty - no_qty
        token_notional = gross_qty * max(0.01, min(0.99, float(q)))
        token_net_notional = abs(net_qty) * max(0.01, min(0.99, float(q)))

        portfolio_gross = 0.0
        portfolio_net = 0.0
        for tkn in self.books.keys():
            token_q = max(0.01, min(0.99, float(self._last_q_by_token.get(tkn, 0.5))))
            y = float(self.inventory_yes.get(tkn, 0.0))
            n = float(self.inventory_no.get(tkn, 0.0))
            portfolio_gross += (abs(y) + abs(n)) * token_q
            portfolio_net += abs(y - n) * token_q

        per_market_caps = self._cap_state["per_market"]
        portfolio_caps = self._cap_state["portfolio"]
        hard_breach = bool(
            token_notional >= per_market_caps["gross"]
            or token_net_notional >= per_market_caps["net"]
            or portfolio_gross >= portfolio_caps["gross"]
            or portfolio_net >= portfolio_caps["net"]
        )
        soft_breach = bool(token_notional >= per_market_caps["soft"] and not hard_breach)
        return {
            "within_limits": not hard_breach,
            "hard_breach": hard_breach,
            "soft_breach": soft_breach,
            "token_notional": token_notional,
            "token_net_notional": token_net_notional,
            "portfolio_gross": portfolio_gross,
            "portfolio_net": portfolio_net,
            "caps": self._cap_state,
        }

    def _normalize_open_order_row(self, order_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        token_id = str(payload.get("token_id") or "")
        side = str(payload.get("side") or "").lower()
        if not token_id or side not in {"buy", "sell"}:
            return None
        mode = str(payload.get("mode") or "MAKE").upper()
        if mode == "TAKE":
            return None
        row_order_id = str(payload.get("order_id") or order_id)
        updated_ts_ms = _maybe_int(
            payload.get("updated_ts_ms")
            or payload.get("updated_at_ms")
            or payload.get("updated_at")
            or payload.get("ts_ms")
        )
        return {
            "order_id": row_order_id,
            "token_id": token_id,
            "side": side,
            "quote_slot": int(_maybe_int(payload.get("quote_slot")) or 0),
            "client_order_id": str(payload.get("client_order_id") or f"{row_order_id}:client"),
            "price": float(payload.get("price") or 0.0),
            "qty": float(payload.get("size") or 0.0),
            "quote_group_id": str(payload.get("quote_group_id") or f"recovered:{token_id}:{side}"),
            "idempotency_key": str(payload.get("idempotency_key") or f"recovered:{token_id}:{side}"),
            "status": str(payload.get("status") or "open"),
            "updated_ts_ms": int(updated_ts_ms or 0),
            "payload": dict(payload),
        }

    def _open_order_slot_key(self, row: Dict[str, Any]) -> Tuple[str, str, int]:
        return (
            str(row.get("token_id") or ""),
            str(row.get("side") or ""),
            int(_maybe_int(row.get("quote_slot")) or 0),
        )

    def _open_order_duplicate_signature(self, row: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            str(row.get("token_id") or ""),
            str(row.get("side") or ""),
            int(_maybe_int(row.get("quote_slot")) or 0),
            _maybe_float(row.get("price")),
            _maybe_float(row.get("qty")),
        )

    def _select_open_order_plan(
        self,
        open_orders: Dict[str, Any],
    ) -> Tuple[Dict[Tuple[str, str, int], Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        by_slot: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = defaultdict(list)
        expected_slots = {
            (str(token_id), side, 0)
            for token_id in sorted(self.books.keys())
            for side in ("buy", "sell")
        }
        unknown_rows: List[Dict[str, Any]] = []
        for order_id in sorted(open_orders.keys()):
            payload = open_orders.get(order_id)
            if not isinstance(payload, dict):
                continue
            row = self._normalize_open_order_row(str(order_id), payload)
            if row is None:
                continue
            slot_key = self._open_order_slot_key(row)
            if slot_key not in expected_slots:
                unknown_rows.append(row)
                continue
            by_slot[slot_key].append(row)

        keepers: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
        extras: List[Dict[str, Any]] = []
        for slot in sorted(by_slot.keys()):
            rows = sorted(
                by_slot[slot],
                key=lambda row: (
                    int(row.get("updated_ts_ms") or 0),
                    str(row.get("order_id") or ""),
                ),
            )
            if len(rows) > 1 and self._single_level_quoting:
                if not self._startup_allow_exact_duplicate_cleanup:
                    raise RuntimeError(
                        "RECON_STARTUP_INVARIANT_VIOLATION:"
                        f"slot={slot[0]}:{slot[1]}:{slot[2]}:duplicate_count={len(rows)}"
                    )
                signatures = {self._open_order_duplicate_signature(row) for row in rows}
                if len(signatures) > 1:
                    raise RuntimeError(
                        "RECON_STARTUP_INVARIANT_VIOLATION:"
                        f"slot={slot[0]}:{slot[1]}:{slot[2]}:non_exact_duplicates={len(rows)}"
                    )
            keepers[slot] = rows[-1]
            extras.extend(rows[:-1])
        extras = sorted(
            extras,
            key=lambda row: (
                str(row.get("token_id") or ""),
                str(row.get("side") or ""),
                int(_maybe_int(row.get("quote_slot")) or 0),
                str(row.get("order_id") or ""),
            ),
        )
        unknown_rows = sorted(
            unknown_rows,
            key=lambda row: (
                str(row.get("token_id") or ""),
                str(row.get("side") or ""),
                int(_maybe_int(row.get("quote_slot")) or 0),
                str(row.get("order_id") or ""),
            ),
        )
        return keepers, extras, unknown_rows

    def _persist_open_orders_snapshot(self, now_ms: int, open_orders: Dict[str, Any]) -> None:
        if not isinstance(open_orders, dict) or not open_orders:
            return
        rows: List[Dict[str, Any]] = []
        for order_id in sorted(open_orders.keys()):
            payload = open_orders.get(order_id)
            if not isinstance(payload, dict):
                continue
            token_id = str(payload.get("token_id") or "")
            side = str(payload.get("side") or "").lower()
            rows.append(
                {
                    "ts_ms": int(now_ms),
                    "event_id": uuid.uuid4().hex,
                    "run_id": self.run_id,
                    "mode": self.mode,
                    "token_id": token_id,
                    "side": side if side in {"buy", "sell"} else None,
                    "order_id": str(payload.get("order_id") or order_id),
                    "price": _maybe_float(payload.get("price")),
                    "size": _maybe_float(payload.get("size")),
                    "status": str(payload.get("status") or ""),
                    "client_order_id": str(payload.get("client_order_id") or ""),
                    "quote_group_id": str(payload.get("quote_group_id") or ""),
                    "payload_json": json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
                }
            )
        if rows:
            self.db.insert_many("open_orders_snapshot", rows)

    def adopt_open_orders(self, snapshot: BrokerSnapshot, now_ms: int) -> int:
        open_orders = snapshot.open_orders or {}
        if not isinstance(open_orders, dict):
            return 0
        keepers, _, _ = self._select_open_order_plan(open_orders)
        adopted = 0
        for (token_id, side, _slot) in sorted(keepers.keys()):
            row = keepers[(token_id, side, _slot)]
            self.open_quotes[token_id][side] = OpenQuote(
                order_id=str(row["order_id"]),
                client_order_id=str(row["client_order_id"]),
                side=side,
                price=float(row["price"]),
                qty=float(row["qty"]),
                post_only=True,
                quote_group_id=str(row["quote_group_id"]),
                idempotency_key=str(row["idempotency_key"]),
                updated_ms=int(now_ms),
            )
            adopted += 1
            self.db.insert(
                "recovery_events",
                {
                    "ts_ms": int(now_ms),
                    "event_id": uuid.uuid4().hex,
                    "run_id": self.run_id,
                    "mode": self.mode,
                    "recovery_action": "ADOPT_OPEN_ORDER",
                    "token_id": token_id,
                    "side": side,
                    "order_id": str(row["order_id"]),
                    "price": _maybe_float(row.get("price")),
                    "size": _maybe_float(row.get("qty")),
                    "adopted_order_count": adopted,
                    "payload_json": json.dumps(snapshot.meta or {}, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
                },
            )
        return adopted

    async def adopt_open_orders_with_cleanup(self, snapshot: BrokerSnapshot, now_ms: int) -> Dict[str, int]:
        open_orders = snapshot.open_orders or {}
        if not isinstance(open_orders, dict):
            return {"adopted": 0, "duplicates_canceled": 0}
        invariant_payload: Dict[str, Any] = {
            "single_level_quoting": bool(self._single_level_quoting),
            "startup_allow_exact_duplicate_cleanup": bool(self._startup_allow_exact_duplicate_cleanup),
            "open_order_count": int(len(open_orders)),
        }
        try:
            keepers, extras, unknown_rows = self._select_open_order_plan(open_orders)
        except RuntimeError as exc:
            invariant_payload["status"] = "VIOLATION"
            invariant_payload["reason"] = str(exc)
            self.db.insert(
                "recovery_events",
                {
                    "ts_ms": int(now_ms),
                    "event_id": uuid.uuid4().hex,
                    "run_id": self.run_id,
                    "mode": self.mode,
                    "recovery_action": "STARTUP_QUOTING_INVARIANT_CHECK",
                    "token_id": None,
                    "side": None,
                    "order_id": None,
                    "price": None,
                    "size": None,
                    "adopted_order_count": 0,
                    "payload_json": json.dumps(invariant_payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
                },
            )
            raise
        invariant_payload["status"] = "PASS"
        invariant_payload["slot_count"] = int(len(keepers))
        invariant_payload["duplicate_candidates"] = int(len(extras))
        invariant_payload["unknown_order_count"] = int(len(unknown_rows))
        self.db.insert(
            "recovery_events",
            {
                "ts_ms": int(now_ms),
                "event_id": uuid.uuid4().hex,
                "run_id": self.run_id,
                "mode": self.mode,
                "recovery_action": "STARTUP_QUOTING_INVARIANT_CHECK",
                "token_id": None,
                "side": None,
                "order_id": None,
                "price": None,
                "size": None,
                "adopted_order_count": 0,
                "payload_json": json.dumps(invariant_payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )
        adopted = self.adopt_open_orders(snapshot=snapshot, now_ms=now_ms)
        if unknown_rows and self.mode in {"PAPER", "TRADE"}:
            self._startup_unknown_order_quarantine = [dict(row) for row in unknown_rows]
            self._reconciliation_frozen = True
            self._reconciliation_freeze_reason = "RECON_UNKNOWN_ORDER_QUARANTINE"
            self.db.append_alert(
                int(now_ms),
                "critical",
                "RECON_UNKNOWN_ORDER_QUARANTINE",
                f"unknown_open_orders={len(unknown_rows)}",
                payload={
                    "unknown_orders": [
                        {
                            "token_id": str(row.get("token_id") or ""),
                            "side": str(row.get("side") or ""),
                            "order_id": str(row.get("order_id") or ""),
                            "quote_slot": int(_maybe_int(row.get("quote_slot")) or 0),
                            "price": _maybe_float(row.get("price")),
                            "qty": _maybe_float(row.get("qty")),
                        }
                        for row in unknown_rows
                    ]
                },
            )
            self.db.insert(
                "recovery_events",
                {
                    "ts_ms": int(now_ms),
                    "event_id": uuid.uuid4().hex,
                    "run_id": self.run_id,
                    "mode": self.mode,
                    "recovery_action": "UNKNOWN_OPEN_ORDER_QUARANTINE",
                    "token_id": None,
                    "side": None,
                    "order_id": None,
                    "price": None,
                    "size": None,
                    "adopted_order_count": int(adopted),
                    "payload_json": json.dumps(
                        {
                            "unknown_order_count": int(len(unknown_rows)),
                            "freeze_reason": "RECON_UNKNOWN_ORDER_QUARANTINE",
                        },
                        separators=(",", ":"),
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                },
            )
        canceled = 0
        if extras and self.mode in {"PAPER", "TRADE"} and self.broker is not None:
            for row in extras:
                order_id = str(row.get("order_id") or "")
                if not order_id:
                    continue
                events = await self._broker_cancel(order_id)
                canceled_ok = any(event.event_type == "order_cancel" for event in events)
                if canceled_ok:
                    canceled += 1
                token_id = str(row.get("token_id") or "")
                side = str(row.get("side") or "")
                self.db.insert(
                    "recovery_events",
                    {
                        "ts_ms": int(now_ms),
                        "event_id": uuid.uuid4().hex,
                        "run_id": self.run_id,
                        "mode": self.mode,
                        "recovery_action": "CANCEL_DUPLICATE_OPEN_ORDER",
                        "token_id": token_id or None,
                        "side": side if side in {"buy", "sell"} else None,
                        "order_id": order_id,
                        "price": _maybe_float(row.get("price")),
                        "size": _maybe_float(row.get("qty")),
                        "adopted_order_count": adopted,
                        "payload_json": json.dumps(
                            {
                                "events": [event.event_type for event in events],
                                "keepers": sorted(
                                    f"{token}:{slot_side}:{slot}"
                                    for token, slot_side, slot in keepers.keys()
                                ),
                            },
                            separators=(",", ":"),
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    },
                )
        self._persist_open_orders_snapshot(now_ms=now_ms, open_orders=open_orders)
        return {
            "adopted": int(adopted),
            "duplicates_canceled": int(canceled),
            "unknown_quarantined": int(len(unknown_rows)),
        }

    def _fair_probability(self, token_id: str, symbol: str, snap: BookSnapshot, pstar: PStar, now_ms: int) -> float:
        mid = snap.mid()
        if mid is None:
            return 0.5
        q = float(mid)
        if pstar.valid and pstar.value is not None and symbol:
            prev = self.last_pstar_value.get(symbol)
            prev_ts = self.last_pstar_ts.get(symbol)
            self.last_pstar_value[symbol] = float(pstar.value)
            self.last_pstar_ts[symbol] = int(pstar.ts_event_ms or now_ms)
            if prev is not None and prev > 0 and prev_ts is not None:
                dt_s = max(1e-3, (now_ms - prev_ts) / 1000.0)
                ret = (float(pstar.value) - prev) / prev
                drift = _clamp(ret / dt_s * 2.0, -0.03, 0.03)
                q = _clamp(mid + drift, 0.01, 0.99)
        return _clamp(q, 0.01, 0.99)

    def _compute_quotes(
        self,
        q: float,
        mid: Optional[float],
        constraint: OrderConstraints,
        half_spread_bps: float,
        inventory_skew: float,
        risk_padding_bps: float,
    ) -> Tuple[float, float]:
        base = float(mid if mid is not None else q)
        half = max(constraint.min_tick, base * (half_spread_bps / 10000.0))
        risk_pad = base * (risk_padding_bps / 10000.0)
        bid = q - half - inventory_skew - risk_pad
        ask = q + half - inventory_skew + risk_pad
        bid = _clamp(bid, constraint.min_price, constraint.max_price - constraint.min_tick)
        ask = _clamp(ask, constraint.min_price + constraint.min_tick, constraint.max_price)
        if ask <= bid:
            ask = min(constraint.max_price, bid + constraint.min_tick)
        bid = _round_down(bid, constraint.min_tick)
        ask = _round_up(ask, constraint.min_tick)
        return bid, ask

    async def run_stats_cycle(self, now_ms: int, liveness_inputs: Optional[Dict[str, Any]] = None) -> None:
        p50_send_ack = _quantile(list(self.send_ack_samples), 0.50)
        p95_send_ack = _quantile(list(self.send_ack_samples), 0.95)
        p50_ack_fill = _quantile(list(self.ack_fill_samples), 0.50)
        p95_ack_fill = _quantile(list(self.ack_fill_samples), 0.95)
        ws_lag = _quantile(list(self.ws_lag_samples), 0.95)
        p50_ws_lag = _quantile(list(self.ws_lag_samples), 0.50)
        p95_ws_lag = _quantile(list(self.ws_lag_samples), 0.95)
        p50_pstar_age = _quantile(list(self.pstar_age_samples), 0.50)
        p95_pstar_age = _quantile(list(self.pstar_age_samples), 0.95)
        p50_signal_age = _quantile(list(self.signal_age_samples), 0.50)
        p95_signal_age = _quantile(list(self.signal_age_samples), 0.95)
        self.db.insert(
            "latency_stats",
            {
                "ts_ms": now_ms,
                "p50_send_ack_ms": p50_send_ack,
                "p95_send_ack_ms": p95_send_ack,
                "p50_ack_fill_ms": p50_ack_fill,
                "p95_ack_fill_ms": p95_ack_fill,
                "ws_lag_ms": ws_lag,
                "p50_signal_age_ms": p50_signal_age,
                "p95_signal_age_ms": p95_signal_age,
                "p50_ws_lag_ms": p50_ws_lag,
                "p95_ws_lag_ms": p95_ws_lag,
                "p50_pstar_age_ms": p50_pstar_age,
                "p95_pstar_age_ms": p95_pstar_age,
            },
        )
        for token_id in sorted(self.books.keys()):
            recv_q = self._book_recv_ts_ms_by_token[token_id]
            while recv_q and int(recv_q[0]) < int(now_ms - 60_000):
                recv_q.popleft()
            snap = self._latest_book_snapshot_by_token.get(token_id)
            ages = list(self._book_age_samples_by_token[token_id])
            self.db.insert(
                "book_health_stats",
                {
                    "ts_ms": int(now_ms),
                    "event_id": uuid.uuid4().hex,
                    "token_id": str(token_id),
                    "book_asof_ts_ms": _maybe_int(snap.book_asof_ts_ms if snap else None),
                    "book_recv_ts_ms": _maybe_int(snap.book_recv_ts_ms if snap else None),
                    "book_seq": int(snap.book_seq if snap else 0),
                    "book_level_count": int(snap.book_level_count if snap else 0),
                    "book_health_state": str(snap.book_health_state if snap else BookHealthState.DOWN.value),
                    "book_age_p50_ms": _maybe_float(_quantile(ages, 0.50)),
                    "book_age_p95_ms": _maybe_float(_quantile(ages, 0.95)),
                    "ws_recv_rate_msgs_min": float(len(recv_q)),
                },
            )
        corrected_execution_rows = self._flush_execution_quality(now_ms)
        self._record_execution_quality_stats(now_ms)
        self._record_queue_quality_stats(now_ms)
        inputs = dict(liveness_inputs or {})
        clock_drift_ms = _maybe_float(inputs.get("clock_drift_ms"))
        sequence_gap_rate_per_min = _maybe_float(inputs.get("sequence_gap_rate_per_min"))
        sequence_gap_count_1m = _maybe_int(inputs.get("sequence_gap_count_1m"))
        active_market_lag_ms = _maybe_float(inputs.get("active_market_lag_ms"))
        starved_tokens: List[str] = []
        starvation_by_token: Dict[str, Optional[float]] = {}
        max_ws_starvation_ms = 0.0
        for token_id in sorted(self.books.keys()):
            snap = self._latest_book_snapshot_by_token.get(token_id)
            recv_ts_ms = _maybe_int(snap.book_recv_ts_ms) if snap is not None else None
            starvation_ms = None
            if recv_ts_ms is not None:
                starvation_ms = max(0.0, float(now_ms - int(recv_ts_ms)))
            if starvation_ms is None:
                starvation_by_token[token_id] = None
                starved_tokens.append(token_id)
                continue
            starvation_by_token[token_id] = float(starvation_ms)
            max_ws_starvation_ms = max(float(max_ws_starvation_ms), float(starvation_ms))
            if float(starvation_ms) > float(self._ws_starvation_max_ms):
                starved_tokens.append(token_id)

        liveness_reasons: List[str] = []
        if clock_drift_ms is not None and float(clock_drift_ms) > float(self._clock_drift_max_ms):
            liveness_reasons.append("E_CLOCK_DRIFT_HIGH")
        if starved_tokens:
            liveness_reasons.append("E_WS_STARVATION")
        if sequence_gap_rate_per_min is not None and float(sequence_gap_rate_per_min) > 0.0:
            liveness_reasons.append("E_WS_SEQUENCE_GAP")
        if active_market_lag_ms is not None and float(active_market_lag_ms) > float(self._ws_starvation_max_ms):
            liveness_reasons.append("E_ACTIVE_MARKET_LAG_HIGH")
        freeze_candidate = bool(("E_CLOCK_DRIFT_HIGH" in liveness_reasons) or ("E_WS_STARVATION" in liveness_reasons))
        startup_grace_active = bool(int(now_ms - self.run_epoch_ms) < int(self._startup_liveness_grace_ms))
        prev_liveness_frozen = bool(self._liveness_freeze_active)
        self._liveness_freeze_active = bool(freeze_candidate and not startup_grace_active)
        self._liveness_reason_codes = sorted(set(liveness_reasons))
        if freeze_candidate and startup_grace_active:
            self._emit_rate_limited_alert(
                now_ms,
                severity="warning",
                code="LIVENESS_DEGRADED_STARTUP",
                message="startup_liveness_grace_active",
                payload={
                    "state": ALERT_STATE_DEGRADED,
                    "clock_drift_ms": _maybe_float(clock_drift_ms),
                    "starved_tokens": list(starved_tokens),
                    "startup_grace_ms": int(self._startup_liveness_grace_ms),
                    "reasons": list(self._liveness_reason_codes),
                },
                dedupe_key="LIVENESS_DEGRADED_STARTUP",
                cooldown_ms=max(5_000, int(self._startup_liveness_grace_ms // 2 or 5_000)),
            )
        if self._liveness_freeze_active and not prev_liveness_frozen:
            self._emit_rate_limited_alert(
                int(now_ms),
                severity="critical",
                code="LIVENESS_FROZEN_EDGE",
                message=f"liveness_freeze_reasons={','.join(self._liveness_reason_codes)}",
                payload={
                    "state": ALERT_STATE_FROZEN,
                    "clock_drift_ms": _maybe_float(clock_drift_ms),
                    "starved_tokens": list(starved_tokens),
                    "sequence_gap_rate_per_min": _maybe_float(sequence_gap_rate_per_min),
                    "active_market_lag_ms": _maybe_float(active_market_lag_ms),
                },
                dedupe_key="LIVENESS_FROZEN_EDGE",
            )
        elif (not self._liveness_freeze_active) and prev_liveness_frozen:
            self._emit_rate_limited_alert(
                int(now_ms),
                severity="info",
                code="LIVENESS_UNFROZEN_EDGE",
                message="liveness_freeze_cleared",
                payload={"state": ALERT_STATE_OK},
                dedupe_key="LIVENESS_UNFROZEN_EDGE",
                cooldown_ms=1_000,
            )
        self.db.insert(
            "liveness_stats",
            {
                "ts_ms": int(now_ms),
                "event_id": uuid.uuid4().hex,
                "mode": self.mode,
                "clock_drift_ms": _maybe_float(clock_drift_ms),
                "sequence_gap_rate_per_min": _maybe_float(sequence_gap_rate_per_min),
                "sequence_gap_count_1m": _maybe_int(sequence_gap_count_1m),
                "ws_starvation_token_count": int(len(starved_tokens)),
                "max_ws_starvation_ms": float(max_ws_starvation_ms),
                "active_market_lag_ms": _maybe_float(active_market_lag_ms),
                "freeze_state": 1 if self._liveness_freeze_active else 0,
                "reason_codes": ",".join(self._liveness_reason_codes),
                "payload_json": json.dumps(
                    {
                        "starved_tokens": starved_tokens,
                        "starvation_by_token_ms": starvation_by_token,
                        "corrected_execution_rows": int(corrected_execution_rows),
                        "freeze_candidate": bool(freeze_candidate),
                        "startup_grace_active": bool(startup_grace_active),
                    },
                    separators=(",", ":"),
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            },
        )
        retention_cutoff_ms = int(now_ms - (7 * 24 * 60 * 60 * 1000))
        self.db.execute("DELETE FROM decision_ticks WHERE ts_ms < ?", [retention_cutoff_ms])
        if self._next_reconcile_due_ms <= 0 or int(now_ms) >= int(self._next_reconcile_due_ms):
            await self._record_reconciliation(now_ms)
            self._next_reconcile_due_ms = int(now_ms) + int(max(250, self._reconcile_period_ms))

    async def startup_feed_guard(
        self,
        mode: str,
        tracked_symbols: List[str],
        max_wait_secs: int,
        min_updates_per_token: int,
        max_book_age_ms: int,
        max_pstar_age_ms: int,
    ) -> Tuple[bool, Dict[str, Any]]:
        self._startup_readiness_state = FEED_READINESS_BOOTING
        self._startup_readiness_reason_codes = []
        self._startup_readiness_payload = {}
        deadline = time.monotonic() + float(max_wait_secs)
        while time.monotonic() < deadline:
            now_ms = _now_ms()
            for token_id, book in self.books.items():
                self._snapshot_book(token_id=token_id, book=book, now_ms=now_ms)
            token_status: Dict[str, Dict[str, Any]] = {}
            token_ok = True
            for token_id in sorted(self.books.keys()):
                snap = self._latest_book_snapshot_by_token.get(token_id)
                updates = int(self._book_update_count_by_token.get(token_id, 0))
                book_age = _maybe_int(snap.age_ms(now_ms) if snap else None)
                wired = bool(
                    updates >= int(min_updates_per_token)
                    or (updates >= 1 and book_age is not None and int(book_age) < int(max_book_age_ms))
                )
                token_status[token_id] = {"updates": updates, "book_age_ms": book_age, "wired": wired}
                token_ok = token_ok and wired

            symbol_status: Dict[str, Dict[str, Any]] = {}
            symbol_ok = True
            for symbol in sorted({s for s in tracked_symbols if s}):
                pstar = self.pstar_builder.build(symbol, now_ms)
                self._latest_pstar_by_symbol[symbol] = pstar
                pstar_age = _maybe_int(now_ms - int(pstar.ts_event_ms)) if pstar.ts_event_ms is not None else None
                wired = bool(pstar.valid and pstar_age is not None and int(pstar_age) < int(max_pstar_age_ms))
                symbol_status[symbol] = {
                    "valid": bool(pstar.valid),
                    "pstar_age_ms": pstar_age,
                    "invalid_reason": str(pstar.invalid_reason or ""),
                    "wired": wired,
                    "state": str(self._pstar_state(pstar, now_ms)),
                    "provider": ",".join(sorted(pstar.sources_used)),
                }
                symbol_ok = symbol_ok and wired

            token_wired_count = int(sum(1 for status in token_status.values() if bool(status.get("wired"))))
            symbol_wired_count = int(sum(1 for status in symbol_status.values() if bool(status.get("wired"))))
            token_total = int(len(token_status))
            symbol_total = int(len(symbol_status))
            readiness_state = FEED_READINESS_BOOTING
            if token_ok and symbol_ok:
                readiness_state = FEED_READINESS_READY
            elif token_wired_count > 0 or symbol_wired_count > 0:
                readiness_state = FEED_READINESS_PARTIAL

            payload = {
                "token_status": token_status,
                "symbol_status": symbol_status,
                "max_book_age_ms": int(max_book_age_ms),
                "max_pstar_age_ms": int(max_pstar_age_ms),
                "required_updates": int(min_updates_per_token),
                "readiness_state": str(readiness_state),
                "token_wired_count": token_wired_count,
                "token_total": token_total,
                "symbol_wired_count": symbol_wired_count,
                "symbol_total": symbol_total,
                "mode": str(mode),
                "a_pipeline_diag": self._a_pipeline_diag(now_ms=int(now_ms)),
            }
            if token_ok and symbol_ok:
                self._startup_readiness_state = FEED_READINESS_READY
                self._startup_readiness_reason_codes = []
                self._startup_readiness_payload = dict(payload)
                return True, payload
            self._startup_readiness_state = str(readiness_state)
            self._startup_readiness_reason_codes = ["DEGRADED_STARTUP"]
            self._startup_readiness_payload = dict(payload)
            await asyncio.sleep(1.0)

        failure_payload = {
            "token_status": {
                token_id: {
                    "updates": int(self._book_update_count_by_token.get(token_id, 0)),
                    "book_age_ms": _maybe_int(
                        self._latest_book_snapshot_by_token[token_id].age_ms(_now_ms())
                        if token_id in self._latest_book_snapshot_by_token
                        else None
                    ),
                }
                for token_id in sorted(self.books.keys())
            },
            "symbol_status": {
                symbol: {
                    "valid": bool(self._latest_pstar_by_symbol.get(symbol).valid)
                    if symbol in self._latest_pstar_by_symbol
                    else False,
                    "pstar_age_ms": _maybe_int(
                        _now_ms() - int(self._latest_pstar_by_symbol[symbol].ts_event_ms)
                        if symbol in self._latest_pstar_by_symbol
                        and self._latest_pstar_by_symbol[symbol].ts_event_ms is not None
                        else None
                    ),
                    "invalid_reason": str(self._latest_pstar_by_symbol[symbol].invalid_reason or "")
                    if symbol in self._latest_pstar_by_symbol
                    else "missing_symbol_pstar",
                }
                for symbol in sorted({s for s in tracked_symbols if s})
            },
            "mode": mode,
            "readiness_state": str(self._startup_readiness_state or FEED_READINESS_BOOTING),
            "a_pipeline_diag": self._a_pipeline_diag(now_ms=int(_now_ms())),
        }
        self._startup_readiness_state = str(failure_payload.get("readiness_state") or FEED_READINESS_BOOTING)
        self._startup_readiness_reason_codes = ["DEGRADED_STARTUP"]
        self._startup_readiness_payload = dict(failure_payload)
        return False, failure_payload

    async def prepare_rollover(self, next_token_ids: List[str], now_ms: int) -> Dict[str, Any]:
        old_tokens = set(self.books.keys())
        new_tokens = set(str(token) for token in next_token_ids if token)
        removed = sorted(old_tokens - new_tokens)
        cancelled_orders = 0

        if removed and self.mode in {"PAPER", "TRADE"} and self.broker is not None:
            for token_id in removed:
                token_quotes = list((self.open_quotes.get(token_id) or {}).items())
                for side, quote in token_quotes:
                    for event in await self._broker_cancel(quote.order_id):
                        self._handle_broker_event(
                            token_id=token_id,
                            side=side,
                            event=event,
                            decision_id=f"rollover_prepare:{now_ms}",
                        )
                    cancelled_orders += 1
                self.open_quotes.pop(token_id, None)
        return {
            "removed_tokens": removed,
            "cancelled_orders": int(cancelled_orders),
        }

    def reset_per_market_state(self, reason: str, token_ids: List[str], now_ms: int) -> None:
        symbols_to_reset: set[str] = set()
        for token_id in token_ids:
            token_meta = self.market_meta.get(token_id) or {}
            symbol = str(token_meta.get("reference_symbol") or "")
            if symbol:
                symbols_to_reset.add(symbol)
            self.fsms[token_id] = ExecutionFSM(rebalance_timeout_ms=self.policy_thresholds.hedge_timeout_ms)
            self._last_fsm_state_by_token[token_id] = ExecutionState.QUOTING_BOTH.value
            self.open_quotes[token_id] = {}
            self.pending_freeze[token_id] = []
            self._unwind_state.pop(token_id, None)
            self._post_only_attempts_by_token[token_id] = 0
            self._post_only_rejects_by_token[token_id] = 0
            self._book_seq_by_token[token_id] = 0
            self._book_update_count_by_token[token_id] = 0
            self._last_book_recv_mono_by_token[token_id] = 0
            self._book_recv_ts_ms_by_token[token_id] = deque(maxlen=5000)
            self._book_age_samples_by_token[token_id] = deque(maxlen=2000)
            self._latest_book_snapshot_by_token.pop(token_id, None)
            # Reset quote revision counters for deterministic ids after rollover.
            for side in ("buy", "sell"):
                self._quote_revision.pop((token_id, side), None)
            self.db.insert(
                "recovery_events",
                {
                    "ts_ms": int(now_ms),
                    "event_id": uuid.uuid4().hex,
                    "run_id": self.run_id,
                    "mode": self.mode,
                    "recovery_action": "MARKET_STATE_RESET",
                    "token_id": token_id,
                    "side": None,
                    "order_id": None,
                    "price": None,
                    "size": None,
                    "adopted_order_count": None,
                    "payload_json": json.dumps(
                        {"reason": reason},
                        separators=(",", ":"),
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                },
            )
        for symbol in sorted(symbols_to_reset):
            self._latest_pstar_by_symbol.pop(symbol, None)
        if symbols_to_reset:
            self.pstar_builder.reset_symbols(symbols_to_reset)

    def commit_rollover_swap(
        self,
        books: Dict[str, OrderBook],
        constraints: Dict[str, OrderConstraints],
        market_meta: Dict[str, Dict[str, Any]],
        now_ms: int,
    ) -> Dict[str, Any]:
        old_tokens = set(self.books.keys())
        new_tokens = set(books.keys())
        removed = sorted(old_tokens - new_tokens)
        added = sorted(new_tokens - old_tokens)

        self.books = books
        self.constraints = constraints
        self.market_meta = market_meta
        self.book_cache = BookCache()

        self._cap_state = self._build_caps(
            trading_cfg=self.constitution.get("trading", {}),
            execution_cfg=self.constitution.get("execution", {}),
        )

        for token_id in new_tokens:
            self.inventory_yes.setdefault(token_id, 0.0)
            self.inventory_no.setdefault(token_id, 0.0)
            self._last_q_by_token.setdefault(token_id, 0.5)
        self.reset_per_market_state(reason="rollover_commit", token_ids=sorted(new_tokens), now_ms=now_ms)

        for token_id in removed:
            self.fsms.pop(token_id, None)
            self._last_fsm_state_by_token.pop(token_id, None)
            self.inventory_yes.pop(token_id, None)
            self.inventory_no.pop(token_id, None)
            self.open_quotes.pop(token_id, None)
            self.pending_freeze.pop(token_id, None)
            self._unwind_state.pop(token_id, None)
            self._post_only_attempts_by_token.pop(token_id, None)
            self._post_only_rejects_by_token.pop(token_id, None)
            self._last_q_by_token.pop(token_id, None)
            self._book_seq_by_token.pop(token_id, None)
            self._book_update_count_by_token.pop(token_id, None)
            self._last_book_recv_mono_by_token.pop(token_id, None)
            self._book_recv_ts_ms_by_token.pop(token_id, None)
            self._book_age_samples_by_token.pop(token_id, None)
            self._latest_book_snapshot_by_token.pop(token_id, None)

        return {
            "added_tokens": added,
            "removed_tokens": removed,
            "swap_ts_ms": int(now_ms),
        }

    def _to_units(self, value: Optional[float], scale: int) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(round(float(value) * float(max(1, int(scale)))))
        except (TypeError, ValueError):
            return None

    def _fill_event_key(
        self,
        *,
        event_id: str,
        order_id: str,
        token_id: str,
        side: str,
        fill_qty: float,
        fill_price: float,
        fill_ts: int,
    ) -> str:
        if event_id:
            return f"broker:{event_id}"
        fill_qty_units = int(self._to_units(fill_qty, self._qty_scale) or 0)
        fill_price_units = int(self._to_units(fill_price, self._usdc_scale) or 0)
        ts_bucket = int(fill_ts // 1000)
        raw = f"{order_id}|{token_id}|{side}|{fill_qty_units}|{fill_price_units}|{ts_bucket}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"derived:{digest}"

    async def _cancel_all_open_quotes(self, now_ms: int, reason: str) -> int:
        cancelled = 0
        for token_id in sorted(self.open_quotes.keys()):
            for side in sorted(self.open_quotes[token_id].keys()):
                quote = self.open_quotes[token_id].get(side)
                if quote is None:
                    continue
                if self.broker is None or self.mode not in {"PAPER", "TRADE"}:
                    self.open_quotes[token_id].pop(side, None)
                    continue
                events = await self._broker_cancel(quote.order_id)
                for event in events:
                    self._handle_broker_event(
                        token_id=token_id,
                        side=side,
                        event=event,
                        decision_id=f"reconciliation_freeze:{now_ms}",
                    )
                self.db.insert(
                    "recovery_events",
                    {
                        "ts_ms": int(now_ms),
                        "event_id": uuid.uuid4().hex,
                        "run_id": self.run_id,
                        "mode": self.mode,
                        "recovery_action": "CANCEL_OPEN_QUOTE_ON_FREEZE",
                        "token_id": token_id,
                        "side": side,
                        "order_id": quote.order_id,
                        "price": _maybe_float(quote.price),
                        "size": _maybe_float(quote.qty),
                        "adopted_order_count": None,
                        "payload_json": json.dumps(
                            {"reason": reason, "events": [event.event_type for event in events]},
                            separators=(",", ":"),
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    },
                )
                self.open_quotes[token_id].pop(side, None)
                cancelled += 1
        return cancelled

    def _record_missed_fill_correction(
        self,
        *,
        now_ms: int,
        token_id: str,
        side: str,
        order_id: str,
        fill_qty: float,
        fill_price: float,
        fill_event_id: str,
        payload: Dict[str, Any],
    ) -> None:
        fill_ts = int(_maybe_int(payload.get("ts_ms")) or now_ms)
        self.db.insert(
            "fills",
            {
                "ts_ms": int(fill_ts),
                "event_id": str(fill_event_id),
                "order_id": str(order_id),
                "token_id": str(token_id),
                "side": str(side),
                "fill_price": float(fill_price),
                "fill_qty": float(fill_qty),
                "fee": _maybe_float(payload.get("fees_bps")),
                "liquidity": str(payload.get("liquidity") or "unknown"),
                "payload_json": json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )
        self._fill_event_ts.append(int(fill_ts))
        self._fill_event_ts_by_token[str(token_id)].append(int(fill_ts))
        self._queue_execution_quality(
            token_id=str(token_id),
            side=str(side),
            order_id=str(order_id),
            fill_ts_ms=int(fill_ts),
            fill_price=float(fill_price),
            fill_qty=float(fill_qty),
            fee_bps=float(_maybe_float(payload.get("fees_bps")) or 0.0),
        )
        self.db.insert(
            "recovery_events",
            {
                "ts_ms": int(now_ms),
                "event_id": uuid.uuid4().hex,
                "run_id": self.run_id,
                "mode": self.mode,
                "recovery_action": "MISSED_FILL_CORRECTION",
                "token_id": str(token_id),
                "side": str(side),
                "order_id": str(order_id),
                "price": _maybe_float(fill_price),
                "size": _maybe_float(fill_qty),
                "adopted_order_count": None,
                "payload_json": json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )

    def _reconcile_missed_fills(self, snapshot: BrokerSnapshot, now_ms: int) -> int:
        meta = snapshot.meta if isinstance(snapshot.meta, dict) else {}
        raw_fill_events = meta.get("fill_events")
        if not isinstance(raw_fill_events, list):
            return 0
        normalized: List[Dict[str, Any]] = []
        for row in raw_fill_events:
            if not isinstance(row, dict):
                continue
            token_id = str(row.get("token_id") or row.get("asset_id") or "")
            side = str(row.get("side") or "").lower()
            if not token_id or side not in {"buy", "sell"}:
                continue
            fill_qty = _maybe_float(row.get("fill_qty") or row.get("size") or row.get("qty"))
            if fill_qty is None or fill_qty <= 0:
                continue
            fill_ts = int(_maybe_int(row.get("ts_ms") or row.get("t_fill_wall_ms")) or now_ms)
            fill_price = float(_maybe_float(row.get("fill_price") or row.get("price")) or 0.0)
            order_id = str(row.get("order_id") or row.get("id") or f"recon:{token_id}:{side}:{fill_ts}")
            event_id = str(row.get("event_id") or "")
            fill_event_key = self._fill_event_key(
                event_id=event_id,
                order_id=order_id,
                token_id=token_id,
                side=side,
                fill_qty=float(fill_qty),
                fill_price=float(fill_price),
                fill_ts=fill_ts,
            )
            normalized.append(
                {
                    "fill_event_key": fill_event_key,
                    "event_id": event_id,
                    "token_id": token_id,
                    "side": side,
                    "fill_qty": float(fill_qty),
                    "fill_price": float(fill_price),
                    "fill_ts": fill_ts,
                    "order_id": order_id,
                    "payload": dict(row),
                }
            )
        corrected = 0
        for row in sorted(
            normalized,
            key=lambda item: (
                int(item["fill_ts"]),
                str(item["token_id"]),
                str(item["side"]),
                str(item["order_id"]),
                str(item["fill_event_key"]),
            ),
        ):
            fill_event_key = str(row["fill_event_key"])
            if fill_event_key in self._seen_reconcile_fill_ids:
                self.db.insert(
                    "recovery_events",
                    {
                        "ts_ms": int(now_ms),
                        "event_id": uuid.uuid4().hex,
                        "run_id": self.run_id,
                        "mode": self.mode,
                        "recovery_action": "MISSED_FILL_DUPLICATE_SKIPPED",
                        "token_id": str(row["token_id"]),
                        "side": str(row["side"]),
                        "order_id": str(row["order_id"]),
                        "price": _maybe_float(row.get("fill_price")),
                        "size": _maybe_float(row.get("fill_qty")),
                        "adopted_order_count": None,
                        "payload_json": json.dumps(
                            {
                                "fill_event_key": fill_event_key,
                                "reason": "in_memory_seen",
                            },
                            separators=(",", ":"),
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    },
                )
                continue
            marked = self.db.mark_fill_event_seen(
                fill_event_key=fill_event_key,
                first_seen_ts_ms=int(row["fill_ts"]),
                source="reconcile",
                payload={
                    "token_id": str(row["token_id"]),
                    "side": str(row["side"]),
                    "order_id": str(row["order_id"]),
                    "fill_qty": _maybe_float(row.get("fill_qty")),
                    "fill_price": _maybe_float(row.get("fill_price")),
                    "event_id": str(row.get("event_id") or ""),
                },
            )
            if not marked:
                self.db.insert(
                    "recovery_events",
                    {
                        "ts_ms": int(now_ms),
                        "event_id": uuid.uuid4().hex,
                        "run_id": self.run_id,
                        "mode": self.mode,
                        "recovery_action": "MISSED_FILL_DUPLICATE_SKIPPED",
                        "token_id": str(row["token_id"]),
                        "side": str(row["side"]),
                        "order_id": str(row["order_id"]),
                        "price": _maybe_float(row.get("fill_price")),
                        "size": _maybe_float(row.get("fill_qty")),
                        "adopted_order_count": None,
                        "payload_json": json.dumps(
                            {
                                "fill_event_key": fill_event_key,
                                "reason": "persistent_seen",
                            },
                            separators=(",", ":"),
                            ensure_ascii=True,
                            sort_keys=True,
                        ),
                    },
                )
                self._seen_reconcile_fill_ids.add(fill_event_key)
                continue
            token_id = str(row["token_id"])
            if token_id not in self.fsms:
                self._seen_reconcile_fill_ids.add(fill_event_key)
                continue
            side = str(row["side"])
            fill_qty = float(row["fill_qty"])
            fill_ts = int(row["fill_ts"])
            prev_state = self.fsms[token_id].status().state.value
            if side == "buy":
                self.inventory_yes[token_id] += fill_qty
            else:
                self.inventory_yes[token_id] -= fill_qty
            self.fsms[token_id].on_fill(side=side, qty=fill_qty, ts_ms=fill_ts)
            self.fsms[token_id].reset_if_flat()
            self._record_fsm_transition(
                token_id=token_id,
                prev_state=prev_state,
                new_state=self.fsms[token_id].status().state.value,
                ts_ms=fill_ts,
                reason="reconcile_missed_fill",
            )
            self._record_missed_fill_correction(
                now_ms=now_ms,
                token_id=token_id,
                side=side,
                order_id=str(row["order_id"]),
                fill_qty=fill_qty,
                fill_price=float(row["fill_price"]),
                fill_event_id=str(row["event_id"] or uuid.uuid4().hex),
                payload=row["payload"],
            )
            self._seen_reconcile_fill_ids.add(fill_event_key)
            corrected += 1
        return corrected

    async def _record_reconciliation(self, now_ms: int) -> None:
        broker_open_orders = 0
        broker_inventory = None
        onchain_inventory = None
        derived_inventory = float(sum(self.inventory_yes.values()) - sum(self.inventory_no.values()))
        mismatch_count = 0
        unresolved_mismatch_count = 0
        inventory_delta_qty = None
        inventory_delta_usdc = None
        outside_tolerance = False
        payload: Dict[str, Any] = {}
        corrected_missed_fills = 0
        freeze_triggered = False
        unfreeze_triggered = False
        freeze_reason = ""

        snapshot = BrokerSnapshot(open_orders={}, meta={})
        if self.broker is not None and self.mode in {"PAPER", "TRADE"}:
            snapshot = await asyncio.to_thread(self.broker.snapshot)
            corrected_missed_fills = self._reconcile_missed_fills(snapshot=snapshot, now_ms=now_ms)

        broker_orders = snapshot.open_orders if isinstance(snapshot.open_orders, dict) else {}
        broker_open_orders = len(broker_orders)
        self._persist_open_orders_snapshot(now_ms=now_ms, open_orders=broker_orders)

        broker_ids = set(str(order_id) for order_id in broker_orders.keys())
        local_ids = {
            quote.order_id
            for token_quotes in self.open_quotes.values()
            for quote in token_quotes.values()
        }
        unresolved_unknown_ids: List[str] = []
        if self._startup_unknown_order_quarantine:
            unknown_ids = {
                str(row.get("order_id") or "")
                for row in self._startup_unknown_order_quarantine
                if str(row.get("order_id") or "")
            }
            unresolved_unknown_ids = sorted(unknown_ids.intersection(broker_ids))
            if not unresolved_unknown_ids:
                self._startup_unknown_order_quarantine = []
                self.db.append_alert(
                    int(now_ms),
                    "info",
                    "RECON_UNKNOWN_ORDER_QUARANTINE_CLEARED",
                    "startup_unknown_orders_resolved",
                    payload={},
                )
        only_local = sorted(local_ids - broker_ids)
        only_broker = sorted(broker_ids - local_ids)

        meta = snapshot.meta if isinstance(snapshot.meta, dict) else {}
        broker_inventory = _maybe_float(meta.get("broker_inventory"))
        onchain_inventory = _maybe_float(meta.get("onchain_inventory"))

        compare_inventory = onchain_inventory if onchain_inventory is not None else derived_inventory
        if broker_inventory is not None and compare_inventory is not None:
            inventory_delta_qty = float(broker_inventory - compare_inventory)
        broker_usdc = _maybe_float(meta.get("broker_usdc"))
        onchain_usdc = _maybe_float(meta.get("onchain_usdc"))
        if broker_usdc is not None and onchain_usdc is not None:
            inventory_delta_usdc = float(broker_usdc - onchain_usdc)

        inventory_delta_qty_units = self._to_units(inventory_delta_qty, self._qty_scale)
        inventory_delta_usdc_units = self._to_units(inventory_delta_usdc, self._usdc_scale)
        tolerance_qty_units = int(self._to_units(self._mismatch_tolerance_qty, self._qty_scale) or 0)
        tolerance_usdc_units = int(self._to_units(self._mismatch_tolerance_usdc, self._usdc_scale) or 0)
        outside_qty = bool(
            inventory_delta_qty_units is not None
            and abs(int(inventory_delta_qty_units)) > int(tolerance_qty_units)
        )
        outside_usdc = bool(
            inventory_delta_usdc_units is not None
            and abs(int(inventory_delta_usdc_units)) > int(tolerance_usdc_units)
        )
        outside_tolerance = bool(outside_qty or outside_usdc)

        mismatch_count = int(len(only_local) + len(only_broker))
        if outside_qty:
            mismatch_count += 1
        if outside_usdc:
            mismatch_count += 1
        unresolved_mismatch_count = int(mismatch_count)

        if unresolved_mismatch_count > 0:
            self._consecutive_mismatch_cycles += 1
        else:
            self._consecutive_mismatch_cycles = 0

        onchain_delta_qty_units = None
        if broker_inventory is not None and onchain_inventory is not None:
            onchain_delta_qty_units = self._to_units(float(broker_inventory) - float(onchain_inventory), self._qty_scale)
        onchain_disagree = bool(
            onchain_delta_qty_units is not None
            and abs(int(onchain_delta_qty_units)) > int(tolerance_qty_units)
        )
        if onchain_disagree:
            self._consecutive_onchain_disagree_cycles += 1
        else:
            self._consecutive_onchain_disagree_cycles = 0

        if unresolved_mismatch_count == 0 and not onchain_disagree:
            self._consecutive_clean_cycles += 1
        else:
            self._consecutive_clean_cycles = 0

        if unresolved_mismatch_count > 0:
            self.db.append_alert(
                now_ms,
                "warning",
                "RECON_MISMATCH",
                f"mismatch_count={mismatch_count}",
                payload={
                    "only_local": only_local,
                    "only_broker": only_broker,
                    "inventory_delta_qty": _maybe_float(inventory_delta_qty),
                    "inventory_delta_qty_units": _maybe_int(inventory_delta_qty_units),
                    "inventory_delta_usdc": _maybe_float(inventory_delta_usdc),
                    "inventory_delta_usdc_units": _maybe_int(inventory_delta_usdc_units),
                    "tolerance_qty": float(self._mismatch_tolerance_qty),
                    "tolerance_qty_units": int(tolerance_qty_units),
                    "tolerance_usdc": float(self._mismatch_tolerance_usdc),
                    "tolerance_usdc_units": int(tolerance_usdc_units),
                    "meta": meta,
                },
            )

        should_freeze_trade = bool(
            self.mode == "TRADE"
            and (
                self._consecutive_mismatch_cycles >= max(1, int(self._mismatch_freeze_cycles))
                or self._consecutive_onchain_disagree_cycles >= max(1, int(self._onchain_disagree_freeze_cycles))
            )
        )
        should_mark_fail = bool(
            self.mode == "PAPER"
            and (
                self._consecutive_mismatch_cycles >= max(1, int(self._mismatch_freeze_cycles))
                or self._consecutive_onchain_disagree_cycles >= max(1, int(self._onchain_disagree_freeze_cycles))
            )
        )
        if should_freeze_trade:
            freeze_reason = (
                "RECON_ONCHAIN_DIVERGENCE"
                if self._consecutive_onchain_disagree_cycles >= max(1, int(self._onchain_disagree_freeze_cycles))
                else "RECONCILIATION_MISMATCH_CRITICAL"
            )
            if not self._reconciliation_frozen:
                freeze_triggered = True
                self._reconciliation_frozen = True
                self._reconciliation_freeze_reason = freeze_reason
                self.db.append_alert(
                    now_ms,
                    "critical",
                    "RECONCILIATION_FROZEN_EDGE",
                    f"freeze_edge reason={freeze_reason}",
                    payload={
                        "consecutive_mismatch_cycles": int(self._consecutive_mismatch_cycles),
                        "consecutive_onchain_disagree_cycles": int(self._consecutive_onchain_disagree_cycles),
                        "consecutive_clean_cycles": int(self._consecutive_clean_cycles),
                    },
                )
                self.db.append_alert(
                    now_ms,
                    "critical",
                    "RECONCILIATION_MISMATCH_CRITICAL",
                    f"freeze_trade reason={freeze_reason}",
                    payload={
                        "consecutive_mismatch_cycles": int(self._consecutive_mismatch_cycles),
                        "consecutive_onchain_disagree_cycles": int(self._consecutive_onchain_disagree_cycles),
                        "mismatch_freeze_cycles": int(self._mismatch_freeze_cycles),
                        "onchain_disagree_freeze_cycles": int(self._onchain_disagree_freeze_cycles),
                    },
                )
                expected_cancel_count = int(
                    sum(len(token_quotes) for token_quotes in self.open_quotes.values())
                )
                canceled_count = await self._cancel_all_open_quotes(now_ms=now_ms, reason=freeze_reason)
                if int(canceled_count) != int(expected_cancel_count):
                    self.db.append_alert(
                        now_ms,
                        "critical",
                        "RECON_FREEZE_CANCEL_ASSERT_FAIL",
                        f"expected_cancel={expected_cancel_count} actual={canceled_count}",
                        payload={
                            "expected_cancel_count": int(expected_cancel_count),
                            "actual_cancel_count": int(canceled_count),
                            "freeze_reason": freeze_reason,
                        },
                    )
        elif self._reconciliation_frozen and self._consecutive_clean_cycles >= max(1, int(self._reconcile_clean_unfreeze_cycles)):
            self._reconciliation_frozen = False
            self._reconciliation_freeze_reason = ""
            unfreeze_triggered = True
            self.db.append_alert(
                now_ms,
                "info",
                "RECONCILIATION_UNFROZEN_EDGE",
                "unfreeze_edge clean_cycles_threshold_reached",
                payload={
                    "consecutive_clean_cycles": int(self._consecutive_clean_cycles),
                    "required_clean_cycles": int(self._reconcile_clean_unfreeze_cycles),
                },
            )
            self.db.append_alert(
                now_ms,
                "info",
                "RECONCILIATION_MISMATCH_RESOLVED",
                "reconciliation mismatch resolved",
                payload={
                    "consecutive_mismatch_cycles": int(self._consecutive_mismatch_cycles),
                    "consecutive_onchain_disagree_cycles": int(self._consecutive_onchain_disagree_cycles),
                    "consecutive_clean_cycles": int(self._consecutive_clean_cycles),
                },
            )

        if should_mark_fail:
            self.db.append_alert(
                now_ms,
                "critical",
                "RECONCILIATION_MISMATCH_CRITICAL",
                "paper_mode_mismatch_fail_observe",
                payload={
                    "mode": self.mode,
                    "consecutive_mismatch_cycles": int(self._consecutive_mismatch_cycles),
                    "consecutive_onchain_disagree_cycles": int(self._consecutive_onchain_disagree_cycles),
                },
            )

        payload = {
            "only_local": only_local,
            "only_broker": only_broker,
            "meta": meta,
            "corrected_missed_fills": int(corrected_missed_fills),
            "startup_unknown_quarantine_count": int(len(self._startup_unknown_order_quarantine)),
            "startup_unknown_unresolved_order_ids": unresolved_unknown_ids,
            "reconciliation_freeze_reason": str(self._reconciliation_freeze_reason or freeze_reason),
            "freeze_triggered": bool(freeze_triggered),
            "unfreeze_triggered": bool(unfreeze_triggered),
            "inventory_delta_qty": _maybe_float(inventory_delta_qty),
            "inventory_delta_qty_units": _maybe_int(inventory_delta_qty_units),
            "inventory_delta_usdc": _maybe_float(inventory_delta_usdc),
            "inventory_delta_usdc_units": _maybe_int(inventory_delta_usdc_units),
            "onchain_delta_qty_units": _maybe_int(onchain_delta_qty_units),
            "tolerance_qty": float(self._mismatch_tolerance_qty),
            "tolerance_qty_units": int(tolerance_qty_units),
            "tolerance_usdc": float(self._mismatch_tolerance_usdc),
            "tolerance_usdc_units": int(tolerance_usdc_units),
            "outside_tolerance": bool(outside_tolerance),
            "consecutive_mismatch_cycles": int(self._consecutive_mismatch_cycles),
            "consecutive_onchain_disagree_cycles": int(self._consecutive_onchain_disagree_cycles),
            "consecutive_clean_cycles": int(self._consecutive_clean_cycles),
            "required_clean_cycles_to_unfreeze": int(self._reconcile_clean_unfreeze_cycles),
        }

        self.db.insert(
            "reconciliation_stats",
            {
                "ts_ms": int(now_ms),
                "event_id": uuid.uuid4().hex,
                "run_id": self.run_id,
                "mode": self.mode,
                "broker_open_orders": int(broker_open_orders),
                "broker_inventory": _maybe_float(broker_inventory),
                "onchain_inventory": _maybe_float(onchain_inventory),
                "derived_inventory": _maybe_float(derived_inventory),
                "inventory_delta_qty": _maybe_float(inventory_delta_qty),
                "inventory_delta_usdc": _maybe_float(inventory_delta_usdc),
                "tolerance_qty": float(self._mismatch_tolerance_qty),
                "tolerance_usdc": float(self._mismatch_tolerance_usdc),
                "outside_tolerance": 1 if outside_tolerance else 0,
                "mismatch_count": int(mismatch_count),
                "unresolved_mismatch_count": int(unresolved_mismatch_count),
                "consecutive_mismatch_cycles": int(self._consecutive_mismatch_cycles),
                "consecutive_onchain_disagree_cycles": int(self._consecutive_onchain_disagree_cycles),
                "freeze_state": 1 if self._reconciliation_frozen else 0,
                "freeze_reason": str(self._reconciliation_freeze_reason or freeze_reason),
                "payload_json": json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True),
            },
        )


def _one_leg_age_ms(start_ms: Optional[int], now_ms: int) -> Optional[int]:
    if start_ms is None:
        return None
    return int(max(0, now_ms - start_ms))


def _maybe_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _load_constitution(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "trading": {},
            "policy": {},
            "execution": {},
        }
    raw = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("constitution_invalid_format")
    return data


def _apply_paper_experiment_profile(mode: str, constitution: Dict[str, Any]) -> Optional[str]:
    profile = str(os.getenv("PAPER_EXPERIMENT_PROFILE", "")).strip().lower()
    if mode != "PAPER" or profile != "aggressive_two_sided":
        return None
    trading_cfg = constitution.setdefault("trading", {})
    policy_cfg = constitution.setdefault("policy", {})
    execution_cfg = constitution.setdefault("execution", {})
    if not isinstance(trading_cfg, dict) or not isinstance(policy_cfg, dict) or not isinstance(execution_cfg, dict):
        return None
    policy_cfg["max_spread_bps"] = 750.0
    policy_cfg["max_slippage_bps"] = 300.0
    # These remain diagnostic upper bounds; the active gate is still stricter.
    policy_cfg["paper_hard_block_spread_bps"] = 900.0
    policy_cfg["paper_hard_block_slippage_bps"] = 450.0
    execution_cfg["maker_half_spread_bps"] = 25.0
    trading_cfg["paper_experiment_profile"] = profile
    return profile


def _build_constraints(
    resolved_markets: List[Any],
    policy_cfg: Dict[str, Any],
    settings: Any,
) -> Dict[str, OrderConstraints]:
    return {
        asset_id: OrderConstraints(
            min_tick=market.min_tick,
            min_size=market.min_size,
            min_price=market.min_price,
            max_price=market.max_price,
            max_spread_bps=float(policy_cfg.get("max_spread_bps", settings.max_spread_bps)),
            max_slippage_bps=float(policy_cfg.get("max_slippage_bps", settings.max_slippage_bps)),
            max_book_staleness_ms=int(policy_cfg.get("max_book_age_ms", settings.max_book_staleness_ms)),
        )
        for market in resolved_markets
        for asset_id in market.token_ids
        if asset_id
    }


def _resolve_primary_market_state(
    resolved_markets: List[Any],
    asset_meta: Dict[str, Dict[str, Any]],
) -> Optional[MarketState]:
    if len(resolved_markets) != 1:
        return None
    return market_state_from_resolved(resolved_markets[0], asset_meta)


def _write_rollover_event(
    event_tape: EventTape,
    event_type: str,
    now_ms: int,
    market: Optional[str],
    payload: Dict[str, Any],
) -> None:
    event_tape.write(
        channel="system",
        event_type=event_type,
        market=market,
        asset_id=None,
        t_event_ms=int(now_ms),
        raw=payload,
        source="market_rollover",
        parse_warnings=[],
        out_of_order=False,
        t_recv_wall_ms=int(now_ms),
        t_recv_wall_iso=_utc_iso_from_ms(int(now_ms)),
        t_recv_mono_ns=time.monotonic_ns(),
    )


def _write_rollover_decision_boundary(
    decision_tape: DecisionTape,
    time_mapper: TimeMapper,
    event_type: str,
    now_ms: int,
    payload: Dict[str, Any],
) -> None:
    market_slug = payload.get("market_slug_new") or payload.get("market_slug_prev")
    condition_id = payload.get("condition_id_new") or payload.get("condition_id_prev")
    record = DecisionRecord(
        schema_version="decision_v4_system",
        engine_version="run_system_v1",
        run_id=decision_tape.run_id,
        t_decision_wall_iso=_utc_iso_from_ms(int(now_ms)),
        t_decision_wall_ms=int(now_ms),
        t_decision_mono_ns=int(time_mapper.mono_ns_from_wall_ms(int(now_ms))),
        asset_id="__rollover__",
        market_slug=str(market_slug) if market_slug is not None else None,
        condition_id=str(condition_id) if condition_id is not None else None,
        token_id="__rollover__",
        outcome=None,
        outcome_by_token=None,
        book={},
        p_market_mid=None,
        p_market_exec_buy=None,
        p_market_exec_sell=None,
        p_market=None,
        p_fair=None,
        edge_net_buy=None,
        edge_net_sell=None,
        p_star=None,
        labels=None,
        features_raw=None,
        features_ortho=None,
        whitening=None,
        gates={"allow": False, "reasons": [str(event_type)]},
        exec_cost={},
        notes={"rollover": payload, "event_type": str(event_type)},
        as_of_ts_ms=int(now_ms),
        pstar_diag=None,
        policy_codes=[str(event_type)],
        latency={},
        fsm_state=None,
    )
    decision_tape.write(record)


def _discovery_requests_from_summary(discovery_summary: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(discovery_summary, dict):
        return []
    requests = discovery_summary.get("discovery_requests")
    if not isinstance(requests, list):
        return []
    return [dict(item) for item in requests if isinstance(item, dict)]


def _dedupe_discovery_requests(discovery_requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for payload in discovery_requests:
        key = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(payload)
    return deduped


def _append_discovery_request_rows(
    db: SQLiteStore,
    ts_ms: int,
    discovery_requests: List[Dict[str, Any]],
) -> None:
    for payload in discovery_requests:
        status = _discovery_status_from_payload(payload)
        counts = {
            "n_total": _maybe_int(payload.get("n_total")),
            "n_btc_15m": _maybe_int(payload.get("n_btc_15m")),
            "n_with_end_ts": _maybe_int(payload.get("n_with_end_ts")),
            "n_active_now": _maybe_int(payload.get("n_active_now")),
            "n_tradable_now": _maybe_int(payload.get("n_tradable_now")),
        }
        db.append_discovery_request(
            ts_ms=int(ts_ms),
            requested_symbol=str(payload.get("requested_symbol", "")),
            requested_horizon=str(payload.get("requested_horizon", "")),
            mode=str(payload.get("requested_mode", "")),
            status=status,
            now_ms=int(_maybe_int(payload.get("now_wall_ms")) or ts_ms),
            selected_slug=_coerce_optional_str(payload.get("selected_slug")),
            end_ts_ms=_maybe_int(payload.get("selected_end_ts_ms")),
            end_ts_source=_coerce_optional_str(payload.get("selected_end_ts_source")),
            reason_code=_coerce_optional_str(payload.get("error_code")),
            retry_index=_maybe_int(payload.get("retry_index")),
            next_retry_ts_ms=_maybe_int(payload.get("next_retry_ts_ms")),
            counts=counts,
            payload=payload,
        )


def _discovery_status_from_payload(payload: Dict[str, Any]) -> str:
    explicit = _coerce_optional_str(payload.get("status"))
    if explicit:
        return explicit
    if _coerce_optional_str(payload.get("error_code")) == "NO_ACTIVE_BTC_15M":
        return "NONE_FOUND"
    if _coerce_optional_str(payload.get("selected_slug")):
        return "SELECTED"
    return "ERROR"


def _discovery_none_found_retry_delay_ms(retry_index: int) -> int:
    idx = max(0, int(retry_index))
    schedule_ms = (1_000, 2_000, 5_000)
    if idx < len(schedule_ms):
        return int(schedule_ms[idx])
    return 10_000


def _discovery_error_retry_delay_ms(retry_index: int) -> int:
    return _discovery_none_found_retry_delay_ms(retry_index)


def _discovery_effective_next_retry_ts_ms(
    now_ms: int,
    retry_index: int,
    discovery_period_ms: int,
) -> int:
    schedule_due_ms = int(now_ms) + int(_discovery_none_found_retry_delay_ms(retry_index))
    throttle_due_ms = int(now_ms) + int(max(0, discovery_period_ms))
    return int(max(schedule_due_ms, throttle_due_ms))


def _build_discovery_error_payload(
    *,
    exc: Exception,
    current_state: Optional[Any],
    discovery_summary: Optional[Dict[str, Any]],
    now_ms: int,
    retry_index: int,
    next_retry_ts_ms: int,
) -> Dict[str, Any]:
    summary_requests = _discovery_requests_from_summary(discovery_summary)
    payload_base: Dict[str, Any] = {
        "event": "DISCOVERY_REQUEST",
        "status": "ERROR",
        "requested_symbol": getattr(current_state, "reference_symbol", None) or "BTC",
        "requested_horizon": "15m",
        "requested_mode": "latest_active",
        "now_wall_ms": int(now_ms),
        "retry_index": int(retry_index),
        "retry_delay_ms": int(max(0, int(next_retry_ts_ms) - int(now_ms))),
        "next_retry_ts_ms": int(next_retry_ts_ms),
    }
    if summary_requests:
        latest = summary_requests[-1]
        for key in (
            "requested_symbol",
            "requested_horizon",
            "requested_mode",
            "n_total",
            "n_btc_15m",
            "n_with_end_ts",
            "n_active_now",
            "n_tradable_now",
        ):
            if latest.get(key) is not None:
                payload_base[key] = latest.get(key)
    if isinstance(exc, GammaFetchError):
        payload_base.update(
            {
                "error": str(exc),
                "error_code": exc.error_code,
                "error_status": exc.status,
                "error_detail": exc.error_detail,
                "gamma_url": exc.url,
                "transient": bool(exc.transient),
            }
        )
    else:
        payload_base.update(
            {
                "error": str(exc),
                "error_code": exc.__class__.__name__.upper(),
            }
        )
    return payload_base


def _discovery_none_found_deadline_exceeded(
    start_ts_ms: Optional[int],
    now_ms: int,
    deadline_ms: int,
) -> bool:
    if start_ts_ms is None:
        return False
    return int(max(0, int(now_ms) - int(start_ts_ms))) >= int(max(0, deadline_ms))


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    parsed = str(value).strip()
    return parsed if parsed else None


def _confirm_diag_summary(
    confirm_diag: Optional[Dict[str, Any]],
    confirm_wait_ms: Optional[float],
) -> Dict[str, Any]:
    diag = confirm_diag if isinstance(confirm_diag, dict) else {}
    required_updates = max(1, int(_maybe_int(diag.get("required_updates_per_token")) or 1))
    counts_in = diag.get("counts_by_asset")
    rejects_in = diag.get("rejects_by_asset")
    counts_raw = counts_in if isinstance(counts_in, dict) else {}
    rejects_raw = rejects_in if isinstance(rejects_in, dict) else {}
    pending_assets = diag.get("pending_asset_ids")
    assets: Set[str] = set()
    assets.update(str(key) for key in counts_raw.keys())
    assets.update(str(key) for key in rejects_raw.keys())
    if isinstance(pending_assets, list):
        assets.update(str(item) for item in pending_assets if item)
    ordered_assets = sorted(asset for asset in assets if asset)
    counts_by_asset = {
        asset: int(_maybe_int(counts_raw.get(asset)) or 0)
        for asset in ordered_assets
    }
    rejects_by_asset = {
        asset: int(_maybe_int(rejects_raw.get(asset)) or 0)
        for asset in ordered_assets
    }
    reasons = diag.get("reasons")
    reason_counts: Dict[str, int] = {}
    for raw_reason in reasons if isinstance(reasons, list) else []:
        reason = str(raw_reason)
        reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
    reject_reasons_top = [
        {"reason": reason, "count": int(reason_counts[reason])}
        for reason in sorted(reason_counts.keys(), key=lambda key: (-int(reason_counts[key]), key))
    ]
    missing_assets = [
        asset for asset in ordered_assets if int(counts_by_asset.get(asset, 0)) < int(required_updates)
    ]
    return {
        "required_updates_per_token": int(required_updates),
        "counts_by_asset": counts_by_asset,
        "rejects_by_asset": rejects_by_asset,
        "reject_reasons_top": reject_reasons_top,
        "missing_assets": missing_assets,
        "confirm_wait_ms": _maybe_float(confirm_wait_ms),
        "failure_class": str(diag.get("failure_class") or "") or None,
        "reconnect_attempted": bool(diag.get("reconnect_attempted")),
        "unsubscribe_before_subscribe": bool(diag.get("unsubscribe_before_subscribe")),
    }


def _old_market_non_viable(*, now_ms: int, market_end_ts_ms: Optional[int]) -> bool:
    end_ts_ms = _maybe_int(market_end_ts_ms)
    return end_ts_ms is not None and int(now_ms) >= int(end_ts_ms)


def _should_adopt_switched_market_without_readiness(
    *,
    token_ids_changed: bool,
    switch_status: str,
    commit_action: str,
    old_market_non_viable: bool,
) -> bool:
    return (
        bool(token_ids_changed)
        and str(switch_status) == "committed"
        and str(commit_action) != "COMMIT"
        and bool(old_market_non_viable)
    )


async def _rollback_post_switch_abort(
    *,
    market_client: Any,
    runtime: Any,
    previous_token_ids: List[str],
    confirm_timeout_secs: float,
) -> Dict[str, Any]:
    market_client.set_books(runtime.books)
    rollback_result = await market_client.resubscribe(
        new_asset_ids=list(previous_token_ids),
        first_book_timeout_secs=confirm_timeout_secs,
    )
    market_client.set_books(runtime.books)
    return {
        "post_switch_abort": True,
        "rollback_attempted": True,
        "rollback_status": str(rollback_result.status),
        "rollback_confirm_diag": dict(rollback_result.confirm_diag or {}),
        "rollback_confirm_diag_summary": _confirm_diag_summary(
            rollback_result.confirm_diag,
            _maybe_float(rollback_result.confirm_wait_ms),
        ),
        "rollback_confirm_wait_ms": _maybe_float(rollback_result.confirm_wait_ms),
        "rollback_unsubscribe_ms": _maybe_float(rollback_result.unsubscribe_ms),
        "rollback_active_subscription_after": int(rollback_result.active_subscription_id),
    }


def _candidate_tradability(
    asset_meta: Dict[str, Dict[str, Any]],
    token_ids: List[str],
) -> Tuple[bool, str, Dict[str, Any]]:
    details: Dict[str, Any] = {"by_token": {}}
    seen_metadata = False
    for token_id in token_ids:
        meta = asset_meta.get(token_id) or {}
        active = meta.get("active")
        closed = meta.get("closed")
        accepting = meta.get("accepting_orders")
        details["by_token"][token_id] = {
            "active": active,
            "closed": closed,
            "accepting_orders": accepting,
        }
        if active is not None or closed is not None or accepting is not None:
            seen_metadata = True
        if active is True and (closed is True or accepting is False):
            continue
        if active is False:
            return False, "CANDIDATE_INACTIVE", details
        if closed is True:
            return False, "CANDIDATE_CLOSED", details
        if accepting is False:
            return False, "CANDIDATE_NOT_ACCEPTING_ORDERS", details
    if not seen_metadata:
        return True, "CANDIDATE_TRADABILITY_UNKNOWN", details
    for token_id in token_ids:
        meta = asset_meta.get(token_id) or {}
        active = meta.get("active")
        closed = meta.get("closed")
        accepting = meta.get("accepting_orders")
        if active is True and (closed is True or accepting is False):
            return True, "CANDIDATE_TRADABILITY_AMBIGUOUS", details
    return True, "CANDIDATE_TRADABLE", details


def _candidate_liveness(
    asset_meta: Dict[str, Dict[str, Any]],
    token_ids: List[str],
    now_ms: int,
    market_end_ts_ms: Optional[int],
) -> Tuple[bool, str, Dict[str, Any]]:
    tradable_ok, tradability_state, tradability_details = _candidate_tradability(asset_meta, token_ids)
    details = {
        "tradability_state": tradability_state,
        "tradability_details": tradability_details,
        "market_end_ts_ms": _maybe_int(market_end_ts_ms),
    }
    if market_end_ts_ms is not None and int(now_ms) >= int(market_end_ts_ms):
        return False, "CANDIDATE_ENDED", details
    if not tradable_ok:
        return False, str(tradability_state), details
    return True, "CANDIDATE_LIVE", details


def _pending_books_liveness(token_ids: List[str], books: Dict[str, OrderBook]) -> Tuple[bool, Dict[str, Any]]:
    by_token: Dict[str, bool] = {}
    any_book_update = False
    for token_id in [str(token) for token in token_ids if token]:
        book = books.get(token_id)
        seen = bool(book is not None and (int(book.last_recv_mono_ns or 0) > 0 or book.last_event_ts_ms is not None))
        by_token[token_id] = seen
        any_book_update = any_book_update or seen
    all_tokens_seen = all(by_token.values()) if by_token else False
    liveness_ok = bool(all_tokens_seen or any_book_update)
    return liveness_ok, {
        "all_tokens_seen": bool(all_tokens_seen),
        "any_book_update": bool(any_book_update),
        "by_token": by_token,
    }


def _rollover_commit_decision(
    *,
    now_ms: int,
    readiness_ready: bool,
    escape_hatch_open: bool,
    liveness_ok: bool,
) -> RolloverCommitDecision:
    if readiness_ready:
        return RolloverCommitDecision(
            action="COMMIT",
            force_observe_only=False,
            reason="READINESS_READY",
        )
    if escape_hatch_open and liveness_ok:
        return RolloverCommitDecision(
            action="COMMIT",
            force_observe_only=True,
            reason="ESCAPE_HATCH_LIVENESS_ONLY",
        )
    if escape_hatch_open and not liveness_ok:
        return RolloverCommitDecision(
            action="RETRY",
            force_observe_only=False,
            reason="ESCAPE_HATCH_NO_LIVENESS",
        )
    return RolloverCommitDecision(
        action="RETRY",
        force_observe_only=False,
        reason="READINESS_NOT_READY",
    )


async def _run() -> None:
    args = _parse_args()
    settings = load_settings()
    constitution_path = Path(args.constitution or "config/constitution.yaml")
    constitution = _load_constitution(constitution_path)
    trading_cfg = constitution.get("trading", {}) if isinstance(constitution, dict) else {}
    policy_cfg = constitution.get("policy", {}) if isinstance(constitution, dict) else {}
    mode_raw = (args.mode or settings.trading_mode or trading_cfg.get("mode_default") or "OBSERVE").upper()
    mode, observe_live_alias = _resolve_runtime_mode(mode_raw)
    if mode not in {"OBSERVE", "PAPER", "TRADE"}:
        raise ValueError(f"unsupported_mode:{mode_raw}")
    if args.sim_exec and args.cli_exec:
        raise ValueError("execution_mode_flags_conflict")
    paper_experiment_profile = _apply_paper_experiment_profile(mode, constitution)
    trading_cfg = constitution.get("trading", {}) if isinstance(constitution, dict) else {}
    policy_cfg = constitution.get("policy", {}) if isinstance(constitution, dict) else {}

    effective_auto_discover = _effective_auto_discover(
        cli_auto_discover=bool(args.auto_discover),
        settings_auto_discover=bool(settings.auto_discover),
    )
    markets_path = args.markets or settings.track_markets_yaml
    markets = load_markets(markets_path)
    validate_markets_config(markets, auto_discover=effective_auto_discover)

    log_dir = Path(args.log_dir or settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db_path or settings.runtime_db_path)
    db = SQLiteStore(db_path)
    run_id = uuid.uuid4().hex
    event_tape = EventTape(log_dir=str(log_dir), run_id=run_id)
    decision_tape = DecisionTape(log_dir=str(log_dir), run_id=run_id)
    trade_tape = TradeTape(log_dir=str(log_dir), run_id=run_id)
    metrics = Metrics()
    time_mapper = TimeMapper.from_wall_and_mono(wall_ms=_now_ms(), mono_ns=time.monotonic_ns())
    startup_discovery_summary: Dict[str, Any] = {}
    startup_discovery_now_ms = int(time_mapper.wall_ms(time.monotonic_ns()))
    try:
        resolved_markets, asset_meta = await resolve_markets(
            markets=markets,
            auto_discover=effective_auto_discover,
            cache_path=log_dir / "cache_gamma_markets.json",
            gamma_base_url=GAMMA_BASE_URL,
            now_ts=int(startup_discovery_now_ms / 1000),
            discovery_summary=startup_discovery_summary,
        )
    except NoActiveMarketError as exc:
        discovery_requests = _discovery_requests_from_summary(startup_discovery_summary)
        if exc.request_payload:
            discovery_requests.append(dict(exc.request_payload))
        discovery_requests = _dedupe_discovery_requests(discovery_requests)
        _append_discovery_request_rows(
            db=db,
            ts_ms=startup_discovery_now_ms,
            discovery_requests=discovery_requests,
        )
        db.append_log(
            startup_discovery_now_ms,
            "ERROR",
            "startup_discovery_no_active_market",
            {
                "error_code": "NO_ACTIVE_BTC_15M",
                "error": str(exc),
                "discovery_requests": discovery_requests,
                "diagnostics": dict(exc.diagnostics),
            },
        )
        raise
    except GammaFetchError as exc:
        next_retry_ts_ms = _discovery_effective_next_retry_ts_ms(
            now_ms=int(startup_discovery_now_ms),
            retry_index=0,
            discovery_period_ms=int(trading_cfg.get("rollover_check_period_ms", 30_000) or 30_000),
        )
        error_payload = _build_discovery_error_payload(
            exc=exc,
            current_state=None,
            discovery_summary=startup_discovery_summary,
            now_ms=int(startup_discovery_now_ms),
            retry_index=0,
            next_retry_ts_ms=int(next_retry_ts_ms),
        )
        discovery_requests = _discovery_requests_from_summary(startup_discovery_summary)
        discovery_requests.append(dict(error_payload))
        discovery_requests = _dedupe_discovery_requests(discovery_requests)
        _append_discovery_request_rows(
            db=db,
            ts_ms=startup_discovery_now_ms,
            discovery_requests=discovery_requests,
        )
        db.append_log(
            startup_discovery_now_ms,
            "ERROR",
            "startup_discovery_fetch_error",
            {
                "error": str(exc),
                "error_code": exc.error_code,
                "next_retry_ts_ms": int(next_retry_ts_ms),
                "discovery_requests": discovery_requests,
            },
        )
        raise
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("no_markets_found_for_symbol") or msg.startswith("no_markets_found_for_slug_prefix"):
            discovery_requests = _discovery_requests_from_summary(startup_discovery_summary)
            _append_discovery_request_rows(
                db=db,
                ts_ms=startup_discovery_now_ms,
                discovery_requests=discovery_requests,
            )
            db.append_log(
                startup_discovery_now_ms,
                "ERROR",
                "startup_discovery_no_markets",
                {
                    "error": msg,
                    "discovery_summary": dict(startup_discovery_summary),
                    "discovery_requests": discovery_requests,
                },
            )
        raise
    _append_discovery_request_rows(
        db=db,
        ts_ms=startup_discovery_now_ms,
        discovery_requests=_discovery_requests_from_summary(startup_discovery_summary),
    )
    asset_ids = sorted({token for market in resolved_markets for token in market.token_ids if token})
    if not asset_ids:
        raise ValueError("no_asset_ids_resolved")

    books = {asset_id: OrderBook(asset_id=asset_id, bids={}, asks={}) for asset_id in asset_ids}
    constraints = _build_constraints(resolved_markets, policy_cfg, settings)

    policy_thresholds = PolicyThresholds(
        max_book_age_ms=int(policy_cfg.get("max_book_age_ms", settings.max_book_staleness_ms)),
        book_stale_after_ms=int(policy_cfg.get("book_stale_after_ms", 30_000)),
        book_down_after_ms=int(policy_cfg.get("book_down_after_ms", 120_000)),
        max_spread_bps=float(policy_cfg.get("max_spread_bps", settings.max_spread_bps)),
        max_slippage_bps=float(policy_cfg.get("max_slippage_bps", settings.max_slippage_bps)),
        min_depth_at_qty=float(policy_cfg.get("min_depth_at_qty", 1.0)),
        max_signal_age_ms=int(policy_cfg.get("signal_age_max_ms", settings.signal_age_max_ms)),
        max_ack_p95_ms=float(policy_cfg.get("ack_p95_max_ms", settings.ack_p95_max_ms)),
        max_ws_lag_ms=float(policy_cfg.get("ws_lag_max_ms", settings.ws_lag_max_ms)),
        hedge_timeout_ms=int(trading_cfg.get("rebalance_timeout_ms", settings.rebalance_timeout_ms)),
    )

    pstar_builder = PStarBuilder(
        max_age_ms=int(policy_cfg.get("pstar_max_age_ms", settings.pstar_max_age_ms)),
        freeze_disagree_bps=float(policy_cfg.get("pstar_freeze_disagree_bps", settings.pstar_freeze_disagree_bps)),
        degrade_disagree_bps=float(policy_cfg.get("pstar_degrade_disagree_bps", 10.0)),
        allow_degraded_single_source=bool(policy_cfg.get("allow_degraded_single_source", True)),
    )
    readiness_config = MarketReadinessConfig(
        book_max_age_ms=int(trading_cfg.get("rollover_book_max_age_ms", policy_cfg.get("max_book_age_ms", settings.max_book_staleness_ms))),
        book_max_spread_bps=float(trading_cfg.get("rollover_book_max_spread_bps", policy_cfg.get("max_spread_bps", settings.max_spread_bps))),
        depth_target_qty=float(trading_cfg.get("rollover_depth_target_qty", constitution.get("execution", {}).get("maker_quote_size", 1.0))),
        pstar_max_age_ms=int(trading_cfg.get("rollover_pstar_max_age_ms", pstar_builder.max_age_ms)),
    )

    broker = None
    if args.sim_exec:
        broker = SimBroker(
            books=books,
            constraints=constraints,
            time_mapper=time_mapper,
            fee_status_by_asset={asset_id: "unknown" for asset_id in asset_ids},
            config=SimBrokerConfig(latency_ms=0, fee_mode="MAKE"),
        )
    elif args.cli_exec and mode in {"PAPER", "TRADE"}:
        broker = CLIBroker(
            CLIBrokerConfig(
                dry_run=bool(args.dry_run),
                timeout_secs=float(trading_cfg.get("cli_timeout_secs", 10.0) or 10.0),
            )
        )
    elif mode == "PAPER":
        broker = SimBroker(
            books=books,
            constraints=constraints,
            time_mapper=time_mapper,
            fee_status_by_asset={asset_id: "unknown" for asset_id in asset_ids},
            config=SimBrokerConfig(latency_ms=0, fee_mode="MAKE"),
        )
    elif mode == "TRADE":
        expected_client_version = str(
            trading_cfg.get("clob_client_version")
            or os.getenv("CLOB_CLIENT_VERSION")
            or EXPECTED_CLOB_CLIENT_VERSION
        )
        if not args.dry_run:
            try:
                PolymarketBroker.assert_contract(
                    expected_version=expected_client_version,
                    strict=True,
                )
            except BrokerContractError as exc:
                raise RuntimeError(f"broker_contract_guard_failed:{exc}") from exc
        broker = PolymarketBroker(
            api_key=settings.polymarket_api_key,
            secret=settings.polymarket_secret,
            passphrase=settings.polymarket_passphrase,
            private_key=settings.polymarket_private_key,
            config=PolymarketBrokerConfig(
                dry_run=args.dry_run,
                expected_client_version=expected_client_version,
                strict_contract=True,
            ),
        )

    run_epoch_ms = _now_ms()
    runtime = RuntimeEngine(
        mode=mode,
        db=db,
        decision_tape=decision_tape,
        trade_tape=trade_tape,
        books=books,
        constraints=constraints,
        market_meta=asset_meta,
        pstar_builder=pstar_builder,
        policy_thresholds=policy_thresholds,
        constitution=constitution,
        time_mapper=time_mapper,
        broker=broker,
        run_epoch_ms=run_epoch_ms,
        run_id=run_id,
        readiness_config=readiness_config,
        reference_poll_secs=float(settings.reference_poll_secs),
    )

    if mode in {"PAPER", "TRADE"} and broker is not None:
        snapshot = await asyncio.to_thread(broker.snapshot)
        try:
            recovery_diag = await runtime.adopt_open_orders_with_cleanup(snapshot=snapshot, now_ms=_now_ms())
        except RuntimeError as exc:
            err_msg = str(exc)
            if err_msg.startswith("RECON_STARTUP_INVARIANT_VIOLATION:"):
                db.append_alert(
                    _now_ms(),
                    "critical",
                    "RECON_STARTUP_INVARIANT_VIOLATION",
                    err_msg,
                    payload={
                        "mode": mode,
                        "open_order_count": int(len(snapshot.open_orders or {})),
                    },
                )
            db.append_log(
                _now_ms(),
                "ERROR",
                "startup_recovery_failed",
                {
                    "mode": mode,
                    "error": err_msg,
                    "open_order_count": int(len(snapshot.open_orders or {})),
                },
            )
            event_tape.close()
            decision_tape.close()
            trade_tape.close()
            db.close()
            raise
        db.append_log(
            _now_ms(),
            "INFO",
            "startup_recovery_complete",
            {
                "adopted_order_count": int(recovery_diag.get("adopted", 0)),
                "duplicates_canceled": int(recovery_diag.get("duplicates_canceled", 0)),
                "unknown_quarantined": int(recovery_diag.get("unknown_quarantined", 0)),
                "open_order_count": int(len(snapshot.open_orders or {})),
            },
        )

    ws_config = WSConfig(
        reconnect_base_ms=settings.ws_reconnect_base_ms,
        reconnect_max_ms=settings.ws_reconnect_max_ms,
    )
    market_client = MarketWSClient(
        asset_ids=asset_ids,
        books=books,
        tape=event_tape,
        metrics=metrics,
        config=ws_config,
        decision_engine=None,
    )
    rollover_manager: Optional[MarketRolloverManager] = None
    auto_discover_enabled = bool(args.auto_discover or settings.auto_discover)
    if auto_discover_enabled and len(markets) == 1 and len(resolved_markets) == 1:
        primary_market_state = _resolve_primary_market_state(resolved_markets, asset_meta)
        if primary_market_state is not None:
            rollover_manager = MarketRolloverManager(
                current=primary_market_state,
                config=MarketRolloverConfig(
                    prefetch_ms=int(trading_cfg.get("rollover_prefetch_ms", 90_000)),
                    stale_ms=int(trading_cfg.get("rollover_ws_stale_ms", 15_000)),
                    discovery_period_ms=int(trading_cfg.get("rollover_discovery_period_ms", 30_000)),
                    grace_ms=int(trading_cfg.get("rollover_grace_ms", 60_000)),
                ),
            )
            db.append_log(
                _now_ms(),
                "INFO",
                "rollover_manager_enabled",
                {
                    "market_slug": primary_market_state.market_slug,
                    "market_end_ts_ms": primary_market_state.market_end_ts_ms,
                    "market_end_source": primary_market_state.market_end_source,
                },
            )
    elif auto_discover_enabled:
        db.append_log(
            _now_ms(),
            "INFO",
            "rollover_manager_disabled",
            {"reason": "requires_single_market_config"},
        )

    quote_interval_ms = int(args.quote_interval_ms or trading_cfg.get("quote_interval_ms", settings.quote_interval_ms))
    stats_interval_ms = int(args.stats_interval_ms or trading_cfg.get("stats_interval_ms", settings.stats_interval_ms))
    rollover_quiet_window_ms = int(trading_cfg.get("rollover_quiet_window_ms", 500))
    unknown_alert_policy_cfg = trading_cfg.get("unknown_alert_policy", {}) if isinstance(trading_cfg, dict) else {}
    unknown_alert_threshold_per_min = int(
        unknown_alert_policy_cfg.get(
            "threshold_per_min",
            trading_cfg.get("rollover_unknown_alert_threshold_per_min", 120),
        )
    )
    unknown_alert_cooldown_ms = int(
        float(
            unknown_alert_policy_cfg.get(
                "cooldown_secs",
                float(trading_cfg.get("rollover_unknown_alert_cooldown_ms", 60_000)) / 1000.0,
            )
        )
        * 1000.0
    )
    unknown_alert_min_ratio = float(
        unknown_alert_policy_cfg.get(
            "min_ratio_vs_active",
            trading_cfg.get("rollover_unknown_alert_min_ratio_vs_active", 2.0),
        )
    )
    unknown_alert_startup_grace_ms = int(
        unknown_alert_policy_cfg.get(
            "startup_grace_ms",
            trading_cfg.get("rollover_unknown_alert_startup_grace_ms", 180_000),
        )
    )
    unknown_alert_min_samples = int(unknown_alert_policy_cfg.get("min_samples", 20))
    unknown_alert_sustain_windows = int(unknown_alert_policy_cfg.get("sustain_windows", 2))
    unknown_alert_clear_windows = int(unknown_alert_policy_cfg.get("clear_windows", 2))
    reference_sources = args.reference_source or settings.reference_source or "poll_coinbase,poll_binance_perp"

    stop_event = asyncio.Event()
    tasks: List[asyncio.Task] = []
    tasks.append(asyncio.create_task(market_client.run()))

    ref_feeds: List[ReferenceFeed] = []
    ref_ws_clients: List[ReferenceWSClient] = []
    symbols = sorted({market.reference_symbol for market in resolved_markets if market.reference_symbol})

    def _on_reference_quote(quote) -> None:
        ts_recv_ms = int(getattr(quote, "t_recv_wall_ms", _now_ms()) or _now_ms())
        runtime.on_reference(
            source=str(getattr(quote, "source", "spot")),
            symbol=str(getattr(quote, "symbol", "")),
            value=float(getattr(quote, "value", 0.0)),
            ts_event_ms=getattr(quote, "t_event_ms", None),
            ts_recv_ms=ts_recv_ms,
        )

    for source in [s.strip().lower() for s in reference_sources.split(",") if s.strip()]:
        if source.startswith("poll_"):
            feed = ReferenceFeed(
                aggregator=None,  # type: ignore[arg-type]
                tape=event_tape,
                config=ReferenceFeedConfig(
                    symbols=symbols,
                    poll_interval_secs=settings.reference_poll_secs,
                    source=source,
                ),
                on_quote=_on_reference_quote,
                reference_store=None,
            )
            ref_feeds.append(feed)
            tasks.append(asyncio.create_task(feed.run()))
        elif source == "ws_kraken":
            ws_client = ReferenceWSClient(
                tape=event_tape,
                config=ReferenceWSConfig(venue="kraken", symbols=symbols),
                on_quote=_on_reference_quote,
                reference_store=None,
            )
            ref_ws_clients.append(ws_client)
            tasks.append(asyncio.create_task(ws_client.run()))
        elif source == "ws_kraken_futures_perp":
            ws_client = ReferenceWSClient(
                tape=event_tape,
                config=ReferenceWSConfig(venue="kraken_futures", symbols=symbols),
                on_quote=_on_reference_quote,
                reference_store=None,
            )
            ref_ws_clients.append(ws_client)
            tasks.append(asyncio.create_task(ws_client.run()))

    guard_ok, guard_payload = await runtime.startup_feed_guard(
        mode=mode,
        tracked_symbols=symbols,
        max_wait_secs=30,
        min_updates_per_token=10,
        max_book_age_ms=int(policy_thresholds.max_book_age_ms),
        max_pstar_age_ms=int(pstar_builder.max_age_ms),
    )
    guard_live_blocked = _handle_startup_guard_result(
        db=db,
        mode=mode,
        guard_ok=guard_ok,
        guard_payload=guard_payload,
    )
    if guard_live_blocked:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        db.append_log(_now_ms(), "ERROR", "startup_feed_guard_failed", guard_payload)
        event_tape.close()
        decision_tape.close()
        trade_tape.close()
        db.close()
        raise RuntimeError("feed_not_wired_for_live_mode")
    db.append_log(
        _now_ms(),
        "INFO",
        "startup_a_pipeline_diag",
        runtime._a_pipeline_diag(now_ms=_now_ms()),
    )
    runtime_lock = asyncio.Lock()

    async def _quote_loop() -> None:
        while not stop_event.is_set():
            now_ms = int(time_mapper.wall_ms(time.monotonic_ns()))
            try:
                async with runtime_lock:
                    await runtime.run_quote_cycle(now_ms)
            except Exception as exc:
                db.append_alert(
                    now_ms,
                    "critical",
                    "QUOTE_LOOP_ERROR",
                    f"quote_loop_exception={type(exc).__name__}",
                    payload={
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
                db.append_log(
                    now_ms,
                    "ERROR",
                    "quote_loop_exception",
                    {"error": str(exc), "traceback": traceback.format_exc()},
                )
            await asyncio.sleep(max(0.05, quote_interval_ms / 1000.0))

    async def _stats_loop() -> None:
        last_unknown_alert_ms: int = 0
        unknown_breach_streak = 0
        unknown_clear_streak = 0
        unknown_alert_active = False
        last_a_pipeline_diag_log_ms: int = 0
        while not stop_event.is_set():
            now_ms = int(time_mapper.wall_ms(time.monotonic_ns()))
            try:
                wall_now_ms = _now_ms()
                active_recv_wall_ms = market_client.active_last_book_recv_wall_ms()
                active_market_lag_ms = (
                    float(max(0, now_ms - int(active_recv_wall_ms)))
                    if active_recv_wall_ms is not None
                    else None
                )
                liveness_inputs = {
                    "clock_drift_ms": float(abs(int(wall_now_ms) - int(now_ms))),
                    "sequence_gap_rate_per_min": float(metrics.sequence_gap_rate_per_min(now_ms)),
                    "sequence_gap_count_1m": int(metrics.sequence_gap_count(now_ms)),
                    "active_market_lag_ms": _maybe_float(active_market_lag_ms),
                }
                async with runtime_lock:
                    await runtime.run_stats_cycle(now_ms, liveness_inputs=liveness_inputs)
                    guard_state = runtime.rollover_guard_status(now_ms)
                unknown_count = metrics.market_unknown_count()
                unknown_rate = metrics.market_unknown_rate_per_min(now_ms)
                unknown_breakdown = metrics.market_unknown_breakdown_per_min(now_ms)
                unknown_top_signatures = metrics.market_unknown_signature_top(now_ms, limit=5)
                unknown_samples = metrics.market_unknown_sample_count(now_ms)
                ignored_old_rate = metrics.market_ignored_old_rate_per_min(now_ms)
                active_rate = metrics.market_active_rate_per_min(now_ms)
                current_market_slug = rollover_manager.current.market_slug if rollover_manager is not None else None
                current_selection_key = rollover_manager.current.selection_key if rollover_manager is not None else None
                current_end_source = rollover_manager.current.market_end_source if rollover_manager is not None else None
                db.append_rollover_metric(
                    ts_ms=now_ms,
                    metric_name="unknown_msg_count",
                    metric_value=float(unknown_count),
                    market_slug=current_market_slug,
                    selection_key=current_selection_key,
                    payload={
                        "unknown_rate_per_min": unknown_rate,
                        "active_rate_per_min": active_rate,
                        "unknown_breakdown_per_min": unknown_breakdown,
                        "unknown_sample_count": int(unknown_samples),
                    },
                )
                db.append_rollover_metric(
                    ts_ms=now_ms,
                    metric_name="ignored_old_rate_per_min",
                    metric_value=float(ignored_old_rate),
                    market_slug=current_market_slug,
                    selection_key=current_selection_key,
                    payload={"unknown_msg_count": unknown_count},
                )
                db.append_rollover_status(
                    ts_ms=now_ms,
                    event_type="GUARD_HEARTBEAT",
                    market_slug=current_market_slug,
                    selection_key=current_selection_key,
                    end_ts_source=current_end_source,
                    readiness_ok=bool(guard_state.get("last_ready", True)),
                    readiness_reason_codes=[str(code) for code in guard_state.get("last_reason_codes", [])],
                    confirm_wait_ms=None,
                    commit_block_ms=None,
                    unsubscribe_ms=None,
                    unknown_msg_count=int(unknown_count),
                    ignored_old_rate_per_min=float(ignored_old_rate),
                    payload=guard_state,
                )
                if (
                    _should_emit_unknown_ws_alert(
                        mode=mode,
                        now_ms=now_ms,
                        run_epoch_ms=runtime.run_epoch_ms,
                        unknown_rate_per_min=float(unknown_rate),
                        unknown_sample_count=int(unknown_samples),
                        active_rate_per_min=float(active_rate),
                        threshold_per_min=int(unknown_alert_threshold_per_min),
                        min_samples=int(unknown_alert_min_samples),
                        min_ratio_vs_active=float(unknown_alert_min_ratio),
                        startup_grace_ms=int(unknown_alert_startup_grace_ms),
                    )
                ):
                    unknown_breach_streak += 1
                    unknown_clear_streak = 0
                else:
                    unknown_clear_streak += 1
                    unknown_breach_streak = 0
                if (
                    unknown_breach_streak >= int(max(1, unknown_alert_sustain_windows))
                    and int(now_ms - last_unknown_alert_ms) >= int(max(1, unknown_alert_cooldown_ms))
                ):
                    last_unknown_alert_ms = int(now_ms)
                    unknown_alert_active = True
                    runtime._emit_rate_limited_alert(
                        now_ms,
                        severity="warning",
                        code="WS_UNKNOWN_RATE_HIGH",
                        message=f"unknown_market_msgs_per_min={unknown_rate:.1f}",
                        payload={
                            "state": ALERT_STATE_DEGRADED,
                            "unknown_msg_count": int(unknown_count),
                            "unknown_rate_per_min": float(unknown_rate),
                            "unknown_sample_count": int(unknown_samples),
                            "unknown_breakdown_per_min": unknown_breakdown,
                            "active_rate_per_min": float(active_rate),
                            "threshold_per_min": int(unknown_alert_threshold_per_min),
                            "min_samples": int(unknown_alert_min_samples),
                            "sustain_windows": int(unknown_alert_sustain_windows),
                            "clear_windows": int(unknown_alert_clear_windows),
                            "min_ratio_vs_active": float(unknown_alert_min_ratio),
                            "top_signatures": unknown_top_signatures,
                            "window_secs": 60,
                        },
                        dedupe_key="WS_UNKNOWN_RATE_HIGH",
                        cooldown_ms=unknown_alert_cooldown_ms,
                    )
                if unknown_alert_active and unknown_clear_streak >= int(max(1, unknown_alert_clear_windows)):
                    unknown_alert_active = False
                    runtime._emit_rate_limited_alert(
                        now_ms,
                        severity="info",
                        code="WS_UNKNOWN_RATE_RECOVERED",
                        message="unknown_market_rate_recovered",
                        payload={
                            "state": ALERT_STATE_OK,
                            "unknown_rate_per_min": float(unknown_rate),
                            "unknown_sample_count": int(unknown_samples),
                            "clear_windows": int(unknown_alert_clear_windows),
                        },
                        dedupe_key="WS_UNKNOWN_RATE_RECOVERED",
                        cooldown_ms=1_000,
                    )
                if int(now_ms - last_a_pipeline_diag_log_ms) >= 60_000:
                    last_a_pipeline_diag_log_ms = int(now_ms)
                    db.append_log(
                        now_ms,
                        "INFO",
                        "a_pipeline_diag",
                        runtime._a_pipeline_diag(now_ms=int(now_ms)),
                    )
            except Exception as exc:
                db.append_alert(
                    now_ms,
                    "critical",
                    "STATS_LOOP_ERROR",
                    f"stats_loop_exception={type(exc).__name__}",
                    payload={
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
                db.append_log(
                    now_ms,
                    "ERROR",
                    "stats_loop_exception",
                    {"error": str(exc), "traceback": traceback.format_exc()},
                )
            await asyncio.sleep(max(0.1, stats_interval_ms / 1000.0))

    async def _rollover_loop() -> None:
        if rollover_manager is None:
            return
        confirm_timeout_secs = max(
            1.0,
            float(trading_cfg.get("rollover_confirm_timeout_ms", 5_000)) / 1000.0,
        )
        check_period_secs = max(
            0.25,
            float(trading_cfg.get("rollover_check_period_ms", 1_000)) / 1000.0,
        )
        health_gate = RolloverHealthGate(
            abort_threshold=int(trading_cfg.get("rollover_abort_threshold", 3)),
            abort_window_ms=int(trading_cfg.get("rollover_abort_window_ms", 10 * 60_000)),
            cooldown_ms=int(trading_cfg.get("rollover_health_cooldown_ms", trading_cfg.get("rollover_abort_window_ms", 10 * 60_000))),
        )
        readiness_pass_count = 0
        readiness_fail_count = 0
        readiness_last_reasons: List[str] = []
        none_found_retry_index = 0
        none_found_next_retry_ts_ms: Optional[int] = None
        none_found_start_ts_ms: Optional[int] = None
        discovery_error_retry_index = 0
        discovery_error_next_retry_ts_ms: Optional[int] = None
        none_found_deadline_alerted = False
        none_found_quotes_canceled = False
        none_found_deadline_ms = int(trading_cfg.get("rollover_none_found_deadline_ms", 180_000))

        def _emit_rollover_abort(
            *,
            now_ms: int,
            current_state: MarketState,
            intent_payload: Dict[str, Any],
            abort_reason: str,
            extra_payload: Optional[Dict[str, Any]] = None,
            log_level: str = "WARNING",
            log_code: str = "rollover_abort",
            count_toward_health: bool = True,
        ) -> None:
            abort_payload = {
                **intent_payload,
                "abort_reason": str(abort_reason),
            }
            if extra_payload:
                abort_payload.update(extra_payload)
            _write_rollover_event(
                event_tape=event_tape,
                event_type="ROLLOVER_ABORT",
                now_ms=now_ms,
                market=current_state.market_slug,
                payload=abort_payload,
            )
            _write_rollover_decision_boundary(
                decision_tape=decision_tape,
                time_mapper=time_mapper,
                event_type="ROLLOVER_ABORT",
                now_ms=now_ms,
                payload=abort_payload,
            )
            db.append_log(now_ms, log_level, log_code, abort_payload)
            db.append_rollover_status(
                ts_ms=now_ms,
                event_type="ABORT",
                market_slug=current_state.market_slug,
                selection_key=current_state.selection_key,
                end_ts_source=current_state.market_end_source,
                readiness_ok=False,
                readiness_reason_codes=[str(code) for code in abort_payload.get("readiness_reason_codes", [])],
                confirm_wait_ms=_maybe_float(abort_payload.get("confirm_wait_ms")),
                commit_block_ms=_maybe_float(abort_payload.get("commit_block_ms")),
                unsubscribe_ms=_maybe_float(abort_payload.get("unsubscribe_ms")),
                unknown_msg_count=metrics.market_unknown_count(),
                ignored_old_rate_per_min=metrics.market_ignored_old_rate_per_min(now_ms),
                payload=abort_payload,
            )
            if not count_toward_health:
                return
            freeze_diag = health_gate.note_abort(now_ms)
            if freeze_diag is None:
                return
            freeze_payload = {
                **abort_payload,
                **freeze_diag,
                "event": "ROLLOVER_HEALTH_FREEZE",
            }
            _write_rollover_event(
                event_tape=event_tape,
                event_type="ROLLOVER_HEALTH_FREEZE",
                now_ms=now_ms,
                market=current_state.market_slug,
                payload=freeze_payload,
            )
            _write_rollover_decision_boundary(
                decision_tape=decision_tape,
                time_mapper=time_mapper,
                event_type="ROLLOVER_HEALTH_FREEZE",
                now_ms=now_ms,
                payload=freeze_payload,
            )
            db.append_alert(
                now_ms,
                "critical",
                "ROLLOVER_HEALTH_FREEZE",
                "Rollover aborted too frequently; freezing rollover attempts",
                payload=freeze_payload,
            )
            db.append_log(now_ms, "ERROR", "rollover_health_freeze", freeze_payload)
            db.append_rollover_status(
                ts_ms=now_ms,
                event_type="HEALTH_FREEZE",
                market_slug=current_state.market_slug,
                selection_key=current_state.selection_key,
                end_ts_source=current_state.market_end_source,
                readiness_ok=False,
                readiness_reason_codes=[str(code) for code in freeze_payload.get("readiness_reason_codes", [])],
                confirm_wait_ms=_maybe_float(freeze_payload.get("confirm_wait_ms")),
                commit_block_ms=_maybe_float(freeze_payload.get("commit_block_ms")),
                unsubscribe_ms=_maybe_float(freeze_payload.get("unsubscribe_ms")),
                unknown_msg_count=metrics.market_unknown_count(),
                ignored_old_rate_per_min=metrics.market_ignored_old_rate_per_min(now_ms),
                payload=freeze_payload,
            )

        while not stop_event.is_set():
            now_mono_ns = time.monotonic_ns()
            now_ms = int(time_mapper.wall_ms(now_mono_ns))
            if health_gate.is_frozen(now_ms):
                await asyncio.sleep(check_period_secs)
                continue
            if none_found_next_retry_ts_ms is not None and int(now_ms) < int(none_found_next_retry_ts_ms):
                await asyncio.sleep(check_period_secs)
                continue
            if discovery_error_next_retry_ts_ms is not None and int(now_ms) < int(discovery_error_next_retry_ts_ms):
                await asyncio.sleep(check_period_secs)
                continue
            last_book_recv_mono_ns = market_client.active_last_book_recv_mono_ns()
            last_book_recv_wall_ms = (
                int(time_mapper.wall_ms(last_book_recv_mono_ns)) if last_book_recv_mono_ns > 0 else None
            )
            trigger_reasons = rollover_manager.evaluate_reasons(
                now_ms=now_ms,
                last_book_recv_wall_ms=last_book_recv_wall_ms,
                market_closed=market_client.active_market_closed(),
            )
            if not rollover_manager.should_attempt_discovery(now_ms, trigger_reasons):
                await asyncio.sleep(check_period_secs)
                continue

            rollover_manager.mark_discovery_attempt(now_ms)
            current_state = rollover_manager.current
            active_sub_before = market_client.active_subscription_id()
            intent_payload = {
                "market_slug_prev": current_state.market_slug,
                "market_slug_new": None,
                "condition_id_prev": current_state.condition_id,
                "condition_id_new": None,
                "token_ids_prev": list(current_state.token_ids),
                "token_ids_new": None,
                "market_end_ts_ms_prev": current_state.market_end_ts_ms,
                "market_end_ts_ms_new": None,
                "market_end_source_prev": current_state.market_end_source,
                "market_end_source_new": None,
                "selection_key_prev": current_state.selection_key,
                "selection_key_new": None,
                "trigger_reasons": sorted(set(trigger_reasons)),
                "as_of_ts_ms": now_ms,
                "rollover_count": int(rollover_manager.rollover_count),
                "active_subscription_id_before": int(active_sub_before),
                "active_subscription_id_after": int(active_sub_before),
                "confirm_diag": {},
            }
            _write_rollover_event(
                event_tape=event_tape,
                event_type="ROLLOVER_INTENT",
                now_ms=now_ms,
                market=current_state.market_slug,
                payload=intent_payload,
            )
            _write_rollover_decision_boundary(
                decision_tape=decision_tape,
                time_mapper=time_mapper,
                event_type="ROLLOVER_INTENT",
                now_ms=now_ms,
                payload=intent_payload,
            )
            db.append_log(now_ms, "INFO", "rollover_intent", intent_payload)
            db.append_rollover_status(
                ts_ms=now_ms,
                event_type="INTENT",
                market_slug=current_state.market_slug,
                selection_key=current_state.selection_key,
                end_ts_source=current_state.market_end_source,
                readiness_ok=None,
                readiness_reason_codes=None,
                confirm_wait_ms=None,
                commit_block_ms=None,
                unsubscribe_ms=None,
                unknown_msg_count=metrics.market_unknown_count(),
                ignored_old_rate_per_min=metrics.market_ignored_old_rate_per_min(now_ms),
                payload=intent_payload,
            )

            discovery_summary: Dict[str, Any] = {}
            try:
                discovered_markets, discovered_asset_meta = await resolve_markets(
                    markets=markets,
                    auto_discover=auto_discover_enabled,
                    cache_path=log_dir / "cache_gamma_markets.json",
                    gamma_base_url=GAMMA_BASE_URL,
                    now_ts=int(now_ms / 1000),
                    discovery_summary=discovery_summary,
                )
                _append_discovery_request_rows(
                    db=db,
                    ts_ms=now_ms,
                    discovery_requests=_discovery_requests_from_summary(discovery_summary),
                )
            except NoActiveMarketError as exc:
                if none_found_start_ts_ms is None:
                    none_found_start_ts_ms = int(now_ms)
                elapsed_none_found_ms = int(max(0, int(now_ms) - int(none_found_start_ts_ms)))
                next_retry_ts_ms = _discovery_effective_next_retry_ts_ms(
                    now_ms=int(now_ms),
                    retry_index=int(none_found_retry_index),
                    discovery_period_ms=int(rollover_manager.config.discovery_period_ms),
                )
                retry_delay_ms = int(max(0, int(next_retry_ts_ms) - int(now_ms)))
                retry_index = int(none_found_retry_index)
                none_found_retry_index += 1
                none_found_next_retry_ts_ms = int(next_retry_ts_ms)

                discovery_requests = _discovery_requests_from_summary(discovery_summary)
                if exc.request_payload:
                    payload = dict(exc.request_payload)
                    payload["status"] = "NONE_FOUND"
                    payload["retry_index"] = int(retry_index)
                    payload["retry_delay_ms"] = int(retry_delay_ms)
                    payload["next_retry_ts_ms"] = int(next_retry_ts_ms)
                    payload["none_found_elapsed_ms"] = int(elapsed_none_found_ms)
                    payload["none_found_deadline_ms"] = int(none_found_deadline_ms)
                    discovery_requests.append(payload)
                discovery_requests = _dedupe_discovery_requests(discovery_requests)
                _append_discovery_request_rows(
                    db=db,
                    ts_ms=now_ms,
                    discovery_requests=discovery_requests,
                )
                if mode in {"PAPER", "TRADE"} and not none_found_quotes_canceled:
                    async with runtime_lock:
                        canceled_count = await runtime._cancel_all_open_quotes(
                            now_ms=int(now_ms),
                            reason="discovery_none_found",
                        )
                    none_found_quotes_canceled = True
                else:
                    canceled_count = 0
                async with runtime_lock:
                    runtime.activate_rollover_guard(
                        token_ids=list(current_state.token_ids),
                        quiet_until_ms=int(next_retry_ts_ms),
                        require_readiness=True,
                    )
                db.append_alert(
                    ts_ms=now_ms,
                    severity="warning",
                    code="NO_ACTIVE_BTC_15M",
                    message="No active BTC 15m market found during rollover discovery",
                    payload={
                        "error": str(exc),
                        "diagnostics": dict(exc.diagnostics),
                        "discovery_requests": discovery_requests,
                        "retry_index": int(retry_index),
                        "retry_delay_ms": int(retry_delay_ms),
                        "next_retry_ts_ms": int(next_retry_ts_ms),
                        "none_found_elapsed_ms": int(elapsed_none_found_ms),
                        "cancelled_quotes": int(canceled_count),
                        "status": "NONE_FOUND",
                    },
                )
                if (
                    not none_found_deadline_alerted
                    and _discovery_none_found_deadline_exceeded(
                        start_ts_ms=none_found_start_ts_ms,
                        now_ms=int(now_ms),
                        deadline_ms=int(none_found_deadline_ms),
                    )
                ):
                    none_found_deadline_alerted = True
                    db.append_alert(
                        ts_ms=now_ms,
                        severity="critical",
                        code="DISCOVERY_NONE_FOUND_DEADLINE_EXCEEDED",
                        message="No active BTC 15m market found beyond deadline; continuing deterministic retries",
                        payload={
                            "none_found_start_ts_ms": int(none_found_start_ts_ms),
                            "none_found_elapsed_ms": int(elapsed_none_found_ms),
                            "none_found_deadline_ms": int(none_found_deadline_ms),
                            "retry_index": int(retry_index),
                            "next_retry_ts_ms": int(next_retry_ts_ms),
                            "status": "NONE_FOUND",
                        },
                    )
                _emit_rollover_abort(
                    now_ms=now_ms,
                    current_state=current_state,
                    intent_payload=intent_payload,
                    abort_reason="DISCOVERY_NO_ACTIVE_MARKET",
                    extra_payload={
                        "error": str(exc),
                        "diagnostics": dict(exc.diagnostics),
                        "discovery_summary": discovery_summary,
                        "discovery_requests": discovery_requests,
                        "retry_index": int(retry_index),
                        "retry_delay_ms": int(retry_delay_ms),
                        "next_retry_ts_ms": int(next_retry_ts_ms),
                        "none_found_elapsed_ms": int(elapsed_none_found_ms),
                        "none_found_deadline_ms": int(none_found_deadline_ms),
                        "status": "NONE_FOUND",
                    },
                    log_level="WARNING",
                    log_code="rollover_abort_discovery_no_active_market",
                    count_toward_health=False,
                )
                await asyncio.sleep(check_period_secs)
                continue
            except Exception as exc:
                retry_index = int(discovery_error_retry_index)
                next_retry_ts_ms = _discovery_effective_next_retry_ts_ms(
                    now_ms=int(now_ms),
                    retry_index=retry_index,
                    discovery_period_ms=int(trading_cfg.get("rollover_check_period_ms", rollover_manager.config.discovery_period_ms)),
                )
                discovery_error_retry_index += 1
                discovery_error_next_retry_ts_ms = int(next_retry_ts_ms)
                error_payload = _build_discovery_error_payload(
                    exc=exc,
                    current_state=current_state,
                    discovery_summary=discovery_summary,
                    now_ms=int(now_ms),
                    retry_index=retry_index,
                    next_retry_ts_ms=int(next_retry_ts_ms),
                )
                discovery_requests = _discovery_requests_from_summary(discovery_summary)
                discovery_requests.append(dict(error_payload))
                discovery_requests = _dedupe_discovery_requests(discovery_requests)
                _append_discovery_request_rows(
                    db=db,
                    ts_ms=now_ms,
                    discovery_requests=discovery_requests,
                )
                _emit_rollover_abort(
                    now_ms=now_ms,
                    current_state=current_state,
                    intent_payload=intent_payload,
                    abort_reason="DISCOVERY_ERROR",
                    extra_payload={
                        **error_payload,
                        "discovery_summary": discovery_summary,
                        "discovery_requests": discovery_requests,
                    },
                    log_level="WARNING",
                    log_code="rollover_abort_discovery_error",
                    count_toward_health=False,
                )
                await asyncio.sleep(check_period_secs)
                continue

            none_found_retry_index = 0
            none_found_next_retry_ts_ms = None
            none_found_start_ts_ms = None
            discovery_error_retry_index = 0
            discovery_error_next_retry_ts_ms = None
            none_found_deadline_alerted = False
            none_found_quotes_canceled = False

            candidate_state = _resolve_primary_market_state(discovered_markets, discovered_asset_meta)
            if candidate_state is None:
                _emit_rollover_abort(
                    now_ms=now_ms,
                    current_state=current_state,
                    intent_payload=intent_payload,
                    abort_reason="DISCOVERY_NOT_SINGLE_MARKET",
                    extra_payload={"discovery_summary": discovery_summary},
                    log_level="WARNING",
                    log_code="rollover_abort_discovery_not_single",
                )
                await asyncio.sleep(check_period_secs)
                continue

            candidate_payload = {
                "market_slug_new": candidate_state.market_slug,
                "condition_id_new": candidate_state.condition_id,
                "token_ids_new": list(candidate_state.token_ids),
                "market_end_ts_ms_new": candidate_state.market_end_ts_ms,
                "market_end_source_new": candidate_state.market_end_source,
                "selection_key_new": candidate_state.selection_key,
            }

            if not rollover_manager.has_market_changed(candidate_state):
                await asyncio.sleep(check_period_secs)
                continue

            if not rollover_manager.can_commit_switch(now_ms, trigger_reasons):
                _emit_rollover_abort(
                    now_ms=now_ms,
                    current_state=current_state,
                    intent_payload=intent_payload,
                    abort_reason="PREFETCH_WAIT_UNTIL_END",
                    extra_payload=candidate_payload,
                    log_level="INFO",
                    log_code="rollover_deferred_prefetch",
                    count_toward_health=False,
                )
                await asyncio.sleep(check_period_secs)
                continue

            candidate_live, candidate_liveness_state, candidate_liveness_details = _candidate_liveness(
                asset_meta=discovered_asset_meta,
                token_ids=candidate_state.token_ids,
                now_ms=now_ms,
                market_end_ts_ms=candidate_state.market_end_ts_ms,
            )
            if not candidate_live:
                _emit_rollover_abort(
                    now_ms=now_ms,
                    current_state=current_state,
                    intent_payload=intent_payload,
                    abort_reason="CANDIDATE_NOT_LIVE",
                    extra_payload={
                        **candidate_payload,
                        "candidate_liveness_state": candidate_liveness_state,
                        "candidate_liveness_details": candidate_liveness_details,
                        "discovery_summary": discovery_summary,
                    },
                    log_level="WARNING",
                    log_code="rollover_abort_candidate_not_live",
                )
                await asyncio.sleep(check_period_secs)
                continue

            token_ids_changed = sorted(current_state.token_ids) != sorted(candidate_state.token_ids)
            prepare_diag: Dict[str, Any] = {"removed_tokens": [], "cancelled_orders": 0}
            switch_result: ResubscribeResult
            committed_books: Dict[str, OrderBook]
            if token_ids_changed:
                try:
                    prepare_diag = await runtime.prepare_rollover(
                        next_token_ids=list(candidate_state.token_ids),
                        now_ms=now_ms,
                    )
                except Exception as exc:
                    market_client.set_books(runtime.books)
                    _emit_rollover_abort(
                        now_ms=now_ms,
                        current_state=current_state,
                        intent_payload=intent_payload,
                        abort_reason="PREPARE_ERROR",
                        extra_payload={
                            **candidate_payload,
                            "error": str(exc),
                        },
                        log_level="ERROR",
                        log_code="rollover_abort_prepare_error",
                    )
                    await asyncio.sleep(check_period_secs)
                    continue

                candidate_books: Dict[str, OrderBook] = dict(runtime.books)
                for token_id in candidate_state.token_ids:
                    if token_id not in candidate_books:
                        candidate_books[token_id] = OrderBook(asset_id=token_id, bids={}, asks={})
                market_client.set_books(candidate_books)
                switch_result = await market_client.resubscribe(
                    new_asset_ids=list(candidate_state.token_ids),
                    first_book_timeout_secs=confirm_timeout_secs,
                )
                confirm_diag_summary = _confirm_diag_summary(
                    switch_result.confirm_diag,
                    _maybe_float(switch_result.confirm_wait_ms),
                )
                if switch_result.status != "committed":
                    market_client.set_books(runtime.books)
                    db.append_rollover_metric(
                        ts_ms=now_ms,
                        metric_name="rollover_confirm_wait_ms",
                        metric_value=_maybe_float(switch_result.confirm_wait_ms),
                        market_slug=candidate_state.market_slug,
                        selection_key=candidate_state.selection_key,
                        payload={"status": switch_result.status, "abort": True},
                    )
                    db.append_rollover_metric(
                        ts_ms=now_ms,
                        metric_name="rollover_unsubscribe_ms",
                        metric_value=_maybe_float(switch_result.unsubscribe_ms),
                        market_slug=candidate_state.market_slug,
                        selection_key=candidate_state.selection_key,
                        payload={"status": switch_result.status, "abort": True},
                    )
                    db.append_rollover_metric(
                        ts_ms=now_ms,
                        metric_name="rollover_confirm_missing_assets",
                        metric_value=float(len(confirm_diag_summary.get("missing_assets", []))),
                        market_slug=candidate_state.market_slug,
                        selection_key=candidate_state.selection_key,
                        payload=confirm_diag_summary,
                    )
                    db.append_rollover_metric(
                        ts_ms=now_ms,
                        metric_name="rollover_confirm_reject_total",
                        metric_value=float(
                            sum(
                                int(value)
                                for value in (confirm_diag_summary.get("rejects_by_asset") or {}).values()
                            )
                        ),
                        market_slug=candidate_state.market_slug,
                        selection_key=candidate_state.selection_key,
                        payload=confirm_diag_summary,
                    )
                    _emit_rollover_abort(
                        now_ms=now_ms,
                        current_state=current_state,
                        intent_payload=intent_payload,
                        abort_reason=switch_result.abort_reason or "SWITCH_ABORT",
                        extra_payload={
                            **candidate_payload,
                            "confirm_diag": switch_result.confirm_diag,
                            "confirm_diag_summary": confirm_diag_summary,
                            "switch_status": switch_result.status,
                            "active_subscription_id_after": int(switch_result.active_subscription_id),
                            "prepare_diag": prepare_diag,
                            "confirm_wait_ms": _maybe_float(switch_result.confirm_wait_ms),
                            "unsubscribe_ms": _maybe_float(switch_result.unsubscribe_ms),
                        },
                        log_level="WARNING",
                        log_code="rollover_abort_switch",
                    )
                    await asyncio.sleep(check_period_secs)
                    continue
                committed_books = {
                    token_id: candidate_books.get(token_id, OrderBook(asset_id=token_id, bids={}, asks={}))
                    for token_id in candidate_state.token_ids
                }
            else:
                switch_result = ResubscribeResult(
                    status="commit_metadata_only",
                    previous_asset_ids=list(current_state.token_ids),
                    new_asset_ids=list(candidate_state.token_ids),
                    active_subscription_id=market_client.active_subscription_id(),
                    confirm_diag={},
                )
                confirm_diag_summary = _confirm_diag_summary(
                    switch_result.confirm_diag,
                    _maybe_float(switch_result.confirm_wait_ms),
                )
                committed_books = dict(runtime.books)

            db.append_rollover_status(
                ts_ms=now_ms,
                event_type="CONFIRM",
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                end_ts_source=candidate_state.market_end_source,
                readiness_ok=None,
                readiness_reason_codes=None,
                confirm_wait_ms=_maybe_float(switch_result.confirm_wait_ms),
                commit_block_ms=None,
                unsubscribe_ms=_maybe_float(switch_result.unsubscribe_ms),
                unknown_msg_count=metrics.market_unknown_count(),
                ignored_old_rate_per_min=metrics.market_ignored_old_rate_per_min(now_ms),
                payload={
                    "status": switch_result.status,
                    "confirm_diag": switch_result.confirm_diag,
                    "confirm_diag_summary": confirm_diag_summary,
                    "active_subscription_id": int(switch_result.active_subscription_id),
                },
            )

            readiness_result = runtime.evaluate_market_readiness(
                token_ids=list(candidate_state.token_ids),
                now_ms=now_ms,
                books_override=committed_books,
                market_meta_override=discovered_asset_meta,
            )
            if readiness_result.ready:
                readiness_pass_count += 1
            else:
                readiness_fail_count += 1
                readiness_last_reasons = list(readiness_result.reason_codes)
            db.append_rollover_metric(
                ts_ms=now_ms,
                metric_name="readiness_pass_count",
                metric_value=float(readiness_pass_count),
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                payload={"last_reasons": list(readiness_last_reasons)},
            )
            db.append_rollover_metric(
                ts_ms=now_ms,
                metric_name="readiness_fail_count",
                metric_value=float(readiness_fail_count),
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                payload={"last_reasons": list(readiness_last_reasons)},
            )
            liveness_ok, liveness_details = _pending_books_liveness(candidate_state.token_ids, committed_books)
            commit_decision = _rollover_commit_decision(
                now_ms=now_ms,
                readiness_ready=bool(readiness_result.ready),
                escape_hatch_open=rollover_manager.escape_hatch_open(now_ms),
                liveness_ok=bool(liveness_ok),
            )
            old_market_non_viable = _old_market_non_viable(
                now_ms=now_ms,
                market_end_ts_ms=current_state.market_end_ts_ms,
            )
            readiness_payload = {
                **candidate_payload,
                "readiness_ok": bool(readiness_result.ready),
                "readiness_reason_codes": list(readiness_result.reason_codes),
                "readiness_details": readiness_result.details,
                "readiness_pass_count": int(readiness_pass_count),
                "readiness_fail_count": int(readiness_fail_count),
                "readiness_last_reasons": list(readiness_last_reasons),
                "commit_decision": commit_decision.action,
                "commit_decision_reason": commit_decision.reason,
                "liveness_ok": bool(liveness_ok),
                "liveness_details": liveness_details,
                "old_market_non_viable": bool(old_market_non_viable),
            }
            db.append_rollover_status(
                ts_ms=now_ms,
                event_type="READINESS_CHECK",
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                end_ts_source=candidate_state.market_end_source,
                readiness_ok=bool(readiness_result.ready),
                readiness_reason_codes=list(readiness_result.reason_codes),
                confirm_wait_ms=_maybe_float(switch_result.confirm_wait_ms),
                commit_block_ms=None,
                unsubscribe_ms=_maybe_float(switch_result.unsubscribe_ms),
                unknown_msg_count=metrics.market_unknown_count(),
                ignored_old_rate_per_min=metrics.market_ignored_old_rate_per_min(now_ms),
                payload=readiness_payload,
            )
            adopted_without_readiness = False
            rollback_skipped_reason: Optional[str] = None
            candidate_constraints = _build_constraints(discovered_markets, policy_cfg, settings)
            runtime_diag: Dict[str, Any]
            commit_block_ms: Optional[float] = None
            previous_state = current_state
            commit_already_performed = False
            if commit_decision.action != "COMMIT":
                if _should_adopt_switched_market_without_readiness(
                    token_ids_changed=bool(token_ids_changed),
                    switch_status=str(switch_result.status),
                    commit_action=str(commit_decision.action),
                    old_market_non_viable=bool(old_market_non_viable),
                ):
                    rollback_skipped_reason = "OLD_MARKET_NON_VIABLE"
                    adopted_without_readiness = True
                    commit_decision = RolloverCommitDecision(
                        action="COMMIT",
                        force_observe_only=True,
                        reason="READINESS_BLOCK_ADOPT_NEW_MARKET",
                    )
                    try:
                        commit_block_start_ns = time.monotonic_ns()
                        async with runtime_lock:
                            runtime_diag = runtime.commit_rollover_swap(
                                books=committed_books,
                                constraints=candidate_constraints,
                                market_meta=discovered_asset_meta,
                                now_ms=now_ms,
                            )
                        commit_block_ms = float(max(0.0, (time.monotonic_ns() - commit_block_start_ns) / 1_000_000.0))
                        market_client.set_books(runtime.books)
                        previous_state = rollover_manager.commit(candidate_state)
                        resolved_markets[:] = discovered_markets
                        asset_meta.clear()
                        asset_meta.update(discovered_asset_meta)
                        commit_already_performed = True
                    except Exception as exc:
                        market_client.set_books(runtime.books)
                        abort_payload = {
                            **intent_payload,
                            "abort_reason": "RUNTIME_ROLLOVER_ERROR",
                            "error": str(exc),
                            **candidate_payload,
                            "prepare_diag": prepare_diag,
                            "confirm_diag": switch_result.confirm_diag,
                            "confirm_diag_summary": confirm_diag_summary,
                            "switch_status": switch_result.status,
                            "active_subscription_id_after": int(switch_result.active_subscription_id),
                            "confirm_wait_ms": _maybe_float(switch_result.confirm_wait_ms),
                            "unsubscribe_ms": _maybe_float(switch_result.unsubscribe_ms),
                            "readiness_reason_codes": list(readiness_result.reason_codes),
                            "old_market_non_viable": True,
                            "rollback_attempted": False,
                            "rollback_skipped_reason": rollback_skipped_reason,
                            "adopted_without_readiness": False,
                        }
                        _emit_rollover_abort(
                            now_ms=now_ms,
                            current_state=current_state,
                            intent_payload=intent_payload,
                            abort_reason="RUNTIME_ROLLOVER_ERROR",
                            extra_payload=abort_payload,
                            log_level="ERROR",
                            log_code="rollover_abort_runtime_error",
                        )
                        await asyncio.sleep(check_period_secs)
                        continue
                else:
                    rollback_payload: Dict[str, Any] = {}
                    if token_ids_changed and switch_result.status == "committed":
                        rollback_payload = await _rollback_post_switch_abort(
                            market_client=market_client,
                            runtime=runtime,
                            previous_token_ids=list(current_state.token_ids),
                            confirm_timeout_secs=confirm_timeout_secs,
                        )
                    else:
                        market_client.set_books(runtime.books)
                    _emit_rollover_abort(
                        now_ms=now_ms,
                        current_state=current_state,
                        intent_payload=intent_payload,
                        abort_reason=commit_decision.reason,
                        extra_payload={
                            **readiness_payload,
                            "prepare_diag": prepare_diag,
                            "confirm_diag": switch_result.confirm_diag,
                            "confirm_diag_summary": confirm_diag_summary,
                            "switch_status": switch_result.status,
                            "confirm_wait_ms": _maybe_float(switch_result.confirm_wait_ms),
                            "unsubscribe_ms": _maybe_float(switch_result.unsubscribe_ms),
                            **rollback_payload,
                        },
                        log_level="INFO",
                        log_code="rollover_abort_readiness_or_liveness",
                        count_toward_health=not rollover_manager.escape_hatch_open(now_ms),
                    )
                    await asyncio.sleep(check_period_secs)
                    continue

            if not commit_already_performed:
                try:
                    commit_block_start_ns = time.monotonic_ns()
                    async with runtime_lock:
                        runtime_diag = runtime.commit_rollover_swap(
                            books=committed_books,
                            constraints=candidate_constraints,
                            market_meta=discovered_asset_meta,
                            now_ms=now_ms,
                        )
                    commit_block_ms = float(max(0.0, (time.monotonic_ns() - commit_block_start_ns) / 1_000_000.0))
                    market_client.set_books(runtime.books)
                    previous_state = rollover_manager.commit(candidate_state)
                    resolved_markets[:] = discovered_markets
                    asset_meta.clear()
                    asset_meta.update(discovered_asset_meta)
                except Exception as exc:
                    rollback_payload = {}
                    if token_ids_changed and switch_result.status == "committed":
                        rollback_payload = await _rollback_post_switch_abort(
                            market_client=market_client,
                            runtime=runtime,
                            previous_token_ids=list(current_state.token_ids),
                            confirm_timeout_secs=confirm_timeout_secs,
                        )
                    else:
                        market_client.set_books(runtime.books)
                    abort_payload = {
                        **intent_payload,
                        "abort_reason": "RUNTIME_ROLLOVER_ERROR",
                        "error": str(exc),
                        **candidate_payload,
                        "prepare_diag": prepare_diag,
                        "confirm_diag": switch_result.confirm_diag,
                        "confirm_diag_summary": confirm_diag_summary,
                        "switch_status": switch_result.status,
                        "active_subscription_id_after": int(switch_result.active_subscription_id),
                        "confirm_wait_ms": _maybe_float(switch_result.confirm_wait_ms),
                        "unsubscribe_ms": _maybe_float(switch_result.unsubscribe_ms),
                        "readiness_reason_codes": list(readiness_result.reason_codes),
                        **rollback_payload,
                    }
                    _emit_rollover_abort(
                        now_ms=now_ms,
                        current_state=current_state,
                        intent_payload=intent_payload,
                        abort_reason="RUNTIME_ROLLOVER_ERROR",
                        extra_payload=abort_payload,
                        log_level="ERROR",
                        log_code="rollover_abort_runtime_error",
                    )
                    await asyncio.sleep(check_period_secs)
                    continue

            commit_payload = {
                "market_slug_prev": previous_state.market_slug,
                "market_slug_new": candidate_state.market_slug,
                "condition_id_prev": previous_state.condition_id,
                "condition_id_new": candidate_state.condition_id,
                "token_ids_prev": list(previous_state.token_ids),
                "token_ids_new": list(candidate_state.token_ids),
                "market_end_ts_ms_prev": previous_state.market_end_ts_ms,
                "market_end_ts_ms_new": candidate_state.market_end_ts_ms,
                "market_end_source_prev": previous_state.market_end_source,
                "market_end_source_new": candidate_state.market_end_source,
                "selection_key_prev": previous_state.selection_key,
                "selection_key_new": candidate_state.selection_key,
                "as_of_ts_ms": now_ms,
                "trigger_reasons": sorted(set(list(trigger_reasons) + ["DISCOVERY_NEW_MARKET_FOUND"])),
                "rollover_count": int(rollover_manager.rollover_count),
                "prepare_diag": prepare_diag,
                "confirm_diag": switch_result.confirm_diag,
                "confirm_diag_summary": confirm_diag_summary,
                "switch_status": switch_result.status,
                "confirm_wait_ms": _maybe_float(switch_result.confirm_wait_ms),
                "commit_block_ms": _maybe_float(commit_block_ms),
                "unsubscribe_ms": _maybe_float(switch_result.unsubscribe_ms),
                "readiness_ok": bool(readiness_result.ready),
                "readiness_reason_codes": list(readiness_result.reason_codes),
                "readiness_details": readiness_result.details,
                "commit_decision_reason": commit_decision.reason,
                "adopted_without_readiness": bool(adopted_without_readiness),
                "old_market_non_viable": bool(old_market_non_viable),
                "rollback_skipped_reason": rollback_skipped_reason,
                "liveness_ok": bool(liveness_ok),
                "liveness_details": liveness_details,
                "runtime_diag": runtime_diag,
                "discovery_summary": discovery_summary,
                "active_subscription_id_before": int(active_sub_before),
                "active_subscription_id_after": int(switch_result.active_subscription_id),
            }
            db.append_rollover_metric(
                ts_ms=now_ms,
                metric_name="rollover_confirm_wait_ms",
                metric_value=_maybe_float(switch_result.confirm_wait_ms),
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                payload={"status": switch_result.status},
            )
            db.append_rollover_metric(
                ts_ms=now_ms,
                metric_name="rollover_commit_block_ms",
                metric_value=_maybe_float(commit_block_ms),
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                payload={"status": switch_result.status},
            )
            db.append_rollover_metric(
                ts_ms=now_ms,
                metric_name="rollover_unsubscribe_ms",
                metric_value=_maybe_float(switch_result.unsubscribe_ms),
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                payload={"status": switch_result.status},
            )
            db.append_rollover_metric(
                ts_ms=now_ms,
                metric_name="rollover_confirm_missing_assets",
                metric_value=float(len(confirm_diag_summary.get("missing_assets", []))),
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                payload=confirm_diag_summary,
            )
            db.append_rollover_metric(
                ts_ms=now_ms,
                metric_name="rollover_confirm_reject_total",
                metric_value=float(
                    sum(
                        int(value)
                        for value in (confirm_diag_summary.get("rejects_by_asset") or {}).values()
                    )
                ),
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                payload=confirm_diag_summary,
            )
            db.append_rollover_status(
                ts_ms=now_ms,
                event_type="COMMIT",
                market_slug=candidate_state.market_slug,
                selection_key=candidate_state.selection_key,
                end_ts_source=candidate_state.market_end_source,
                readiness_ok=bool(readiness_result.ready),
                readiness_reason_codes=list(readiness_result.reason_codes),
                confirm_wait_ms=_maybe_float(switch_result.confirm_wait_ms),
                commit_block_ms=_maybe_float(commit_block_ms),
                unsubscribe_ms=_maybe_float(switch_result.unsubscribe_ms),
                unknown_msg_count=metrics.market_unknown_count(),
                ignored_old_rate_per_min=metrics.market_ignored_old_rate_per_min(now_ms),
                payload=commit_payload,
            )
            quiet_until_ms = int(now_ms + max(0, rollover_quiet_window_ms))
            async with runtime_lock:
                runtime.activate_rollover_guard(
                    token_ids=list(candidate_state.token_ids),
                    quiet_until_ms=quiet_until_ms,
                    require_readiness=not adopted_without_readiness,
                )
            if commit_decision.force_observe_only:
                db.append_log(
                    now_ms,
                    "INFO",
                    "rollover_commit_observe_only",
                    {
                        "market_slug": candidate_state.market_slug,
                        "quiet_until_ts_ms": quiet_until_ms,
                        "reason": commit_decision.reason,
                        "readiness_reason_codes": list(readiness_result.reason_codes),
                    },
                )
            _write_rollover_event(
                event_tape=event_tape,
                event_type="ROLLOVER_COMMIT",
                now_ms=now_ms,
                market=candidate_state.market_slug,
                payload=commit_payload,
            )
            _write_rollover_decision_boundary(
                decision_tape=decision_tape,
                time_mapper=time_mapper,
                event_type="ROLLOVER_COMMIT",
                now_ms=now_ms,
                payload=commit_payload,
            )
            db.append_log(now_ms, "INFO", "rollover_commit", commit_payload)
            await asyncio.sleep(check_period_secs)

    tasks.append(asyncio.create_task(_quote_loop()))
    tasks.append(asyncio.create_task(_stats_loop()))
    tasks.append(asyncio.create_task(_rollover_loop()))

    def _shutdown(*_args) -> None:
        stop_event.set()
        market_client.stop()
        for feed in ref_feeds:
            feed.stop()
        for client in ref_ws_clients:
            client.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    db.append_log(
        _now_ms(),
        "INFO",
        f"run_system_started mode={mode}",
        {
            "run_id": run_id,
            "mode_requested": mode_raw,
            "mode_effective": mode,
            "observe_live_alias": bool(observe_live_alias),
            "sim_exec": bool(args.sim_exec),
            "cli_exec": bool(args.cli_exec),
            "order_actions_enabled": bool(mode in {"PAPER", "TRADE"}),
            "reference_poll_secs": float(settings.reference_poll_secs),
            "pstar_max_age_ms": int(pstar_builder.max_age_ms),
            "a_pipeline_diag": runtime._a_pipeline_diag(now_ms=_now_ms()),
        },
    )
    await stop_event.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    db.append_log(_now_ms(), "INFO", "run_system_stopped", {"run_id": run_id})
    event_tape.close()
    decision_tape.close()
    trade_tape.close()
    db.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket V1 dual-loop system runner")
    parser.add_argument("--mode", default=None, help="OBSERVE|OBSERVE_LIVE|PAPER|TRADE")
    parser.add_argument("--markets", default=None, help="Path to markets config")
    parser.add_argument("--log-dir", default=None, help="Directory for JSONL compatibility tapes")
    parser.add_argument("--db-path", default=None, help="SQLite path")
    parser.add_argument("--constitution", default=None, help="Path to constitution config")
    parser.add_argument("--auto_discover", action="store_true", help="Resolve markets via discovery")
    parser.add_argument("--reference_source", default=None, help="CSV sources: poll_coinbase,poll_binance_perp,ws_kraken,ws_kraken_futures_perp")
    parser.add_argument("--quote-interval-ms", type=int, default=None, help="Quote loop interval in ms")
    parser.add_argument("--stats-interval-ms", type=int, default=None, help="Stats loop interval in ms")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run live broker methods")
    parser.add_argument("--sim_exec", action="store_true", help="Force simulated execution broker")
    parser.add_argument("--cli_exec", action="store_true", help="Force CLI execution broker")
    return parser.parse_args()


def _effective_auto_discover(cli_auto_discover: bool, settings_auto_discover: bool) -> bool:
    return bool(cli_auto_discover or settings_auto_discover)


def _resolve_runtime_mode(mode_raw: str) -> Tuple[str, bool]:
    mode_upper = str(mode_raw or "OBSERVE").upper()
    if mode_upper == "OBSERVE_LIVE":
        # Explicit live-feed/no-order operator mode alias.
        return "OBSERVE", True
    return mode_upper, False


def _handle_startup_guard_result(
    db: SQLiteStore,
    mode: str,
    guard_ok: bool,
    guard_payload: Dict[str, Any],
) -> bool:
    if guard_ok:
        return False
    readiness_state = str(guard_payload.get("readiness_state") or FEED_READINESS_BOOTING).upper()
    severity = "critical"
    code = "FEED_NOT_WIRED"
    state = ALERT_STATE_FROZEN
    if mode == "OBSERVE":
        severity = "warning"
        code = "FEED_NOT_WIRED_OBSERVE"
        state = ALERT_STATE_DEGRADED
    db.append_alert(
        _now_ms(),
        severity,
        code,
        f"startup_feed_guard_failed mode={mode} readiness_state={readiness_state}",
        payload={**guard_payload, "state": state, "readiness_state": readiness_state},
    )
    if mode in {"PAPER", "TRADE"}:
        return readiness_state != FEED_READINESS_READY
    return False


def _should_emit_unknown_ws_alert(
    mode: str,
    now_ms: int,
    run_epoch_ms: int,
    unknown_rate_per_min: float,
    unknown_sample_count: int,
    active_rate_per_min: float,
    threshold_per_min: int,
    min_samples: int,
    min_ratio_vs_active: float,
    startup_grace_ms: int,
) -> bool:
    # Noise suppression for OBSERVE: keep telemetry but avoid operational paging.
    if mode not in {"PAPER", "TRADE"}:
        return False
    if int(now_ms - run_epoch_ms) < int(max(0, startup_grace_ms)):
        return False
    if int(unknown_sample_count) < int(max(1, min_samples)):
        return False
    if float(unknown_rate_per_min) < float(max(1, threshold_per_min)):
        return False
    active = max(1.0, float(active_rate_per_min))
    ratio = float(unknown_rate_per_min) / active
    return ratio >= float(max(0.1, min_ratio_vs_active))


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
