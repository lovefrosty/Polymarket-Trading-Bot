from __future__ import annotations

from time import perf_counter
from typing import Callable, Optional

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
    view_mode: str = "developer",
    build_signals_view: Optional[Callable] = None,
    allow_widgets: bool = True,
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

    display = build_signals_view(dec) if build_signals_view is not None else dec
    if display.empty:
        st.info("No signals right now - widen filters / check degraded state.")
    else:
        st.dataframe(display, width="stretch", height=300)

    st.subheader("Decision Drill-down")
    if dec.empty:
        st.info("No signals for current filters.")
        return

    if is_dev and allow_widgets:
        ids = dec["decision_id"].astype(str).tolist()
        selected_decision_id = st.selectbox("Decision ID", ids, index=0)
        row = dec[dec["decision_id"].astype(str) == selected_decision_id].head(1)
        if row.empty:
            st.info("Decision not found.")
            return
        payload = safe_json(row.iloc[0].get("policy_json"))
        st.json(payload)
    else:
        latest = dec.head(1)
        payload = safe_json(latest.iloc[0].get("policy_json")) if not latest.empty else {}
        summary = {
            "strategy": latest.iloc[0].get("strategy") if not latest.empty else "N/A",
            "gate_result": latest.iloc[0].get("gate_result") if not latest.empty else "N/A",
            "reason_codes": latest.iloc[0].get("reason_codes") if not latest.empty else "",
            "has_policy_payload": bool(payload),
        }
        st.json(summary)

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
    is_dev = str(view_mode).lower() == "developer"
