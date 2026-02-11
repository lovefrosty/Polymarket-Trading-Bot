from __future__ import annotations

from time import perf_counter
from typing import Callable

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, PanelDependency
from dashboard.data_access import adapt_decisions, query_df, require_sources, safe_json

SIGNALS_DEP = PanelDependency(
    panel_id="signals",
    required_sources=("decisions",),
    optional_sources=(),
)


def render_signals_panel(
    filters: DashboardFilters,
    start_ts: int,
    apply_decision_filters: Callable,
    panel_budget_ms: int = 400,
) -> None:
    assert st is not None
    t0 = perf_counter()
    ok, missing_required, _ = require_sources(SIGNALS_DEP.required_sources)
    if not ok:
        st.markdown(
            f"<div class='warn'><b>DEGRADED</b> missing required table(s): {', '.join(missing_required)}</div>",
            unsafe_allow_html=True,
        )
        return

    dec = adapt_decisions(
        query_df(
            """
            SELECT ts_ms, decision_id, market, token_id, action, reason_codes, p_hat, expected_edge, expected_cost, policy_json
            FROM decisions
            WHERE ts_ms >= ?
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (start_ts, max(2000, filters.lookback_rows)),
        )
    )
    dec = apply_decision_filters(dec, filters)

    display_cols = [
        col
        for col in ["ts", "decision_id", "market", "token_id", "action", "strategy", "p_hat", "ev", "gate_result", "reason_codes"]
        if col in dec.columns
    ]
    st.dataframe(dec[display_cols], use_container_width=True, height=300)

    st.subheader("Decision Drill-down")
    if dec.empty:
        st.info("No signals for current filters.")
        return

    ids = dec["decision_id"].astype(str).tolist()
    selected_decision_id = st.selectbox("Decision ID", ids, index=0)
    row = dec[dec["decision_id"].astype(str) == selected_decision_id].head(1)
    if row.empty:
        st.info("Decision not found.")
        return

    payload = safe_json(row.iloc[0].get("policy_json"))
    st.json(payload)

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
