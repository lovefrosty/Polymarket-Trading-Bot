from __future__ import annotations

import sqlite3
from pathlib import Path
import os

import altair as alt
import pandas as pd
import streamlit as st


_default_db = os.getenv("RUNTIME_DB_PATH", "runtime.db")
try:
    _secret_db = st.secrets.get("runtime_db_path", _default_db)  # type: ignore[attr-defined]
except Exception:
    _secret_db = _default_db
DB_PATH = Path(_secret_db)


DARK_CSS = """
<style>
:root { --bg:#0b0f17; --panel:#121826; --muted:#8b95a7; --text:#e6eaf2; --border:#1f2a3a; }
html, body, [class*="css"]  { background-color: var(--bg) !important; color: var(--text) !important; }
.block-container { padding-top: 1.2rem; }
div[data-testid="stMetric"] { background: var(--panel); border: 1px solid var(--border); padding: 14px; border-radius: 14px; }
div[data-testid="stDataFrame"] { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 6px; }
section[data-testid="stSidebar"] { background: var(--panel); border-right: 1px solid var(--border); }
h1,h2,h3 { color: var(--text) !important; }
small, .muted { color: var(--muted) !important; }
.alert { background:#2a0f14; border:1px solid #5a1b25; padding:10px 12px; border-radius:12px; }
.ok { background:#0f2a1a; border:1px solid #1b5a33; padding:10px 12px; border-radius:12px; }
</style>
"""


def q(sql: str) -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH.as_posix()) as cx:
        return pd.read_sql_query(sql, cx)


def safe_first(df: pd.DataFrame, col: str, default=0):
    if df.empty or col not in df.columns:
        return default
    v = df.iloc[0][col]
    return v if pd.notna(v) else default


st.set_page_config(page_title="Polymarket V1 Monitor", layout="wide")
st.markdown(DARK_CSS, unsafe_allow_html=True)
st.title("Polymarket V1 Dashboard")
st.caption("SQLite canonical runtime monitor with A–E alerts.")

st.sidebar.header("Controls")
lookback_n = st.sidebar.slider("Rows", 20, 1000, 200)

state = q("SELECT as_of_ts, is_frozen, reasons, mode FROM system_state ORDER BY as_of_ts DESC LIMIT 1")
if bool(safe_first(state, "is_frozen", 0)):
    st.markdown(f'<div class="alert"><b>TRADING FROZEN</b> — {safe_first(state, "reasons", "")}</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="ok"><b>OK</b> — no active freeze</div>', unsafe_allow_html=True)

kpi = q(
    """
    SELECT
      (SELECT COUNT(*) FROM decisions) AS decisions,
      (SELECT COUNT(*) FROM orders) AS orders,
      (SELECT COUNT(*) FROM fills) AS fills,
      (SELECT AVG(confidence) FROM pstar WHERE valid=1) AS avg_pstar_conf,
      (SELECT p95_send_ack_ms FROM latency_stats ORDER BY ts_ms DESC LIMIT 1) AS ack_p95
    """
)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Decisions", int(safe_first(kpi, "decisions", 0)))
c2.metric("Orders", int(safe_first(kpi, "orders", 0)))
c3.metric("Fills", int(safe_first(kpi, "fills", 0)))
c4.metric("P* Confidence", round(float(safe_first(kpi, "avg_pstar_conf", 0.0)), 3))
c5.metric("Ack p95 (ms)", round(float(safe_first(kpi, "ack_p95", 0.0)), 2))

tab_overview, tab_health, tab_inventory, tab_stats, tab_logs = st.tabs(
    ["Overview", "Health (A–E)", "Inventory & Quotes", "Statistics", "Logs"]
)

with tab_overview:
    st.subheader("Recent Decisions")
    decisions = q(f"SELECT ts_ms, market, token_id, action, reason_codes, p_hat FROM decisions ORDER BY ts_ms DESC LIMIT {lookback_n}")
    if not decisions.empty:
        decisions["ts"] = pd.to_datetime(decisions["ts_ms"], unit="ms", utc=True)
    st.dataframe(decisions, use_container_width=True, height=280)

    ev = q("SELECT p_hat FROM decisions WHERE p_hat IS NOT NULL ORDER BY ts_ms DESC LIMIT 2000")
    if not ev.empty:
        ev["bucket"] = pd.cut(ev["p_hat"], bins=20)
        dist = ev.groupby("bucket").size().reset_index(name="count")
        dist["mid"] = dist["bucket"].apply(lambda x: (x.left + x.right) / 2)
        chart = alt.Chart(dist).mark_bar().encode(
            x=alt.X("mid:Q", title="p_hat"),
            y=alt.Y("count:Q", title="Count"),
        ).properties(height=180)
        st.altair_chart(chart, use_container_width=True)

with tab_health:
    st.subheader("Latest Alerts")
    alerts = q(f"SELECT ts_ms, severity, code, message FROM alerts ORDER BY ts_ms DESC LIMIT {lookback_n}")
    if not alerts.empty:
        alerts["ts"] = pd.to_datetime(alerts["ts_ms"], unit="ms", utc=True)
    st.dataframe(alerts, use_container_width=True, height=240)

    st.subheader("P* Health")
    pstar = q("SELECT ts_ms, symbol, disagreement_bps, confidence, age_spot_ms, age_perp_ms, valid FROM pstar_stats ORDER BY ts_ms DESC LIMIT 500")
    if not pstar.empty:
        pstar["ts"] = pd.to_datetime(pstar["ts_ms"], unit="ms", utc=True)
        chart = alt.Chart(pstar).mark_line().encode(
            x="ts:T",
            y="disagreement_bps:Q",
            color="symbol:N",
            tooltip=["ts", "symbol", "disagreement_bps", "confidence", "age_spot_ms", "age_perp_ms", "valid"],
        ).properties(height=220)
        st.altair_chart(chart, use_container_width=True)

with tab_inventory:
    st.subheader("Inventory Snapshot")
    inv = q(
        """
        SELECT i.ts_ms, i.token_id, i.yes_qty, i.no_qty, i.source
        FROM inventory i
        INNER JOIN (
          SELECT token_id, MAX(ts_ms) AS max_ts
          FROM inventory
          GROUP BY token_id
        ) x ON i.token_id = x.token_id AND i.ts_ms = x.max_ts
        ORDER BY i.token_id
        """
    )
    st.dataframe(inv, use_container_width=True, height=260)

    st.subheader("Recent Order Events")
    orders = q(f"SELECT ts_ms, order_id, token_id, side, price, qty, status, reason, fsm_state FROM orders ORDER BY ts_ms DESC LIMIT {lookback_n}")
    st.dataframe(orders, use_container_width=True, height=260)

with tab_stats:
    st.subheader("Latency")
    lat = q("SELECT ts_ms, p50_send_ack_ms, p95_send_ack_ms, p50_ack_fill_ms, p95_ack_fill_ms, ws_lag_ms FROM latency_stats ORDER BY ts_ms DESC LIMIT 500")
    if not lat.empty:
        lat["ts"] = pd.to_datetime(lat["ts_ms"], unit="ms", utc=True)
        melt = lat.melt(
            id_vars=["ts"],
            value_vars=["p50_send_ack_ms", "p95_send_ack_ms", "p50_ack_fill_ms", "p95_ack_fill_ms", "ws_lag_ms"],
            var_name="metric",
            value_name="value",
        )
        chart = alt.Chart(melt).mark_line().encode(
            x="ts:T",
            y="value:Q",
            color="metric:N",
            tooltip=["ts", "metric", "value"],
        ).properties(height=260)
        st.altair_chart(chart, use_container_width=True)

    st.subheader("Decision Actions")
    actions = q("SELECT action, COUNT(*) AS n FROM decisions GROUP BY action")
    st.dataframe(actions, use_container_width=True, height=200)

with tab_logs:
    logs = q(f"SELECT ts_ms, level, msg FROM logs ORDER BY ts_ms DESC LIMIT {lookback_n}")
    if not logs.empty:
        logs["ts"] = pd.to_datetime(logs["ts_ms"], unit="ms", utc=True)
    st.dataframe(logs, use_container_width=True, height=400)
