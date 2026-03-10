from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class AutoDiscoverSpec:
    symbol: str
    horizon: str
    mode: str


@dataclass(frozen=True)
class MarketConfig:
    name: str
    condition_id: Optional[str]
    token_ids: List[str]
    slug_prefix: Optional[str]
    reference_symbol: str
    min_tick: float
    min_size: float
    max_price: float
    min_price: float
    discovery_backend: Optional[str] = None
    selection_regex: Optional[str] = None
    allow_unknown_symbol: bool = False
    auto_discover: Optional[AutoDiscoverSpec] = None


@dataclass(frozen=True)
class Settings:
    polymarket_api_key: str
    polymarket_secret: str
    polymarket_passphrase: str
    log_dir: str
    track_markets_yaml: str
    user_ws_enabled: bool
    market_ws_enabled: bool
    max_book_staleness_ms: int
    ws_reconnect_base_ms: int
    ws_reconnect_max_ms: int
    max_spread_bps: float
    max_slippage_bps: float
    dry_run_interval_secs: float
    dry_run_size: float
    sim_balance_usd: float
    sim_balance_tokens_default: float
    status_json_enabled: bool
    fee_rate_bps: float
    fee_mode: str
    auto_discover: bool
    depth_within_ticks_n: int
    depth_at_notional_target: float
    reference_enabled: bool
    reference_source: str
    reference_poll_secs: float
    reference_staleness_ms: int
    reference_lag_guard_ms: int
    reference_disagreement_bps: float
    reference_disagreement_bps_soft: float
    reference_disagreement_bps_hard: float
    reference_disagreement_decay_k: float
    reference_min_confidence: float
    reference_allow_partial: bool
    reference_partial_confidence: float
    hl_vol_sec: float
    vol_pctl_window_sec: int
    sigma10s_floor: float
    edge_min: float
    edge_exit: float
    edge_stop: float
    z_mom_min: float
    t_min_secs: float
    hold_max_secs: float
    vol_pct_hi: float
    edge_min_mult_hivol: float
    tox_max: float
    hedge_min: float
    hedge_max: float
    hedge_required_vol_pct: float
    pf_bias: float
    pf_w_mom: float
    pf_w_revert: float
    pf_z_clip: float
    pf_vol_dampen_enabled: bool
    pf_vol_floor: float
    onchain_ingest_enabled: bool
    polygon_rpc_http: str
    polygon_rpc_ws: str
    onchain_poll_secs: float
    onchain_heartbeat_secs: float
    onchain_max_block_range: int
    onchain_window_secs: float
    onchain_whales_path: str
    onchain_use_ws: bool
    onchain_poll_reconcile_secs: float
    onchain_ws_loop_sleep_secs: float
    onchain_dedupe_lru_size: int
    onchain_recreate_filter_after_secs: float
    onchain_log_level: str
    trading_mode: str
    polymarket_private_key: str
    post_only_enabled: bool
    quote_interval_ms: int
    stats_interval_ms: int
    rebalance_timeout_ms: int
    emergency_taker_enabled: bool
    emergency_taker_after_ms: int
    pstar_max_age_ms: int
    pstar_freeze_disagree_bps: float
    book_stale_after_ms: int
    book_down_after_ms: int
    signal_age_max_ms: int
    ack_p95_max_ms: float
    ws_lag_max_ms: float
    ctf_split_merge_enabled: bool
    runtime_db_path: str


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    edge_min = float(os.getenv("EDGE_MIN", "0.015"))
    ref_disagree_bps = float(os.getenv("REFERENCE_DISAGREE_BPS", "50"))
    ref_disagree_soft = float(os.getenv("REFERENCE_DISAGREE_SOFT_BPS", str(ref_disagree_bps)))
    ref_disagree_hard = float(os.getenv("REFERENCE_DISAGREE_HARD_BPS", str(ref_disagree_bps)))
    ref_disagree_decay = float(os.getenv("REFERENCE_DISAGREE_DECAY_K", "1.0"))
    poll_reconcile_env = os.getenv("ONCHAIN_POLL_RECONCILE_SECS")
    if poll_reconcile_env is None:
        poll_reconcile_env = os.getenv("ONCHAIN_POLL_SECS", "30")
    poll_reconcile_secs = float(poll_reconcile_env)
    heartbeat_secs = max(1.0, float(os.getenv("ONCHAIN_HEARTBEAT_SECS", "2.0")))
    return Settings(
        polymarket_api_key=os.getenv("POLYMARKET_API_KEY", ""),
        polymarket_secret=os.getenv("POLYMARKET_SECRET", ""),
        polymarket_passphrase=os.getenv("POLYMARKET_PASSPHRASE", ""),
        log_dir=os.getenv("LOG_DIR", "./logs"),
        track_markets_yaml=os.getenv("TRACK_MARKETS_YAML", "./config/markets.yaml"),
        user_ws_enabled=_parse_bool(os.getenv("USER_WS_ENABLED"), False),
        market_ws_enabled=_parse_bool(os.getenv("MARKET_WS_ENABLED"), True),
        max_book_staleness_ms=int(os.getenv("MAX_BOOK_STALENESS_MS", "2000")),
        ws_reconnect_base_ms=int(os.getenv("WS_RECONNECT_BASE_MS", "250")),
        ws_reconnect_max_ms=int(os.getenv("WS_RECONNECT_MAX_MS", "10000")),
        max_spread_bps=float(os.getenv("MAX_SPREAD_BPS", "500.0")),
        max_slippage_bps=float(os.getenv("MAX_SLIPPAGE_BPS", "200.0")),
        dry_run_interval_secs=float(os.getenv("DRY_RUN_INTERVAL_SECS", "5")),
        dry_run_size=float(os.getenv("DRY_RUN_SIZE", "1")),
        sim_balance_usd=float(os.getenv("SIM_BALANCE_USD", "1000")),
        sim_balance_tokens_default=float(os.getenv("SIM_BALANCE_TOKENS_DEFAULT", "100")),
        status_json_enabled=_parse_bool(os.getenv("STATUS_JSON_ENABLED"), False),
        fee_rate_bps=float(os.getenv("FEE_RATE_BPS", "25")),
        fee_mode=os.getenv("FEE_MODE", "taker"),
        auto_discover=_parse_bool(os.getenv("AUTO_DISCOVER"), False),
        depth_within_ticks_n=int(os.getenv("DEPTH_WITHIN_TICKS_N", "5")),
        depth_at_notional_target=float(os.getenv("DEPTH_AT_NOTIONAL_TARGET", "10")),
        reference_enabled=_parse_bool(os.getenv("REFERENCE_ENABLED"), False),
        reference_source=os.getenv("REFERENCE_SOURCE", "poll_coinbase,poll_binance_perp"),
        reference_poll_secs=float(os.getenv("REFERENCE_POLL_SECS", "1")),
        reference_staleness_ms=int(os.getenv("REFERENCE_STALENESS_MS", "5000")),
        reference_lag_guard_ms=int(os.getenv("REFERENCE_LAG_GUARD_MS", "0")),
        reference_disagreement_bps=ref_disagree_bps,
        reference_disagreement_bps_soft=ref_disagree_soft,
        reference_disagreement_bps_hard=ref_disagree_hard,
        reference_disagreement_decay_k=ref_disagree_decay,
        reference_min_confidence=float(os.getenv("REFERENCE_MIN_CONFIDENCE", "0.5")),
        reference_allow_partial=_parse_bool(os.getenv("REFERENCE_ALLOW_PARTIAL"), True),
        reference_partial_confidence=float(os.getenv("REFERENCE_PARTIAL_CONFIDENCE", "0.6")),
        hl_vol_sec=float(os.getenv("HL_VOL_SEC", "120")),
        vol_pctl_window_sec=int(os.getenv("VOL_PCTL_WINDOW_SEC", "21600")),
        sigma10s_floor=float(os.getenv("SIGMA10S_FLOOR", "1e-5")),
        # Entry/exit defaults (probability points, seconds, percentile thresholds)
        edge_min=edge_min,
        edge_exit=float(os.getenv("EDGE_EXIT", str(0.25 * edge_min))),
        edge_stop=float(os.getenv("EDGE_STOP", str(0.5 * edge_min))),
        z_mom_min=float(os.getenv("Z_MOM_MIN", "1.0")),
        t_min_secs=float(os.getenv("T_MIN_SECS", "90")),
        hold_max_secs=float(os.getenv("HOLD_MAX_SECS", "480")),
        vol_pct_hi=float(os.getenv("VOL_PCT_HI", "95")),
        edge_min_mult_hivol=float(os.getenv("EDGE_MIN_MULT_HIVOL", "1.5")),
        tox_max=float(os.getenv("TOX_MAX", "0.0008")),
        hedge_min=float(os.getenv("H_MIN", "0.0")),
        hedge_max=float(os.getenv("H_MAX", "1.0")),
        hedge_required_vol_pct=float(os.getenv("HEDGE_REQUIRED_VOL_PCT", "95")),
        # p_fair baseline defaults
        pf_bias=float(os.getenv("PF_BIAS", "0.0")),
        pf_w_mom=float(os.getenv("PF_W_MOM", "0.35")),
        pf_w_revert=float(os.getenv("PF_W_REVERT", "0.15")),
        pf_z_clip=float(os.getenv("PF_Z_CLIP", "4.0")),
        pf_vol_dampen_enabled=_parse_bool(os.getenv("PF_VOL_DAMPEN_ENABLED"), True),
        pf_vol_floor=float(os.getenv("PF_VOL_FLOOR", "0.6")),
        onchain_ingest_enabled=_parse_bool(os.getenv("ONCHAIN_INGEST_ENABLED"), False),
        polygon_rpc_http=os.getenv("POLYGON_RPC_HTTP", ""),
        polygon_rpc_ws=os.getenv("POLYGON_RPC_WS", ""),
        onchain_poll_secs=float(os.getenv("ONCHAIN_POLL_SECS", "0.2")),
        onchain_heartbeat_secs=heartbeat_secs,
        onchain_max_block_range=int(os.getenv("ONCHAIN_MAX_BLOCK_RANGE", "500")),
        onchain_window_secs=float(os.getenv("ONCHAIN_WINDOW_SECS", "60.0")),
        onchain_whales_path=os.getenv("ONCHAIN_WHALES_PATH", "config/whales.json"),
        onchain_use_ws=_parse_bool(os.getenv("ONCHAIN_USE_WS"), True),
        onchain_poll_reconcile_secs=poll_reconcile_secs,
        onchain_ws_loop_sleep_secs=float(os.getenv("ONCHAIN_WS_LOOP_SLEEP_SECS", "0.2")),
        onchain_dedupe_lru_size=int(os.getenv("ONCHAIN_DEDUPE_LRU_SIZE", "5000")),
        onchain_recreate_filter_after_secs=float(os.getenv("ONCHAIN_RECREATE_FILTER_AFTER_SECS", "30")),
        onchain_log_level=os.getenv("ONCHAIN_LOG_LEVEL", "INFO"),
        trading_mode=os.getenv("TRADING_MODE", "OBSERVE").upper(),
        polymarket_private_key=os.getenv("POLYMARKET_PRIVATE_KEY", ""),
        post_only_enabled=_parse_bool(os.getenv("POST_ONLY_ENABLED"), True),
        quote_interval_ms=int(os.getenv("QUOTE_INTERVAL_MS", "1000")),
        stats_interval_ms=int(os.getenv("STATS_INTERVAL_MS", "5000")),
        rebalance_timeout_ms=int(os.getenv("REBALANCE_TIMEOUT_MS", "5000")),
        emergency_taker_enabled=_parse_bool(os.getenv("EMERGENCY_TAKER_ENABLED"), True),
        emergency_taker_after_ms=int(os.getenv("EMERGENCY_TAKER_AFTER_MS", "1000")),
        pstar_max_age_ms=int(os.getenv("PSTAR_MAX_AGE_MS", os.getenv("REFERENCE_STALENESS_MS", "3000"))),
        pstar_freeze_disagree_bps=float(os.getenv("PSTAR_FREEZE_DISAGREE_BPS", "50")),
        book_stale_after_ms=int(os.getenv("BOOK_STALE_AFTER_MS", "30000")),
        book_down_after_ms=int(os.getenv("BOOK_DOWN_AFTER_MS", "120000")),
        signal_age_max_ms=int(os.getenv("SIGNAL_AGE_MAX_MS", "1200")),
        ack_p95_max_ms=float(os.getenv("ACK_P95_MAX_MS", "400")),
        ws_lag_max_ms=float(os.getenv("WS_LAG_MAX_MS", "1000")),
        ctf_split_merge_enabled=_parse_bool(os.getenv("CTF_SPLIT_MERGE_ENABLED"), False),
        runtime_db_path=os.getenv("RUNTIME_DB_PATH", "./runtime.db"),
    )


def _load_yaml_or_json(path: Path) -> Dict:
    raw = path.read_text()
    try:
        import yaml  # type: ignore

        return yaml.safe_load(raw) or {}
    except ModuleNotFoundError:
        pass
    except Exception:
        pass

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "markets.yaml must be valid JSON or install PyYAML for YAML parsing"
        ) from exc


def _parse_auto_discover_spec(value: object) -> Optional[AutoDiscoverSpec]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("market:auto_discover must be an object when provided")
    symbol = str(value.get("symbol", "")).strip().upper()
    horizon = str(value.get("horizon", "")).strip().lower()
    mode = str(value.get("mode", "")).strip().lower()
    if not symbol or not horizon or not mode:
        raise ValueError("market:auto_discover requires symbol, horizon, and mode")
    return AutoDiscoverSpec(symbol=symbol, horizon=horizon, mode=mode)


def load_markets(path: str) -> List[MarketConfig]:
    data = _load_yaml_or_json(Path(path))
    markets_raw = data.get("markets", []) if isinstance(data, dict) else []
    markets: List[MarketConfig] = []
    for entry in markets_raw:
        markets.append(
            MarketConfig(
                name=str(entry.get("name", "")),
                condition_id=entry.get("condition_id") or None,
                token_ids=[str(token) for token in entry.get("token_ids", [])],
                slug_prefix=entry.get("slug_prefix"),
                reference_symbol=str(entry.get("reference_symbol", "")),
                min_tick=float(entry.get("min_tick", 0.01)),
                min_size=float(entry.get("min_size", 1)),
                max_price=float(entry.get("max_price", 0.99)),
                min_price=float(entry.get("min_price", 0.01)),
                discovery_backend=entry.get("discovery_backend"),
                selection_regex=entry.get("selection_regex"),
                allow_unknown_symbol=bool(entry.get("allow_unknown_symbol", False)),
                auto_discover=_parse_auto_discover_spec(entry.get("auto_discover")),
            )
        )
    return markets


def validate_markets_config(markets: List[MarketConfig], auto_discover: bool) -> None:
    if not markets:
        raise ValueError(
            "markets list missing. Provide config/markets.yaml or run discovery to populate markets."
        )

    for market in markets:
        name = market.name or "<unnamed>"
        token_ids = market.token_ids or []
        has_empty_tokens = any(token.strip() == "" for token in token_ids)
        has_tokens = any(token.strip() != "" for token in token_ids)
        has_condition = bool(market.condition_id)
        has_slug_prefix = bool(market.slug_prefix)
        auto_discover_spec = market.auto_discover

        if has_empty_tokens:
            raise ValueError(
                f"market:{name} token_ids contains empty strings. "
                "Fix token_ids (remove blanks) or rely on slug_prefix with auto-discovery."
            )

        if auto_discover_spec is not None:
            symbol = auto_discover_spec.symbol.strip().upper()
            horizon = auto_discover_spec.horizon.strip().lower()
            mode = auto_discover_spec.mode.strip().lower()
            if (symbol, horizon, mode) != ("BTC", "15m", "latest_active"):
                raise ValueError(
                    f"market:{name} auto_discover unsupported tuple: "
                    f"symbol={symbol}, horizon={horizon}, mode={mode}. "
                    "Supported: BTC/15m/latest_active."
                )
            if market.reference_symbol and market.reference_symbol.strip().upper() != symbol:
                raise ValueError(
                    f"market:{name} auto_discover symbol={symbol} "
                    f"must match reference_symbol={market.reference_symbol.strip().upper()}."
                )

        if not has_condition and not auto_discover:
            raise ValueError(
                f"market:{name} missing condition_id. "
                "Provide condition_id or enable --auto_discover."
            )

        if not has_tokens and not auto_discover:
            raise ValueError(
                f"market:{name} missing token_ids. "
                "Provide token_ids or enable --auto_discover."
            )

        if (not has_condition or not has_tokens) and auto_discover and not has_slug_prefix:
            if not market.reference_symbol:
                raise ValueError(
                    f"market:{name} missing reference_symbol required for auto_discover without slug_prefix."
                )
