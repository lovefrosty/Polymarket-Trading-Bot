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

EMPTY_LOG_MESSAGES = {
    "alerts": "No recent errors - system operating normally.",
    "breaches": "No gate breaches in the selected window.",
    "logs": "No warning/error logs for this window.",
}

SCOREBOARD_LOOKBACK_ROWS = 500
FREEZE_TREND_HOURS = 24
MS_PER_HOUR = 3_600_000


def _sanitize_df_for_view(
    df: pd.DataFrame,
    view_mode: str,
    label_token_fn: Optional[Callable[[Any, Any], Dict[str, str]]] = None,
    reason_humanizer: Optional[Callable[[Optional[str], Optional[str]], str]] = None,
) -> pd.DataFrame:
    if str(view_mode).lower() == "developer":
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
    is_dev = str(view_mode).lower() == "developer"
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

    if not is_dev:
        gate_help = {
            "A": "Price feed validity",
            "B": "Feature/decision causality",
            "C": "Book spread and depth",
            "D": "Hedge completeness",
            "E": "Latency and signal age",
        }
        blockers = [gate for gate, status in gate_map.items() if status.status == "CRITICAL"]
        warns = [gate for gate, status in gate_map.items() if status.status == "WARN"]
        if blockers:
            blocked_text = ", ".join(sorted(blockers))
            st.markdown(
                f"<div class='alert'><b>Tradeability:</b> WAIT | Blocked by gate(s): {blocked_text}</div>",
                unsafe_allow_html=True,
            )
        elif warns:
            warn_text = ", ".join(sorted(warns))
            st.markdown(
                f"<div class='warn'><b>Tradeability:</b> CAUTION | Degraded gate(s): {warn_text}</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div class='ok'><b>Tradeability:</b> YES | All A-E gates healthy for this window</div>",
                unsafe_allow_html=True,
            )

        summary_rows = []
        for gate in ["A", "B", "C", "D", "E"]:
            status = gate_map[gate]
            if status.status == "CRITICAL":
                action = "Wait - hard block active"
            elif status.status == "WARN":
                action = "Monitor and wait for improvement"
            else:
                action = "Healthy"
            summary_rows.append(
                {
                    "Gate": gate,
                    "State": status.status,
                    "Checks": gate_help.get(gate, ""),
                    "Current": status.summary,
                    "Action": action,
                }
            )
        st.subheader("A-E Summary")
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", height=210)

        pstar_latest = query_df(
            """
            SELECT ts_ms, symbol, disagreement_bps, age_spot_ms, age_perp_ms, valid
            FROM pstar_stats
            ORDER BY ts_ms DESC
            LIMIT 1
            """
        )
        st.subheader("A: Price feed")
        if pstar_latest.empty:
            st.info("No recent price feed sample.")
        else:
            row = pstar_latest.iloc[0]
            valid = int(row.get("valid") or 0) == 1
            age_spot = float(row.get("age_spot_ms") or 0.0) / 1000.0
            age_perp = float(row.get("age_perp_ms") or 0.0) / 1000.0
            disagree = row.get("disagreement_bps")
            validity_text = "valid" if valid else "invalid"
            disagree_text = f"{float(disagree):.1f} bps" if disagree is not None and not pd.isna(disagree) else "N/A"
            st.markdown(
                f"Latest feed is {validity_text}. spot_age={age_spot:.1f}s perp_age={age_perp:.1f}s disagreement={disagree_text}."
            )

        st.subheader("B: Causality")
        causality_counts = query_df(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN max_feature_ts_ms >= ts_ms THEN 1 ELSE 0 END) AS violations
            FROM decisions
            WHERE ts_ms >= ?
            """,
            (int(pd.Timestamp.utcnow().timestamp() * 1000) - filters.window_minutes * 60_000,),
        )
        total = int(causality_counts.iloc[0]["total"]) if not causality_counts.empty and causality_counts.iloc[0]["total"] is not None else 0
        violations = int(causality_counts.iloc[0]["violations"]) if not causality_counts.empty and causality_counts.iloc[0]["violations"] is not None else 0
        if violations > 0:
            st.markdown(f"<div class='alert'>Detected {violations} causality violation(s) out of {total} decisions.</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='ok'>No causality violations in the selected window.</div>", unsafe_allow_html=True)

        st.subheader("C: Book quality")
        book_latest = query_df(
            """
            SELECT ts_ms, spread_bps, depth_at_qty_buy, depth_at_qty_sell, book_health
            FROM microstructure_stats
            ORDER BY ts_ms DESC
            LIMIT 1
            """
        )
        if book_latest.empty:
            st.info("No recent order book snapshot.")
        else:
            row = book_latest.iloc[0]
            spread = row.get("spread_bps")
            spread_text = f"{float(spread):.1f} bps" if spread is not None and not pd.isna(spread) else "N/A"
            buy = row.get("depth_at_qty_buy")
            sell = row.get("depth_at_qty_sell")
            depth_text = "N/A"
            if buy is not None and sell is not None and not pd.isna(buy) and not pd.isna(sell):
                depth_text = f"{(float(buy) + float(sell)) / 2.0:.3f}"
            health = str(row.get("book_health") or "UNKNOWN").upper()
            st.markdown(f"Spread={spread_text} | Depth={depth_text} | Book health={health}")

        st.subheader("D: Hedge risk")
        d_state = gate_map.get("D")
        if d_state is not None:
            completeness = d_state.details.get("hedge_completeness")
            one_leg = int(d_state.details.get("one_leg_alerts") or 0)
            if completeness is not None:
                st.markdown(f"Hedge completeness={float(completeness) * 100.0:.1f}% | one-leg alerts={one_leg}")
            else:
                st.markdown(f"One-leg alerts={one_leg}")

        st.subheader("E: Latency")
        e_state = gate_map.get("E")
        if e_state is not None:
            ws_lag = e_state.details.get("ws_lag_p95_ms")
            ack = e_state.details.get("ack_p95_ms")
            sig = e_state.details.get("signal_age_p95_ms")
            ws_text = f"{float(ws_lag):.1f} ms" if ws_lag is not None else "N/A"
            ack_text = f"{float(ack):.1f} ms" if ack is not None else "N/A"
            sig_text = f"{float(sig):.1f} ms" if sig is not None else "N/A"
            st.markdown(f"ws_lag p95={ws_text} | ack p95={ack_text} | signal_age p95={sig_text}")

        st.subheader("Recent critical alerts")
        critical = query_df(
            """
            SELECT ts_ms, code, message
            FROM alerts
            WHERE LOWER(severity)='critical'
            ORDER BY ts_ms DESC
            LIMIT 10
            """
        )
        if critical.empty:
            st.info("No recent critical alerts - system operating normally.")
        else:
            critical["ts"] = pd.to_datetime(critical["ts_ms"], unit="ms", utc=True)
            if reason_humanizer is not None:
                critical["reason"] = critical.apply(lambda row: reason_humanizer(row.get("code"), row.get("message")), axis=1)
            critical = critical.drop(columns=["message"], errors="ignore")
            st.dataframe(critical, width="stretch", height=180)
        elapsed = (perf_counter() - t0) * 1000.0
        if elapsed > panel_budget_ms:
            st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
        return

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
    is_dev = str(view_mode).lower() == "developer"
    if is_dev:
        alerts_view = _sanitize_df_for_view(alerts, view_mode, reason_humanizer=reason_humanizer)
        if alerts_view.empty:
            st.info(EMPTY_LOG_MESSAGES["alerts"])
        else:
            st.dataframe(alerts_view, width="stretch", height=220)
    else:
        trader_alerts = pd.DataFrame()
        if not alerts.empty:
            trader_alerts = pd.DataFrame(
                {
                    "Time": alerts["ts"].astype(str),
                    "Severity": alerts.get("severity", pd.Series(dtype=str)).astype(str).str.upper(),
                    "Message": alerts.get("reason", alerts.get("message", pd.Series(dtype=str))).astype(str),
                }
            )
        if trader_alerts.empty:
            st.info(EMPTY_LOG_MESSAGES["alerts"])
        else:
            st.dataframe(trader_alerts.head(min(filters.lookback_rows, 50)), width="stretch", height=220)
            with st.expander("Technical details", expanded=False):
                raw_cols = [col for col in ["ts", "severity", "code", "message"] if col in alerts.columns]
                st.dataframe(alerts[raw_cols].head(50), width="stretch", height=220)

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
    if is_dev:
        breaches_view = _sanitize_df_for_view(breaches, view_mode, reason_humanizer=reason_humanizer)
        if breaches_view.empty:
            st.info(EMPTY_LOG_MESSAGES["breaches"])
        else:
            st.dataframe(breaches_view, width="stretch", height=180)
    else:
        if breaches.empty:
            st.info(EMPTY_LOG_MESSAGES["breaches"])
        else:
            trader_breaches = pd.DataFrame(
                {
                    "Time": breaches["ts"].astype(str),
                    "Severity": "GATE",
                    "Message": breaches.get("reason", breaches.get("message", pd.Series(dtype=str))).astype(str),
                }
            )
            st.dataframe(trader_breaches.head(min(filters.lookback_rows, 50)), width="stretch", height=180)

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
    if is_dev:
        if logs.empty:
            st.info(EMPTY_LOG_MESSAGES["logs"])
        else:
            st.dataframe(logs, width="stretch", height=220)
    else:
        if logs.empty:
            st.info(EMPTY_LOG_MESSAGES["logs"])
        else:
            trader_logs = pd.DataFrame(
                {
                    "Time": logs["ts"].astype(str),
                    "Severity": logs.get("level", pd.Series(dtype=str)).astype(str).str.upper(),
                    "Message": logs.get("msg", pd.Series(dtype=str)).astype(str),
                }
            )
            st.dataframe(trader_logs.head(min(filters.lookback_rows, 50)), width="stretch", height=220)
            with st.expander("Log details", expanded=False):
                st.dataframe(logs.head(50), width="stretch", height=220)

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
        LIMIT ?
        """
        ,
        (SCOREBOARD_LOOKBACK_ROWS,),
    )
    # Aggregate over bounded recent windows instead of materializing full frames.
    pstar = query_df(
        """
        SELECT
          AVG(CASE WHEN valid = 0 THEN 1.0 ELSE 0.0 END) AS invalid_ratio,
          AVG(CASE WHEN disagreement_bps > 50.0 THEN 1.0 ELSE 0.0 END) AS disagree_ratio
        FROM (
          SELECT valid, disagreement_bps
          FROM pstar_stats
          ORDER BY ts_ms DESC
          LIMIT ?
        )
        """
        ,
        (SCOREBOARD_LOOKBACK_ROWS,),
    )
    rec = query_df(
        """
        SELECT
          AVG(CASE WHEN outside_tolerance = 1 THEN 1.0 ELSE 0.0 END) AS outside_tolerance_ratio,
          AVG(CASE WHEN unresolved_mismatch_count > 0 THEN 1.0 ELSE 0.0 END) AS unresolved_ratio
        FROM (
          SELECT outside_tolerance, unresolved_mismatch_count
          FROM reconciliation_stats
          ORDER BY ts_ms DESC
          LIMIT ?
        )
        """
        ,
        (SCOREBOARD_LOOKBACK_ROWS,),
    )
    lookback_start_ms = _lookback_start_ms(FREEZE_TREND_HOURS)
    state_ratio = query_df(
        """
        SELECT AVG(CASE WHEN is_frozen = 1 THEN 1.0 ELSE 0.0 END) AS freeze_ratio
        FROM system_state
        WHERE as_of_ts >= ?
        """
        ,
        (lookback_start_ms,),
    )
    # Hourly aggregate trend queries lower dashboard refresh cost while preserving visible semantics.
    alerts_hourly = query_df(
        """
        SELECT
          ((ts_ms / ?) * ?) AS hour_ms,
          COUNT(*) AS alerts_total,
          SUM(
            CASE
              WHEN UPPER(COALESCE(code, '')) LIKE '%FREEZE%' THEN 1
              WHEN SUBSTR(UPPER(COALESCE(code, '')), 1, 2) IN ('A_', 'B_', 'C_', 'D_', 'E_') THEN 1
              ELSE 0
            END
          ) AS freeze_related_alerts
        FROM alerts
        WHERE ts_ms >= ?
        GROUP BY hour_ms
        ORDER BY hour_ms DESC
        LIMIT ?
        """
        ,
        (MS_PER_HOUR, MS_PER_HOUR, lookback_start_ms, FREEZE_TREND_HOURS),
    )
    state_hourly = query_df(
        """
        SELECT
          ((as_of_ts / ?) * ?) AS hour_ms,
          SUM(CASE WHEN is_frozen = 1 THEN 1 ELSE 0 END) AS frozen_samples
        FROM system_state
        WHERE as_of_ts >= ?
        GROUP BY hour_ms
        ORDER BY hour_ms DESC
        LIMIT ?
        """
        ,
        (MS_PER_HOUR, MS_PER_HOUR, lookback_start_ms, FREEZE_TREND_HOURS),
    )

    ws_lag = _safe_float(lat["p95_ws_lag_ms"].median()) if not lat.empty and "p95_ws_lag_ms" in lat.columns else 0.0
    ack = _safe_float(lat["p95_send_ack_ms"].median()) if not lat.empty and "p95_send_ack_ms" in lat.columns else 0.0
    signal = _safe_float(lat["p95_signal_age_ms"].median()) if not lat.empty and "p95_signal_age_ms" in lat.columns else 0.0
    invalid_ratio = _safe_float(pstar.iloc[0].get("invalid_ratio")) if not pstar.empty else 0.0
    disagree_ratio = _safe_float(pstar.iloc[0].get("disagree_ratio")) if not pstar.empty else 0.0
    outside_ratio = rec.iloc[0].get("outside_tolerance_ratio") if not rec.empty else None
    unresolved_ratio = rec.iloc[0].get("unresolved_ratio") if not rec.empty else None
    mismatch_ratio = _safe_float(outside_ratio if outside_ratio is not None and not pd.isna(outside_ratio) else unresolved_ratio)
    freeze_ratio = _safe_float(state_ratio.iloc[0].get("freeze_ratio")) if not state_ratio.empty else 0.0

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

    freeze_trend = _merge_hourly_trend(alerts_hourly, state_hourly)
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


def _lookback_start_ms(hours: int) -> int:
    now_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
    return max(0, now_ms - int(hours) * MS_PER_HOUR)


def _merge_hourly_trend(alerts_hourly: pd.DataFrame, state_hourly: pd.DataFrame) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}

    if not alerts_hourly.empty:
        for _, row in alerts_hourly.iterrows():
            hour_ms = _safe_int(row.get("hour_ms"))
            if hour_ms is None:
                continue
            ts = _bucket_hour(hour_ms)
            if ts is None:
                continue
            bucket = buckets.setdefault(
                ts,
                {"hour_ms": hour_ms, "hour_utc": ts, "alerts_total": 0, "freeze_related_alerts": 0, "frozen_samples": 0},
            )
            bucket["alerts_total"] = int(row.get("alerts_total") or 0)
            bucket["freeze_related_alerts"] = int(row.get("freeze_related_alerts") or 0)

    if not state_hourly.empty:
        for _, row in state_hourly.iterrows():
            hour_ms = _safe_int(row.get("hour_ms"))
            if hour_ms is None:
                continue
            ts = _bucket_hour(hour_ms)
            if ts is None:
                continue
            bucket = buckets.setdefault(
                ts,
                {"hour_ms": hour_ms, "hour_utc": ts, "alerts_total": 0, "freeze_related_alerts": 0, "frozen_samples": 0},
            )
            bucket["frozen_samples"] = int(row.get("frozen_samples") or 0)

    rows = list(buckets.values())
    rows.sort(key=lambda item: int(item.get("hour_ms") or 0), reverse=True)
    out: List[Dict[str, Any]] = []
    for item in rows[:FREEZE_TREND_HOURS]:
        out.append(
            {
                "hour_utc": item["hour_utc"],
                "alerts_total": item["alerts_total"],
                "freeze_related_alerts": item["freeze_related_alerts"],
                "frozen_samples": item["frozen_samples"],
            }
        )
    return out


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
