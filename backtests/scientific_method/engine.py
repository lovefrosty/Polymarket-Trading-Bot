from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from core.market_time import window_start_end_ms
from core.model_fit import fit_ridge_logistic, predict_proba

from backtests.scientific_method.constraints import PortfolioConstraints, load_portfolio_constraints
from backtests.scientific_method.execution import apply_fee
from backtests.scientific_method.features import (
    FeatureHistory,
    belief_lag_metric,
    imbalance_persistence,
    logit,
    odds_momentum,
    time_of_day_features,
)
from backtests.scientific_method.io_utils import expand_paths, load_jsonl, write_json
from backtests.scientific_method.regime import HMMFilter, load_hmm_params
from backtests.scientific_method.sizing import SizingConfig, size_from_kelly


REFERENCE_BASE_SYMBOL = "BTC"
ALLOWED_BELIEF_SYMBOLS = {"BTC", "ETH", "SOL", "XRP"}

HMM_OBS_RETURN_SEC = 300
HMM_VOL_HALF_LIFE_SEC = 1800.0
HMM_SAMPLE_SEC = 300
HMM_LAMBDA = math.exp(-math.log(2.0) / 6.0)

BELIEF_LAG_WINDOW_SEC = 24 * 3600
BELIEF_LAG_LAGS_SEC = [step * HMM_SAMPLE_SEC for step in range(1, 13)]
BELIEF_LAG_MIN_CORR = 0.15

VOL_OF_VOL_THRESHOLD = 0.1
VOL_OF_VOL_DECAY = 5.0

CONFIDENCE_FLOOR = 0.25

REQUIRED_REF_SOURCES = {"spot", "perp"}


@dataclass
class ExperimentSpec:
    raw: Dict[str, Any]

    @property
    def experiment_id(self) -> str:
        return str(self.raw.get("hypothesis_id") or self.raw.get("name"))

    @property
    def name(self) -> str:
        return str(self.raw.get("name"))


@dataclass
class DecisionRow:
    t_decision_ms: int
    t_decision_mono: Optional[int]
    asset_id: str
    condition_id: Optional[str]
    outcome: Optional[str]
    market_slug: Optional[str]
    reference_symbol: Optional[str]
    features: Dict[str, Optional[float]]
    feature_timestamps: Dict[str, Optional[int]]
    feature_asof_ts_ms: Optional[int]
    feature_asof_source: Optional[str]
    feature_asof_detail: Optional[str]
    label: Optional[int]
    label_time_ms: Optional[int]
    p_market_buy: Optional[float]
    p_market_sell: Optional[float]
    depth_buy: Optional[float]
    depth_sell: Optional[float]
    slippage_buy: Optional[float]
    slippage_sell: Optional[float]
    book_spread_bps: Optional[float]
    best_bid_size: Optional[float]
    best_ask_size: Optional[float]
    depth_within_ticks_bid: Optional[float]
    depth_within_ticks_ask: Optional[float]
    depth_within_ticks_n: Optional[int]
    depth_at_notional_bid: Optional[float]
    depth_at_notional_ask: Optional[float]
    depth_at_notional_target: Optional[float]
    depth_units: Optional[str]
    imbalance_l1: Optional[float]
    imbalance_depth: Optional[float]
    diff_bps: Optional[float]
    alpha_basis: Optional[float]
    pstar_disagreement_extreme: bool
    belief_lag_sec: Optional[float]
    belief_lag_corr: Optional[float]
    alpha_lag: float
    alpha_vov: float
    sigma_t: Optional[float]
    sigma_prev: Optional[float]
    regime_pi: Optional[List[float]] = None
    regime_id: Optional[int] = None


@dataclass
class Position:
    asset_id: str
    condition_id: Optional[str]
    outcome: Optional[str]
    symbol: Optional[str]
    side: str
    size: float
    entry_price: float
    entry_time_ms: int
    resolution_time_ms: int
    label: Optional[int]
    regime_id: Optional[int]


@dataclass
class PortfolioState:
    equity: float
    positions: List[Position] = field(default_factory=list)
    peak_equity: float = 0.0
    equity_history: Deque[Tuple[int, float]] = field(default_factory=lambda: deque(maxlen=10000))
    initial_equity: float = 0.0

    def update_equity(self, t_ms: int, pnl: float) -> None:
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        self.equity_history.append((t_ms, self.equity))

    def drawdown_pct(self, lookback_days: int, t_ms: int) -> Optional[float]:
        if not self.equity_history:
            return None
        cutoff = t_ms - lookback_days * 24 * 60 * 60 * 1000
        peak = None
        for ts, eq in self.equity_history:
            if ts < cutoff:
                continue
            if peak is None or eq > peak:
                peak = eq
        if peak is None or peak <= 0:
            return None
        return (peak - self.equity) / peak


@dataclass
class HMMVolState:
    var: Optional[float] = None
    sigma: Optional[float] = None
    sigma_prev: Optional[float] = None
    last_bar_ts: Optional[int] = None

    def update(self, r: float, bar_ts: int) -> None:
        if self.last_bar_ts is not None and bar_ts <= self.last_bar_ts:
            return
        self.sigma_prev = self.sigma
        if self.var is None:
            self.var = r * r
        else:
            self.var = HMM_LAMBDA * self.var + (1.0 - HMM_LAMBDA) * (r * r)
        self.sigma = math.sqrt(max(self.var, 0.0))
        self.last_bar_ts = bar_ts


@dataclass
class ReferenceState:
    prices: Dict[str, Deque[Tuple[int, float]]] = field(default_factory=dict)
    last_bar_ts: Dict[str, int] = field(default_factory=dict)
    last_return: Dict[str, Tuple[int, float]] = field(default_factory=dict)
    diff_bps: Dict[str, float] = field(default_factory=dict)
    diff_ts: Dict[str, int] = field(default_factory=dict)
    hmm_vol: HMMVolState = field(default_factory=HMMVolState)
    hmm_last_obs_ts: Optional[int] = None


@dataclass
class DecisionLog:
    t_decision_ms: int
    asset_id: str
    side: Optional[str]
    size: float
    entry_price: Optional[float]
    p_hat: Optional[float]
    confidence: Optional[float]
    confidence_final: Optional[float]
    trade_allowed: bool
    model_calibration_factor: Optional[float]
    regime_posterior_factor: Optional[float]
    basis_disagreement_factor: Optional[float]
    mapping_error_factor: Optional[float]
    alpha_lag: Optional[float]
    alpha_vov: Optional[float]
    belief_lag_sec: Optional[float]
    belief_lag_corr: Optional[float]
    sigma_t: Optional[float]
    sigma_prev: Optional[float]
    diff_bps: Optional[float]
    feature_asof_ts_ms: Optional[int]
    feature_asof_source: Optional[str]
    feature_asof_detail: Optional[str]
    regime_id: Optional[int]
    blockers: List[str]


def run_experiment(spec_path: Path, logs_dir: Path = Path("./logs")) -> Dict[str, Any]:
    spec = ExperimentSpec(json.loads(spec_path.read_text()))
    _validate_label(spec.raw.get("label"))
    _validate_locked_spec(spec.raw)

    inputs = spec.raw.get("inputs") or {}
    decision_paths = _resolve_paths(inputs.get("decision_paths") or [], logs_dir)
    reference_paths = _resolve_paths(inputs.get("reference_paths") or [], logs_dir)

    constraints = _load_constraints(spec)
    hmm_filter = _load_regime_filter(spec)

    feature_cfg = spec.raw.get("features") or {}
    ref_return_horizons = [int(x) for x in (feature_cfg.get("reference_returns_sec") or [])]
    imbalance_windows = [int(x) for x in (feature_cfg.get("imbalance_windows_sec") or [])]
    momentum_windows = [int(x) for x in (feature_cfg.get("momentum_windows_sec") or [])]

    pstar_diff_bps_soft = _require_float(spec.raw, "pstar_diff_bps_soft")
    pstar_diff_bps_hard = _require_float(spec.raw, "pstar_diff_bps_hard")
    pstar_diff_bps_decay_k = _require_float(spec.raw, "pstar_diff_bps_decay_k")
    c_trade_min = _require_float(spec.raw, "c_trade_min")
    depth_within_ticks_n = _require_int(spec.raw, "depth_within_ticks_n")
    depth_at_notional_target = _require_float(spec.raw, "depth_at_notional_target")

    model_cfg = spec.raw.get("model") or {}
    train_frac = float(model_cfg.get("train_frac") or 0.7)
    l2_lambda = float(model_cfg.get("l2_lambda") or 1.0)
    max_iter = int(model_cfg.get("max_iter") or 500)
    tol = float(model_cfg.get("tol") or 1e-6)
    seed = int(model_cfg.get("seed") or 0)

    sizing_cfg = spec.raw.get("sizing") or {}
    initial_equity = float(sizing_cfg.get("initial_equity") or 1000.0)
    kelly_max = float(sizing_cfg.get("kelly_fraction_max") or 0.25)
    sizing = SizingConfig(kelly_fraction_max=kelly_max)

    fee_bps = float((spec.raw.get("execution") or {}).get("fee_bps") or 0.0)
    fee_rate = fee_bps / 10_000.0

    decisions = _load_decisions(decision_paths)
    reference_events = _load_reference_events(reference_paths)
    ref_prices_full = _build_reference_series(reference_events)
    market_series_full = _build_market_series_from_decisions(decisions)

    if not decisions:
        raise ValueError("no_decisions_loaded")

    history = FeatureHistory()
    ref_state = ReferenceState()

    rows: List[DecisionRow] = []
    inv = {
        "feature_time_violations": 0,
        "feature_from_future": 0,
        "execution_not_vwap": 0,
        "fee_double_count": 0,
        "warnings": [],
    }

    ref_idx = 0
    reference_events.sort(key=lambda e: e["t_event_ms"])

    selection = spec.raw.get("market_selection") or {}
    for decision in decisions:
        if not _match_selection_decision(decision, selection):
            continue
        t_decision_ms = decision["t_decision_ms"]

        while ref_idx < len(reference_events) and reference_events[ref_idx]["t_event_ms"] < t_decision_ms:
            _ingest_reference_event(reference_events[ref_idx], ref_state, history)
            ref_idx += 1

        row = _build_row(
            decision,
            history,
            t_decision_ms,
            imbalance_windows,
            momentum_windows,
            ref_return_horizons,
            feature_cfg,
            spec.raw.get("label") or {},
            depth_within_ticks_n,
            depth_at_notional_target,
            ref_state.prices,
            ref_prices_full,
            market_series_full,
        )
        if row is None:
            continue

        _check_feature_times(row, inv)
        row.regime_pi = _update_hmm_for_decision(hmm_filter, ref_state)
        row.regime_id = _regime_id(row.regime_pi)

        _apply_belief_lag(row, history)
        _apply_vol_of_vol(row, ref_state)
        _apply_basis_disagreement(
            row,
            ref_state,
            pstar_diff_bps_soft,
            pstar_diff_bps_hard,
            pstar_diff_bps_decay_k,
        )
        _set_feature_asof(row, history, ref_state)
        _enforce_feature_asof(row, inv)

        rows.append(row)
        _update_history_from_decision(history, row)

    if not rows:
        raise ValueError("no_feature_rows")

    feature_order = _feature_order(rows[0].features)
    X, y, conditioned_order = _build_matrix(rows, feature_order, hmm_filter is not None)
    feature_order = conditioned_order
    if not X:
        raise ValueError("no_training_rows")

    split_idx = max(1, int(len(X) * train_frac))
    X_train, y_train = X[:split_idx], y[:split_idx]
    X_test, y_test = X[split_idx:], y[split_idx:]
    mode = spec.raw.get("mode") or "default"
    if mode == "null":
        train_metrics = {}
        p_test = [0.5 for _ in y_test]
    else:
        w, b, train_metrics = fit_ridge_logistic(X_train, y_train, l2_lambda, max_iter, tol, seed)
        if X_test:
            p_test = predict_proba(X_test, w, b)
        else:
            p_test = []

    portfolio = PortfolioState(equity=initial_equity, peak_equity=initial_equity, initial_equity=initial_equity)
    pnl, trades, regime_pnl, decision_logs = _simulate_trades(
        rows[split_idx:],
        p_test,
        portfolio,
        constraints,
        sizing,
        fee_rate,
        c_trade_min,
        inv,
        mode,
    )
    decision_summary = _summarize_decisions(decision_logs, c_trade_min)
    depth_stats = _depth_telemetry_stats(rows)
    pstar_stats = _pstar_stats(rows)

    results = {
        "schema_version": "scientific_method_result_v1",
        "hypothesis_id": spec.experiment_id,
        "name": spec.name,
        "feature_order": feature_order,
        "train_metrics": train_metrics,
        "test_metrics": _metrics(p_test, y_test),
        "pnl": pnl,
        "trades": trades,
        "regime_pnl": regime_pnl,
        "invariants": inv,
        "decision_summary": decision_summary,
        "depth_telemetry": depth_stats,
        "pstar_stats": pstar_stats,
        "config": {
            "pstar_diff_bps_soft": pstar_diff_bps_soft,
            "pstar_diff_bps_hard": pstar_diff_bps_hard,
            "pstar_diff_bps_decay_k": pstar_diff_bps_decay_k,
            "c_trade_min": c_trade_min,
            "depth_within_ticks_n": depth_within_ticks_n,
            "depth_at_notional_target": depth_at_notional_target,
        },
    }
    results["pass"] = _evaluate_acceptance(results, spec.raw.get("acceptance") or {})

    output_root = Path("backtests/scientific_method")
    results_path = output_root / "results" / f"{spec.experiment_id}.json"
    report_path = output_root / "reports" / f"{spec.experiment_id}.md"
    decisions_path = output_root / "results" / f"{spec.experiment_id}_decisions.jsonl"
    write_json(results_path, results)
    report_path.write_text(_render_report(results))
    _write_decision_logs(decisions_path, decision_logs)
    return results


def _resolve_paths(patterns: List[str], logs_dir: Path) -> List[str]:
    if not patterns:
        return []
    resolved: List[str] = []
    for pattern in patterns:
        if pattern.startswith("./") or pattern.startswith("../") or pattern.startswith("/"):
            resolved.extend(expand_paths([pattern]))
        else:
            resolved.extend(expand_paths([str(logs_dir / pattern)]))
    return sorted(set(resolved))


def _load_constraints(spec: ExperimentSpec) -> PortfolioConstraints:
    cfg = spec.raw.get("constraints") or {}
    path = cfg.get("portfolio_path") or "config/portfolio.yaml"
    return load_portfolio_constraints(Path(path))


def _load_regime_filter(spec: ExperimentSpec) -> Optional[HMMFilter]:
    regime_cfg = spec.raw.get("regime") or {}
    if not regime_cfg.get("enabled", False):
        raise ValueError("regime_filter_required")
    path = regime_cfg.get("hmm_path")
    if not path:
        raise ValueError("missing_hmm_path")
    params = load_hmm_params(Path(path))
    return HMMFilter(params)


def _load_decisions(paths: List[str]) -> List[Dict[str, Any]]:
    records = load_jsonl(paths)
    decisions = []
    for record in records:
        t_decision_ms = record.get("t_decision_wall_ms")
        if t_decision_ms is None:
            continue
        asset_id = record.get("asset_id")
        if not asset_id:
            continue
        decisions.append(
            {
                "t_decision_ms": int(t_decision_ms),
                "t_decision_mono": record.get("t_decision_mono_ns"),
                "asset_id": asset_id,
                "condition_id": record.get("condition_id"),
                "outcome": record.get("outcome"),
                "market_slug": record.get("market_slug"),
                "reference_symbol": ((record.get("notes") or {}).get("resolved_market") or {}).get(
                    "reference_symbol"
                ),
                "book": record.get("book") or {},
                "exec_cost": record.get("exec_cost") or {},
                "p_market_exec_buy": record.get("p_market_exec_buy"),
                "p_market_exec_sell": record.get("p_market_exec_sell"),
            }
        )
    decisions.sort(key=lambda row: row["t_decision_ms"])
    return decisions


def _load_reference_events(paths: List[str]) -> List[Dict[str, Any]]:
    records = load_jsonl(paths)
    events: List[Dict[str, Any]] = []
    for record in records:
        if record.get("channel") != "reference":
            continue
        t_event_ms = record.get("t_event_ms")
        if t_event_ms is None:
            raise ValueError("reference_event_missing_t_event_ms")
        raw = record.get("raw") or {}
        symbol = raw.get("symbol") or record.get("market")
        price = raw.get("value")
        sources = raw.get("sources")
        if symbol is None or price is None or sources is None:
            raise ValueError("reference_event_missing_pstar_fields")
        if not isinstance(sources, list) or not REQUIRED_REF_SOURCES.issubset(set(sources)):
            raise ValueError("pstar_missing_sources")
        diff_bps = _to_float(raw.get("diff_bps"))
        spot_value = _to_float(raw.get("spot_value") or raw.get("spot"))
        perp_value = _to_float(raw.get("perp_value") or raw.get("perp"))
        if diff_bps is None and spot_value is not None and perp_value is not None:
            diff_bps = _diff_bps(spot_value, perp_value)
        if diff_bps is None:
            raise ValueError("pstar_missing_disagreement")
        events.append(
            {
                "t_event_ms": int(t_event_ms),
                "symbol": str(symbol),
                "price": float(price),
                "diff_bps": diff_bps,
            }
        )
    return events


def _build_reference_series(events: List[Dict[str, Any]]) -> Dict[str, Deque[Tuple[int, float]]]:
    series: Dict[str, List[Tuple[int, float]]] = {}
    for event in events:
        symbol = event["symbol"]
        series.setdefault(symbol, []).append((event["t_event_ms"], event["price"]))
    output: Dict[str, Deque[Tuple[int, float]]] = {}
    for symbol, values in series.items():
        values_sorted = sorted(values, key=lambda item: item[0])
        output[symbol] = deque(values_sorted)
    return output


def _build_market_series_from_decisions(decisions: List[Dict[str, Any]]) -> Dict[str, List[Tuple[int, float]]]:
    series: Dict[str, List[Tuple[int, float]]] = {}
    for decision in decisions:
        p_exec = decision.get("p_market_exec_buy")
        if p_exec is None:
            continue
        logit_price = logit(float(p_exec))
        if logit_price is None:
            continue
        series.setdefault(decision["asset_id"], []).append((decision["t_decision_ms"], logit_price))
    for values in series.values():
        values.sort(key=lambda item: item[0])
    return series


def _ingest_reference_event(
    event: Dict[str, Any],
    ref_state: ReferenceState,
    history: FeatureHistory,
) -> None:
    symbol = event["symbol"]
    t_ms = event["t_event_ms"]
    price = event["price"]
    series = ref_state.prices.setdefault(symbol, deque(maxlen=20000))
    series.append((t_ms, price))
    diff_bps = event.get("diff_bps")
    if diff_bps is not None:
        ref_state.diff_bps[symbol] = float(diff_bps)
        ref_state.diff_ts[symbol] = t_ms

    bar_ms = HMM_SAMPLE_SEC * 1000
    bar_ts = (t_ms // bar_ms) * bar_ms
    last_bar = ref_state.last_bar_ts.get(symbol)
    if last_bar is not None and bar_ts <= last_bar:
        return

    price_now = _price_asof(series, bar_ts)
    price_prev = _price_asof(series, bar_ts - bar_ms)
    ref_state.last_bar_ts[symbol] = bar_ts
    if price_now is None or price_prev is None or price_prev <= 0:
        return

    r = math.log(price_now / price_prev)
    history.update_ref_return(symbol, bar_ts, r)
    ref_state.last_return[symbol] = (bar_ts, r)
    if symbol == REFERENCE_BASE_SYMBOL:
        ref_state.hmm_vol.update(r, bar_ts)


def _update_hmm_for_decision(hmm_filter: Optional[HMMFilter], ref_state: ReferenceState) -> Optional[List[float]]:
    if hmm_filter is None:
        return None
    bar_ts = ref_state.hmm_vol.last_bar_ts
    if bar_ts is None or ref_state.hmm_last_obs_ts == bar_ts:
        return hmm_filter.last_pi or list(hmm_filter.params.pi0)
    last_return = ref_state.last_return.get(REFERENCE_BASE_SYMBOL)
    if last_return is None or last_return[0] != bar_ts:
        return hmm_filter.last_pi or list(hmm_filter.params.pi0)
    sigma = ref_state.hmm_vol.sigma
    sigma_prev = ref_state.hmm_vol.sigma_prev
    if sigma is None or sigma_prev is None or sigma <= 0 or sigma_prev <= 0:
        return hmm_filter.last_pi or list(hmm_filter.params.pi0)
    obs = [abs(last_return[1]), math.log(sigma), math.log(sigma / sigma_prev)]
    pi = hmm_filter.update(obs)
    ref_state.hmm_last_obs_ts = bar_ts
    return pi


def _build_row(
    decision: Dict[str, Any],
    history: FeatureHistory,
    t_decision_ms: int,
    imbalance_windows: List[int],
    momentum_windows: List[int],
    ref_return_horizons: List[int],
    feature_cfg: Dict[str, Any],
    label_cfg: Dict[str, Any],
    depth_within_ticks_n: int,
    depth_at_notional_target: float,
    ref_prices_state: Dict[str, Deque[Tuple[int, float]]],
    ref_prices: Dict[str, Deque[Tuple[int, float]]],
    market_series_full: Dict[str, List[Tuple[int, float]]],
) -> Optional[DecisionRow]:
    asset_id = decision["asset_id"]
    p_market_buy = _to_float(decision.get("p_market_exec_buy"))
    p_market_sell = _to_float(decision.get("p_market_exec_sell"))
    if p_market_buy is None or p_market_sell is None:
        raise ValueError("missing_execution_price")

    book = decision.get("book") or {}
    exec_cost = decision.get("exec_cost") or {}

    best_bid_size = _to_float(book.get("best_bid_size"))
    best_ask_size = _to_float(book.get("best_ask_size"))
    imbalance_l1 = _imbalance(best_bid_size, best_ask_size)

    depth_within_ticks_bid = _to_float(book.get("depth_within_ticks_bid"))
    depth_within_ticks_ask = _to_float(book.get("depth_within_ticks_ask"))
    depth_within_ticks_n_value = book.get("depth_within_ticks_n")
    try:
        depth_within_ticks_n_value = int(depth_within_ticks_n_value)
    except (TypeError, ValueError):
        depth_within_ticks_n_value = depth_within_ticks_n

    depth_at_notional_bid = _to_float(book.get("depth_at_notional_bid"))
    depth_at_notional_ask = _to_float(book.get("depth_at_notional_ask"))
    depth_at_notional_target_value = _to_float(book.get("depth_at_notional_target"))
    if depth_at_notional_target_value is None:
        depth_at_notional_target_value = depth_at_notional_target
    depth_units = book.get("depth_units") or "shares"

    depth_buy = _to_float(exec_cost.get("depth_at_qty_buy") or exec_cost.get("depth_at_qty"))
    depth_sell = _to_float(exec_cost.get("depth_at_qty_sell") or exec_cost.get("depth_at_qty"))
    imbalance_depth = None
    if depth_within_ticks_bid is not None and depth_within_ticks_ask is not None:
        imbalance_depth = _imbalance(depth_within_ticks_bid, depth_within_ticks_ask)
    elif depth_at_notional_bid is not None and depth_at_notional_ask is not None:
        imbalance_depth = _imbalance(depth_at_notional_bid, depth_at_notional_ask)
    elif depth_buy is not None and depth_sell is not None:
        imbalance_depth = _imbalance(depth_buy, depth_sell)

    book_spread_bps = _to_float(book.get("spread_bps"))
    slippage_buy = _to_float(exec_cost.get("slippage_bps_buy") or exec_cost.get("slippage_bps"))
    slippage_sell = _to_float(exec_cost.get("slippage_bps_sell") or exec_cost.get("slippage_bps"))

    features: Dict[str, Optional[float]] = {}
    feature_ts: Dict[str, Optional[int]] = {}

    include_book = bool(feature_cfg.get("book_metrics"))
    include_imbalance = bool(feature_cfg.get("imbalance_depth") or feature_cfg.get("imbalance_windows_sec"))
    include_momentum = bool(feature_cfg.get("momentum_windows_sec"))

    book_ts = t_decision_ms - 1
    if include_imbalance and (best_bid_size is None or best_ask_size is None):
        raise ValueError("missing_best_sizes")
    if include_imbalance and imbalance_depth is None:
        raise ValueError("missing_depth_sizes")
    if include_book and book_spread_bps is None:
        raise ValueError("missing_spread_bps")
    if include_book and depth_buy is None:
        raise ValueError("missing_depth_at_qty")
    if include_book and slippage_buy is None:
        raise ValueError("missing_slippage_bps")
    if include_imbalance and imbalance_l1 is not None:
        features["imbalance_l1"] = imbalance_l1
        feature_ts["imbalance_l1"] = book_ts
    if include_imbalance and imbalance_depth is not None:
        features["imbalance_depth"] = imbalance_depth
        feature_ts["imbalance_depth"] = book_ts
    if include_book and book_spread_bps is not None:
        features["spread_bps"] = book_spread_bps
        feature_ts["spread_bps"] = book_ts
    if include_book and depth_buy is not None:
        features["depth_at_qty"] = depth_buy
        feature_ts["depth_at_qty"] = book_ts
    if include_book and slippage_buy is not None:
        features["slippage_bps"] = slippage_buy
        feature_ts["slippage_bps"] = book_ts

    if include_imbalance and imbalance_windows:
        persistence = imbalance_persistence(history, asset_id, t_decision_ms, imbalance_windows)
        features.update(persistence)
        imb_ts = _latest_history_ts(history.imbalance.get(asset_id))
        for key in persistence:
            feature_ts[key] = imb_ts

    if include_momentum and momentum_windows:
        momentum = odds_momentum(history, asset_id, t_decision_ms, momentum_windows)
        features.update(momentum)
        logit_ts = _latest_history_ts(history.logit_price.get(asset_id))
        for key in momentum:
            feature_ts[key] = logit_ts

    if feature_cfg.get("time_of_day"):
        features.update(time_of_day_features(t_decision_ms))

    symbol = decision.get("reference_symbol")
    if ref_return_horizons and symbol is None:
        raise ValueError("missing_reference_symbol")
    if symbol and ref_return_horizons:
        for horizon in ref_return_horizons:
            ret, ret_ts = _ref_return(ref_prices_state, symbol, t_decision_ms, horizon)
            features[f"ref_ret_{horizon}s"] = ret
            feature_ts[f"ref_ret_{horizon}s"] = ret_ts

    label, label_time = _label_for_decision(
        decision,
        label_cfg,
        ref_prices,
        market_series_full,
        t_decision_ms,
    )

    return DecisionRow(
        t_decision_ms=t_decision_ms,
        t_decision_mono=decision.get("t_decision_mono"),
        asset_id=asset_id,
        condition_id=decision.get("condition_id"),
        outcome=decision.get("outcome"),
        market_slug=decision.get("market_slug"),
        reference_symbol=symbol,
        features=features,
        feature_timestamps=feature_ts,
        feature_asof_ts_ms=None,
        feature_asof_source=None,
        feature_asof_detail=None,
        label=label,
        label_time_ms=label_time,
        p_market_buy=p_market_buy,
        p_market_sell=p_market_sell,
        depth_buy=depth_buy,
        depth_sell=depth_sell,
        slippage_buy=slippage_buy,
        slippage_sell=slippage_sell,
        book_spread_bps=book_spread_bps,
        best_bid_size=best_bid_size,
        best_ask_size=best_ask_size,
        depth_within_ticks_bid=depth_within_ticks_bid,
        depth_within_ticks_ask=depth_within_ticks_ask,
        depth_within_ticks_n=depth_within_ticks_n_value,
        depth_at_notional_bid=depth_at_notional_bid,
        depth_at_notional_ask=depth_at_notional_ask,
        depth_at_notional_target=depth_at_notional_target_value,
        depth_units=str(depth_units),
        imbalance_l1=imbalance_l1,
        imbalance_depth=imbalance_depth,
        diff_bps=None,
        alpha_basis=None,
        pstar_disagreement_extreme=False,
        belief_lag_sec=None,
        belief_lag_corr=None,
        alpha_lag=1.0,
        alpha_vov=1.0,
        sigma_t=None,
        sigma_prev=None,
    )


def _update_history_from_decision(history: FeatureHistory, row: DecisionRow) -> None:
    logit_price = logit(row.p_market_buy) if row.p_market_buy is not None else None
    imbalance_l1 = row.imbalance_l1 if row.imbalance_l1 is not None else 0.0
    imbalance_depth = row.imbalance_depth if row.imbalance_depth is not None else 0.0
    history.update_market(row.asset_id, row.t_decision_ms, imbalance_l1, imbalance_depth, logit_price)


def _apply_belief_lag(row: DecisionRow, history: FeatureHistory) -> None:
    symbol = row.reference_symbol
    if symbol is None or symbol not in ALLOWED_BELIEF_SYMBOLS:
        row.alpha_lag = 1.0
        return
    if symbol == REFERENCE_BASE_SYMBOL:
        row.alpha_lag = 1.0
        return
    belief = belief_lag_metric(
        history,
        symbol,
        REFERENCE_BASE_SYMBOL,
        row.t_decision_ms,
        BELIEF_LAG_LAGS_SEC,
        BELIEF_LAG_WINDOW_SEC,
        BELIEF_LAG_MIN_CORR,
    )
    lag_sec = belief.get("belief_lag_sec")
    corr = belief.get("belief_lag_corr")
    row.belief_lag_sec = lag_sec
    row.belief_lag_corr = corr
    if lag_sec is None or corr is None or corr < BELIEF_LAG_MIN_CORR:
        row.alpha_lag = 1.0
        return
    tau_steps = float(lag_sec) / float(HMM_SAMPLE_SEC)
    row.alpha_lag = math.exp(-tau_steps / 12.0)


def _apply_vol_of_vol(row: DecisionRow, ref_state: ReferenceState) -> None:
    sigma = ref_state.hmm_vol.sigma
    sigma_prev = ref_state.hmm_vol.sigma_prev
    row.sigma_t = sigma
    row.sigma_prev = sigma_prev
    if sigma is None or sigma_prev is None or sigma <= 0 or sigma_prev <= 0:
        row.alpha_vov = 1.0
        return
    v_t = abs(math.log(sigma / sigma_prev))
    if v_t <= VOL_OF_VOL_THRESHOLD:
        row.alpha_vov = 1.0
        return
    row.alpha_vov = math.exp(-VOL_OF_VOL_DECAY * (v_t - VOL_OF_VOL_THRESHOLD))


def _label_for_decision(
    decision: Dict[str, Any],
    label_cfg: Dict[str, Any],
    ref_prices: Dict[str, Deque[Tuple[int, float]]],
    market_series_full: Dict[str, List[Tuple[int, float]]],
    t_decision_ms: int,
) -> Tuple[Optional[int], Optional[int]]:
    label_type = label_cfg.get("type")
    if label_type == "window":
        slug = decision.get("market_slug")
        if not slug:
            return None, None
        window = window_start_end_ms(str(slug), window_secs=int(label_cfg.get("window_secs") or 900))
        if window is None:
            return None, None
        start_ms, end_ms = window
        if t_decision_ms >= end_ms:
            raise ValueError("leakage_decision_after_label")
        symbol = decision.get("reference_symbol")
        if symbol is None:
            return None, None
        series = ref_prices.get(symbol) or deque()
        p_start = _price_at_or_after(series, start_ms)
        p_end = _price_at_or_after(series, end_ms)
        if p_start is None or p_end is None:
            return None, end_ms
        return (1 if p_end >= p_start else 0), end_ms
    if label_type == "micro":
        horizons = label_cfg.get("horizons_sec") or []
        if not horizons:
            return None, None
        horizon_sec = int(horizons[0])
        label_time = t_decision_ms + horizon_sec * 1000
        if label_time <= t_decision_ms:
            raise ValueError("leakage_micro_label")
        asset_id = decision.get("asset_id")
        if not asset_id:
            return None, None
        series = market_series_full.get(asset_id) or []
        now = _price_at_or_before(series, t_decision_ms)
        future = _price_at_or_after(series, label_time)
        if now is None or future is None:
            return None, label_time
        delta = future - now
        return (1 if delta > 0 else 0), label_time
    return None, None


def _check_feature_times(row: DecisionRow, inv: Dict[str, Any]) -> None:
    for key, ts in row.feature_timestamps.items():
        if ts is None:
            continue
        if ts >= row.t_decision_ms:
            inv["feature_time_violations"] += 1
            raise ValueError("feature_from_future")


def _build_matrix(
    rows: List[DecisionRow], feature_order: List[str], regime_enabled: bool
) -> Tuple[List[List[float]], List[int], List[str]]:
    X: List[List[float]] = []
    y: List[int] = []
    output_order = list(feature_order)
    for row in rows:
        if row.label is None:
            continue
        values: List[float] = []
        missing = False
        for key in feature_order:
            value = row.features.get(key)
            if value is None:
                missing = True
                break
            values.append(float(value))
        if missing:
            continue
        if regime_enabled:
            if not row.regime_pi:
                continue
            values = _condition_features(values, row.regime_pi)
            output_order = _conditioned_feature_order(feature_order, len(row.regime_pi))
        X.append(values)
        y.append(int(row.label))
    return X, y, output_order


def _condition_features(values: List[float], pi: List[float]) -> List[float]:
    conditioned: List[float] = []
    for k in range(len(pi)):
        for value in values:
            conditioned.append(pi[k] * value)
    return conditioned


def _conditioned_feature_order(feature_order: List[str], k: int) -> List[str]:
    output: List[str] = []
    for idx in range(k):
        for name in feature_order:
            output.append(f"{name}_regime{idx}")
    return output


def _feature_order(features: Dict[str, Optional[float]]) -> List[str]:
    return sorted(features.keys())


def _simulate_trades(
    rows: List[DecisionRow],
    p_hat: List[float],
    portfolio: PortfolioState,
    constraints: PortfolioConstraints,
    sizing: SizingConfig,
    fee_rate: float,
    c_trade_min: float,
    inv: Dict[str, Any],
    mode: str,
) -> Tuple[float, int, Dict[str, float], List[DecisionLog]]:
    pnl = 0.0
    trades = 0
    regime_pnl: Dict[str, float] = {}
    positions = portfolio.positions
    decision_logs: List[DecisionLog] = []

    for row, prob in zip(rows, p_hat):
        _resolve_positions(positions, portfolio, row.t_decision_ms)
        model_calibration_factor = 1.0
        regime_posterior_factor = max(row.regime_pi) if row.regime_pi else 1.0
        basis_disagreement_factor = row.alpha_basis if row.alpha_basis is not None else 1.0
        mapping_error_factor = 1.0
        confidence_raw = (
            model_calibration_factor
            * regime_posterior_factor
            * (row.alpha_lag if row.alpha_lag is not None else 1.0)
            * (row.alpha_vov if row.alpha_vov is not None else 1.0)
            * basis_disagreement_factor
            * mapping_error_factor
        )
        confidence_final = max(CONFIDENCE_FLOOR, min(1.0, confidence_raw))
        if mode == "null":
            decision_logs.append(
                DecisionLog(
                    t_decision_ms=row.t_decision_ms,
                    asset_id=row.asset_id,
                    side=None,
                    size=0.0,
                    entry_price=None,
                    p_hat=prob,
                    confidence=confidence_raw,
                    confidence_final=confidence_final,
                    trade_allowed=False,
                    model_calibration_factor=model_calibration_factor,
                    regime_posterior_factor=regime_posterior_factor,
                    basis_disagreement_factor=basis_disagreement_factor,
                    mapping_error_factor=mapping_error_factor,
                    alpha_lag=row.alpha_lag,
                    alpha_vov=row.alpha_vov,
                    belief_lag_sec=row.belief_lag_sec,
                    belief_lag_corr=row.belief_lag_corr,
                    sigma_t=row.sigma_t,
                    sigma_prev=row.sigma_prev,
                    diff_bps=row.diff_bps,
                    feature_asof_ts_ms=row.feature_asof_ts_ms,
                    feature_asof_source=row.feature_asof_source,
                    feature_asof_detail=row.feature_asof_detail,
                    regime_id=row.regime_id,
                    blockers=["NULL_MODE"],
                )
            )
            continue

        side, exec_price = _choose_side(prob, row.p_market_buy, row.p_market_sell, fee_rate)
        blockers: List[str] = []
        if side is None or exec_price is None:
            blockers.append("NO_EDGE")
            decision_logs.append(
                DecisionLog(
                    t_decision_ms=row.t_decision_ms,
                    asset_id=row.asset_id,
                    side=None,
                    size=0.0,
                    entry_price=None,
                    p_hat=prob,
                    confidence=confidence_raw,
                    confidence_final=confidence_final,
                    trade_allowed=False,
                    model_calibration_factor=model_calibration_factor,
                    regime_posterior_factor=regime_posterior_factor,
                    basis_disagreement_factor=basis_disagreement_factor,
                    mapping_error_factor=mapping_error_factor,
                    alpha_lag=row.alpha_lag,
                    alpha_vov=row.alpha_vov,
                    belief_lag_sec=row.belief_lag_sec,
                    belief_lag_corr=row.belief_lag_corr,
                    sigma_t=row.sigma_t,
                    sigma_prev=row.sigma_prev,
                    diff_bps=row.diff_bps,
                    feature_asof_ts_ms=row.feature_asof_ts_ms,
                    feature_asof_source=row.feature_asof_source,
                    feature_asof_detail=row.feature_asof_detail,
                    regime_id=row.regime_id,
                    blockers=blockers,
                )
            )
            continue

        trade_allowed = True
        if row.pstar_disagreement_extreme:
            blockers.append("pstar_disagreement_extreme")
            inv["pstar_disagreement_extreme"] = inv.get("pstar_disagreement_extreme", 0) + 1
            trade_allowed = False
        if confidence_final < c_trade_min:
            blockers.append("low_confidence")
            inv["low_confidence"] = inv.get("low_confidence", 0) + 1
            trade_allowed = False
        if not trade_allowed:
            decision_logs.append(
                DecisionLog(
                    t_decision_ms=row.t_decision_ms,
                    asset_id=row.asset_id,
                    side=side,
                    size=0.0,
                    entry_price=exec_price,
                    p_hat=prob,
                    confidence=confidence_raw,
                    confidence_final=confidence_final,
                    trade_allowed=False,
                    model_calibration_factor=model_calibration_factor,
                    regime_posterior_factor=regime_posterior_factor,
                    basis_disagreement_factor=basis_disagreement_factor,
                    mapping_error_factor=mapping_error_factor,
                    alpha_lag=row.alpha_lag,
                    alpha_vov=row.alpha_vov,
                    belief_lag_sec=row.belief_lag_sec,
                    belief_lag_corr=row.belief_lag_corr,
                    sigma_t=row.sigma_t,
                    sigma_prev=row.sigma_prev,
                    diff_bps=row.diff_bps,
                    feature_asof_ts_ms=row.feature_asof_ts_ms,
                    feature_asof_source=row.feature_asof_source,
                    feature_asof_detail=row.feature_asof_detail,
                    regime_id=row.regime_id,
                    blockers=blockers,
                )
            )
            continue

        max_size, blockers = _max_size(row, portfolio, constraints, exec_price, side)
        if blockers:
            decision_logs.append(
                DecisionLog(
                    t_decision_ms=row.t_decision_ms,
                    asset_id=row.asset_id,
                    side=side,
                    size=0.0,
                    entry_price=exec_price,
                    p_hat=prob,
                    confidence=confidence_raw,
                    confidence_final=confidence_final,
                    trade_allowed=False,
                    model_calibration_factor=model_calibration_factor,
                    regime_posterior_factor=regime_posterior_factor,
                    basis_disagreement_factor=basis_disagreement_factor,
                    mapping_error_factor=mapping_error_factor,
                    alpha_lag=row.alpha_lag,
                    alpha_vov=row.alpha_vov,
                    belief_lag_sec=row.belief_lag_sec,
                    belief_lag_corr=row.belief_lag_corr,
                    sigma_t=row.sigma_t,
                    sigma_prev=row.sigma_prev,
                    diff_bps=row.diff_bps,
                    feature_asof_ts_ms=row.feature_asof_ts_ms,
                    feature_asof_source=row.feature_asof_source,
                    feature_asof_detail=row.feature_asof_detail,
                    regime_id=row.regime_id,
                    blockers=blockers,
                )
            )
            continue

        size = size_from_kelly(
            portfolio.equity,
            prob,
            exec_price,
            side,
            sizing,
            max_size,
            confidence=confidence_final,
        )
        if size <= 0:
            blockers.append("KELLY_ZERO")
            decision_logs.append(
                DecisionLog(
                    t_decision_ms=row.t_decision_ms,
                    asset_id=row.asset_id,
                    side=side,
                    size=0.0,
                    entry_price=exec_price,
                    p_hat=prob,
                    confidence=confidence_raw,
                    confidence_final=confidence_final,
                    trade_allowed=False,
                    model_calibration_factor=model_calibration_factor,
                    regime_posterior_factor=regime_posterior_factor,
                    basis_disagreement_factor=basis_disagreement_factor,
                    mapping_error_factor=mapping_error_factor,
                    alpha_lag=row.alpha_lag,
                    alpha_vov=row.alpha_vov,
                    belief_lag_sec=row.belief_lag_sec,
                    belief_lag_corr=row.belief_lag_corr,
                    sigma_t=row.sigma_t,
                    sigma_prev=row.sigma_prev,
                    diff_bps=row.diff_bps,
                    feature_asof_ts_ms=row.feature_asof_ts_ms,
                    feature_asof_source=row.feature_asof_source,
                    feature_asof_detail=row.feature_asof_detail,
                    regime_id=row.regime_id,
                    blockers=blockers,
                )
            )
            continue

        position = Position(
            asset_id=row.asset_id,
            condition_id=row.condition_id,
            outcome=row.outcome,
            symbol=row.reference_symbol,
            side=side,
            size=size,
            entry_price=exec_price,
            entry_time_ms=row.t_decision_ms,
            resolution_time_ms=row.label_time_ms or row.t_decision_ms,
            label=row.label,
            regime_id=row.regime_id,
        )
        positions.append(position)
        trades += 1
        decision_logs.append(
            DecisionLog(
                t_decision_ms=row.t_decision_ms,
                asset_id=row.asset_id,
                side=side,
                size=size,
                entry_price=exec_price,
                p_hat=prob,
                confidence=confidence_raw,
                confidence_final=confidence_final,
                trade_allowed=True,
                model_calibration_factor=model_calibration_factor,
                regime_posterior_factor=regime_posterior_factor,
                basis_disagreement_factor=basis_disagreement_factor,
                mapping_error_factor=mapping_error_factor,
                alpha_lag=row.alpha_lag,
                alpha_vov=row.alpha_vov,
                belief_lag_sec=row.belief_lag_sec,
                belief_lag_corr=row.belief_lag_corr,
                sigma_t=row.sigma_t,
                sigma_prev=row.sigma_prev,
                diff_bps=row.diff_bps,
                feature_asof_ts_ms=row.feature_asof_ts_ms,
                feature_asof_source=row.feature_asof_source,
                feature_asof_detail=row.feature_asof_detail,
                regime_id=row.regime_id,
                blockers=[],
            )
        )

    _resolve_positions(positions, portfolio, float("inf"), regime_pnl)
    pnl = portfolio.equity - portfolio.initial_equity
    return pnl, trades, regime_pnl, decision_logs


def _resolve_positions(
    positions: List[Position],
    portfolio: PortfolioState,
    t_ms: int,
    regime_pnl: Optional[Dict[str, float]] = None,
) -> None:
    remaining: List[Position] = []
    for pos in positions:
        if pos.resolution_time_ms <= t_ms:
            pnl = _pnl_for_position(pos)
            portfolio.update_equity(pos.resolution_time_ms, pnl)
            if regime_pnl is not None:
                key = "unknown" if pos.regime_id is None else str(pos.regime_id)
                regime_pnl[key] = regime_pnl.get(key, 0.0) + pnl
        else:
            remaining.append(pos)
    positions[:] = remaining


def _pnl_for_position(pos: Position) -> float:
    if pos.label is None:
        return 0.0
    payout = 1.0 if pos.label == 1 else 0.0
    if pos.side == "buy":
        return (payout - pos.entry_price) * pos.size
    return (pos.entry_price - payout) * pos.size


def _choose_side(
    p_hat: float, buy_price: Optional[float], sell_price: Optional[float], fee_rate: float
) -> Tuple[Optional[str], Optional[float]]:
    if buy_price is None or sell_price is None:
        return None, None
    buy_entry = apply_fee(buy_price, fee_rate, "buy")
    sell_entry = apply_fee(sell_price, fee_rate, "sell")
    edge_buy = p_hat - buy_entry
    edge_sell = sell_entry - p_hat
    if edge_buy <= 0 and edge_sell <= 0:
        return None, None
    if edge_buy >= edge_sell:
        return "buy", buy_entry
    return "sell", sell_entry


def _max_size(
    row: DecisionRow,
    portfolio: PortfolioState,
    constraints: PortfolioConstraints,
    price: float,
    side: str,
) -> Tuple[float, List[str]]:
    blockers: List[str] = []
    equity = max(portfolio.equity, 1.0)
    gross = 0.0
    net = 0.0
    for pos in portfolio.positions:
        notional = pos.size * price
        gross += abs(notional)
        net += notional if pos.side == "buy" else -notional
    gross_ratio = gross / equity
    net_ratio = net / equity
    if gross_ratio >= constraints.max_gross_delta:
        blockers.append("MAX_GROSS_DELTA")
    if abs(net_ratio) >= constraints.max_net_delta:
        blockers.append("MAX_NET_DELTA")
    if blockers:
        return 0.0, blockers

    remaining_gross = max(0.0, constraints.max_gross_delta * equity - gross)
    if side == "buy":
        remaining_net = max(0.0, constraints.max_net_delta * equity - net)
    else:
        remaining_net = max(0.0, constraints.max_net_delta * equity + net)

    max_size = float("inf")
    max_size = min(max_size, constraints.max_position_fraction * equity / price)
    max_size = min(max_size, constraints.max_asset_fraction * equity / price)
    max_size = min(max_size, remaining_gross / price)
    max_size = min(max_size, remaining_net / price)

    depth = row.depth_buy if side == "buy" else row.depth_sell
    if depth is not None and constraints.min_liquidity_ratio > 0:
        max_size = min(max_size, depth / constraints.min_liquidity_ratio)

    dd = portfolio.drawdown_pct(constraints.drawdown_lookback_days, row.t_decision_ms)
    if dd is not None and dd >= constraints.max_drawdown_pct:
        blockers.append("DRAWDOWN_STOP")
        return 0.0, blockers
    if len(portfolio.positions) >= constraints.max_open_positions:
        blockers.append("MAX_OPEN_POSITIONS")
        return 0.0, blockers

    return max_size, blockers


def _metrics(p: List[float], y: List[int]) -> Dict[str, float]:
    if not p or not y:
        return {}
    eps = 1e-8
    total = len(y)
    brier = sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / total
    logloss = 0.0
    for pi, yi in zip(p, y):
        pi = min(max(pi, eps), 1.0 - eps)
        logloss += -(yi * math.log(pi) + (1 - yi) * math.log(1 - pi))
    logloss /= total
    acc = sum((pi >= 0.5) == (yi == 1) for pi, yi in zip(p, y)) / total
    return {"brier": brier, "logloss": logloss, "accuracy": acc}


def _evaluate_acceptance(results: Dict[str, Any], acceptance: Dict[str, Any]) -> bool:
    if not acceptance:
        return True
    test_metrics = results.get("test_metrics") or {}
    for key, value in acceptance.items():
        if key == "max_brier":
            if test_metrics.get("brier") is None or test_metrics["brier"] > float(value):
                return False
        if key == "min_accuracy":
            if test_metrics.get("accuracy") is None or test_metrics["accuracy"] < float(value):
                return False
        if key == "max_logloss":
            if test_metrics.get("logloss") is None or test_metrics["logloss"] > float(value):
                return False
    return True


def _summarize_decisions(decisions: List[DecisionLog], c_trade_min: float) -> Dict[str, Any]:
    feature_sources: Dict[str, int] = {}
    confidence_buckets: Dict[str, int] = {}
    trade_rate_by_regime: Dict[str, Dict[str, int]] = {}
    for row in decisions:
        source = row.feature_asof_source or "unknown"
        feature_sources[source] = feature_sources.get(source, 0) + 1

        conf = row.confidence_final
        if conf is None:
            bucket = "unknown"
        elif conf < c_trade_min:
            bucket = f"<{c_trade_min}"
        elif conf < 0.5:
            bucket = f"{c_trade_min}-0.5"
        elif conf < 0.7:
            bucket = "0.5-0.7"
        elif conf < 0.9:
            bucket = "0.7-0.9"
        else:
            bucket = "0.9-1.0"
        confidence_buckets[bucket] = confidence_buckets.get(bucket, 0) + 1

        regime_key = "unknown" if row.regime_id is None else str(row.regime_id)
        entry = trade_rate_by_regime.get(regime_key)
        if entry is None:
            entry = {"total": 0, "allowed": 0}
            trade_rate_by_regime[regime_key] = entry
        entry["total"] += 1
        if row.trade_allowed:
            entry["allowed"] += 1

    trade_rates: Dict[str, float] = {}
    for key, counts in trade_rate_by_regime.items():
        total = counts.get("total", 0)
        allowed = counts.get("allowed", 0)
        trade_rates[key] = 0.0 if total == 0 else allowed / total

    return {
        "feature_asof_sources": feature_sources,
        "confidence_buckets": confidence_buckets,
        "trade_rate_by_regime": trade_rates,
    }


def _depth_telemetry_stats(rows: List[DecisionRow]) -> Dict[str, Any]:
    imb_l1 = []
    imb_depth = []
    depth_bid = []
    depth_ask = []
    notional_bid = []
    notional_ask = []
    for row in rows:
        if row.imbalance_l1 is not None and row.imbalance_depth is not None:
            imb_l1.append(row.imbalance_l1)
            imb_depth.append(row.imbalance_depth)
        if row.depth_within_ticks_bid is not None:
            depth_bid.append(row.depth_within_ticks_bid)
        if row.depth_within_ticks_ask is not None:
            depth_ask.append(row.depth_within_ticks_ask)
        if row.depth_at_notional_bid is not None:
            notional_bid.append(row.depth_at_notional_bid)
        if row.depth_at_notional_ask is not None:
            notional_ask.append(row.depth_at_notional_ask)

    return {
        "imbalance_corr": _corr(imb_l1, imb_depth),
        "depth_within_ticks_bid": _summary_stats(depth_bid),
        "depth_within_ticks_ask": _summary_stats(depth_ask),
        "depth_at_notional_bid": _summary_stats(notional_bid),
        "depth_at_notional_ask": _summary_stats(notional_ask),
    }


def _pstar_stats(rows: List[DecisionRow]) -> Dict[str, Any]:
    diff_values = []
    alpha_values = []
    extreme = 0
    for row in rows:
        if row.diff_bps is not None:
            diff_values.append(row.diff_bps)
        if row.alpha_basis is not None:
            alpha_values.append(row.alpha_basis)
        if row.pstar_disagreement_extreme:
            extreme += 1
    return {
        "diff_bps": _summary_stats(diff_values),
        "alpha_basis": _summary_stats(alpha_values),
        "extreme_count": extreme,
    }


def _summary_stats(values: List[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"count": 0}
    values = sorted(values)
    return {
        "count": len(values),
        "min": values[0],
        "p50": _percentile(values, 0.5),
        "p90": _percentile(values, 0.9),
        "p95": _percentile(values, 0.95),
    }


def _corr(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    idx = int(round((len(values) - 1) * p))
    return values[idx]


def _render_report(results: Dict[str, Any]) -> str:
    lines = []
    status = "PASS" if results.get("pass", True) else "FAIL"
    lines.append(f"{status}: {results.get('hypothesis_id')} {results.get('name')}")
    lines.append("")
    lines.append("Summary")
    lines.append(f"- pnl: {results.get('pnl')}")
    lines.append(f"- trades: {results.get('trades')}")
    lines.append(f"- test_metrics: {json.dumps(results.get('test_metrics'), sort_keys=True)}")
    lines.append("")
    lines.append("Invariants")
    inv = results.get("invariants") or {}
    lines.append(f"- feature_time_violations: {inv.get('feature_time_violations')}")
    lines.append(f"- feature_from_future: {inv.get('feature_from_future')}")
    lines.append(f"- execution_not_vwap: {inv.get('execution_not_vwap')}")
    lines.append(f"- fee_double_count: {inv.get('fee_double_count')}")
    lines.append(f"- low_confidence_blocks: {inv.get('low_confidence', 0)}")
    lines.append(f"- pstar_disagreement_extreme: {inv.get('pstar_disagreement_extreme', 0)}")
    lines.append("")

    summary = results.get("decision_summary") or {}
    lines.append("Feature As-Of Sources")
    source_counts = summary.get("feature_asof_sources") or {}
    for key in sorted(source_counts.keys()):
        lines.append(f"- {key}: {source_counts[key]}")
    lines.append("")

    lines.append("Confidence Buckets")
    buckets = summary.get("confidence_buckets") or {}
    for key in sorted(buckets.keys()):
        lines.append(f"- {key}: {buckets[key]}")
    lines.append("")

    lines.append("Trade Rate by Regime")
    regime_rates = summary.get("trade_rate_by_regime") or {}
    for key in sorted(regime_rates.keys()):
        lines.append(f"- {key}: {regime_rates[key]}")
    lines.append("")

    depth_stats = results.get("depth_telemetry") or {}
    if depth_stats:
        lines.append("Depth Telemetry")
        lines.append(f"- imbalance_corr: {depth_stats.get('imbalance_corr')}")
        lines.append(f"- depth_within_ticks_bid: {depth_stats.get('depth_within_ticks_bid')}")
        lines.append(f"- depth_within_ticks_ask: {depth_stats.get('depth_within_ticks_ask')}")
        lines.append(f"- depth_at_notional_bid: {depth_stats.get('depth_at_notional_bid')}")
        lines.append(f"- depth_at_notional_ask: {depth_stats.get('depth_at_notional_ask')}")
        lines.append("")

    pstar_stats = results.get("pstar_stats") or {}
    if pstar_stats:
        lines.append("P* Disagreement")
        lines.append(f"- diff_bps: {pstar_stats.get('diff_bps')}")
        lines.append(f"- alpha_basis: {pstar_stats.get('alpha_basis')}")
        lines.append(f"- extreme_count: {pstar_stats.get('extreme_count')}")
    return "\n".join(lines)


def _validate_label(label: Optional[Dict[str, Any]]) -> None:
    if not label:
        raise ValueError("missing_label_definition")
    label_type = label.get("type")
    if label_type not in {"micro", "window"}:
        raise ValueError("label_type_must_be_micro_or_window")


def _validate_locked_spec(spec: Dict[str, Any]) -> None:
    inputs = spec.get("inputs") or {}
    market_paths = inputs.get("market_paths") or []
    if market_paths:
        raise ValueError("market_paths_not_allowed")

    regime_cfg = spec.get("regime") or {}
    if not regime_cfg.get("enabled", False):
        raise ValueError("regime_must_be_enabled")
    hmm_path = regime_cfg.get("hmm_path")
    if hmm_path and hmm_path != "models/regimes/hmm_reference.json":
        raise ValueError("hmm_path_must_be_default")
    if "obs_return_sec" in regime_cfg and int(regime_cfg["obs_return_sec"]) != HMM_OBS_RETURN_SEC:
        raise ValueError("obs_return_sec_locked")
    if "vol_half_life_sec" in regime_cfg and float(regime_cfg["vol_half_life_sec"]) != HMM_VOL_HALF_LIFE_SEC:
        raise ValueError("vol_half_life_sec_locked")

    feature_cfg = spec.get("features") or {}
    if "volatility" in feature_cfg or "vol_regime_window_sec" in feature_cfg:
        raise ValueError("volatility_features_not_allowed")
    belief_cfg = feature_cfg.get("belief_lag") or {}
    if belief_cfg:
        if "window_sec" in belief_cfg and int(belief_cfg["window_sec"]) != BELIEF_LAG_WINDOW_SEC:
            raise ValueError("belief_window_locked")
        if "min_corr" in belief_cfg and float(belief_cfg["min_corr"]) != BELIEF_LAG_MIN_CORR:
            raise ValueError("belief_min_corr_locked")
        if "lags_sec" in belief_cfg:
            expected = BELIEF_LAG_LAGS_SEC
            actual = [int(x) for x in belief_cfg["lags_sec"]]
            if actual != expected:
                raise ValueError("belief_lags_locked")

    required_keys = [
        "pstar_diff_bps_soft",
        "pstar_diff_bps_hard",
        "pstar_diff_bps_decay_k",
        "c_trade_min",
        "depth_within_ticks_n",
        "depth_at_notional_target",
        "shock_horizon_sec",
        "shock_quantile_q",
        "shock_min_count",
    ]
    for key in required_keys:
        if key not in spec:
            raise ValueError(f"missing_required_key:{key}")

    sizing_cfg = spec.get("sizing") or {}
    kelly_max = float(sizing_cfg.get("kelly_fraction_max") or 0.25)
    if kelly_max > 0.25:
        raise ValueError("kelly_fraction_max_exceeds_limit")


def _price_asof(series: Deque[Tuple[int, float]], t_ms: int) -> Optional[float]:
    latest = None
    for ts, value in series:
        if ts <= t_ms:
            latest = value
        else:
            break
    return latest


def _price_at_or_before(series: List[Tuple[int, float]], t_ms: int) -> Optional[float]:
    latest = None
    for ts, value in series:
        if ts <= t_ms:
            latest = value
        else:
            break
    return latest


def _price_at_or_after(series: Deque[Tuple[int, float]], t_ms: int) -> Optional[float]:
    for ts, value in series:
        if ts >= t_ms:
            return value
    return None


def _ref_return(
    ref_prices: Dict[str, Deque[Tuple[int, float]]],
    symbol: str,
    t_ms: int,
    horizon_sec: int,
) -> Tuple[Optional[float], Optional[int]]:
    series = ref_prices.get(symbol) or deque()
    t_now = t_ms - 1
    t_prev = t_ms - horizon_sec * 1000
    price_now = _price_asof(series, t_now)
    price_prev = _price_asof(series, t_prev)
    if price_now is None or price_prev is None or price_prev <= 0:
        return None, None
    r = math.log(price_now / price_prev)
    return r, t_now


def _latest_history_ts(rows: Optional[Deque[Tuple[int, float]]]) -> Optional[int]:
    if not rows:
        return None
    return rows[-1][0]


def _imbalance(bid_size: Optional[float], ask_size: Optional[float]) -> Optional[float]:
    if bid_size is None or ask_size is None:
        return None
    denom = bid_size + ask_size
    if denom <= 0:
        return 0.0
    return (bid_size - ask_size) / denom


def _to_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _match_selection_decision(decision: Dict[str, Any], selection: Dict[str, Any]) -> bool:
    condition_ids = set(selection.get("condition_ids") or [])
    token_ids = set(selection.get("token_ids") or [])
    if condition_ids and decision.get("condition_id") not in condition_ids:
        return False
    if token_ids and decision.get("asset_id") not in token_ids:
        return False
    return True


def _regime_id(pi: Optional[List[float]]) -> Optional[int]:
    if not pi:
        return None
    return int(max(range(len(pi)), key=lambda idx: pi[idx]))


def _apply_basis_disagreement(
    row: DecisionRow,
    ref_state: ReferenceState,
    soft_bps: float,
    hard_bps: float,
    decay_k: float,
) -> None:
    symbol = row.reference_symbol
    if symbol is None:
        row.alpha_basis = 1.0
        return
    diff_bps = ref_state.diff_bps.get(symbol)
    row.diff_bps = diff_bps
    if diff_bps is None:
        row.alpha_basis = 1.0
        return
    if diff_bps > hard_bps:
        row.alpha_basis = 0.0
        row.pstar_disagreement_extreme = True
        return
    row.alpha_basis = _disagreement_multiplier(diff_bps, soft_bps, hard_bps, decay_k)


def _set_feature_asof(row: DecisionRow, history: FeatureHistory, ref_state: ReferenceState) -> None:
    sources: List[str] = []
    timestamps: List[int] = []
    for key, ts in row.feature_timestamps.items():
        if ts is None:
            continue
        timestamps.append(ts)
        if key.startswith("ref_ret_"):
            sources.append("ref_bars_5m")
        elif key in {"spread_bps", "depth_at_qty", "slippage_bps"} or key.startswith("imbalance"):
            sources.append("poly_book")
        elif key.startswith("logit_ret_") or key.startswith("mean_revert_"):
            sources.append("poly_book")
    if row.belief_lag_sec is not None or row.belief_lag_corr is not None:
        symbol = row.reference_symbol
        if symbol:
            ref_ts = _latest_history_ts(history.ref_returns.get(symbol))
            if ref_ts is not None:
                timestamps.append(ref_ts)
                sources.append("ref_bars_5m")
    if row.sigma_t is not None or row.sigma_prev is not None:
        bar_ts = ref_state.hmm_vol.last_bar_ts
        if bar_ts is not None:
            timestamps.append(bar_ts)
            sources.append("ref_bars_5m")
    if row.diff_bps is not None:
        symbol = row.reference_symbol
        if symbol:
            diff_ts = ref_state.diff_ts.get(symbol)
            if diff_ts is not None:
                timestamps.append(diff_ts)
                sources.append("ref_ticks")
    if not timestamps and row.features:
        row.feature_asof_ts_ms = row.t_decision_ms - 1
        row.feature_asof_source = "clock"
        row.feature_asof_detail = "time_of_day"
        return
    if not timestamps:
        row.feature_asof_ts_ms = None
        row.feature_asof_source = None
        row.feature_asof_detail = None
        return
    max_ts = max(timestamps)
    row.feature_asof_ts_ms = max_ts
    unique_sources = sorted(set(sources))
    if not unique_sources:
        row.feature_asof_source = "unknown"
    elif len(unique_sources) == 1:
        row.feature_asof_source = unique_sources[0]
    else:
        row.feature_asof_source = "hybrid"
    row.feature_asof_detail = f"sources={','.join(unique_sources)}"


def _enforce_feature_asof(row: DecisionRow, inv: Dict[str, Any]) -> None:
    if row.feature_asof_ts_ms is None:
        raise ValueError("missing_feature_asof")
    if row.feature_asof_ts_ms >= row.t_decision_ms:
        inv["feature_from_future"] += 1
        raise ValueError("feature_from_future")


def _disagreement_multiplier(diff_bps: float, soft: float, hard: float, decay_k: float) -> float:
    if hard <= soft:
        return 1.0 if diff_bps <= soft else 0.0
    if diff_bps <= soft:
        return 1.0
    if diff_bps >= hard:
        return 0.0
    x = (diff_bps - soft) / (hard - soft)
    return float(math.exp(-decay_k * x))


def _diff_bps(spot_value: float, perp_value: float) -> Optional[float]:
    mid = (spot_value + perp_value) / 2.0
    if mid <= 0:
        return None
    return abs(spot_value - perp_value) / mid * 10000.0


def _require_float(spec: Dict[str, Any], key: str) -> float:
    if key not in spec:
        raise ValueError(f"missing_required_key:{key}")
    value = spec.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid_required_key:{key}")


def _require_int(spec: Dict[str, Any], key: str) -> int:
    if key not in spec:
        raise ValueError(f"missing_required_key:{key}")
    value = spec.get(key)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid_required_key:{key}")


def _write_decision_logs(path: Path, rows: List[DecisionLog]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.__dict__, sort_keys=True, separators=(",", ":")) + "\n")
