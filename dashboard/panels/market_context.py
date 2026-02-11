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

    c1, c2, c3 = st.columns(3)
    c1.metric("Resolved market", topbar.market_slug)
    c2.metric("Token count", str(len(topbar.token_ids)))
    c3.metric("Window end ETA", topbar.time_to_window_end)

    token_preview = ", ".join(topbar.token_ids) if topbar.token_ids else "N/A"
    st.caption(f"Token IDs: {token_preview}")

    reqs = query_df(
        """
        SELECT ts_ms, requested_symbol, requested_horizon, mode,
               selected_slug, end_ts_ms, end_ts_source,
               reason_code, counts_json
        FROM discovery_requests
        ORDER BY ts_ms DESC
        LIMIT 50
        """
    )
    if not reqs.empty:
        reqs["ts"] = pd.to_datetime(reqs["ts_ms"], unit="ms", utc=True)
        st.subheader("Market discovery evidence")
        st.dataframe(reqs, use_container_width=True, height=220)

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
