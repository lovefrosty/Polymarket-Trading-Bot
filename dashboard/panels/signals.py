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
    is_dev = str(view_mode).lower() == "developer"
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
        st.info("No signals right now - widen filters or wait for spread compression.")
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
        strategy = str(latest.iloc[0].get("strategy") or "N/A") if not latest.empty else "N/A"
        gate = str(latest.iloc[0].get("gate_result") or "N/A") if not latest.empty else "N/A"
        reason_codes = str(latest.iloc[0].get("reason_codes") or "") if not latest.empty else ""
        p_hat = latest.iloc[0].get("p_hat") if not latest.empty else None
        ev = latest.iloc[0].get("ev") if not latest.empty else None
        if gate.upper() == "ALLOW":
            hint = "Eligible now - monitor entry window and spread."
        else:
            hint = "WAIT - gate blocked. Review Health tab for current blocker."
        if p_hat is not None and ev is not None:
            st.markdown(
                f"Latest signal: strategy={strategy} | gate={gate} | p_hat={float(p_hat):.3f} | ev={float(ev):.4f}"
            )
        else:
            st.markdown(f"Latest signal: strategy={strategy} | gate={gate}")
        if reason_codes:
            st.caption(f"Reason: {reason_codes}")
        st.caption(f"Action hint: {hint}")
        st.caption(f"Policy payload available: {'yes' if bool(payload) else 'no'}")

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
