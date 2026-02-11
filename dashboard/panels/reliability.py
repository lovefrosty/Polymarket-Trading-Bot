from __future__ import annotations

from time import perf_counter
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, HealthGateStatus, PanelDependency
from dashboard.data_access import query_df, require_sources
from core.metrics import classify_reliability_rows

HEALTH_DEP = PanelDependency(
    panel_id="health_a_to_e",
    required_sources=("pstar_stats", "decisions", "alerts", "latency_stats"),
    optional_sources=("book_health_stats", "fills", "decision_ticks", "reconciliation_stats", "system_state"),
)

LOGS_DEP = PanelDependency(
    panel_id="logs",
    required_sources=("alerts",),
    optional_sources=("logs",),
)


def render_health_panel(filters: DashboardFilters, gate_map: Dict[str, HealthGateStatus], panel_budget_ms: int = 400) -> None:
    assert st is not None
    t0 = perf_counter()
    ok, missing_required, missing_optional = require_sources(
        HEALTH_DEP.required_sources,
        HEALTH_DEP.optional_sources,
    )
    if not ok:
        st.markdown(
            f"<div class='warn'><b>DEGRADED</b> missing required table(s): {', '.join(missing_required)}</div>",
            unsafe_allow_html=True,
        )
        return
    if missing_optional:
        st.caption(f"DEGRADED optional missing: {', '.join(missing_optional)}")

    cards = st.columns(5)
    for idx, gate in enumerate(["A", "B", "C", "D", "E"]):
        status = gate_map[gate]
        klass = "ok"
        if status.status == "CRITICAL":
            klass = "alert"
        elif status.status == "WARN":
            klass = "warn"
        cards[idx].markdown(
            f'<div class="{klass}"><b>{status.gate}</b> {status.status}<br/><small>{status.summary}</small></div>',
            unsafe_allow_html=True,
        )

    st.subheader("A: P* validity")
    pstar = query_df(
        "SELECT ts_ms, symbol, disagreement_bps, confidence, age_spot_ms, age_perp_ms, valid FROM pstar_stats ORDER BY ts_ms DESC LIMIT 500"
    )
    if not pstar.empty:
        pstar["ts"] = pd.to_datetime(pstar["ts_ms"], unit="ms", utc=True)
    st.dataframe(pstar, use_container_width=True, height=200)

    st.subheader("B: Causality")
    causality = query_df(
        """
        SELECT ts_ms, decision_id, market, token_id, max_feature_ts_ms, decision_ts_event_ms,
               CASE WHEN max_feature_ts_ms >= ts_ms THEN 1 ELSE 0 END AS violation
        FROM decisions
        ORDER BY ts_ms DESC
        LIMIT ?
        """,
        (filters.lookback_rows,),
    )
    if not causality.empty:
        causality["ts"] = pd.to_datetime(causality["ts_ms"], unit="ms", utc=True)
    st.dataframe(causality, use_container_width=True, height=180)

    st.subheader("C: Book")
    book = query_df(
        """
        SELECT ts_ms, token_id, book_health_state, book_age_p50_ms, book_age_p95_ms, ws_recv_rate_msgs_min
        FROM book_health_stats
        ORDER BY ts_ms DESC
        LIMIT 500
        """
    )
    if not book.empty:
        book["ts"] = pd.to_datetime(book["ts_ms"], unit="ms", utc=True)
    st.dataframe(book, use_container_width=True, height=180)

    st.subheader("D: Hedge / one-leg risk")
    d_alerts = query_df(
        """
        SELECT ts_ms, severity, code, message
        FROM alerts
        WHERE UPPER(code) LIKE '%ONE_LEG%' OR UPPER(code) LIKE '%HEDGE%'
        ORDER BY ts_ms DESC
        LIMIT ?
        """,
        (filters.lookback_rows,),
    )
    if not d_alerts.empty:
        d_alerts["ts"] = pd.to_datetime(d_alerts["ts_ms"], unit="ms", utc=True)
    st.dataframe(d_alerts, use_container_width=True, height=160)

    st.subheader("E: Latency")
    lat = query_df(
        """
        SELECT ts_ms, p50_ws_lag_ms, p95_ws_lag_ms, p50_send_ack_ms, p95_send_ack_ms,
               p50_signal_age_ms, p95_signal_age_ms
        FROM latency_stats
        ORDER BY ts_ms DESC
        LIMIT 500
        """
    )
    if not lat.empty:
        lat["ts"] = pd.to_datetime(lat["ts_ms"], unit="ms", utc=True)
    st.dataframe(lat, use_container_width=True, height=200)

    st.subheader("Reliability Scoreboard")
    scoreboard = compute_reliability_scoreboard(latency_df=lat)
    rows = scoreboard.get("rows") or []
    if rows:
        score_df = pd.DataFrame(rows)
        st.dataframe(score_df, use_container_width=True, height=180)
        top = rows[0]
        st.markdown(
            f"<div class='warn'><b>Top degradation source:</b> {top.get('source')} | "
            f"score={top.get('score')} | status={top.get('status')} | reasons={', '.join(top.get('reasons') or [])}</div>",
            unsafe_allow_html=True,
        )
    else:
        st.caption("DEGRADED reliability scoreboard unavailable (insufficient telemetry).")

    trend = scoreboard.get("freeze_trend") or []
    st.subheader("Freeze trend (last 24h)")
    if trend:
        st.dataframe(pd.DataFrame(trend), use_container_width=True, height=160)
    else:
        st.caption("DEGRADED freeze trend unavailable.")

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")


def render_logs_panel(filters: DashboardFilters, start_ts: int, panel_budget_ms: int = 400) -> None:
    assert st is not None
    t0 = perf_counter()
    ok, missing_required, missing_optional = require_sources(
        LOGS_DEP.required_sources,
        LOGS_DEP.optional_sources,
    )
    if not ok:
        st.markdown(
            f"<div class='warn'><b>DEGRADED</b> missing required table(s): {', '.join(missing_required)}</div>",
            unsafe_allow_html=True,
        )
        return
    if missing_optional:
        st.caption(f"DEGRADED optional missing: {', '.join(missing_optional)}")

    if filters.severity_filter == "ALL":
        alerts = query_df(
            "SELECT ts_ms, severity, code, message FROM alerts WHERE ts_ms >= ? ORDER BY ts_ms DESC LIMIT ?",
            (start_ts, filters.lookback_rows),
        )
    else:
        alerts = query_df(
            "SELECT ts_ms, severity, code, message FROM alerts WHERE ts_ms >= ? AND LOWER(severity)=? ORDER BY ts_ms DESC LIMIT ?",
            (start_ts, filters.severity_filter.lower(), filters.lookback_rows),
        )
    if not alerts.empty:
        alerts["ts"] = pd.to_datetime(alerts["ts_ms"], unit="ms", utc=True)
    st.subheader("Recent WARN/ERROR alerts")
    st.dataframe(alerts, use_container_width=True, height=220)

    breaches = query_df(
        """
        SELECT ts_ms, code, message
        FROM alerts
        WHERE ts_ms >= ?
          AND (code LIKE 'A_%' OR code LIKE 'B_%' OR code LIKE 'C_%' OR code LIKE 'D_%' OR code LIKE 'E_%')
        ORDER BY ts_ms DESC
        LIMIT ?
        """,
        (start_ts, filters.lookback_rows),
    )
    if not breaches.empty:
        breaches["ts"] = pd.to_datetime(breaches["ts_ms"], unit="ms", utc=True)
    st.subheader("Gate breaches timeline")
    st.dataframe(breaches, use_container_width=True, height=180)

    logs = query_df(
        """
        SELECT ts_ms, level, msg
        FROM logs
        WHERE ts_ms >= ? AND (UPPER(level) IN ('WARN', 'ERROR') OR LOWER(msg) LIKE '%manifest%')
        ORDER BY ts_ms DESC
        LIMIT ?
        """,
        (start_ts, filters.lookback_rows),
    )
    if not logs.empty:
        logs["ts"] = pd.to_datetime(logs["ts_ms"], unit="ms", utc=True)
    st.subheader("Logs")
    st.dataframe(logs, use_container_width=True, height=220)

    manifest = logs[logs["msg"].str.contains("manifest", case=False, na=False)] if not logs.empty else pd.DataFrame()
    last_manifest_hash = "N/A"
    if not manifest.empty:
        last_manifest_hash = str(manifest.iloc[0]["msg"])
    st.metric("Last manifest hash", last_manifest_hash)

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")


def compute_reliability_scoreboard(latency_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    lat = latency_df if latency_df is not None else query_df(
        """
        SELECT ts_ms, p95_ws_lag_ms, p95_send_ack_ms, p95_signal_age_ms
        FROM latency_stats
        ORDER BY ts_ms DESC
        LIMIT 500
        """
    )
    pstar = query_df(
        """
        SELECT ts_ms, disagreement_bps, valid
        FROM pstar_stats
        ORDER BY ts_ms DESC
        LIMIT 500
        """
    )
    rec = query_df(
        """
        SELECT ts_ms, outside_tolerance, unresolved_mismatch_count
        FROM reconciliation_stats
        ORDER BY ts_ms DESC
        LIMIT 500
        """
    )
    alerts = query_df(
        """
        SELECT ts_ms, severity, code
        FROM alerts
        WHERE ts_ms >= (CAST(strftime('%s','now') AS INTEGER) - 86400) * 1000
        ORDER BY ts_ms DESC
        """
    )
    state = query_df(
        """
        SELECT as_of_ts, is_frozen, reasons
        FROM system_state
        WHERE as_of_ts >= (CAST(strftime('%s','now') AS INTEGER) - 86400) * 1000
        ORDER BY as_of_ts DESC
        """
    )

    ws_lag = _safe_float(lat["p95_ws_lag_ms"].median()) if not lat.empty and "p95_ws_lag_ms" in lat.columns else 0.0
    ack = _safe_float(lat["p95_send_ack_ms"].median()) if not lat.empty and "p95_send_ack_ms" in lat.columns else 0.0
    signal = _safe_float(lat["p95_signal_age_ms"].median()) if not lat.empty and "p95_signal_age_ms" in lat.columns else 0.0
    invalid_ratio = 0.0
    disagree_ratio = 0.0
    if not pstar.empty:
        invalid_ratio = float((pstar["valid"] == 0).mean()) if "valid" in pstar.columns else 0.0
        if "disagreement_bps" in pstar.columns:
            disagree_ratio = float((pstar["disagreement_bps"] > 50.0).mean())
    mismatch_ratio = 0.0
    if not rec.empty:
        if "outside_tolerance" in rec.columns:
            mismatch_ratio = float((rec["outside_tolerance"] == 1).mean())
        elif "unresolved_mismatch_count" in rec.columns:
            mismatch_ratio = float((rec["unresolved_mismatch_count"] > 0).mean())
    freeze_ratio = 0.0
    if not state.empty and "is_frozen" in state.columns:
        freeze_ratio = float((state["is_frozen"] == 1).mean())

    rows = classify_reliability_rows(
        {
            "reference_pipeline": {
                "invalid_ratio": invalid_ratio + disagree_ratio * 0.5,
                "freeze_ratio": freeze_ratio * 0.25,
            },
            "market_data_ws": {
                "ws_lag_ms": ws_lag,
                "freeze_ratio": freeze_ratio * 0.25,
            },
            "execution_path": {
                "ack_ms": ack,
                "freeze_ratio": freeze_ratio * 0.25,
            },
            "reconciliation": {
                "mismatch_ratio": mismatch_ratio,
                "freeze_ratio": freeze_ratio * 0.25,
            },
            "signal_pipeline": {
                "ws_lag_ms": signal,
                "invalid_ratio": disagree_ratio * 0.5,
            },
        }
    )

    freeze_trend = _freeze_trend(alerts, state)
    return {
        "rows": [
            {
                "source": row.source,
                "score": row.score,
                "status": row.status,
                "reasons": row.reasons,
            }
            for row in rows
        ],
        "freeze_trend": freeze_trend,
    }


def _freeze_trend(alerts: pd.DataFrame, state: pd.DataFrame) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}

    if not alerts.empty and "ts_ms" in alerts.columns:
        for _, row in alerts.iterrows():
            ts = _bucket_hour(_safe_int(row.get("ts_ms")))
            if ts is None:
                continue
            bucket = buckets.setdefault(ts, {"hour_utc": ts, "alerts_total": 0, "freeze_related_alerts": 0, "frozen_samples": 0})
            bucket["alerts_total"] += 1
            code = str(row.get("code") or "").upper()
            if "FREEZE" in code or code.startswith(("A_", "B_", "C_", "D_", "E_")):
                bucket["freeze_related_alerts"] += 1

    if not state.empty:
        ts_col = "as_of_ts" if "as_of_ts" in state.columns else None
        if ts_col is not None and "is_frozen" in state.columns:
            for _, row in state.iterrows():
                ts = _bucket_hour(_safe_int(row.get(ts_col)))
                if ts is None:
                    continue
                bucket = buckets.setdefault(ts, {"hour_utc": ts, "alerts_total": 0, "freeze_related_alerts": 0, "frozen_samples": 0})
                if int(row.get("is_frozen") or 0) == 1:
                    bucket["frozen_samples"] += 1

    rows = list(buckets.values())
    rows.sort(key=lambda item: item["hour_utc"], reverse=True)
    return rows[:24]


def _bucket_hour(ts_ms: Optional[int]) -> Optional[str]:
    if ts_ms is None:
        return None
    try:
        hour_ms = int(ts_ms // 3_600_000 * 3_600_000)
        return pd.to_datetime(hour_ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:00")
    except Exception:
        return None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
