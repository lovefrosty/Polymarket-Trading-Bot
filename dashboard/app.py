from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import altair as alt
except ModuleNotFoundError:  # pragma: no cover
    alt = None  # type: ignore[assignment]

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from core.market_time import window_start_end_ms
from dashboard.contracts import DashboardFilters, HealthGateStatus, RefreshPolicy, TopBarMetrics
from dashboard import data_access as da
from dashboard.panels.export import render_export_panel
from dashboard.panels.market_context import render_market_context_panel
from dashboard.panels.reliability import render_health_panel, render_logs_panel
from dashboard.panels.replay_diff import render_replay_diff_panel
from dashboard.panels.signals import render_signals_panel
from dashboard.panels.staleness import render_staleness_panel


TERMINAL_CSS = """
<style>
:root {
  --bg:#0b0f14;
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
div[data-testid="stHorizontalBlock"], section[data-testid="stSidebar"] {
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
        "SELECT as_of_ts, is_frozen, reasons, mode FROM system_state ORDER BY as_of_ts DESC LIMIT 1"
    )
    mode = str(safe_first(state, "mode", "OBSERVE")).upper()
    frozen = bool(int(safe_first(state, "is_frozen", 0) or 0))
    reasons = _parse_reasons(safe_first(state, "reasons", ""))

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
        freeze_reasons=reasons,
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
    c_down = int((c_df.get("book_health_state", pd.Series(dtype=str)).astype(str).str.upper() == "DOWN").sum()) if not c_df.empty else 0
    c_stale = int((c_df.get("book_age_p95_ms", pd.Series(dtype=float)) > 5000).sum()) if not c_df.empty else 0
    c_status = "CRITICAL" if c_down > 0 else ("WARN" if c_stale > 0 else "OK")

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
            summary=f"book_down={c_down} stale={c_stale}",
            details={"book_down": c_down, "book_stale": c_stale},
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


def _build_filters() -> Tuple[DashboardFilters, RefreshPolicy]:
    assert st is not None
    st.sidebar.header("Controls")

    markets_df = query_df(
        "SELECT market, MAX(ts_ms) AS max_ts FROM decisions GROUP BY market ORDER BY max_ts DESC"
    )
    markets = ["ALL"] + markets_df["market"].dropna().astype(str).tolist() if not markets_df.empty else ["ALL"]

    selected_market = st.sidebar.selectbox("Market", markets, index=0)

    tokens = _selected_market_tokens(selected_market)
    selected_token = st.sidebar.selectbox("Token", ["ALL"] + tokens, index=0)

    window_label = st.sidebar.selectbox("Time Window", ["5m", "15m", "1h", "6h", "24h"], index=2)
    window_map = {"5m": 5, "15m": 15, "1h": 60, "6h": 360, "24h": 1440}

    lookback_rows = st.sidebar.slider("Rows", 50, 2000, 200, step=50)
    severity_filter = st.sidebar.selectbox("Alert Severity", ["ALL", "critical", "warn", "info"], index=0)
    strategy_filter = st.sidebar.text_input("Strategy filter", value="")
    positive_ev_only = st.sidebar.checkbox("Signals with EV > 0", value=False)
    allow_only = st.sidebar.checkbox("Gate = ALLOW only", value=False)

    st.sidebar.divider()
    auto_refresh = st.sidebar.checkbox("Auto refresh", value=True)
    refresh_ms = st.sidebar.selectbox("Refresh interval (ms)", [1000, 1500, 2000], index=0)
    heavy_every_ticks = st.sidebar.slider("Heavy chart every N ticks", 2, 10, 5)

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
    return filters, policy


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


def _render_topbar(metrics: TopBarMetrics) -> None:
    assert st is not None
    freeze_class = "alert" if metrics.is_frozen else "ok"
    freeze_label = "FROZEN" if metrics.is_frozen else "OK"
    reasons = ", ".join(metrics.freeze_reasons) if metrics.freeze_reasons else "none"

    st.markdown('<div class="topbar">', unsafe_allow_html=True)
    st.markdown(
        f'<div class="{freeze_class}"><b>Mode:</b> {metrics.mode} &nbsp; <b>Freeze:</b> {freeze_label} &nbsp; <b>Reasons:</b> {reasons}</div>',
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
    st.markdown("</div>", unsafe_allow_html=True)


def _render_overview(filters: DashboardFilters, heavy_refresh: bool) -> None:
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
    st.subheader("Live Feed")
    st.dataframe(feed_df, use_container_width=True, height=260)

    st.subheader("Top Signals")
    signal_df = _apply_decision_filters(decisions, filters)
    signal_df = signal_df.sort_values("ts_ms", ascending=False).head(filters.lookback_rows)
    top_cols = [
        col
        for col in ["ts", "market", "token_id", "action", "p_hat", "ev", "strategy", "gate_result", "reason_codes"]
        if col in signal_df.columns
    ]
    st.dataframe(signal_df[top_cols], use_container_width=True, height=240)

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
            st.altair_chart(_style_chart(chart), use_container_width=True)


def _render_inventory_quotes(filters: DashboardFilters, heavy_refresh: bool) -> None:
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
    st.subheader("Inventory per token")
    st.dataframe(inv, use_container_width=True, height=230)

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
    cols = [
        col
        for col in ["ts", "order_id", "token_id", "side", "price", "qty", "post_only", "status", "event_kind", "age_s"]
        if col in orders.columns
    ]
    st.dataframe(orders[cols], use_container_width=True, height=220)

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
    st.dataframe(skew, use_container_width=True, height=180)

    st.markdown('<div class="readonly-btn"><b>READ ONLY:</b> Cancel all quotes</div>', unsafe_allow_html=True)
    st.button("Cancel all quotes", disabled=True, help="Dashboard is read-only unless explicitly enabled in a future ops phase.")


def _render_microstructure(filters: DashboardFilters, heavy_refresh: bool) -> None:
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

    st.subheader("Microstructure table")
    st.dataframe(micro, use_container_width=True, height=260)

    if not micro.empty and alt is not None:
        spread_chart = alt.Chart(micro).mark_line().encode(
            x="ts:T",
            y="spread_bps:Q",
            color="token_id:N",
            tooltip=["ts", "token_id", "spread_bps"],
        ).properties(height=160)
        st.altair_chart(_style_chart(spread_chart), use_container_width=True)

        depth = micro.copy()
        depth["depth_at_qty"] = (depth["depth_at_qty_buy"].fillna(0) + depth["depth_at_qty_sell"].fillna(0)) / 2.0
        depth_chart = alt.Chart(depth).mark_line().encode(
            x="ts:T",
            y="depth_at_qty:Q",
            color="token_id:N",
            tooltip=["ts", "token_id", "depth_at_qty"],
        ).properties(height=160)
        st.altair_chart(_style_chart(depth_chart), use_container_width=True)

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
    st.dataframe(fill_latency, use_container_width=True, height=180)


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

    filters, policy = _build_filters()

    def _render_live() -> None:
        tick, heavy_refresh = _next_tick(policy)
        metrics = compute_topbar_metrics(filters)
        health = compute_health_a_to_e(filters)
        _render_topbar(metrics)
        st.caption(f"tick={tick} heavy_refresh={heavy_refresh} utc={_iso(_now_ms())}")

        start_ts, end_ts = _time_filter(filters.window_minutes)
        tab_overview, tab_health, tab_signals, tab_inventory, tab_micro, tab_logs = st.tabs(
            ["Overview", "Health (A-E)", "Signals", "Inventory & Quotes", "Microstructure", "Logs"]
        )

        with tab_overview:
            _render_panel("market_context", lambda: render_market_context_panel(filters, metrics), budget_ms=200)
            _render_panel("overview", lambda: _render_overview(filters, heavy_refresh), budget_ms=450)

        with tab_health:
            _render_panel("health", lambda: render_health_panel(filters, health), budget_ms=500)
            context = render_staleness_panel(filters, start_ts, end_ts)
            if context is not None:
                st.session_state["drillthrough_context"] = context

        with tab_signals:
            _render_panel(
                "signals",
                lambda: render_signals_panel(filters, start_ts, _apply_decision_filters),
                budget_ms=450,
            )
            _render_panel(
                "replay_diff",
                lambda: render_replay_diff_panel(filters, start_ts),
                budget_ms=300,
            )

        with tab_inventory:
            _render_panel("inventory", lambda: _render_inventory_quotes(filters, heavy_refresh), budget_ms=450)

        with tab_micro:
            _render_panel("microstructure", lambda: _render_microstructure(filters, heavy_refresh), budget_ms=500)

        with tab_logs:
            _render_panel("logs", lambda: render_logs_panel(filters, start_ts), budget_ms=450)
            drillthrough_context = st.session_state.get("drillthrough_context")
            _render_panel(
                "incident_export",
                lambda: render_export_panel(filters, start_ts, end_ts, drillthrough_context),
                budget_ms=250,
            )

    if policy.auto_refresh and hasattr(st, "fragment"):
        refresh_seconds = max(1, int(round(policy.topbar_refresh_ms / 1000.0)))

        @st.fragment(run_every=f"{refresh_seconds}s")
        def _live_fragment() -> None:
            _render_live()

        _live_fragment()
    else:
        _render_live()


if __name__ == "__main__":
    render_dashboard()
