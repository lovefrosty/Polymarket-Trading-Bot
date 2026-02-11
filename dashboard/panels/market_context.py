from __future__ import annotations

from time import perf_counter

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, PanelDependency, TopBarMetrics
from dashboard.data_access import query_df, require_sources

MARKET_CONTEXT_DEP = PanelDependency(
    panel_id="market_context",
    required_sources=("decisions",),
    optional_sources=("discovery_requests",),
)


def render_market_context_panel(
    filters: DashboardFilters,
    topbar: TopBarMetrics,
    panel_budget_ms: int = 250,
    view_mode: str = "developer",
    label_token_fn=None,
) -> None:
    assert st is not None
    t0 = perf_counter()
    ok, missing_required, missing_optional = require_sources(
        MARKET_CONTEXT_DEP.required_sources,
        MARKET_CONTEXT_DEP.optional_sources,
    )
    if not ok:
        st.markdown(
            f"<div class='warn'><b>DEGRADED</b> missing required table(s): {', '.join(missing_required)}</div>",
            unsafe_allow_html=True,
        )
        return
    if missing_optional:
        st.caption(f"DEGRADED optional missing: {', '.join(missing_optional)}")

    is_dev = str(view_mode).lower() == "developer"
    market_label = topbar.market_slug
    if label_token_fn is not None:
        try:
            market_label = label_token_fn(topbar.market_slug, None).get("market_label", topbar.market_slug)
        except Exception:
            market_label = topbar.market_slug

    c1, c2, c3 = st.columns(3)
    c1.metric("Resolved market", topbar.market_slug if is_dev else market_label)
    c2.metric("Token count", str(len(topbar.token_ids)))
    c3.metric("Window end ETA", topbar.time_to_window_end)
    if not is_dev:
        st.caption(f"Market detail: {market_label} - closes in {topbar.time_to_window_end}")

    if is_dev:
        token_preview = ", ".join(topbar.token_ids) if topbar.token_ids else "N/A"
        st.caption(f"Token IDs: {token_preview}")
    else:
        st.caption("Outcomes: YES / NO")

    reqs = query_df(
        """
        SELECT ts_ms, requested_symbol, requested_horizon, mode,
               status,
               selected_slug, end_ts_ms, end_ts_source,
               reason_code, retry_index, next_retry_ts_ms, counts_json
        FROM discovery_requests
        ORDER BY ts_ms DESC
        LIMIT 50
        """
    )
    if not reqs.empty:
        reqs["ts"] = pd.to_datetime(reqs["ts_ms"], unit="ms", utc=True)
        if "next_retry_ts_ms" in reqs.columns:
            reqs["next_retry_ts"] = pd.to_datetime(reqs["next_retry_ts_ms"], unit="ms", utc=True, errors="coerce")
        st.subheader("Market discovery evidence")
        if is_dev:
            st.dataframe(reqs, width="stretch", height=220)
        else:
            latest = reqs.head(1).copy()
            if latest.empty:
                st.info("No recent market discovery request.")
            else:
                row = latest.iloc[0]
                status = str(row.get("status") or "unknown").upper()
                selected_slug = str(row.get("selected_slug") or "N/A")
                reason = str(row.get("reason_code") or "none")
                source = str(row.get("end_ts_source") or "unknown")
                retry = row.get("next_retry_ts")
                retry_text = "none scheduled"
                if retry is not None and not pd.isna(retry):
                    retry_text = str(retry)
                st.markdown(
                    f"Status={status} | Selected market={selected_slug} | Selection reason={reason} | End-time source={source} | Next retry={retry_text}"
                )

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
