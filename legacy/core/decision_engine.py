from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.classic_signals import ClassicSignalConfig, ClassicSignalState
from core.decision_tape import DecisionRecord, DecisionTape, TimeMapper
from core.entry_exit_rules import EntryExitParams, PositionState, entry_gate, exit_gate
from core.execution_model import FeeModel
from core.fees import fee_bps
from core.dpds import DpdsEstimator
from core.feature_builder import (
    FEATURE_ORDER,
    FeatureBuildError,
    ReferenceFeatureState,
    build_feature_vector,
    default_feature_config,
    feature_order_hash,
)
from core.hedge_policy import HedgeParams, hedge_policy
from core.market_time import parse_end_epoch_from_slug
from core.model_artifact import ModelArtifact
from core.onchain_signals import OnchainSignalState
from core.order_book import OrderBook
from core.p_fair_baseline import BaselineCfg, p_fair_baseline
from core.reference_price import ReferencePriceAggregator, ReferencePriceResult, ReferenceQuote
from core.reference_signals import ReferenceSignalState
from core.reference_store import ReferenceStore
from core.validators import HypotheticalOrder, OrderConstraints, validate_hypothetical_order


@dataclass(frozen=True)
class DecisionEngineConfig:
    order_size: float
    heartbeat_interval_ns: int = 1_000_000_000
    execution_mode: str = "TAKER_SIM"
    fee_rate: float = 0.0025
    fee_mode: str = "taker"
    engine_version: Optional[str] = None
    depth_within_ticks_n: int = 5
    depth_at_notional_target: float = 10.0
    ref_half_life_sec: float = 120.0
    reference_lag_guard_ms: int = 0
    reference_staleness_ms: int = 5000
    edge_min: float = 0.015
    edge_exit: float = 0.00375
    edge_stop: float = 0.0075
    z_mom_min: float = 1.0
    t_min_secs: float = 90.0
    hold_max_secs: float = 480.0
    vol_pct_hi: float = 95.0
    edge_min_mult_hivol: float = 1.5
    tox_max: float = 0.0008
    hedge_min: float = 0.0
    hedge_max: float = 1.0
    hedge_required_vol_pct: float = 95.0
    pf_bias: float = 0.0
    pf_w_mom: float = 0.35
    pf_w_revert: float = 0.15
    pf_z_clip: float = 4.0
    pf_vol_dampen_enabled: bool = True
    pf_vol_floor: float = 0.6
    classic_signals_enabled: bool = True
    classic_signals_skew_enabled: bool = False
    classic_signals_max_skew_bps: float = 0.0
    classic_signals_inventory_regime_enabled: bool = False
    classic_signal_config: ClassicSignalConfig = field(default_factory=ClassicSignalConfig)


class DecisionEngine:
    def __init__(
        self,
        books: Dict[str, OrderBook],
        constraints: Dict[str, OrderConstraints],
        tape: DecisionTape,
        time_mapper: TimeMapper,
        config: DecisionEngineConfig,
        market_meta: Optional[Dict[str, Dict[str, object]]] = None,
        reference_aggregator: Optional["ReferencePriceAggregator"] = None,
        model_artifact: Optional["ModelArtifact"] = None,
        model_path: Optional[str] = None,
        model_load_error: Optional[str] = None,
        reference_store: Optional["ReferenceStore"] = None,
        dpds_estimator: Optional["DpdsEstimator"] = None,
        onchain_state: Optional["OnchainSignalState"] = None,
        decision_listener: Optional[Callable[[DecisionRecord, List[Dict[str, object]]], None]] = None,
    ) -> None:
        self.books = books
        self.constraints = constraints
        self.tape = tape
        self.time_mapper = time_mapper
        self.config = config
        self._last_bbo: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        self._last_decision_mono_ns: Dict[str, int] = {}
        self._fee_model = FeeModel(fee_rate=config.fee_rate, mode=config.fee_mode)
        self._market_meta = market_meta or {}
        self._reference_aggregator = reference_aggregator
        self._signal_states: Dict[str, ReferenceSignalState] = {}
        self._classic_signal_states: Dict[str, ClassicSignalState] = {}
        self._positions: Dict[str, PositionState] = {}
        self._feature_states: Dict[str, ReferenceFeatureState] = {}
        self._feature_config = default_feature_config()
        self._feature_order_hash = feature_order_hash(FEATURE_ORDER)
        self._model_artifact = model_artifact
        self._model_path = model_path
        self._model_load_error = model_load_error
        self._reference_store = reference_store
        self._dpds_estimator = dpds_estimator or DpdsEstimator()
        self._onchain_state = onchain_state
        self._decision_listener = decision_listener
        self._entry_exit_params = EntryExitParams(
            edge_min=config.edge_min,
            edge_exit=config.edge_exit,
            edge_stop=config.edge_stop,
            z_mom_min=config.z_mom_min,
            t_min_secs=config.t_min_secs,
            hold_max_secs=config.hold_max_secs,
            vol_pct_hi=config.vol_pct_hi,
            edge_min_mult_hivol=config.edge_min_mult_hivol,
        )
        self._hedge_params = HedgeParams(
            edge_min=config.edge_min,
            tox_max=config.tox_max,
            h_min=config.hedge_min,
            h_max=config.hedge_max,
            hedge_required_vol_pct=config.hedge_required_vol_pct,
        )
        self._baseline_cfg = BaselineCfg(
            bias=config.pf_bias,
            w_mom=config.pf_w_mom,
            w_revert=config.pf_w_revert,
            z_clip=config.pf_z_clip,
            vol_dampen_enabled=config.pf_vol_dampen_enabled,
            vol_floor=config.pf_vol_floor,
        )

    def on_reference_event(self, quote: ReferenceQuote) -> None:
        state = self._feature_states.get(quote.symbol)
        if state is None:
            state = ReferenceFeatureState(self._feature_config.max_history_ms)
            self._feature_states[quote.symbol] = state
        state.ingest(quote)
        if self._reference_store is not None:
            record = {
                "market": quote.symbol,
                "t_event_ms": quote.t_event_ms,
                "t_recv_mono_ns": quote.t_recv_mono_ns,
                "t_recv_wall_iso": quote.t_recv_wall_iso,
                "raw": {
                    "symbol": quote.symbol,
                    "value": quote.value,
                    "mid": quote.value,
                    "source": quote.source,
                },
            }
            self._reference_store.ingest_record(record)

    def on_book_update(self, asset_id: str, decision_mono_ns: int) -> None:
        book = self.books.get(asset_id)
        if book is None:
            return
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        last_bbo = self._last_bbo.get(asset_id)
        if last_bbo == (best_bid, best_ask):
            return
        self._last_bbo[asset_id] = (best_bid, best_ask)
        self._emit_decision(asset_id, decision_mono_ns, trigger="bbo_change")

    def emit_heartbeats_until(self, decision_mono_ns: int) -> None:
        for asset_id in self.books.keys():
            last = self._last_decision_mono_ns.get(asset_id)
            if last is None:
                self._emit_decision(asset_id, decision_mono_ns, trigger="heartbeat")
                continue
            while decision_mono_ns - last >= self.config.heartbeat_interval_ns:
                last = last + self.config.heartbeat_interval_ns
                self._emit_decision(asset_id, last, trigger="heartbeat")

    def _emit_decision(self, asset_id: str, decision_mono_ns: int, trigger: str) -> None:
        book = self.books.get(asset_id)
        constraint = self.constraints.get(asset_id)
        if book is None or constraint is None:
            return
        wall_ms = self.time_mapper.wall_ms(decision_mono_ns)
        wall_iso = self.time_mapper.wall_iso(decision_mono_ns)

        best_bid = book.best_bid()
        best_ask = book.best_ask()
        best_bid_size = book.bids.get(best_bid) if best_bid is not None else None
        best_ask_size = book.asks.get(best_ask) if best_ask is not None else None
        depth_within_ticks_bid = book.depth_within_ticks_bid(
            self.config.depth_within_ticks_n, constraint.min_tick
        )
        depth_within_ticks_ask = book.depth_within_ticks_ask(
            self.config.depth_within_ticks_n, constraint.min_tick
        )
        depth_at_notional_bid = book.depth_at_qty("sell", self.config.depth_at_notional_target)
        depth_at_notional_ask = book.depth_at_qty("buy", self.config.depth_at_notional_target)
        mid = book.mid()
        spread_bps = book.spread_bps()
        book_stale = book.book_is_stale(decision_mono_ns, constraint.max_book_staleness_ms)

        exec_buy_diag = book.execution_diagnostics("buy", self.config.order_size, decision_mono_ns)
        exec_sell_diag = book.execution_diagnostics("sell", self.config.order_size, decision_mono_ns)
        exec_buy = exec_buy_diag.vwap_price
        exec_sell = exec_sell_diag.vwap_price

        price = exec_buy if exec_buy is not None else (best_ask or constraint.max_price)
        order = HypotheticalOrder(
            asset_id=asset_id,
            side="BUY",
            price=price,
            size=self.config.order_size,
            t_decision_wall=wall_iso,
            t_decision_mono_ns=decision_mono_ns,
            t_decision_event_ts_ms=book.last_event_ts_ms or 0,
        )
        ok, reasons, metrics = validate_hypothetical_order(
            order,
            book,
            constraint,
            balances=_null_balances(),
            now_mono_ns=decision_mono_ns,
            execution_mode=self.config.execution_mode,
        )

        ref_result = None
        ref_reason = None
        ref_symbol = _reference_symbol(self._market_meta, asset_id)
        if self._reference_aggregator is None or ref_symbol is None:
            ref_reason = "missing_source"
        else:
            ref_result = self._reference_aggregator.validated_price(
                ref_symbol, decision_mono_ns, wall_ms
            )
            ref_conf = None
            if ref_result.price is not None:
                ref_conf = ref_result.price.confidence
            if ref_result.status == "ok":
                ref_reason = None
            elif ref_result.status == "partial":
                if ref_conf is None or ref_conf < self._reference_aggregator.min_confidence:
                    ref_reason = "partial_confidence_low"
            else:
                ref_reason = ref_result.status
        if ref_reason is not None:
            reasons.append(ref_reason)
            ok = False

        signals = _default_signals()
        if ref_symbol is not None:
            state = self._signal_states.setdefault(
                ref_symbol, ReferenceSignalState(half_life_sec=self.config.ref_half_life_sec)
            )
            if ref_result is not None and ref_result.price is not None and ref_reason is None:
                snapshot = state.update(ref_result.price, decision_mono_ns)
            else:
                snapshot = state.snapshot()
            signals["z_mom"] = snapshot.z_mom
            signals["sigma_t"] = snapshot.sigma_t
            signals["r_t"] = snapshot.r_t
            if snapshot.z_mom is not None:
                signals["z_revert"] = -snapshot.z_mom
            if snapshot.sigma_t is not None:
                signals["vol_ewma"] = snapshot.sigma_t
        signals["time_remaining_sec"] = _time_remaining_sec(
            _meta_value(self._market_meta, asset_id, "slug"), wall_ms
        )

        ref_store_blockers: List[str] = []
        ref_mid = None
        ref_latency_ms = None
        ref_store_ts = None
        if self._reference_store is not None and ref_symbol is not None:
            asof = self._reference_store.asof(
                ref_symbol,
                decision_ts_ms=wall_ms,
                lag_guard_ms=int(self.config.reference_lag_guard_ms),
                staleness_ms=int(self.config.reference_staleness_ms),
            )
            ref_store_blockers = list(asof.blockers)
            ref_mid = asof.mid
            ref_latency_ms = asof.latency_ms
            ref_store_ts = asof.t_event_ms
        signals["ref_mid"] = ref_mid
        signals["ref_latency_ms"] = ref_latency_ms
        signals["ref_blockers"] = ref_store_blockers
        if ref_result is not None:
            signals["reference_status"] = ref_result.status
            if ref_result.price is not None:
                signals["reference_confidence"] = ref_result.price.confidence
                signals["reference_sources"] = ref_result.price.sources

        onchain_signals = None
        if self._onchain_state is not None:
            condition_id = _meta_value(self._market_meta, asset_id, "condition_id")
            onchain_signals = self._onchain_state.snapshot(
                asset_id=asset_id,
                condition_id=str(condition_id) if condition_id else None,
                now_mono_ns=decision_mono_ns,
            )

        p_star_payload = _format_reference(ref_result, ref_reason)
        outcome = _meta_value(self._market_meta, asset_id, "outcome")
        p_fair_baseline_value = None
        z_mom = signals.get("z_mom")
        z_revert = signals.get("z_revert")
        vol = signals.get("vol_ewma")
        if outcome is not None and z_mom is not None and z_revert is not None and vol is not None:
            p_fair_baseline_value = p_fair_baseline(
                outcome=str(outcome),
                z_mom=float(z_mom),
                z_revert=float(z_revert),
                vol=float(vol),
                cfg=self._baseline_cfg,
            )
            signals["p_fair_baseline"] = p_fair_baseline_value

        model_used = "baseline"
        model_blockers: List[str] = []
        feature_vector: Optional[List[float]] = None
        feature_order = FEATURE_ORDER
        p_fair_value = p_fair_baseline_value

        if self._model_load_error is not None:
            model_blockers.append(self._model_load_error)

        if self._model_artifact is None:
            model_blockers.append("MODEL_MISSING")
        elif self._model_artifact.feature_order != feature_order:
            model_blockers.append("FEATURE_MISMATCH")
        else:
            ref_symbol = _reference_symbol(self._market_meta, asset_id)
            if ref_symbol is None:
                model_blockers.append("FEATURE_SYMBOL_MISSING")
            else:
                state = self._feature_states.get(ref_symbol)
                if state is None:
                    model_blockers.append("FEATURE_STATE_MISSING")
                else:
                    try:
                        _, feature_vector = build_feature_vector(
                            {"t_decision_wall_ms": wall_ms},
                            state,
                            self._feature_config,
                        )
                        offset = _offset_from_mode(
                            self._model_artifact.offset_mode,
                            exec_buy,
                            mid,
                        )
                        if self._model_artifact.offset_mode and offset is None:
                            model_blockers.append("OFFSET_MISSING")
                        else:
                            logits = _dot(self._model_artifact.w, feature_vector) + self._model_artifact.b
                            if offset is not None:
                                logits += offset
                            p_raw = _sigmoid(logits)
                        p_cal = _apply_platt(p_raw, self._model_artifact.platt)
                        p_fair_value = p_cal
                        model_used = "trained"
                        model_blockers = []
                    except FeatureBuildError as exc:
                        model_blockers.append(exc.code)

        p_fair_model_value = p_fair_value
        classic_snapshot = None
        classic_snapshot_payload = None
        if self.config.classic_signals_enabled:
            classic_state = self._classic_signal_states.setdefault(
                asset_id,
                ClassicSignalState(config=self.config.classic_signal_config),
            )
            classic_snapshot = classic_state.update(
                as_of_ts_ms=wall_ms,
                market_as_of_ts_ms=book.last_event_ts_ms,
                fair_as_of_ts_ms=_classic_fair_asof_ts_ms(ref_result),
                p_fair=p_fair_value,
                best_bid=best_bid,
                best_ask=best_ask,
                best_bid_size=best_bid_size,
                best_ask_size=best_ask_size,
            )
            classic_snapshot_payload = classic_snapshot.as_dict()
            signals["trend_score"] = classic_snapshot.trend_score
            signals["momentum_score"] = classic_snapshot.momentum_score
            signals["residual_zscore"] = classic_snapshot.residual_zscore
            signals["mean_reversion_score"] = classic_snapshot.mean_reversion_score
        classic_overlay = _classic_signal_overlay(
            classic_snapshot=classic_snapshot,
            skew_enabled=self.config.classic_signals_skew_enabled,
            max_skew_bps=self.config.classic_signals_max_skew_bps,
            inventory_regime_enabled=self.config.classic_signals_inventory_regime_enabled,
        )
        classic_overlay["p_fair_model"] = p_fair_model_value
        p_fair_value = _apply_classic_signal_overlay(p_fair_model_value, classic_overlay)
        classic_overlay["p_fair_adjusted"] = p_fair_value
        signals["p_fair"] = p_fair_value

        fee_mode = _fee_mode_from_execution(self.config.execution_mode)
        fee_status = _fee_status_from_meta(self._market_meta, asset_id)
        fee_bps_buy = None if exec_buy is None else fee_bps(exec_buy, fee_mode, fee_status)
        fee_bps_sell = None if exec_sell is None else fee_bps(exec_sell, fee_mode, fee_status)
        fee_per_share_buy = _fee_per_share(exec_buy, fee_bps_buy)
        fee_per_share_sell = _fee_per_share(exec_sell, fee_bps_sell)
        exec_buy_net = None if exec_buy is None else exec_buy + fee_per_share_buy
        exec_sell_net = None if exec_sell is None else exec_sell - fee_per_share_sell

        confidence = None
        mapping_error = _mapping_error(p_fair_value, exec_buy)
        illiquidity = _illiquidity_score(
            spread_bps=spread_bps,
            depth=exec_buy_diag.depth_at_qty,
            slippage_bps=exec_buy_diag.slippage_bps,
        )
        decision_snapshot = {
            "asset_id": asset_id,
            "token_id": asset_id,
            "outcome": outcome,
            "p_fair": p_fair_value,
            "p_market_exec_buy": exec_buy_net,
            "p_market_exec_sell": exec_sell_net,
            "p_star": p_star_payload,
            "gates": {"allow": ok, "reasons": reasons},
            "notes": {"signals": signals},
            "t_decision_mono_ns": decision_mono_ns,
            "confidence": confidence,
            "mapping_error": mapping_error,
            "illiquidity": illiquidity,
            "latency_ms": ref_latency_ms,
        }

        slippage_bps_buy = exec_buy_diag.slippage_bps
        slippage_bps_sell = exec_sell_diag.slippage_bps
        toxicity_haircut_bps = 0.0
        edge_bps_buy = None
        edge_bps_sell = None
        net_edge_bps_buy = None
        net_edge_bps_sell = None
        edge_net_buy = None
        edge_net_sell = None
        if p_fair_value is not None and exec_buy is not None and fee_bps_buy is not None:
            edge_bps_buy = (p_fair_value - exec_buy) * 10000.0
            slippage_bps_val = 0.0 if slippage_bps_buy is None else float(slippage_bps_buy)
            net_edge_bps_buy = edge_bps_buy - fee_bps_buy - slippage_bps_val - toxicity_haircut_bps
            edge_net_buy = net_edge_bps_buy / 10000.0
        if p_fair_value is not None and exec_sell is not None and fee_bps_sell is not None:
            edge_bps_sell = (exec_sell - p_fair_value) * 10000.0
            slippage_bps_val = 0.0 if slippage_bps_sell is None else float(slippage_bps_sell)
            net_edge_bps_sell = edge_bps_sell - fee_bps_sell - slippage_bps_val - toxicity_haircut_bps
            edge_net_sell = net_edge_bps_sell / 10000.0

        decision_snapshot["edge_net_override"] = {"buy": edge_net_buy, "sell": edge_net_sell}
        entry_eval = entry_gate(decision_snapshot, self._entry_exit_params)
        chosen_action = entry_eval.get("chosen_action") or {}
        if signals.get("edge_net") is None:
            signals["edge_net"] = chosen_action.get("edge_net")
        confidence = _edge_confidence(chosen_action.get("edge_net"), self.config.edge_min)
        decision_snapshot["confidence"] = confidence

        position = self._positions.get(asset_id)
        exit_eval = None
        if position is not None:
            exit_eval = exit_gate(position, decision_snapshot, self._entry_exit_params)
            if exit_eval.get("should_exit"):
                self._positions.pop(asset_id, None)
                position = None

        if position is None and entry_eval.get("allow"):
            side = chosen_action.get("side")
            exec_price = chosen_action.get("p_exec")
            notional = None if exec_price is None else float(exec_price) * self.config.order_size
            if side in {"buy", "sell"}:
                position = PositionState(
                    token_id=asset_id,
                    outcome=decision_snapshot.get("outcome"),
                    side=side,
                    entry_mono_ns=decision_mono_ns,
                    entry_edge=chosen_action.get("edge_net"),
                    size=self.config.order_size,
                    notional=notional,
                )
                self._positions[asset_id] = position

        hedge_eval = hedge_policy(position, decision_snapshot, self._hedge_params)

        dpds_result = self._dpds_estimator.estimate_dpds(
            asset_id=asset_id,
            p_market=exec_buy,
            spot_mid=ref_mid,
            now_ts_ms=wall_ms,
        )

        depth_buy = exec_buy_diag.depth_at_qty
        depth_sell = exec_sell_diag.depth_at_qty
        slippage_buy = exec_buy_diag.slippage_bps
        slippage_sell = exec_sell_diag.slippage_bps
        notional_buy = None if exec_buy is None else exec_buy * self.config.order_size
        notional_sell = None if exec_sell is None else exec_sell * self.config.order_size
        fee_buy = (
            None
            if notional_buy is None or fee_bps_buy is None
            else notional_buy * (fee_bps_buy / 10000.0)
        )
        fee_sell = (
            None
            if notional_sell is None or fee_bps_sell is None
            else notional_sell * (fee_bps_sell / 10000.0)
        )

        chosen_side = chosen_action.get("side")
        exec_price_yes = None
        fee_bps_used = None
        slippage_bps_used = None
        edge_bps_used = None
        net_edge_bps_used = None
        if chosen_side == "buy":
            exec_price_yes = exec_buy
            fee_bps_used = fee_bps_buy
            slippage_bps_used = slippage_bps_buy
            edge_bps_used = edge_bps_buy
            net_edge_bps_used = net_edge_bps_buy
        elif chosen_side == "sell":
            exec_price_yes = exec_sell
            fee_bps_used = fee_bps_sell
            slippage_bps_used = slippage_bps_sell
            edge_bps_used = edge_bps_sell
            net_edge_bps_used = net_edge_bps_sell

        record = DecisionRecord(
            schema_version="decision_v3_pf_baseline",
            engine_version=self.config.engine_version,
            run_id=self.tape.run_id,
            t_decision_wall_iso=wall_iso,
            t_decision_wall_ms=wall_ms,
            t_decision_mono_ns=decision_mono_ns,
            asset_id=asset_id,
            token_id=asset_id,
            market_slug=_meta_value(self._market_meta, asset_id, "slug"),
            condition_id=_meta_value(self._market_meta, asset_id, "condition_id"),
            outcome=_meta_value(self._market_meta, asset_id, "outcome"),
            outcome_by_token=_meta_value(self._market_meta, asset_id, "outcome_by_token"),
            book={
                "best_bid": best_bid,
                "best_ask": best_ask,
                "best_bid_size": best_bid_size,
                "best_ask_size": best_ask_size,
                "depth_within_ticks_bid": depth_within_ticks_bid,
                "depth_within_ticks_ask": depth_within_ticks_ask,
                "depth_within_ticks_n": self.config.depth_within_ticks_n,
                "depth_at_notional_bid": depth_at_notional_bid,
                "depth_at_notional_ask": depth_at_notional_ask,
                "depth_at_notional_target": self.config.depth_at_notional_target,
                "depth_units": "shares",
                "mid": mid,
                "spread_bps": spread_bps,
                "book_stale": book_stale,
            },
            p_market_mid=mid,
            p_market_exec_buy=exec_buy,
            p_market_exec_sell=exec_sell,
            p_market=exec_buy,
            p_fair=p_fair_value,
            edge_net_buy=edge_net_buy,
            edge_net_sell=edge_net_sell,
            p_star=p_star_payload,
            labels=None,
            features_raw={
                "classic_signals": None if classic_snapshot_payload is None else dict(classic_snapshot_payload)
            },
            features_ortho=None,
            whitening=None,
            gates={"allow": ok, "reasons": reasons},
            exec_cost={
                "q": self.config.order_size,
                "depth_at_qty_buy": depth_buy,
                "depth_at_qty_sell": depth_sell,
                "vwap_price_buy": exec_buy,
                "vwap_price_sell": exec_sell,
                "slippage_bps_buy": slippage_buy,
                "slippage_bps_sell": slippage_sell,
                "fee_est_buy": fee_buy,
                "fee_est_sell": fee_sell,
                "notional_buy": notional_buy,
                "notional_sell": notional_sell,
                "edge_bps": edge_bps_used,
                "exec_price_yes": exec_price_yes,
                "fee_mode": fee_mode,
                "fee_status": fee_status,
                "fee_bps_used": fee_bps_used,
                "slippage_bps": slippage_bps_used,
                "toxicity_haircut_bps": toxicity_haircut_bps,
                "net_edge_bps": net_edge_bps_used,
                "ev_net": 0.0,
            },
            notes={
                "whitening_enabled": False,
                "trigger": trigger,
                "metrics": metrics,
                "execution_mode": self.config.execution_mode,
                "fee_rate": self._fee_model.fee_rate,
                "fee_mode": self._fee_model.mode,
                "fee_model_version": "v2_piecewise",
                "model_used": model_used,
                "model_path": self._model_path,
                "model_schema_version": None if self._model_artifact is None else self._model_artifact.schema_version,
                "model_blockers": model_blockers,
                "feature_order_hash": self._feature_order_hash,
                "feature_vector": feature_vector,
                "confidence": confidence,
                "hedge_fraction": hedge_eval.get("hedge_ratio_target"),
                "hedge_blockers": hedge_eval.get("blockers"),
                "reference_store": {
                    "mid": ref_mid,
                    "t_event_ms": ref_store_ts,
                    "latency_ms": ref_latency_ms,
                    "blockers": ref_store_blockers,
                },
                "dpds": {
                    "dpds": dpds_result.dpds,
                    "beta_logit": dpds_result.beta_logit,
                    "blockers": dpds_result.blockers,
                },
                "signals": signals,
                "classic_signals": classic_snapshot_payload,
                "classic_signal_overlay": classic_overlay,
                "onchain_signals": onchain_signals,
                "chosen_action": chosen_action,
                "entry_gate": {
                    "allow": entry_eval.get("allow"),
                    "reasons": entry_eval.get("reasons"),
                    "edge_min_required": entry_eval.get("edge_min_required"),
                },
                "exit_recommendation": exit_eval,
                "hedge_policy": hedge_eval,
                "baseline_cfg": {
                    "bias": self._baseline_cfg.bias,
                    "w_mom": self._baseline_cfg.w_mom,
                    "w_revert": self._baseline_cfg.w_revert,
                    "z_clip": self._baseline_cfg.z_clip,
                    "vol_dampen_enabled": self._baseline_cfg.vol_dampen_enabled,
                    "vol_floor": self._baseline_cfg.vol_floor,
                    "model_version": self._baseline_cfg.model_version,
                },
                "resolved_market": self._market_meta.get(asset_id),
            },
        )
        self._last_decision_mono_ns[asset_id] = decision_mono_ns
        self.tape.write(record)
        intents = _build_intents(
            entry_allow=entry_eval.get("allow"),
            chosen_action=chosen_action,
            record=record,
            order_size=self.config.order_size,
            execution_mode=self.config.execution_mode,
        )
        if self._decision_listener is not None:
            self._decision_listener(record, intents)

    @staticmethod
    def _pick_price(book: OrderBook, constraint: OrderConstraints) -> float:
        bid = book.best_bid()
        if bid is None:
            return constraint.min_price
        return bid + constraint.min_tick


def _null_balances():
    from core.validators import SimBalances

    return SimBalances(usd=float("inf"), tokens={}, default_token_balance=float("inf"))


def _meta_value(meta: Dict[str, Dict[str, object]], asset_id: str, key: str) -> Optional[object]:
    entry = meta.get(asset_id)
    if entry is None:
        return None
    return entry.get(key)


def _reference_symbol(meta: Dict[str, Dict[str, object]], asset_id: str) -> Optional[str]:
    value = _meta_value(meta, asset_id, "reference_symbol")
    if value is None:
        return None
    return str(value)


def _format_reference(
    result: Optional[ReferencePriceResult], freeze_reason: Optional[str]
) -> Dict[str, object]:
    if result is None:
        return {
            "pstar_px": None,
            "pstar_asof_wall_ms": None,
            "spot_px": None,
            "spot_asof_wall_ms": None,
            "perp_px": None,
            "perp_asof_wall_ms": None,
            "diff_bps": None,
            "c_basis": None,
            "c_stale": None,
            "c_ref": None,
            "status": freeze_reason or "missing_source",
            "reasons": [freeze_reason] if freeze_reason else [],
            "freeze_reason": freeze_reason,
            "value": None,
            "q_basis": None,
            "q_stale": None,
            "q_total": None,
            "dt_seconds_remaining": None,
            "sigma_1s": None,
            "sigma_5m": None,
            "sigma_15m": None,
            "p_fair_up": None,
        }
    price = result.price
    pstar_px = None if price is None else price.value
    pstar_asof = None if price is None else price.t_recv_wall_ms
    return {
        "pstar_px": pstar_px,
        "pstar_asof_wall_ms": result.pstar_asof_wall_ms or pstar_asof,
        "spot_px": result.spot_px,
        "spot_asof_wall_ms": result.spot_asof_wall_ms,
        "perp_px": result.perp_px,
        "perp_asof_wall_ms": result.perp_asof_wall_ms,
        "diff_bps": result.diff_bps,
        "c_basis": result.c_basis,
        "c_stale": result.c_stale,
        "c_ref": result.c_ref,
        "status": result.status,
        "reasons": result.reasons,
        "freeze_reason": result.freeze_reason or freeze_reason,
        "value": pstar_px,
        "q_basis": result.c_basis,
        "q_stale": result.c_stale,
        "q_total": result.c_ref,
        "dt_seconds_remaining": None,
        "sigma_1s": None,
        "sigma_5m": None,
        "sigma_15m": None,
        "p_fair_up": None,
        "ts_event_ms": None if price is None else price.ts_event_ms,
        "t_recv_mono_ns": None if price is None else price.t_recv_mono_ns,
        "sources": None if price is None else price.sources,
        "confidence": None if price is None else price.confidence,
    }


def _default_signals() -> Dict[str, Optional[float]]:
    return {
        "z_mom": None,
        "edge_net": None,
        "sigma_t": None,
        "sigma_10s": None,
        "vol_regime": None,
        "r_t": None,
        "time_remaining_sec": None,
        "p_fair": None,
        "tox_10s": None,
        "z_revert": None,
        "vol_ewma": None,
        "trend_score": None,
        "momentum_score": None,
        "mean_reversion_score": None,
        "residual_zscore": None,
    }


def _classic_fair_asof_ts_ms(result: Optional[ReferencePriceResult]) -> Optional[int]:
    if result is None:
        return None
    if result.price is not None and result.price.ts_event_ms is not None:
        return int(result.price.ts_event_ms)
    if result.pstar_asof_wall_ms is not None:
        return int(result.pstar_asof_wall_ms)
    return None


def _classic_signal_overlay(
    *,
    classic_snapshot,
    skew_enabled: bool,
    max_skew_bps: float,
    inventory_regime_enabled: bool,
) -> Dict[str, Any]:
    valid = bool(classic_snapshot is not None and classic_snapshot.valid)
    overlay_score = None if classic_snapshot is None else _classic_overlay_score(classic_snapshot)
    enabled = bool(valid and skew_enabled and max_skew_bps > 0.0 and overlay_score is not None)
    skew_bps = 0.0 if not enabled or overlay_score is None else float(overlay_score) * float(max_skew_bps)
    return {
        "enabled": enabled,
        "valid": valid,
        "overlay_score": overlay_score,
        "skew_bps": skew_bps,
        "max_skew_bps": float(max_skew_bps),
        "inventory_regime_enabled": bool(inventory_regime_enabled),
        "inventory_regime": None
        if not (inventory_regime_enabled and valid and classic_snapshot is not None)
        else classic_snapshot.composite_regime,
    }


def _classic_overlay_score(classic_snapshot) -> float:
    score = (
        0.5 * float(classic_snapshot.trend_score)
        + 0.3 * float(classic_snapshot.momentum_score)
        + 0.2 * float(classic_snapshot.mean_reversion_score)
    )
    return max(-1.0, min(1.0, score))


def _apply_classic_signal_overlay(
    p_fair_value: Optional[float],
    overlay: Dict[str, Any],
) -> Optional[float]:
    if p_fair_value is None:
        return None
    adjusted = float(p_fair_value)
    if overlay.get("enabled"):
        adjusted += float(overlay.get("skew_bps") or 0.0) / 10000.0
    return max(0.0, min(1.0, adjusted))


def _time_remaining_sec(slug: Optional[object], wall_ms: int) -> Optional[float]:
    if slug is None:
        return None
    end_sec = parse_end_epoch_from_slug(str(slug))
    if end_sec is None:
        return None
    now_sec = wall_ms / 1000.0
    return end_sec + 900.0 - now_sec


def _fee_per_share(exec_price: Optional[float], fee_bps_value: Optional[float]) -> float:
    if exec_price is None or fee_bps_value is None:
        return 0.0
    return max(0.0, exec_price * (fee_bps_value / 10000.0))


def _dot(weights: List[float], values: List[float]) -> float:
    return sum(w * x for w, x in zip(weights, values))


def _sigmoid(x: float) -> float:
    import math

    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _apply_platt(p_raw: float, platt: Optional[object]) -> float:
    if platt is None:
        return p_raw
    if not hasattr(platt, "a") or not hasattr(platt, "c"):
        return p_raw
    import math

    a = float(getattr(platt, "a"))
    c = float(getattr(platt, "c"))
    p_raw = min(max(p_raw, 1e-6), 1.0 - 1e-6)
    logit = math.log(p_raw / (1.0 - p_raw))
    return _sigmoid(a * logit + c)


def _edge_confidence(edge_net: Optional[object], edge_min: float) -> Optional[float]:
    if edge_net is None:
        return None
    try:
        edge_val = float(edge_net)
    except (TypeError, ValueError):
        return None
    if edge_min <= 0:
        return None
    confidence = min(1.0, abs(edge_val) / edge_min)
    return max(0.0, confidence)


def _offset_from_mode(
    offset_mode: Optional[str],
    exec_buy: Optional[float],
    mid: Optional[float],
) -> Optional[float]:
    if offset_mode is None:
        return 0.0
    mode = str(offset_mode)
    if mode == "logit_p_market_exec_buy":
        return _safe_logit(exec_buy)
    if mode == "logit_p_market_mid":
        return _safe_logit(mid)
    return None


def _safe_logit(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if value <= 0 or value >= 1:
        return None
    return math.log(value / (1.0 - value))


def _mapping_error(p_fair: Optional[float], p_market: Optional[float]) -> Optional[float]:
    logit_fair = _safe_logit(p_fair)
    logit_mkt = _safe_logit(p_market)
    if logit_fair is None or logit_mkt is None:
        return None
    return abs(logit_fair - logit_mkt)


def _illiquidity_score(
    spread_bps: Optional[float],
    depth: Optional[float],
    slippage_bps: Optional[float],
) -> Optional[float]:
    components: List[float] = []
    if spread_bps is not None:
        components.append(min(1.0, max(0.0, float(spread_bps) / 100.0)))
    if depth is not None and depth > 0:
        components.append(min(1.0, 1.0 / float(depth)))
    if slippage_bps is not None:
        components.append(min(1.0, max(0.0, float(slippage_bps) / 100.0)))
    if not components:
        return None
    return sum(components) / len(components)


def _fee_mode_from_execution(execution_mode: str) -> str:
    mode = execution_mode.strip().upper()
    if mode.startswith("MAKE"):
        return "MAKE"
    return "TAKE"


def _fee_status_from_meta(meta: Dict[str, Dict[str, object]], asset_id: str) -> str:
    entry = meta.get(asset_id) or {}
    fee_info = entry.get("fee")
    if isinstance(fee_info, dict):
        status = fee_info.get("status")
        if isinstance(status, str) and status in {"ok", "not_fee_addressable", "unknown"}:
            return status
    return "unknown"


def _build_intents(
    entry_allow: Optional[object],
    chosen_action: Dict[str, Any],
    record: DecisionRecord,
    order_size: float,
    execution_mode: str,
) -> List[Dict[str, object]]:
    if not entry_allow:
        return []
    side = chosen_action.get("side")
    price = chosen_action.get("p_exec")
    if side not in {"buy", "sell"} or price is None:
        return []
    mode = _fee_mode_from_execution(execution_mode)
    decision_id = f"{record.run_id}:{record.t_decision_wall_ms}:{record.asset_id}"
    return [
        {
            "asset_id": record.asset_id,
            "side": side,
            "size": float(order_size),
            "price": float(price),
            "mode": mode,
            "t_decision_wall_ms": record.t_decision_wall_ms,
            "as_of_ts_ms": record.t_decision_wall_ms,
            "decision_id": decision_id,
            "outcome": record.outcome,
        }
    ]
