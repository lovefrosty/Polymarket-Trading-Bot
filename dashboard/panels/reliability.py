from __future__ import annotations

from time import perf_counter
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, HealthGateStatus, PanelDependency
from dashboard.data_access import query_df, query_evidence_rows, require_sources
from core.metrics import classify_reliability_rows

HEALTH_DEP = PanelDependency(
    panel_id="health_a_to_e",
    required_sources=("pstar_stats", "decisions", "alerts", "latency_stats"),
    optional_sources=(
        "book_health_stats",
        "fills",
        "decision_ticks",
        "reconciliation_stats",
        "system_state",
        "execution_quality_stats",
        "queue_quality_stats",
        "liveness_stats",
        "recovery_events",
    ),
)

LOGS_DEP = PanelDependency(
    panel_id="logs",
    required_sources=("alerts",),
    optional_sources=("logs",),
)


def _sanitize_df_for_view(
    df: pd.DataFrame,
    view_mode: str,
    label_token_fn: Optional[Callable[[Any, Any], Dict[str, str]]] = None,
    reason_humanizer: Optional[Callable[[Optional[str], Optional[str]], str]] = None,
) -> pd.DataFrame:
    if df.empty or str(view_mode).lower() == "developer":
        return df
    out = df.copy()
    if "token_id" in out.columns:
        if label_token_fn is not None:
            out["Outcome"] = out["token_id"].apply(lambda token: label_token_fn(None, token).get("outcome_label", "Outcome"))
            out["Market"] = out["token_id"].apply(lambda token: label_token_fn(None, token).get("market_label", "Unknown market"))
        out = out.drop(columns=["token_id"], errors="ignore")
    out = out.drop(columns=["decision_id", "order_id", "event_id"], errors="ignore")
    if reason_humanizer is not None and "code" in out.columns:
        out["reason"] = out.apply(lambda row: reason_humanizer(row.get("code"), row.get("message")), axis=1)
    return out


def render_health_panel(
    filters: DashboardFilters,
    gate_map: Dict[str, HealthGateStatus],
    panel_budget_ms: int = 400,
    view_mode: str = "developer",
    label_token_fn: Optional[Callable[[Any, Any], Dict[str, str]]] = None,
    reason_humanizer: Optional[Callable[[Optional[str], Optional[str]], str]] = None,
    allow_widgets: bool = True,
) -> None:
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
    st.dataframe(_sanitize_df_for_view(pstar, view_mode, label_token_fn, reason_humanizer), width="stretch", height=200)

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
    st.dataframe(_sanitize_df_for_view(causality, view_mode, label_token_fn, reason_humanizer), width="stretch", height=180)

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
    st.dataframe(_sanitize_df_for_view(book, view_mode, label_token_fn, reason_humanizer), width="stretch", height=180)

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
    st.dataframe(_sanitize_df_for_view(d_alerts, view_mode, label_token_fn, reason_humanizer), width="stretch", height=160)

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
    st.dataframe(_sanitize_df_for_view(lat, view_mode, label_token_fn, reason_humanizer), width="stretch", height=200)

    st.subheader("Critical alerts (latest 10)")
    critical = query_df(
        """
        SELECT ts_ms, code, message
        FROM alerts
        WHERE LOWER(severity)='critical'
        ORDER BY ts_ms DESC
        LIMIT 10
        """
    )
    if not critical.empty:
        critical["ts"] = pd.to_datetime(critical["ts_ms"], unit="ms", utc=True)
    st.dataframe(_sanitize_df_for_view(critical, view_mode, label_token_fn, reason_humanizer), width="stretch", height=180)

    st.subheader("Reason Drilldown")
    active_reasons_df = query_df(
        """
        SELECT code, severity, ts_ms
        FROM alerts
        ORDER BY ts_ms DESC
        LIMIT 100
        """
    )
    if not active_reasons_df.empty and "code" in active_reasons_df.columns:
        codes = [str(code) for code in active_reasons_df["code"].dropna().astype(str).tolist()]
        unique_codes = sorted(set(codes))
        if allow_widgets:
            selected_reason = st.selectbox("Active reason code", unique_codes, index=0, key="health_reason_drilldown")
        else:
            selected_reason = unique_codes[0]
            st.caption(f"Reason drilldown: {selected_reason} (auto-refresh mode)")
        reason_rows = active_reasons_df[active_reasons_df["code"] == selected_reason]
        latest_ts = int(reason_rows["ts_ms"].max()) if not reason_rows.empty else int(pd.Timestamp.utcnow().timestamp() * 1000)
        start_ts = int(max(0, latest_ts - filters.window_minutes * 60_000))
        evidence = query_evidence_rows(start_ts_ms=start_ts, end_ts_ms=latest_ts, market=filters.selected_market, token_id=filters.selected_token, limit=200)
        if not evidence.empty and "reason_code" in evidence.columns:
            evidence = evidence[evidence["reason_code"].astype(str).str.contains(selected_reason, na=False)]
        if evidence.empty:
            st.caption(f"DEGRADED no evidence rows found for reason={selected_reason}")
        else:
            if "ts_ms" in evidence.columns:
                evidence["ts"] = pd.to_datetime(evidence["ts_ms"], unit="ms", utc=True)
            st.dataframe(_sanitize_df_for_view(evidence, view_mode, label_token_fn, reason_humanizer), width="stretch", height=180)
    else:
        st.caption("DEGRADED no active reason codes available.")

    st.subheader("Execution quality")
    exec_quality = query_df(
        """
        SELECT ts_ms, token_id, sample_count, p50_realized_spread_bps, p95_realized_spread_bps,
               p50_markout_5s_bps, p95_markout_5s_bps, p50_net_edge_bps, p95_net_edge_bps
        FROM execution_quality_stats
        ORDER BY ts_ms DESC
        LIMIT 100
        """
    )
    if not exec_quality.empty:
        exec_quality["ts"] = pd.to_datetime(exec_quality["ts_ms"], unit="ms", utc=True)
    st.dataframe(_sanitize_df_for_view(exec_quality, view_mode, label_token_fn, reason_humanizer), width="stretch", height=180)

    st.subheader("Queue / fill quality")
    queue_quality = query_df(
        """
        SELECT ts_ms, token_id, post_only_reject_rate, cancel_to_fill_ratio,
               time_to_first_fill_p50_s, time_to_first_fill_p95_s,
               partial_fill_count, orders_per_min, cancels_per_min, fills_per_min
        FROM queue_quality_stats
        ORDER BY ts_ms DESC
        LIMIT 200
        """
    )
    if not queue_quality.empty:
        queue_quality["ts"] = pd.to_datetime(queue_quality["ts_ms"], unit="ms", utc=True)
    st.dataframe(_sanitize_df_for_view(queue_quality, view_mode, label_token_fn, reason_humanizer), width="stretch", height=180)

    st.subheader("Liveness")
    liveness = query_df(
        """
        SELECT ts_ms, mode, clock_drift_ms, sequence_gap_rate_per_min, ws_starvation_token_count,
               max_ws_starvation_ms, active_market_lag_ms, freeze_state, reason_codes
        FROM liveness_stats
        ORDER BY ts_ms DESC
        LIMIT 200
        """
    )
    if not liveness.empty:
        liveness["ts"] = pd.to_datetime(liveness["ts_ms"], unit="ms", utc=True)
    st.dataframe(_sanitize_df_for_view(liveness, view_mode, label_token_fn, reason_humanizer), width="stretch", height=180)

    st.subheader("Quarantine / freeze timeline")
    quarantine = query_df(
        """
        SELECT ts_ms, recovery_action, token_id, side, order_id
        FROM recovery_events
        WHERE recovery_action IN (
            'UNKNOWN_OPEN_ORDER_QUARANTINE',
            'STARTUP_QUOTING_INVARIANT_CHECK',
            'CANCEL_OPEN_QUOTE_ON_FREEZE',
            'MISSED_FILL_CORRECTION'
        )
        ORDER BY ts_ms DESC
        LIMIT 200
        """
    )
    if not quarantine.empty:
        quarantine["ts"] = pd.to_datetime(quarantine["ts_ms"], unit="ms", utc=True)
    st.dataframe(_sanitize_df_for_view(quarantine, view_mode, label_token_fn, reason_humanizer), width="stretch", height=180)

    st.subheader("Reliability Scoreboard")
    scoreboard = compute_reliability_scoreboard(latency_df=lat)
    rows = scoreboard.get("rows") or []
    if rows:
        score_df = pd.DataFrame(rows)
        st.dataframe(_sanitize_df_for_view(score_df, view_mode, label_token_fn, reason_humanizer), width="stretch", height=180)
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
        st.dataframe(pd.DataFrame(trend), width="stretch", height=160)
    else:
        st.caption("DEGRADED freeze trend unavailable.")

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")


def render_logs_panel(
    filters: DashboardFilters,
    start_ts: int,
    panel_budget_ms: int = 400,
    view_mode: str = "developer",
    reason_humanizer: Optional[Callable[[Optional[str], Optional[str]], str]] = None,
) -> None:
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
    if reason_humanizer is not None and "code" in alerts.columns:
        alerts["reason"] = alerts.apply(lambda row: reason_humanizer(row.get("code"), row.get("message")), axis=1)
    alerts = _sanitize_df_for_view(alerts, view_mode, reason_humanizer=reason_humanizer)
    st.dataframe(alerts, width="stretch", height=220)

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
    if reason_humanizer is not None and "code" in breaches.columns:
        breaches["reason"] = breaches.apply(lambda row: reason_humanizer(row.get("code"), row.get("message")), axis=1)
    breaches = _sanitize_df_for_view(breaches, view_mode, reason_humanizer=reason_humanizer)
    st.dataframe(breaches, width="stretch", height=180)

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
    st.dataframe(logs, width="stretch", height=220)

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
