from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from time import perf_counter
import sys
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import altair as alt
except ModuleNotFoundError:  # pragma: no cover
    alt = None  # type: ignore[assignment]

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.market_time import window_start_end_ms
from dashboard.contracts import DashboardFilters, HealthGateStatus, RefreshPolicy, TopBarMetrics, ViewMode
from dashboard import data_access as da
from dashboard.panels.export import render_export_panel
from dashboard.panels.market_context import render_market_context_panel
from dashboard.panels.portfolio import render_portfolio_panel
from dashboard.panels.rollover import render_rollover_panel
from dashboard.panels.reliability import render_health_panel, render_logs_panel
from dashboard.panels.replay_diff import render_replay_diff_panel
from dashboard.panels.signals import render_signals_panel
from dashboard.panels.staleness import render_staleness_panel


TERMINAL_CSS = """
<style>
:root {
  --bg:#000000;
  --panel:transparent;
  --muted:#9aa4b2;
  --text:#e6edf3;
  --border:rgba(230,237,243,0.18);
  --accent-green:#6ee7b7;
  --accent-amber:#facc15;
  --accent-red:#f87171;
}
html, body, [class*="css"], [data-testid="stAppViewContainer"]  {
  background-color: var(--bg) !important;
  color: var(--text) !important;
  font-family: "IBM Plex Mono", "Menlo", "Consolas", "Courier New", monospace !important;
}
body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  background-image: repeating-linear-gradient(
    to bottom,
    rgba(230, 237, 243, 0.02),
    rgba(230, 237, 243, 0.02) 1px,
    transparent 1px,
    transparent 3px
  );
}
.block-container { padding-top: 0.8rem; }
div[data-testid="stMetric"], div[data-testid="stMetric"] > div,
div[data-testid="stContainer"], div[data-testid="stVerticalBlock"],
div[data-testid="stHorizontalBlock"], div[data-testid="stTable"],
div[data-testid="stMarkdownContainer"], section[data-testid="stSidebar"],
[data-testid="stAppViewBlockContainer"] {
  background: transparent !important;
  box-shadow: none !important;
}
div[data-testid="stMetric"] {
  border: 1px solid var(--border);
  padding: 10px;
  border-radius: 10px;
}
div[data-testid="stDataFrame"] {
  background: transparent !important;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 4px;
}
div[data-testid="stDataFrame"] * {
  background: transparent !important;
  color: var(--text) !important;
}
div[data-testid="stMetricLabel"] p,
div[data-testid="stMetricValue"] {
  color: var(--text) !important;
}
section[data-testid="stSidebar"] { border-right: 1px solid var(--border); }
h1,h2,h3 { color: var(--text) !important; }
small, .muted { color: var(--muted) !important; }
.alert { background:rgba(248,113,113,0.08); border:1px solid var(--accent-red); padding:8px 10px; border-radius:10px; }
.ok { background:rgba(110,231,183,0.06); border:1px solid var(--accent-green); padding:8px 10px; border-radius:10px; }
.warn { background:rgba(250,204,21,0.06); border:1px solid var(--accent-amber); padding:8px 10px; border-radius:10px; }
.topbar { border:1px solid var(--border); border-radius:12px; background:transparent; padding:12px; margin-bottom:10px; }
.readonly-btn {
  border:1px dashed var(--accent-amber);
  background:rgba(250,204,21,0.06);
  color:var(--text);
  padding:8px;
  border-radius:10px;
}
div[data-baseweb="tab-list"] {
  background: transparent !important;
}
button[data-baseweb="tab"] {
  border: 1px solid var(--border) !important;
  border-radius: 8px !important;
  margin-right: 6px !important;
}
hr { border: none; border-top: 1px solid var(--border); }
</style>
"""


CODE_TO_HUMAN = {
    "A_PSTAR_INVALID": "Price feed invalid",
    "A_PSTAR_STALE": "Price feed stale",
    "A_PSTAR_DIVERGED": "Spot/perp disagreement high",
    "B_BOOK_TIME_LEAK": "Book causality violation",
    "C_SPREAD_TOO_WIDE": "Spread too wide",
    "C_SLIPPAGE_HIGH": "Slippage risk high",
    "C_BOOK_STALE": "Order book stale",
    "C_NO_EXECUTION_PRICE": "Execution price unavailable",
    "D_ONE_LEG_TIMEOUT": "Hedge incomplete timeout",
    "D_HEDGE_INCOMPLETE": "Hedge incomplete",
    "E_SIGNAL_AGE_HIGH": "Signal too old",
    "E_WS_LAG_HIGH": "Websocket lag high",
    "E_ACK_LATENCY_HIGH": "Order ack latency high",
    "WS_UNKNOWN_RATE_HIGH": "Unknown websocket payload rate high",
    "FEED_NOT_WIRED": "Feed wiring incomplete",
}

EMPTY_STATE_MESSAGES = {
    "signals": "No signals right now - widen filters or wait for spread compression.",
    "positions": "No open positions - review Signals tab for trading opportunities.",
    "live_feed": "No live events yet.",
    "active_orders": "No active orders",
    "quote_skew": "No quote skew telemetry yet.",
    "micro": "No microstructure telemetry yet.",
    "fills": "No fills yet.",
}


def _status_class(level: str) -> str:
    token = str(level or "").strip().upper()
    if token in {"FROZEN", "CRITICAL", "BLOCKED"}:
        return "alert"
    if token in {"DEGRADED", "WARN", "CAUTION", "BOOTING", "PARTIAL", "UNKNOWN"}:
        return "warn"
    return "ok"


DB_PATH = da.resolve_db_path()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _iso(ts_ms: Optional[int]) -> str:
    if ts_ms is None:
        return "N/A"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_ms(value: Optional[float]) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"{value:.1f}"


def _fmt_ratio(value: float) -> str:
    return f"{value * 100.0:.1f}%"


def _normalize_view_mode(raw: str) -> ViewMode:
    return "developer" if str(raw).strip().lower() == "developer" else "trader"


def is_developer_mode(view_mode: ViewMode) -> bool:
    return view_mode == "developer"


def _market_label_from_slug(slug: Optional[str]) -> str:
    if not slug:
        return "Unknown market"
    text = str(slug)
    parts = text.split("-")
    if len(parts) >= 3 and parts[1] == "updown":
        symbol = parts[0].upper()
        horizon = parts[2]
        return f"{symbol} {horizon} Up/Down"
    return text


def _short_token(token_id: Optional[str]) -> str:
    token = str(token_id or "")
    if not token:
        return "N/A"
    if len(token) <= 10:
        return token
    return f"{token[:4]}...{token[-4:]}"


def _normalize_outcome_label(raw: Optional[str]) -> str:
    if raw is None:
        return "Outcome"
    text = str(raw).strip().lower()
    if text in {"yes", "up", "true", "long", "bull"}:
        return "YES"
    if text in {"no", "down", "false", "short", "bear"}:
        return "NO"
    return str(raw).strip().upper() or "Outcome"


def _outcome_fallback(rank: int) -> str:
    return "Outcome A" if rank == 0 else "Outcome B"


def _load_resolved_market_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    root = Path.cwd()
    candidates: List[Path] = []
    for pattern in (
        "logs/resolved_markets*.json",
        "logs/*/resolved_markets*.json",
        "tmp/*/resolved_markets*.json",
        "tmp/*/logs/resolved_markets*.json",
    ):
        candidates.extend(root.glob(pattern))
    candidates = [path for path in candidates if path.exists()]
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    for path in candidates[:8]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("schema_version") == "resolved_markets_v1":
            markets = payload.get("markets", [])
        elif isinstance(payload, list):
            markets = payload
        else:
            markets = payload.get("resolved", []) if isinstance(payload, dict) else []
        for market in markets:
            if not isinstance(market, dict):
                continue
            rows.append(
                {
                    "market_slug": market.get("slug"),
                    "token_ids": list(market.get("token_ids") or []),
                    "outcomes": list(market.get("outcomes") or []),
                    "outcome_by_token": dict(market.get("outcome_by_token") or {}),
                }
            )
    return rows


def _build_label_registry_from_records(
    resolved_rows: Sequence[Dict[str, Any]],
    discovery_payload_rows: Sequence[Dict[str, Any]],
    decision_rows: pd.DataFrame,
) -> Dict[str, Dict[str, Any]]:
    registry: Dict[str, Dict[str, Any]] = {}

    def ensure_market(market_slug: str) -> Dict[str, Any]:
        entry = registry.get(market_slug)
        if entry is None:
            entry = {
                "market_label": _market_label_from_slug(market_slug),
                "token_to_outcome": {},
                "seen_tokens": set(),
            }
            registry[market_slug] = entry
        return entry

    for row in resolved_rows:
        slug = str(row.get("market_slug") or "").strip()
        if not slug:
            continue
        entry = ensure_market(slug)
        token_to_outcome: Dict[str, str] = {}
        explicit = row.get("outcome_by_token") or {}
        if isinstance(explicit, dict):
            for token_id, outcome in explicit.items():
                token = str(token_id)
                if token:
                    token_to_outcome[token] = _normalize_outcome_label(outcome)
        token_ids = [str(token) for token in row.get("token_ids") or [] if str(token)]
        outcomes = [str(outcome) for outcome in row.get("outcomes") or []]
        if token_ids and outcomes and len(token_ids) == len(outcomes):
            for token_id, outcome in zip(token_ids, outcomes):
                token_to_outcome.setdefault(token_id, _normalize_outcome_label(outcome))
        for token_id, outcome in token_to_outcome.items():
            entry["token_to_outcome"].setdefault(token_id, outcome)
            entry["seen_tokens"].add(token_id)

    for item in discovery_payload_rows:
        if not isinstance(item, dict):
            continue
        raw_payload = item.get("payload_json")
        payload = da.safe_json(raw_payload)
        slug = str(payload.get("selected_slug") or "").strip()
        if not slug:
            continue
        entry = ensure_market(slug)
        tokens = [str(token) for token in payload.get("selected_clobTokenIds") or [] if str(token)]
        for rank, token_id in enumerate(tokens):
            entry["token_to_outcome"].setdefault(token_id, _outcome_fallback(rank))
            entry["seen_tokens"].add(token_id)

    if not decision_rows.empty:
        for _, row in decision_rows.iterrows():
            slug = str(row.get("market") or "").strip()
            token = str(row.get("token_id") or "").strip()
            if not slug or not token:
                continue
            entry = ensure_market(slug)
            entry["seen_tokens"].add(token)
            payload = da.safe_json(row.get("policy_json"))
            by_token = payload.get("outcome_by_token")
            if isinstance(by_token, dict):
                for token_id, outcome in by_token.items():
                    token_text = str(token_id or "").strip()
                    if not token_text:
                        continue
                    entry["token_to_outcome"].setdefault(token_text, _normalize_outcome_label(outcome))
            chosen = payload.get("chosen_action")
            if isinstance(chosen, dict):
                chosen_token = str(chosen.get("token_id") or "").strip()
                chosen_outcome = chosen.get("outcome")
                if chosen_token and chosen_outcome is not None:
                    entry["token_to_outcome"].setdefault(chosen_token, _normalize_outcome_label(chosen_outcome))

    for slug, entry in registry.items():
        tokens_sorted = sorted(str(token) for token in entry.get("seen_tokens") or set())
        for rank, token_id in enumerate(tokens_sorted):
            entry["token_to_outcome"].setdefault(token_id, _outcome_fallback(rank if rank < 2 else 1))
        entry.pop("seen_tokens", None)
        entry["market_label"] = _market_label_from_slug(slug)
    return registry


def _build_label_registry_uncached(selected_market: str) -> Dict[str, Dict[str, Any]]:
    where = ""
    params: Tuple[Any, ...] = ()
    if selected_market != "ALL":
        where = "WHERE market = ?"
        params = (selected_market,)
    decision_rows = query_df(
        f"""
        SELECT market, token_id, policy_json
        FROM decisions
        {where}
        ORDER BY ts_ms DESC
        LIMIT 5000
        """,
        params,
    )
    discovery_rows = query_df(
        """
        SELECT payload_json
        FROM discovery_requests
        ORDER BY ts_ms DESC
        LIMIT 300
        """
    )
    resolved_rows = _load_resolved_market_rows()
    return _build_label_registry_from_records(
        resolved_rows=resolved_rows,
        discovery_payload_rows=discovery_rows.to_dict("records"),
        decision_rows=decision_rows,
    )


if st is not None and hasattr(st, "cache_data"):

    @st.cache_data(ttl=5, show_spinner=False)
    def _build_label_registry_cached(selected_market: str, db_sig: str) -> Dict[str, Dict[str, Any]]:
        _ = db_sig
        return _build_label_registry_uncached(selected_market)

else:

    def _build_label_registry_cached(selected_market: str, db_sig: str) -> Dict[str, Dict[str, Any]]:
        _ = db_sig
        return _build_label_registry_uncached(selected_market)


def build_label_registry(selected_market: str) -> Dict[str, Dict[str, Any]]:
    db_sig = "missing"
    if DB_PATH.exists():
        db_sig = f"{DB_PATH}:{int(DB_PATH.stat().st_mtime_ns)}"
    return _build_label_registry_cached(selected_market, db_sig)


def label_token(label_registry: Dict[str, Dict[str, Any]], market_slug: Optional[str], token_id: Optional[str]) -> Dict[str, str]:
    slug = str(market_slug or "")
    token = str(token_id or "")
    if (not slug or slug not in label_registry) and token:
        for candidate_slug in sorted(label_registry.keys()):
            token_map = (label_registry.get(candidate_slug) or {}).get("token_to_outcome") or {}
            if token in token_map:
                slug = candidate_slug
                break
    entry = label_registry.get(slug, {})
    token_map = entry.get("token_to_outcome") or {}
    outcome = token_map.get(token)
    if outcome is None:
        outcome = _outcome_fallback(0)
    return {
        "market_label": str(entry.get("market_label") or _market_label_from_slug(slug)),
        "outcome_label": str(outcome),
        "token_short": _short_token(token),
    }


def human_reason(code: Optional[str], message: Optional[str], view_mode: ViewMode) -> str:
    code_text = str(code or "").strip()
    base = CODE_TO_HUMAN.get(code_text, code_text if code_text else "Unknown")
    msg = str(message or "").strip()
    if is_developer_mode(view_mode):
        if code_text:
            return f"{base} [{code_text}]"
        return base
    return format_trader_reason(code=code_text, payload=None, message=msg)


def _extract_first_number(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def format_trader_reason(code: Optional[str], payload: Optional[Dict[str, Any]], message: Optional[str]) -> str:
    code_text = str(code or "").strip()
    base = CODE_TO_HUMAN.get(code_text, code_text if code_text else "Unknown condition")
    data = payload if isinstance(payload, dict) else {}
    msg = str(message or "").strip()

    spread_bps = data.get("spread_bps")
    if spread_bps is None:
        spread_bps = _extract_first_number(msg) if "spread" in msg.lower() else None
    max_bps = data.get("max_bps")
    if max_bps is None:
        max_bps = data.get("threshold_bps")

    if code_text == "C_SPREAD_TOO_WIDE" and spread_bps is not None:
        if max_bps is not None:
            return f"Spread too wide ({float(spread_bps):.1f} bps > {float(max_bps):.1f} bps max)"
        return f"Spread too wide ({float(spread_bps):.1f} bps)"
    if code_text == "C_SLIPPAGE_HIGH" and spread_bps is not None:
        return f"Slippage risk high ({float(spread_bps):.1f} bps)"

    age_ms = data.get("age_ms")
    threshold_ms = data.get("threshold_ms")
    if code_text.startswith("A_PSTAR") and age_ms is not None:
        if threshold_ms is not None:
            return f"{base} ({float(age_ms)/1000.0:.1f}s > {float(threshold_ms)/1000.0:.1f}s limit)"
        return f"{base} ({float(age_ms)/1000.0:.1f}s)"

    if msg and msg.lower() not in base.lower():
        return f"{base} ({msg[:72]})"
    return base


def classify_spread_state(spread_bps: Optional[float], warn_bps: float = 100.0, block_bps: float = 200.0) -> str:
    if spread_bps is None or (isinstance(spread_bps, float) and math.isnan(spread_bps)):
        return "UNKNOWN"
    value = float(spread_bps)
    if value > block_bps:
        return "BLOCKED"
    if value >= warn_bps:
        return "CAUTION"
    return "OK"


def build_tradeable_hint(row: Dict[str, Any]) -> Tuple[str, str]:
    spread = row.get("spread_bps")
    state = classify_spread_state(spread)
    book_health = str(row.get("book_health") or row.get("book_health_state") or "").strip().upper()
    depth_buy = row.get("depth_at_qty_buy")
    depth_sell = row.get("depth_at_qty_sell")
    min_depth = None
    vals: List[float] = []
    for val in (depth_buy, depth_sell):
        if val is None or (isinstance(val, float) and math.isnan(val)):
            continue
        try:
            vals.append(float(val))
        except (TypeError, ValueError):
            continue
    if vals:
        min_depth = min(vals)

    if book_health == "DOWN":
        return "WAIT", "Book feed degraded"
    if state == "BLOCKED":
        return "WAIT", f"Spread too wide ({float(spread):.1f} bps)"
    if min_depth is not None and min_depth <= 0:
        return "WAIT", "No executable depth"
    if state == "CAUTION":
        return "WAIT", "Spread elevated - wait for compression"
    if state == "UNKNOWN":
        return "WAIT", "Spread unavailable"
    return "YES", "Tradeable"


def build_trader_health_chips(metrics: TopBarMetrics, health_map: Dict[str, HealthGateStatus]) -> List[Dict[str, str]]:
    chips: List[Dict[str, str]] = []

    pstar_ms = metrics.pstar_age_current_ms
    if pstar_ms is not None:
        if pstar_ms < 2_000:
            state = "Fresh"
            klass = "ok"
        elif pstar_ms < 5_000:
            state = "Slightly stale"
            klass = "warn"
        else:
            state = "Stale"
            klass = "alert"
        chips.append({"label": "Price Feed", "state": state, "detail": f"{pstar_ms/1000.0:.1f}s", "klass": klass})

    ws_ms = metrics.ws_lag_current_ms
    if ws_ms is not None:
        if ws_ms < 250:
            conn_state = "Healthy"
            conn_klass = "ok"
        elif ws_ms < 1_500:
            conn_state = "Degraded"
            conn_klass = "warn"
        else:
            conn_state = "Lagging"
            conn_klass = "alert"
        chips.append({"label": "Connection", "state": conn_state, "detail": f"{ws_ms:.0f} ms", "klass": conn_klass})

    ack_ms = metrics.ack_p95_5m_ms
    if ack_ms is not None:
        if ack_ms < 300:
            ex_state = "Healthy"
            ex_klass = "ok"
        elif ack_ms < 1_000:
            ex_state = "Degraded"
            ex_klass = "warn"
        else:
            ex_state = "Lagging"
            ex_klass = "alert"
        chips.append({"label": "Execution Path", "state": ex_state, "detail": f"p95 {ack_ms:.0f} ms", "klass": ex_klass})

    sig_ms = metrics.signal_age_p95_5m_ms
    if sig_ms is not None:
        if sig_ms < 1_000:
            sig_state = "Fresh"
            sig_klass = "ok"
        elif sig_ms < 5_000:
            sig_state = "Aging"
            sig_klass = "warn"
        else:
            sig_state = "Stale"
            sig_klass = "alert"
        chips.append({"label": "Signal Freshness", "state": sig_state, "detail": f"p95 {sig_ms:.0f} ms", "klass": sig_klass})

    # Reflect hard gate state if present.
    gate_e = health_map.get("E")
    if gate_e is not None and gate_e.status == "CRITICAL":
        chips.append({"label": "Latency Gate", "state": "Blocked", "detail": "E gate critical", "klass": "alert"})

    return chips


def _latest_tradeability_summary() -> Tuple[str, str]:
    row = query_df(
        """
        SELECT spread_bps, depth_at_qty_buy, depth_at_qty_sell, book_health
        FROM microstructure_stats
        ORDER BY ts_ms DESC
        LIMIT 1
        """
    )
    if row.empty:
        return "WAIT", "No recent microstructure snapshot"
    status, reason = build_tradeable_hint(row.iloc[0].to_dict())
    return status, reason


def build_signals_table_for_view(
    signal_df: pd.DataFrame,
    view_mode: ViewMode,
    label_registry: Dict[str, Dict[str, Any]],
) -> pd.DataFrame:
    if signal_df.empty:
        if is_developer_mode(view_mode):
            return signal_df
        return pd.DataFrame(columns=["Market", "Direction", "EV", "Suggested price", "Confidence", "Tradeable", "Why blocked", "Action hint"])

    out = signal_df.copy()
    labels = out.apply(lambda row: label_token(label_registry, row.get("market"), row.get("token_id")), axis=1)
    out["market_label"] = labels.apply(lambda item: item.get("market_label"))
    out["outcome_label"] = labels.apply(lambda item: item.get("outcome_label"))

    def action_text(row: pd.Series) -> str:
        action = str(row.get("action") or "").upper()
        side = row.get("outcome_label") or "Outcome"
        if action in {"BUY", "BUY_YES"}:
            return f"Buy {side}"
        if action in {"SELL", "SELL_NO"}:
            return f"Sell {side}"
        if action in {"HOLD", "SKIP", "NO_ACTION"}:
            return "Hold"
        return action.title()

    out["action_label"] = out.apply(action_text, axis=1)
    def _suggested_current(row: pd.Series) -> str:
        value = row.get("p_hat")
        if value is None or pd.isna(value):
            return "N/A"
        try:
            px = float(value)
            return f"{px:.3f}->{px:.3f}"
        except (TypeError, ValueError):
            return "N/A"

    out["suggested_current"] = out.apply(_suggested_current, axis=1)
    out["confidence"] = out.get("ev", pd.Series(dtype=float)).apply(
        lambda value: "High" if pd.notna(value) and float(value) >= 0.03 else ("Med" if pd.notna(value) and float(value) >= 0.01 else "Low")
    )

    def _why_blocked(row: pd.Series) -> str:
        gate = str(row.get("gate_result") or "").upper()
        if gate == "ALLOW":
            return ""
        reason_codes = str(row.get("reason_codes") or "").strip()
        first_code = reason_codes.split(",")[0].strip() if reason_codes else ""
        return format_trader_reason(first_code, None, None)

    out["why_blocked"] = out.apply(_why_blocked, axis=1)
    out["tradeable"] = out["gate_result"].apply(lambda gate: "YES" if str(gate).upper() == "ALLOW" else "WAIT")
    out["action_hint"] = out.apply(
        lambda row: "Eligible - monitor entry window"
        if row.get("tradeable") == "YES"
        else "WAIT for spread < 150 bps",
        axis=1,
    )

    if is_developer_mode(view_mode):
        cols = [
            col
            for col in ["ts", "decision_id", "market", "market_label", "token_id", "outcome_label", "action", "strategy", "p_hat", "ev", "gate_result", "reason_codes"]
            if col in out.columns
        ]
        return out[cols]

    slim = pd.DataFrame(
        {
            "Market": out["market_label"],
            "Direction": out["action_label"],
            "EV": out["ev"],
            "Suggested price": out["suggested_current"],
            "Confidence": out["confidence"],
            "Tradeable": out["tradeable"],
            "Why blocked": out["why_blocked"],
            "Action hint": out["action_hint"],
        }
    )
    return slim


def _runtime_schema_missing() -> bool:
    if not DB_PATH.exists():
        return True
    required = {"system_state", "decisions", "alerts"}
    present = set(da.existing_tables(DB_PATH))
    return not required.issubset(present)


def _table_exists(name: str) -> bool:
    return da.table_exists(name, DB_PATH)


def query_df(sql: str, params: Sequence[Any] = ()) -> pd.DataFrame:
    return da.query_df(sql, params=params, db_path=DB_PATH)


def q(sql: str) -> pd.DataFrame:
    return query_df(sql)


def safe_first(df: pd.DataFrame, col: str, default: Any = 0) -> Any:
    return da.safe_first(df, col, default)


def _time_filter(window_minutes: int) -> Tuple[int, int]:
    end_ts = _now_ms()
    start_ts = end_ts - window_minutes * 60_000
    return start_ts, end_ts


def _heavy_df(key: str, sql: str, params: Sequence[Any], heavy_refresh: bool) -> pd.DataFrame:
    if st is None:
        return query_df(sql, params)
    cache_key = f"heavy::{key}"
    if heavy_refresh or cache_key not in st.session_state:
        st.session_state[cache_key] = query_df(sql, params)
    return st.session_state[cache_key]


def _style_chart(chart):
    if alt is None:
        return chart
    return (
        chart.configure(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(
            labelColor="#e6edf3",
            titleColor="#e6edf3",
            gridColor="rgba(230,237,243,0.12)",
            domainColor="rgba(230,237,243,0.2)",
            tickColor="rgba(230,237,243,0.2)",
        )
        .configure_legend(
            labelColor="#e6edf3",
            titleColor="#e6edf3",
        )
    )


def _parse_reasons(raw: Any) -> List[str]:
    return da.parse_reasons(raw)


def _classify_signal_action(action: str) -> bool:
    return da.classify_signal_action(action)


def adapt_decisions(df: pd.DataFrame) -> pd.DataFrame:
    return da.adapt_decisions(df)


def adapt_orders(df: pd.DataFrame) -> pd.DataFrame:
    return da.adapt_orders(df)


def adapt_fills(df: pd.DataFrame) -> pd.DataFrame:
    return da.adapt_fills(df)


def _selected_market_tokens(selected_market: str) -> List[str]:
    if selected_market == "ALL":
        tokens = query_df("SELECT DISTINCT token_id FROM decisions ORDER BY token_id")
    else:
        tokens = query_df(
            "SELECT DISTINCT token_id FROM decisions WHERE market=? ORDER BY token_id",
            (selected_market,),
        )
    if tokens.empty:
        return []
    return [str(x) for x in tokens["token_id"].dropna().tolist()]


def _time_to_window_end(slug: str) -> str:
    window = window_start_end_ms(slug)
    if window is None:
        return "N/A"
    _, end_ms = window
    remaining = end_ms - _now_ms()
    if remaining <= 0:
        return "closed"
    total_s = int(remaining / 1000)
    mm = total_s // 60
    ss = total_s % 60
    return f"{mm:02d}:{ss:02d}"


def compute_topbar_metrics(filters: DashboardFilters) -> TopBarMetrics:
    state = query_df(
        "SELECT as_of_ts, is_frozen, reasons, mode, payload_json FROM system_state ORDER BY as_of_ts DESC LIMIT 1"
    )
    mode = str(safe_first(state, "mode", "OBSERVE")).upper()
    frozen = bool(int(safe_first(state, "is_frozen", 0) or 0))
    reasons = _parse_reasons(safe_first(state, "reasons", ""))
    state_payload = da.safe_json(safe_first(state, "payload_json", "{}"))
    alert_state = str(state_payload.get("alert_state") or ("FROZEN" if frozen else "OK")).upper()
    if alert_state not in {"OK", "DEGRADED", "FROZEN"}:
        alert_state = "FROZEN" if frozen else ("DEGRADED" if reasons else "OK")
    readiness_state = str(state_payload.get("readiness_state") or ("READY" if alert_state == "OK" else "BOOTING")).upper()

    market_slug = filters.selected_market
    if market_slug == "ALL":
        recent_market = query_df("SELECT market FROM decisions ORDER BY ts_ms DESC LIMIT 1")
        market_slug = str(safe_first(recent_market, "market", "ALL"))

    token_ids = _selected_market_tokens(market_slug)

    now_ms = _now_ms()
    five_min_ago = now_ms - 5 * 60_000
    one_hour_ago = now_ms - 60 * 60_000

    lat5 = query_df(
        """
        SELECT ts_ms, p50_send_ack_ms, p95_send_ack_ms, ws_lag_ms,
               p95_ws_lag_ms, p95_pstar_age_ms, p95_signal_age_ms
        FROM latency_stats
        WHERE ts_ms >= ?
        ORDER BY ts_ms ASC
        """,
        (five_min_ago,),
    )
    lat_current = lat5.tail(1)

    pstar5 = query_df(
        """
        SELECT ts_ms, age_spot_ms, age_perp_ms
        FROM pstar_stats
        WHERE ts_ms >= ?
        ORDER BY ts_ms ASC
        """,
        (five_min_ago,),
    )
    pstar_current = pstar5.tail(1)

    pstar_age_current = None
    if not pstar_current.empty:
        spot = safe_first(pstar_current, "age_spot_ms", None)
        perp = safe_first(pstar_current, "age_perp_ms", None)
        vals = [float(v) for v in [spot, perp] if v is not None and not pd.isna(v)]
        if vals:
            pstar_age_current = max(vals)

    pstar_age_p95_5m = da.percentile(lat5.get("p95_pstar_age_ms", pd.Series(dtype=float)), 0.95)
    ws_lag_current = safe_first(lat_current, "ws_lag_ms", None)
    ws_lag_p95_5m = da.percentile(lat5.get("p95_ws_lag_ms", pd.Series(dtype=float)), 0.95)
    ack_p50_5m = safe_first(lat_current, "p50_send_ack_ms", None)
    ack_p95_5m = safe_first(lat_current, "p95_send_ack_ms", None)
    signal_age_p95_5m = da.percentile(lat5.get("p95_signal_age_ms", pd.Series(dtype=float)), 0.95)

    counters = query_df(
        """
        SELECT
          (SELECT COUNT(*) FROM decisions WHERE ts_ms >= ?) AS decisions,
          (SELECT COUNT(*) FROM decisions WHERE ts_ms >= ? AND UPPER(action) NOT IN ('FREEZE','HOLD','SKIP','NONE','NO_ACTION')) AS signals,
          (SELECT COUNT(*) FROM orders WHERE ts_ms >= ? AND (LOWER(status) LIKE '%cancel%' OR LOWER(COALESCE(reason,'')) LIKE '%cancel%')) AS cancels,
          (SELECT COUNT(*) FROM orders WHERE ts_ms >= ? AND (LOWER(status) LIKE '%replace%' OR LOWER(COALESCE(reason,'')) LIKE '%replace%' OR LOWER(COALESCE(fsm_state,'')) LIKE '%replace%')) AS replaces,
          (SELECT COUNT(*) FROM fills WHERE ts_ms >= ?) AS fills,
          (SELECT COUNT(*) FROM orders WHERE ts_ms >= ? AND (LOWER(status) LIKE '%reject%' OR LOWER(COALESCE(reason,'')) LIKE '%reject%')) AS rejects
        """,
        (one_hour_ago, one_hour_ago, one_hour_ago, one_hour_ago, one_hour_ago, one_hour_ago),
    )

    inv = query_df(
        """
        SELECT i.token_id, i.yes_qty, i.no_qty, i.usdc
        FROM inventory i
        INNER JOIN (
          SELECT token_id, MAX(ts_ms) AS max_ts
          FROM inventory
          GROUP BY token_id
        ) x ON x.token_id = i.token_id AND x.max_ts = i.ts_ms
        ORDER BY i.token_id
        """
    )
    net_yes = float(inv["yes_qty"].sum()) if "yes_qty" in inv.columns else 0.0
    net_no = float(inv["no_qty"].sum()) if "no_qty" in inv.columns else 0.0

    p_hat = query_df(
        """
        SELECT d.token_id, d.p_hat
        FROM decisions d
        INNER JOIN (
          SELECT token_id, MAX(ts_ms) AS max_ts
          FROM decisions
          WHERE p_hat IS NOT NULL
          GROUP BY token_id
        ) x ON x.token_id = d.token_id AND x.max_ts = d.ts_ms
        """
    )
    p_map = {str(row["token_id"]): float(row["p_hat"]) for _, row in p_hat.iterrows()} if not p_hat.empty else {}
    exposure = 0.0
    if not inv.empty:
        for _, row in inv.iterrows():
            token_id = str(row.get("token_id"))
            mid = p_map.get(token_id, 0.5)
            yes_qty = float(row.get("yes_qty") or 0.0)
            no_qty = float(row.get("no_qty") or 0.0)
            exposure += yes_qty * mid - no_qty * (1.0 - mid)

    fills = adapt_fills(
        query_df("SELECT ts_ms, payload_json FROM fills WHERE ts_ms >= ?", (one_hour_ago,))
    )
    if fills.empty:
        hedge_completeness = 1.0
    else:
        hedge = int(fills["is_hedge"].sum())
        primary = max(0, len(fills) - hedge)
        hedge_completeness = 1.0 if primary == 0 else min(1.0, hedge / float(primary))

    alert_codes = query_df(
        """
        SELECT code FROM alerts
        WHERE ts_ms >= ?
        ORDER BY ts_ms DESC
        LIMIT 10
        """,
        (one_hour_ago,),
    )
    if not alert_codes.empty:
        for code in alert_codes["code"].dropna().tolist():
            code_text = str(code)
            if code_text not in reasons:
                reasons.append(code_text)
    reasons = reasons[:5]

    return TopBarMetrics(
        mode=mode,
        is_frozen=frozen,
        alert_state=alert_state,
        freeze_reasons=reasons,
        readiness_state=readiness_state,
        market_slug=market_slug,
        token_ids=token_ids,
        time_to_window_end=_time_to_window_end(market_slug),
        pstar_age_current_ms=float(pstar_age_current) if pstar_age_current is not None else None,
        pstar_age_p95_5m_ms=float(pstar_age_p95_5m) if pstar_age_p95_5m is not None else None,
        ws_lag_current_ms=float(ws_lag_current) if ws_lag_current is not None and not pd.isna(ws_lag_current) else None,
        ws_lag_p95_5m_ms=float(ws_lag_p95_5m) if ws_lag_p95_5m is not None else None,
        ack_p50_5m_ms=float(ack_p50_5m) if ack_p50_5m is not None and not pd.isna(ack_p50_5m) else None,
        ack_p95_5m_ms=float(ack_p95_5m) if ack_p95_5m is not None and not pd.isna(ack_p95_5m) else None,
        signal_age_p95_5m_ms=float(signal_age_p95_5m) if signal_age_p95_5m is not None else None,
        decisions_1h=int(safe_first(counters, "decisions", 0)),
        signals_1h=int(safe_first(counters, "signals", 0)),
        cancels_1h=int(safe_first(counters, "cancels", 0)),
        replaces_1h=int(safe_first(counters, "replaces", 0)),
        fills_1h=int(safe_first(counters, "fills", 0)),
        rejects_1h=int(safe_first(counters, "rejects", 0)),
        net_yes=net_yes,
        net_no=net_no,
        net_usd_exposure=float(exposure),
        hedge_completeness=hedge_completeness,
    )


def compute_health_a_to_e(filters: DashboardFilters) -> Dict[str, HealthGateStatus]:
    start_ts, _ = _time_filter(filters.window_minutes)

    pstar_df = query_df(
        "SELECT ts_ms, symbol, disagreement_bps, confidence, age_spot_ms, age_perp_ms, valid FROM pstar_stats WHERE ts_ms >= ? ORDER BY ts_ms DESC",
        (start_ts,),
    )
    invalid_count = 0
    latest_disagree = None
    latest_age = None
    if not pstar_df.empty:
        invalid_count = int((pstar_df["valid"] == 0).sum())
        latest_disagree = safe_first(pstar_df, "disagreement_bps", None)
        latest_age = max(
            [
                float(v)
                for v in [safe_first(pstar_df, "age_spot_ms", None), safe_first(pstar_df, "age_perp_ms", None)]
                if v is not None and not pd.isna(v)
            ]
            or [0.0]
        )
    a_status = "OK"
    if invalid_count > 0:
        a_status = "CRITICAL"
    elif latest_disagree is not None and float(latest_disagree) > 75.0:
        a_status = "WARN"

    b_df = query_df(
        """
        SELECT ts_ms, max_feature_ts_ms, decision_ts_event_ms
        FROM decisions
        WHERE ts_ms >= ?
        ORDER BY ts_ms DESC
        """,
        (start_ts,),
    )
    b_violations = 0
    if not b_df.empty:
        b_violations = int((b_df["max_feature_ts_ms"] >= b_df["ts_ms"]).sum())
    b_status = "CRITICAL" if b_violations > 0 else "OK"

    c_df = query_df(
        """
        SELECT token_id, book_health_state, book_age_p95_ms
        FROM book_health_stats
        WHERE ts_ms >= ?
        ORDER BY ts_ms DESC
        """,
        (start_ts,),
    )
    c_micro = query_df(
        """
        SELECT spread_bps, depth_at_qty_buy, depth_at_qty_sell, book_health
        FROM microstructure_stats
        WHERE ts_ms >= ?
        ORDER BY ts_ms DESC
        LIMIT 1
        """,
        (start_ts,),
    )
    c_down = int((c_df.get("book_health_state", pd.Series(dtype=str)).astype(str).str.upper() == "DOWN").sum()) if not c_df.empty else 0
    c_stale = int((c_df.get("book_age_p95_ms", pd.Series(dtype=float)) > 5000).sum()) if not c_df.empty else 0
    c_spread_latest = safe_first(c_micro, "spread_bps", None)
    c_depth_buy = safe_first(c_micro, "depth_at_qty_buy", None)
    c_depth_sell = safe_first(c_micro, "depth_at_qty_sell", None)
    c_spread_state = classify_spread_state(float(c_spread_latest) if c_spread_latest is not None and not pd.isna(c_spread_latest) else None)
    c_status = "CRITICAL" if c_down > 0 or c_spread_state == "BLOCKED" else ("WARN" if c_stale > 0 or c_spread_state == "CAUTION" else "OK")

    d_alerts = query_df(
        """
        SELECT COUNT(*) AS n
        FROM alerts
        WHERE ts_ms >= ? AND (UPPER(code) LIKE '%ONE_LEG%' OR UPPER(code) LIKE '%HEDGE%')
        """,
        (start_ts,),
    )
    d_one_leg = int(safe_first(d_alerts, "n", 0))
    d_fills = adapt_fills(query_df("SELECT ts_ms, payload_json FROM fills WHERE ts_ms >= ?", (start_ts,)))
    if d_fills.empty:
        d_hedge = 1.0
        d_since_primary = None
    else:
        hedge = int(d_fills["is_hedge"].sum())
        primary = max(0, len(d_fills) - hedge)
        d_hedge = 1.0 if primary == 0 else min(1.0, hedge / float(primary))
        d_since_primary = (_now_ms() - int(d_fills["ts_ms"].max())) / 1000.0
    d_status = "CRITICAL" if d_one_leg > 0 else ("WARN" if d_hedge < 0.8 else "OK")

    e_df = query_df(
        """
        SELECT ts_ms, p95_ws_lag_ms, p95_send_ack_ms, p95_signal_age_ms
        FROM latency_stats
        WHERE ts_ms >= ?
        ORDER BY ts_ms DESC
        """,
        (start_ts,),
    )
    e_ws = float(safe_first(e_df, "p95_ws_lag_ms", 0.0) or 0.0)
    e_ack = float(safe_first(e_df, "p95_send_ack_ms", 0.0) or 0.0)
    e_sig = float(safe_first(e_df, "p95_signal_age_ms", 0.0) or 0.0)

    ticks = query_df(
        "SELECT decision_ts_ms FROM decision_ticks WHERE ts_ms >= ? ORDER BY decision_ts_ms ASC",
        (start_ts,),
    )
    jitter = 0.0
    if len(ticks) > 2:
        diffs = ticks["decision_ts_ms"].diff().dropna()
        if not diffs.empty:
            jitter = float(diffs.std()) if not pd.isna(diffs.std()) else 0.0
    e_status = "OK"
    if e_ws > 5000 or e_ack > 1000 or e_sig > 5000:
        e_status = "CRITICAL"
    elif e_ws > 1500 or e_ack > 300 or e_sig > 1500:
        e_status = "WARN"

    return {
        "A": HealthGateStatus(
            gate="A",
            status=a_status,
            summary=f"P* invalid={invalid_count} disagreement_bps={_fmt_ms(float(latest_disagree) if latest_disagree is not None else None)}",
            details={"invalid_count": invalid_count, "latest_disagreement_bps": latest_disagree, "latest_age_ms": latest_age},
        ),
        "B": HealthGateStatus(
            gate="B",
            status=b_status,
            summary=f"causality_violations={b_violations}",
            details={"violations": b_violations},
        ),
        "C": HealthGateStatus(
            gate="C",
            status=c_status,
            summary=f"book_down={c_down} stale={c_stale} spread_state={c_spread_state}",
            details={
                "book_down": c_down,
                "book_stale": c_stale,
                "latest_spread_bps": c_spread_latest,
                "depth_at_qty_buy": c_depth_buy,
                "depth_at_qty_sell": c_depth_sell,
                "spread_state": c_spread_state,
            },
        ),
        "D": HealthGateStatus(
            gate="D",
            status=d_status,
            summary=f"one_leg_alerts={d_one_leg} hedge={_fmt_ratio(d_hedge)}",
            details={"one_leg_alerts": d_one_leg, "hedge_completeness": d_hedge, "seconds_since_primary_fill": d_since_primary},
        ),
        "E": HealthGateStatus(
            gate="E",
            status=e_status,
            summary=f"ws_p95={e_ws:.1f} ack_p95={e_ack:.1f} signal_p95={e_sig:.1f}",
            details={"ws_lag_p95_ms": e_ws, "ack_p95_ms": e_ack, "signal_age_p95_ms": e_sig, "loop_jitter_ms": jitter},
        ),
    }


def should_refresh_heavy(tick: int, policy: RefreshPolicy) -> bool:
    return tick % max(policy.heavy_every_ticks, 1) == 0


def _build_filters() -> Tuple[DashboardFilters, RefreshPolicy, ViewMode]:
    assert st is not None
    st.sidebar.header("Controls")
    stored_view = _normalize_view_mode(str(st.session_state.get("view_mode", "trader")))
    selected_view = st.sidebar.radio(
        "View Mode",
        ["Trader", "Developer"],
        index=0 if stored_view == "trader" else 1,
        horizontal=True,
    )
    view_mode: ViewMode = _normalize_view_mode(selected_view)
    st.session_state["view_mode"] = view_mode

    markets_df = query_df(
        "SELECT market, MAX(ts_ms) AS max_ts FROM decisions GROUP BY market ORDER BY max_ts DESC"
    )
    markets = ["ALL"] + markets_df["market"].dropna().astype(str).tolist() if not markets_df.empty else ["ALL"]

    selected_market = st.sidebar.selectbox("Market", markets, index=0)

    window_label = st.sidebar.selectbox("Time Window", ["5m", "15m", "1h", "6h", "24h"], index=2)
    window_map = {"5m": 5, "15m": 15, "1h": 60, "6h": 360, "24h": 1440}

    st.sidebar.divider()
    auto_refresh = st.sidebar.checkbox("Auto refresh", value=True)
    refresh_ms = st.sidebar.selectbox("Refresh interval (ms)", [1000, 1500, 2000], index=0)
    heavy_every_ticks = 5
    tokens = _selected_market_tokens(selected_market)
    selected_token = "ALL"
    lookback_rows = 200
    severity_filter = "ALL"
    strategy_filter = ""
    positive_ev_only = False
    allow_only = False

    if is_developer_mode(view_mode):
        selected_token = st.sidebar.selectbox("Token", ["ALL"] + tokens, index=0)
        lookback_rows = st.sidebar.slider("Rows", 50, 2000, 200, step=50)
        severity_filter = st.sidebar.selectbox("Alert Severity", ["ALL", "critical", "warn", "info"], index=0)
        strategy_filter = st.sidebar.text_input("Strategy filter", value="")
        positive_ev_only = st.sidebar.checkbox("Signals with EV > 0", value=False)
        allow_only = st.sidebar.checkbox("Gate = ALLOW only", value=False)
        heavy_every_ticks = st.sidebar.slider("Heavy chart every N ticks", 2, 10, 5)
    else:
        advanced = st.sidebar.expander("Advanced", expanded=False)
        selected_token = advanced.selectbox("Token", ["ALL"] + tokens, index=0)
        lookback_rows = advanced.slider("Rows", 50, 2000, 200, step=50)
        severity_filter = advanced.selectbox("Alert Severity", ["ALL", "critical", "warn", "info"], index=0)
        strategy_filter = advanced.text_input("Strategy filter", value="")
        positive_ev_only = advanced.checkbox("Signals with EV > 0", value=False)
        allow_only = advanced.checkbox("Gate = ALLOW only", value=False)
        heavy_every_ticks = advanced.slider("Heavy chart every N ticks", 2, 10, 5)

    policy = RefreshPolicy(
        auto_refresh=auto_refresh,
        topbar_refresh_ms=int(refresh_ms),
        heavy_every_ticks=int(heavy_every_ticks),
    )

    filters = DashboardFilters(
        lookback_rows=lookback_rows,
        window_minutes=window_map[window_label],
        selected_market=selected_market,
        selected_token=selected_token,
        severity_filter=severity_filter,
        positive_ev_only=positive_ev_only,
        allow_only=allow_only,
        strategy_filter=strategy_filter.strip(),
    )
    return filters, policy, view_mode


def _next_tick(policy: RefreshPolicy) -> Tuple[int, bool]:
    if st is None:
        return 1, True
    tick = int(st.session_state.get("dashboard_tick", 0)) + 1
    st.session_state["dashboard_tick"] = tick
    return tick, should_refresh_heavy(tick, policy)


def _apply_decision_filters(df: pd.DataFrame, filters: DashboardFilters) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if filters.selected_market != "ALL" and "market" in out.columns:
        out = out[out["market"] == filters.selected_market]
    if filters.selected_token != "ALL" and "token_id" in out.columns:
        out = out[out["token_id"] == filters.selected_token]
    if filters.positive_ev_only and "ev" in out.columns:
        out = out[out["ev"] > 0]
    if filters.allow_only and "gate_result" in out.columns:
        out = out[out["gate_result"] == "ALLOW"]
    if filters.strategy_filter and "strategy" in out.columns:
        needle = filters.strategy_filter.lower()
        out = out[out["strategy"].astype(str).str.lower().str.contains(needle)]
    return out


def _render_topbar(
    metrics: TopBarMetrics,
    health_map: Dict[str, HealthGateStatus],
    view_mode: ViewMode,
    label_registry: Dict[str, Dict[str, Any]],
) -> None:
    assert st is not None
    freeze_class = _status_class(metrics.alert_state)
    freeze_label = metrics.alert_state
    reasons = ", ".join(format_trader_reason(reason, None, None) for reason in metrics.freeze_reasons) if metrics.freeze_reasons else "none"
    market_label = label_token(label_registry, metrics.market_slug, None)["market_label"]
    market_text = f"{market_label} - closes in {metrics.time_to_window_end}"

    c_details = (health_map.get("C").details if health_map.get("C") is not None else {}) if health_map else {}
    spread_bps = c_details.get("latest_spread_bps") if isinstance(c_details, dict) else None
    spread_limit = 150.0

    if metrics.alert_state == "OK":
        state_line = "Trading active - all gates healthy"
    else:
        if spread_bps is not None and not pd.isna(spread_bps):
            spread_text = f"Spread {float(spread_bps):.1f} bps (max {spread_limit:.0f})"
            state_line = f"Trading paused: {spread_text}"
        else:
            state_line = "Trading paused due to safety gates"
        if reasons != "none":
            state_line = f"{state_line} | {reasons}"

    st.markdown('<div class="topbar">', unsafe_allow_html=True)

    if is_developer_mode(view_mode):
        st.markdown(
            f'<div class="{freeze_class}"><b>Mode:</b> {metrics.mode} &nbsp; <b>State:</b> {freeze_label} '
            f'&nbsp; <b>Readiness:</b> {metrics.readiness_state} &nbsp; <b>Status:</b> {state_line}</div>',
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Market", metrics.market_slug)
        c2.metric("Tokens", str(len(metrics.token_ids)))
        c3.metric("Window End ETA", metrics.time_to_window_end)
        c4.metric("P*_age_ms cur/p95", f"{_fmt_ms(metrics.pstar_age_current_ms)}/{_fmt_ms(metrics.pstar_age_p95_5m_ms)}")
        c5.metric("ws_lag_ms cur/p95", f"{_fmt_ms(metrics.ws_lag_current_ms)}/{_fmt_ms(metrics.ws_lag_p95_5m_ms)}")
        c6.metric("ack_ms p50/p95", f"{_fmt_ms(metrics.ack_p50_5m_ms)}/{_fmt_ms(metrics.ack_p95_5m_ms)}")

        q1, q2, q3, q4, q5, q6 = st.columns(6)
        q1.metric("signal_age_ms p95", _fmt_ms(metrics.signal_age_p95_5m_ms))
        q2.metric("decisions (1h)", metrics.decisions_1h)
        q3.metric("signals (1h)", metrics.signals_1h)
        q4.metric("cancels/replaces", f"{metrics.cancels_1h}/{metrics.replaces_1h}")
        q5.metric("fills/rejects", f"{metrics.fills_1h}/{metrics.rejects_1h}")
        q6.metric("hedge completeness", _fmt_ratio(metrics.hedge_completeness))

        i1, i2, i3 = st.columns(3)
        i1.metric("net YES", f"{metrics.net_yes:.3f}")
        i2.metric("net NO", f"{metrics.net_no:.3f}")
        i3.metric("net USD exposure", f"{metrics.net_usd_exposure:.3f}")
        token_preview = ", ".join(metrics.token_ids[:6]) if metrics.token_ids else "N/A"
        st.caption(f"Token IDs: {token_preview}")
    else:
        b1, b2, b3 = st.columns(3)
        b1.markdown(f'<div class="ok"><b>Mode</b><br/>{metrics.mode}</div>', unsafe_allow_html=True)
        b2.markdown(f'<div class="{freeze_class}"><b>State</b><br/>{freeze_label}</div>', unsafe_allow_html=True)
        b3.markdown(
            f'<div class="{_status_class(metrics.readiness_state)}"><b>Readiness</b><br/>{metrics.readiness_state}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="{freeze_class}"><b>Current status:</b> {state_line}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="ok"><b>Market:</b> {market_text}</div>',
            unsafe_allow_html=True,
        )
        chips = build_trader_health_chips(metrics, health_map)
        if chips:
            chip_cols = st.columns(max(1, min(4, len(chips))))
            for idx, chip in enumerate(chips[:4]):
                klass = chip.get("klass", "ok")
                chip_cols[idx].markdown(
                    f'<div class="{klass}"><b>{chip.get("label")}:</b> {chip.get("state")}<br/><small>{chip.get("detail")}</small></div>',
                    unsafe_allow_html=True,
                )
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Decisions (1h)", metrics.decisions_1h)
        s2.metric("Signals (1h)", metrics.signals_1h)
        s3.metric("Fills / Rejects", f"{metrics.fills_1h}/{metrics.rejects_1h}")
        s4.metric("Hedge completeness", _fmt_ratio(metrics.hedge_completeness))
        net_position = abs(metrics.net_yes) + abs(metrics.net_no) + abs(metrics.net_usd_exposure)
        if net_position < 1e-9:
            st.info(EMPTY_STATE_MESSAGES["positions"])
        else:
            i1, i2, i3 = st.columns(3)
            i1.metric("Net YES", f"{metrics.net_yes:.3f}")
            i2.metric("Net NO", f"{metrics.net_no:.3f}")
            i3.metric("Net USD exposure", f"{metrics.net_usd_exposure:.3f}")
    st.markdown("</div>", unsafe_allow_html=True)


def _render_overview(
    filters: DashboardFilters,
    heavy_refresh: bool,
    view_mode: ViewMode,
    label_registry: Dict[str, Dict[str, Any]],
) -> None:
    assert st is not None
    start_ts, _ = _time_filter(filters.window_minutes)

    decisions = adapt_decisions(
        query_df(
            """
            SELECT ts_ms, decision_id, market, token_id, action, reason_codes, p_hat, expected_edge, expected_cost, policy_json
            FROM decisions
            WHERE ts_ms >= ?
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (start_ts, max(filters.lookback_rows, 100)),
        )
    )
    orders = adapt_orders(
        query_df(
            """
            SELECT ts_ms, event_id, order_id, token_id, side, price, qty, status, reason, fsm_state, post_only, payload_json
            FROM orders
            WHERE ts_ms >= ?
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (start_ts, max(filters.lookback_rows, 100)),
        )
    )
    fills = adapt_fills(
        query_df(
            """
            SELECT ts_ms, event_id, order_id, token_id, side, fill_price, fill_qty, payload_json
            FROM fills
            WHERE ts_ms >= ?
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (start_ts, max(filters.lookback_rows, 100)),
        )
    )

    feed_rows: List[Dict[str, Any]] = []
    for _, row in decisions.head(100).iterrows():
        feed_rows.append(
            {
                "ts_ms": row.get("ts_ms"),
                "type": "decision",
                "market": row.get("market"),
                "token_id": row.get("token_id"),
                "event": row.get("action"),
                "details": row.get("reason_codes"),
                "p_hat": row.get("p_hat"),
                "ev": row.get("ev"),
                "strategy": row.get("strategy"),
            }
        )
    for _, row in orders.head(100).iterrows():
        feed_rows.append(
            {
                "ts_ms": row.get("ts_ms"),
                "type": "order",
                "market": None,
                "token_id": row.get("token_id"),
                "event": row.get("event_kind"),
                "details": row.get("status"),
                "p_hat": None,
                "ev": None,
                "strategy": row.get("fsm_state"),
            }
        )
    for _, row in fills.head(100).iterrows():
        fill_label = "hedge_fill" if bool(row.get("is_hedge")) else "primary_fill"
        feed_rows.append(
            {
                "ts_ms": row.get("ts_ms"),
                "type": "fill",
                "market": None,
                "token_id": row.get("token_id"),
                "event": fill_label,
                "details": f"price={row.get('fill_price')} qty={row.get('fill_qty')}",
                "p_hat": None,
                "ev": None,
                "strategy": None,
            }
        )

    feed_df = pd.DataFrame(feed_rows)
    if not feed_df.empty:
        feed_df = feed_df.sort_values("ts_ms", ascending=False).head(100)
        feed_df["ts"] = pd.to_datetime(feed_df["ts_ms"], unit="ms", utc=True)

    if not is_developer_mode(view_mode):
        tradeable, reason = _latest_tradeability_summary()
        klass = "ok" if tradeable == "YES" else "warn"
        st.markdown(
            f'<div class="{klass}"><b>Tradeability now:</b> {tradeable} | {reason}</div>',
            unsafe_allow_html=True,
        )

    st.subheader("Live Feed")
    if not feed_df.empty:
        feed_df["ts"] = pd.to_datetime(feed_df["ts_ms"], unit="ms", utc=True)
        labels = feed_df.apply(lambda row: label_token(label_registry, row.get("market"), row.get("token_id")), axis=1)
        feed_df["Market"] = labels.apply(lambda item: item.get("market_label"))
        feed_df["Side"] = labels.apply(lambda item: item.get("outcome_label"))
        if not is_developer_mode(view_mode):
            feed_df["details"] = feed_df["details"].apply(
                lambda value: human_reason(str(value).split(",")[0].strip(), None, view_mode) if value else ""
            )
    if is_developer_mode(view_mode):
        st.dataframe(feed_df, width="stretch", height=260)
    else:
        trader_cols = [col for col in ["ts", "type", "Market", "Side", "event", "details", "ev", "strategy"] if col in feed_df.columns]
        if feed_df.empty:
            st.info(EMPTY_STATE_MESSAGES["live_feed"])
        else:
            st.dataframe(feed_df[trader_cols], width="stretch", height=260)

    st.subheader("Top Signals")
    signal_df = _apply_decision_filters(decisions, filters)
    signal_df = signal_df.sort_values("ts_ms", ascending=False).head(filters.lookback_rows)
    display_signals = build_signals_table_for_view(signal_df, view_mode, label_registry)
    if display_signals.empty:
        st.info(EMPTY_STATE_MESSAGES["signals"])
    else:
        st.dataframe(display_signals, width="stretch", height=240)

    ev_df = _heavy_df(
        "overview_ev_hist",
        """
        SELECT (expected_edge - expected_cost) AS ev
        FROM decisions
        WHERE ts_ms >= ?
        ORDER BY ts_ms DESC
        LIMIT 5000
        """,
        (start_ts,),
        heavy_refresh,
    )
    positive = 0.0
    if not ev_df.empty and "ev" in ev_df.columns:
        positive = float((ev_df["ev"] > 0).mean())
    st.metric("% positive EV", _fmt_ratio(positive))
    if not ev_df.empty and alt is not None and "ev" in ev_df.columns:
        ev_clean = ev_df[ev_df["ev"].notna()].copy()
        if not ev_clean.empty:
            ev_clean["bucket"] = pd.cut(ev_clean["ev"], bins=24)
            hist = ev_clean.groupby("bucket").size().reset_index(name="n")
            hist["mid"] = hist["bucket"].apply(lambda item: (item.left + item.right) / 2)
            chart = alt.Chart(hist).mark_bar().encode(
                x=alt.X("mid:Q", title="EV"),
                y=alt.Y("n:Q", title="count"),
            ).properties(height=160)
            st.altair_chart(_style_chart(chart), width="stretch")


def _render_inventory_quotes(
    filters: DashboardFilters,
    heavy_refresh: bool,
    view_mode: ViewMode,
    label_registry: Dict[str, Dict[str, Any]],
) -> None:
    assert st is not None
    inv = query_df(
        """
        SELECT i.ts_ms, i.token_id, i.yes_qty, i.no_qty, i.usdc, i.source
        FROM inventory i
        INNER JOIN (
          SELECT token_id, MAX(ts_ms) AS max_ts
          FROM inventory
          GROUP BY token_id
        ) x ON x.token_id = i.token_id AND x.max_ts = i.ts_ms
        ORDER BY i.token_id
        """
    )
    if not inv.empty:
        inv["net_qty"] = inv["yes_qty"].fillna(0) - inv["no_qty"].fillna(0)
        inv["ts"] = pd.to_datetime(inv["ts_ms"], unit="ms", utc=True)
    st.subheader("Inventory")
    if inv.empty:
        st.info(EMPTY_STATE_MESSAGES["positions"])
    elif is_developer_mode(view_mode):
        st.dataframe(inv, width="stretch", height=230)
    else:
        rows: List[Dict[str, Any]] = []
        for _, row in inv.iterrows():
            labels = label_token(label_registry, filters.selected_market if filters.selected_market != "ALL" else None, row.get("token_id"))
            yes_qty = float(row.get("yes_qty") or 0.0)
            no_qty = float(row.get("no_qty") or 0.0)
            if yes_qty != 0:
                rows.append(
                    {
                        "Market": labels["market_label"],
                        "Side": "YES",
                        "Qty": yes_qty,
                        "Entry": "N/A",
                        "Current": "N/A",
                        "PnL ($)": "N/A",
                        "PnL (%)": "N/A",
                        "Status": "open",
                    }
                )
            if no_qty != 0:
                rows.append(
                    {
                        "Market": labels["market_label"],
                        "Side": "NO",
                        "Qty": no_qty,
                        "Entry": "N/A",
                        "Current": "N/A",
                        "PnL ($)": "N/A",
                        "PnL (%)": "N/A",
                        "Status": "open",
                    }
                )
        trader_inv = pd.DataFrame(rows)
        if trader_inv.empty:
            st.info(EMPTY_STATE_MESSAGES["positions"])
        else:
            st.dataframe(trader_inv, width="stretch", height=230)

    st.subheader("Active/Open orders")
    orders = adapt_orders(
        query_df(
            """
            SELECT ts_ms, event_id, order_id, token_id, side, price, qty, post_only, status, reason, fsm_state, payload_json
            FROM orders
            WHERE LOWER(status) IN ('open','working','new','resting','submitted','accepted')
               OR LOWER(COALESCE(fsm_state,'')) LIKE '%quote%'
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (filters.lookback_rows,),
        )
    )
    if orders.empty:
        st.info(EMPTY_STATE_MESSAGES["active_orders"])
    elif is_developer_mode(view_mode):
        cols = [
            col
            for col in ["ts", "order_id", "token_id", "side", "price", "qty", "post_only", "status", "event_kind", "age_s"]
            if col in orders.columns
        ]
        st.dataframe(orders[cols], width="stretch", height=220)
    else:
        rows = []
        for _, row in orders.iterrows():
            labels = label_token(label_registry, filters.selected_market if filters.selected_market != "ALL" else None, row.get("token_id"))
            side = str(row.get("side") or "").lower()
            rows.append(
                {
                    "Market": labels["market_label"],
                    "Side": "YES" if side == "buy" else ("NO" if side == "sell" else side.upper()),
                    "Price": row.get("price"),
                    "Qty": row.get("qty"),
                    "Post-only": row.get("post_only"),
                    "Status": row.get("status"),
                    "Age (s)": row.get("age_s"),
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch", height=220)

    st.subheader("Quote skew view")
    skew = _heavy_df(
        "inventory_skew",
        """
        WITH latest_p AS (
          SELECT d.token_id, d.p_hat
          FROM decisions d
          INNER JOIN (
            SELECT token_id, MAX(ts_ms) AS max_ts
            FROM decisions
            WHERE p_hat IS NOT NULL
            GROUP BY token_id
          ) x ON x.token_id = d.token_id AND x.max_ts = d.ts_ms
        ),
        order_base AS (
          SELECT token_id,
                 AVG(CASE WHEN LOWER(side)='buy' THEN price END) AS avg_bid,
                 AVG(CASE WHEN LOWER(side)='sell' THEN price END) AS avg_ask
          FROM orders
          WHERE ts_ms >= ?
          GROUP BY token_id
        )
        SELECT o.token_id, o.avg_bid, o.avg_ask, p.p_hat AS mid,
               (o.avg_bid - p.p_hat) AS bid_offset,
               (o.avg_ask - p.p_hat) AS ask_offset
        FROM order_base o
        LEFT JOIN latest_p p ON p.token_id = o.token_id
        ORDER BY o.token_id
        """,
        (_now_ms() - 60 * 60_000,),
        heavy_refresh,
    )
    if is_developer_mode(view_mode):
        st.dataframe(skew, width="stretch", height=180)
    else:
        if skew.empty:
            st.info(EMPTY_STATE_MESSAGES["quote_skew"])
        else:
            rows = []
            for _, row in skew.iterrows():
                labels = label_token(label_registry, filters.selected_market if filters.selected_market != "ALL" else None, row.get("token_id"))
                rows.append(
                    {
                        "Market": labels["market_label"],
                        "Bid offset": row.get("bid_offset"),
                        "Ask offset": row.get("ask_offset"),
                        "Mid": row.get("mid"),
                    }
                )
            st.dataframe(pd.DataFrame(rows), width="stretch", height=180)

    st.markdown('<div class="readonly-btn"><b>READ ONLY:</b> Cancel all quotes</div>', unsafe_allow_html=True)
    st.button("Cancel all quotes", disabled=True, help="Dashboard is read-only unless explicitly enabled in a future ops phase.")


def _render_microstructure(
    filters: DashboardFilters,
    heavy_refresh: bool,
    view_mode: ViewMode,
    label_registry: Dict[str, Dict[str, Any]],
) -> None:
    assert st is not None
    start_ts, _ = _time_filter(filters.window_minutes)
    micro = _heavy_df(
        "micro_main",
        """
        SELECT ts_ms, token_id, spread_bps, depth_at_qty_buy, depth_at_qty_sell,
               slippage_bps_buy, slippage_bps_sell, effective_spread_bps_buy, effective_spread_bps_sell, book_health
        FROM microstructure_stats
        WHERE ts_ms >= ?
        ORDER BY ts_ms DESC
        LIMIT 2000
        """,
        (start_ts,),
        heavy_refresh,
    )
    if not micro.empty:
        micro["ts"] = pd.to_datetime(micro["ts_ms"], unit="ms", utc=True)

    st.subheader("Microstructure")
    if not micro.empty:
        labels = micro.apply(lambda row: label_token(label_registry, filters.selected_market if filters.selected_market != "ALL" else None, row.get("token_id")), axis=1)
        micro["market_label"] = labels.apply(lambda item: item.get("market_label"))
        micro["outcome_label"] = labels.apply(lambda item: item.get("outcome_label"))
    if micro.empty:
        st.info(EMPTY_STATE_MESSAGES["micro"])
    elif is_developer_mode(view_mode):
        st.dataframe(micro, width="stretch", height=260)
    else:
        trader_micro = micro.copy()
        trader_micro["Depth"] = (
            trader_micro["depth_at_qty_buy"].fillna(0.0).astype(float)
            + trader_micro["depth_at_qty_sell"].fillna(0.0).astype(float)
        ) / 2.0
        tradeable_rows = []
        for _, row in trader_micro.iterrows():
            hint_status, hint_reason = build_tradeable_hint(row.to_dict())
            spread_state = classify_spread_state(float(row.get("spread_bps")) if pd.notna(row.get("spread_bps")) else None)
            tradeable_rows.append(
                {
                    "Market": row.get("market_label"),
                    "Side": row.get("outcome_label"),
                    "Spread (bps)": row.get("spread_bps"),
                    "Depth": row.get("Depth"),
                    "Spread state": spread_state,
                    "Tradeable": hint_status,
                    "Reason": hint_reason,
                }
            )
        trader_df = pd.DataFrame(tradeable_rows).head(300)
        st.dataframe(trader_df, width="stretch", height=260)
        wait_count = int((trader_df["Tradeable"] == "WAIT").sum()) if "Tradeable" in trader_df.columns else 0
        if wait_count > 0:
            st.markdown(
                f'<div class="warn"><b>Tradeability:</b> {wait_count} row(s) currently WAIT due to spread/depth constraints.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="ok"><b>Tradeability:</b> all displayed rows currently tradeable.</div>',
                unsafe_allow_html=True,
            )

    if not micro.empty and alt is not None and is_developer_mode(view_mode):
        spread_chart = alt.Chart(micro).mark_line().encode(
            x="ts:T",
            y="spread_bps:Q",
            color="token_id:N",
            tooltip=["ts", "token_id", "spread_bps"],
        ).properties(height=160)
        st.altair_chart(_style_chart(spread_chart), width="stretch")

        depth = micro.copy()
        depth["depth_at_qty"] = (depth["depth_at_qty_buy"].fillna(0) + depth["depth_at_qty_sell"].fillna(0)) / 2.0
        depth_chart = alt.Chart(depth).mark_line().encode(
            x="ts:T",
            y="depth_at_qty:Q",
            color="token_id:N",
            tooltip=["ts", "token_id", "depth_at_qty"],
        ).properties(height=160)
        st.altair_chart(_style_chart(depth_chart), width="stretch")

    fill_latency = _heavy_df(
        "micro_fill_latency",
        """
        SELECT f.ts_ms, f.order_id, (f.ts_ms - o.ts_ms) AS fill_latency_ms, f.token_id
        FROM fills f
        LEFT JOIN orders o ON o.order_id = f.order_id
        WHERE f.ts_ms >= ?
        ORDER BY f.ts_ms DESC
        LIMIT 2000
        """,
        (start_ts,),
        heavy_refresh,
    )
    st.subheader("Fill latency distribution")
    if fill_latency.empty:
        st.info(EMPTY_STATE_MESSAGES["fills"])
    elif is_developer_mode(view_mode):
        st.dataframe(fill_latency, width="stretch", height=180)
    else:
        labels = fill_latency.apply(lambda row: label_token(label_registry, filters.selected_market if filters.selected_market != "ALL" else None, row.get("token_id")), axis=1)
        fill_latency["Market"] = labels.apply(lambda item: item.get("market_label"))
        fill_latency["Side"] = labels.apply(lambda item: item.get("outcome_label"))
        cols = [col for col in ["ts_ms", "Market", "Side", "fill_latency_ms"] if col in fill_latency.columns]
        st.dataframe(fill_latency[cols], width="stretch", height=180)


def _render_panel(name: str, fn, budget_ms: int = 400) -> None:
    assert st is not None
    t0 = perf_counter()
    try:
        fn()
    except Exception as exc:
        st.markdown(
            f"<div class='warn'><b>DEGRADED</b> panel={name} error={type(exc).__name__}:{exc}</div>",
            unsafe_allow_html=True,
        )
    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > budget_ms:
        st.caption(f"DEGRADED panel={name} over_budget_ms={elapsed:.1f} budget_ms={budget_ms}")


def render_dashboard() -> None:
    if st is None:
        raise RuntimeError("streamlit_not_installed")

    st.set_page_config(page_title="Polymarket Terminal", layout="wide")
    st.markdown(TERMINAL_CSS, unsafe_allow_html=True)
    st.title("Polymarket Terminal")
    st.caption("Retro operator console for SQLite runtime telemetry and A-E safety gates.")

    if _runtime_schema_missing():
        st.markdown(
            '<div class="warn"><b>Runtime not initialized</b> - start <code>scripts/run_system.py</code> to create tables and live telemetry.</div>',
            unsafe_allow_html=True,
        )

    filters, policy, view_mode = _build_filters()

    topbar_slot = st.empty()
    status_slot = st.empty()

    if is_developer_mode(view_mode):
        tab_overview, tab_rollover, tab_health, tab_signals, tab_inventory, tab_portfolio, tab_micro, tab_logs = st.tabs(
            ["Overview", "Rollover", "Health (A-E)", "Signals", "Inventory & Quotes", "Portfolio", "Microstructure", "Logs"]
        )
    else:
        tab_overview, tab_health, tab_signals, tab_inventory, tab_micro, tab_logs = st.tabs(
            ["Overview", "Health (A-E)", "Signals", "Inventory & Quotes", "Microstructure", "Logs"]
        )
        tab_rollover = None
        tab_portfolio = None

    with tab_overview:
        overview_slot = st.empty()
    with tab_health:
        health_slot = st.empty()
    with tab_signals:
        signals_slot = st.empty()
    with tab_inventory:
        inventory_slot = st.empty()
    with tab_micro:
        micro_slot = st.empty()
    with tab_logs:
        logs_slot = st.empty()
    rollover_slot = None
    portfolio_slot = None
    if tab_rollover is not None:
        with tab_rollover:
            rollover_slot = st.empty()
    if tab_portfolio is not None:
        with tab_portfolio:
            portfolio_slot = st.empty()

    use_fragment = bool(policy.auto_refresh and hasattr(st, "fragment"))

    def _render_live() -> None:
        allow_panel_widgets = not use_fragment
        tick, heavy_refresh = _next_tick(policy)
        label_registry = build_label_registry(filters.selected_market)
        metrics = compute_topbar_metrics(filters)
        health = compute_health_a_to_e(filters)

        with topbar_slot.container():
            _render_topbar(metrics, health, view_mode, label_registry)
        with status_slot.container():
            st.caption(f"tick={tick} heavy_refresh={heavy_refresh} utc={_iso(_now_ms())} view={view_mode}")

        start_ts, end_ts = _time_filter(filters.window_minutes)

        with overview_slot.container():
            _render_panel(
                "market_context",
                lambda: render_market_context_panel(filters, metrics, view_mode=view_mode, label_token_fn=lambda market, token: label_token(label_registry, market, token)),
                budget_ms=200,
            )
            _render_panel("overview", lambda: _render_overview(filters, heavy_refresh, view_mode, label_registry), budget_ms=450)

        if rollover_slot is not None:
            with rollover_slot.container():
                _render_panel("rollover", lambda: render_rollover_panel(filters), budget_ms=350)

        with health_slot.container():
            _render_panel(
                "health",
                lambda: render_health_panel(
                    filters,
                    health,
                    view_mode=view_mode,
                    label_token_fn=lambda market, token: label_token(label_registry, market, token),
                    reason_humanizer=lambda code, msg: human_reason(code, msg, view_mode),
                    allow_widgets=allow_panel_widgets,
                ),
                budget_ms=500,
            )
            context = render_staleness_panel(
                filters,
                start_ts,
                end_ts,
                view_mode=view_mode,
                label_token_fn=lambda market, token: label_token(label_registry, market, token),
                allow_widgets=allow_panel_widgets,
            )
            if context is not None:
                st.session_state["drillthrough_context"] = context

        with signals_slot.container():
            _render_panel(
                "signals",
                lambda: render_signals_panel(
                    filters,
                    start_ts,
                    _apply_decision_filters,
                    view_mode=view_mode,
                    build_signals_view=lambda df: build_signals_table_for_view(df, view_mode, label_registry),
                    allow_widgets=allow_panel_widgets,
                ),
                budget_ms=450,
            )
            if is_developer_mode(view_mode):
                _render_panel(
                    "replay_diff",
                    lambda: render_replay_diff_panel(filters, start_ts),
                    budget_ms=300,
                )

        with inventory_slot.container():
            _render_panel("inventory", lambda: _render_inventory_quotes(filters, heavy_refresh, view_mode, label_registry), budget_ms=450)

        if portfolio_slot is not None:
            with portfolio_slot.container():
                _render_panel(
                    "portfolio",
                    lambda: render_portfolio_panel(filters, start_ts, end_ts),
                    budget_ms=450,
                )

        with micro_slot.container():
            _render_panel("microstructure", lambda: _render_microstructure(filters, heavy_refresh, view_mode, label_registry), budget_ms=500)

        with logs_slot.container():
            _render_panel(
                "logs",
                lambda: render_logs_panel(
                    filters,
                    start_ts,
                    view_mode=view_mode,
                    reason_humanizer=lambda code, msg: human_reason(code, msg, view_mode),
                ),
                budget_ms=450,
            )
            drillthrough_context = st.session_state.get("drillthrough_context")
            _render_panel(
                "incident_export",
                lambda: render_export_panel(filters, start_ts, end_ts, drillthrough_context),
                budget_ms=250,
            )

    if use_fragment:
        refresh_seconds = max(1, int(round(policy.topbar_refresh_ms / 1000.0)))

        @st.fragment(run_every=f"{refresh_seconds}s")
        def _live_fragment() -> None:
            _render_live()

        _live_fragment()
    else:
        _render_live()


if __name__ == "__main__":
    render_dashboard()
