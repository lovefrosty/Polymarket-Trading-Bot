from __future__ import annotations

from time import perf_counter
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, PanelDependency
from dashboard.data_access import query_df, require_sources

ROLLOVER_DEP = PanelDependency(
    panel_id="rollover",
    required_sources=("rollover_status",),
    optional_sources=("rollover_metrics", "discovery_requests"),
)


def _to_ts(df: pd.DataFrame, column: str = "ts_ms") -> pd.DataFrame:
    if not df.empty and column in df.columns:
        out = df.copy()
        out["ts"] = pd.to_datetime(out[column], unit="ms", utc=True)
        return out
    return df


def _metric_stats(rows: pd.DataFrame, metric_name: str) -> Dict[str, Optional[float]]:
    if rows.empty:
        return {"last": None, "p50": None, "p95": None}
    scoped = rows[rows["metric_name"] == metric_name].copy()
    if scoped.empty:
        return {"last": None, "p50": None, "p95": None}
    values = [float(v) for v in scoped["metric_value"].tolist() if v is not None and not pd.isna(v)]
    if not values:
        return {"last": None, "p50": None, "p95": None}
    ordered = sorted(values)
    p50 = ordered[int(max(0, min(len(ordered) - 1, round((len(ordered) - 1) * 0.5))))]
    p95 = ordered[int(max(0, min(len(ordered) - 1, round((len(ordered) - 1) * 0.95))))]
    return {"last": float(values[0]), "p50": float(p50), "p95": float(p95)}


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}"


def render_rollover_panel(filters: DashboardFilters, panel_budget_ms: int = 300) -> None:
    assert st is not None
    _ = filters
    t0 = perf_counter()
    ok, missing_required, missing_optional = require_sources(
        ROLLOVER_DEP.required_sources,
        ROLLOVER_DEP.optional_sources,
    )
    if not ok:
        st.markdown(
            f"<div class='warn'><b>DEGRADED</b> missing required table(s): {', '.join(missing_required)}</div>",
            unsafe_allow_html=True,
        )
        return
    if missing_optional:
        st.caption(f"DEGRADED optional missing: {', '.join(missing_optional)}")

    latest = query_df(
        """
        SELECT ts_ms, event_type, market_slug, selection_key, end_ts_source,
               readiness_ok, readiness_reason_codes, confirm_wait_ms,
               commit_block_ms, unsubscribe_ms, unknown_msg_count, ignored_old_rate_per_min
        FROM rollover_status
        ORDER BY ts_ms DESC
        LIMIT 1
        """
    )
    latest_market = "N/A"
    latest_key = "N/A"
    latest_source = "N/A"
    if not latest.empty:
        row = latest.iloc[0]
        latest_market = str(row.get("market_slug") or "N/A")
        latest_key = str(row.get("selection_key") or "N/A")
        latest_source = str(row.get("end_ts_source") or "N/A")

    m1, m2, m3 = st.columns(3)
    m1.metric("Current market", latest_market)
    m2.metric("Selection key", latest_key)
    m3.metric("End ts source", latest_source)

    events = _to_ts(
        query_df(
            """
            SELECT ts_ms, event_type, market_slug, selection_key, readiness_ok,
                   readiness_reason_codes, confirm_wait_ms, commit_block_ms, unsubscribe_ms
            FROM rollover_status
            WHERE event_type IN ('INTENT','CONFIRM','COMMIT','ABORT','HEALTH_FREEZE','READINESS_CHECK')
            ORDER BY ts_ms DESC
            LIMIT 80
            """
        )
    )
    st.subheader("Rollover timeline")
    st.dataframe(events, width="stretch", height=220)

    metrics = query_df(
        """
        SELECT ts_ms, metric_name, metric_value
        FROM rollover_metrics
        WHERE metric_name IN (
            'rollover_confirm_wait_ms',
            'rollover_commit_block_ms',
            'rollover_unsubscribe_ms'
        )
        ORDER BY ts_ms DESC
        LIMIT 300
        """
    )
    confirm_stats = _metric_stats(metrics, "rollover_confirm_wait_ms")
    commit_stats = _metric_stats(metrics, "rollover_commit_block_ms")
    unsub_stats = _metric_stats(metrics, "rollover_unsubscribe_ms")
    stats_rows: List[Dict[str, Any]] = [
        {
            "metric": "confirm_wait_ms",
            "last": _fmt(confirm_stats["last"]),
            "p50": _fmt(confirm_stats["p50"]),
            "p95": _fmt(confirm_stats["p95"]),
        },
        {
            "metric": "commit_block_ms",
            "last": _fmt(commit_stats["last"]),
            "p50": _fmt(commit_stats["p50"]),
            "p95": _fmt(commit_stats["p95"]),
        },
        {
            "metric": "unsubscribe_ms",
            "last": _fmt(unsub_stats["last"]),
            "p50": _fmt(unsub_stats["p50"]),
            "p95": _fmt(unsub_stats["p95"]),
        },
    ]
    st.subheader("Timing metrics")
    st.dataframe(pd.DataFrame(stats_rows), width="stretch", height=160)

    latest_readiness = _to_ts(
        query_df(
            """
            SELECT ts_ms, event_type, readiness_ok, readiness_reason_codes
            FROM rollover_status
            WHERE event_type IN ('READINESS_CHECK','COMMIT','ABORT')
            ORDER BY ts_ms DESC
            LIMIT 1
            """
        )
    )
    st.subheader("Readiness")
    st.dataframe(latest_readiness, width="stretch", height=90)

    unknown_state = _to_ts(
        query_df(
            """
            SELECT ts_ms, unknown_msg_count, ignored_old_rate_per_min
            FROM rollover_status
            ORDER BY ts_ms DESC
            LIMIT 20
            """
        )
    )
    st.subheader("Unknown / ignored_old")
    st.dataframe(unknown_state, width="stretch", height=140)

    discovery = _to_ts(
        query_df(
            """
            SELECT ts_ms, status, reason_code, retry_index, next_retry_ts_ms, selected_slug
            FROM discovery_requests
            ORDER BY ts_ms DESC
            LIMIT 20
            """
        )
    )
    if not discovery.empty and "next_retry_ts_ms" in discovery.columns:
        discovery["next_retry_ts"] = pd.to_datetime(
            discovery["next_retry_ts_ms"], unit="ms", utc=True, errors="coerce"
        )
    st.subheader("Discovery / retry")
    st.dataframe(discovery, width="stretch", height=180)

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
