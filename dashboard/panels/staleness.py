from __future__ import annotations

from time import perf_counter
from typing import Any, Callable, Dict, Optional

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, DrillthroughContext, PanelDependency
from dashboard.data_access import build_drillthrough_context, query_df, query_evidence_rows, require_sources

STALENESS_DEP = PanelDependency(
    panel_id="staleness",
    required_sources=("pstar_stats",),
    optional_sources=("book_health_stats", "latency_stats"),
)


METRIC_KEYS = [
    "PSTAR_AGE",
    "WS_LAG",
    "BOOK_STALE",
    "FREEZE_REASON",
]


def render_staleness_panel(
    filters: DashboardFilters,
    start_ts: int,
    end_ts: int,
    panel_budget_ms: int = 300,
    view_mode: str = "developer",
    label_token_fn: Optional[Callable[[Any, Any], Dict[str, str]]] = None,
    allow_widgets: bool = True,
) -> Optional[DrillthroughContext]:
    assert st is not None
    t0 = perf_counter()
    is_dev = str(view_mode).lower() == "developer"
    ok, missing_required, missing_optional = require_sources(
        STALENESS_DEP.required_sources,
        STALENESS_DEP.optional_sources,
    )
    if not ok:
        st.markdown(
            f"<div class='warn'><b>DEGRADED</b> missing required table(s): {', '.join(missing_required)}</div>",
            unsafe_allow_html=True,
        )
        return None
    if missing_optional:
        st.caption(f"DEGRADED optional missing: {', '.join(missing_optional)}")

    pstar = query_df(
        """
        SELECT ts_ms, symbol, age_spot_ms, age_perp_ms, disagreement_bps, confidence, valid
        FROM pstar_stats
        WHERE ts_ms BETWEEN ? AND ?
        ORDER BY ts_ms DESC
        LIMIT 200
        """,
        (start_ts, end_ts),
    )
    if not pstar.empty:
        pstar["ts"] = pd.to_datetime(pstar["ts_ms"], unit="ms", utc=True)
    st.subheader("Staleness explainer")
    st.dataframe(pstar, width="stretch", height=180)

    book = query_df(
        """
        SELECT ts_ms, token_id, book_health_state, book_age_p95_ms
        FROM book_health_stats
        WHERE ts_ms BETWEEN ? AND ?
        ORDER BY ts_ms DESC
        LIMIT 200
        """,
        (start_ts, end_ts),
    )
    if not book.empty:
        book["ts"] = pd.to_datetime(book["ts_ms"], unit="ms", utc=True)
    if not is_dev and not book.empty:
        if label_token_fn is not None:
            book["Outcome"] = book["token_id"].apply(lambda token: label_token_fn(None, token).get("outcome_label", "Outcome"))
            book["Market"] = book["token_id"].apply(lambda token: label_token_fn(None, token).get("market_label", "Unknown market"))
        book = book.drop(columns=["token_id"], errors="ignore")
    st.dataframe(book, width="stretch", height=160)

    if allow_widgets:
        selected_metric = st.selectbox("Metric inspector", METRIC_KEYS, index=0)
    else:
        selected_metric = METRIC_KEYS[0]
        st.caption(f"Metric inspector: {selected_metric} (auto-refresh mode)")
    evidence = query_evidence_rows(
        start_ts_ms=start_ts,
        end_ts_ms=end_ts,
        market=filters.selected_market,
        token_id=filters.selected_token,
        limit=200,
    )
    st.subheader("Evidence rows")
    if not is_dev and not evidence.empty:
        evidence = evidence.drop(columns=["token_id", "decision_id", "order_id"], errors="ignore")
    st.dataframe(evidence, width="stretch", height=220)

    context = build_drillthrough_context(
        metric_key=selected_metric,
        start_ts_ms=start_ts,
        end_ts_ms=end_ts,
        market=filters.selected_market,
        token_id=filters.selected_token,
        reason_codes=[],
        evidence_refs=[str(item) for item in evidence.get("event_type", pd.Series(dtype=str)).head(10).tolist()],
        payload={"evidence_rows": int(len(evidence))},
    )
    st.caption(f"context_id={context.context_id} context_hash={context.context_hash[:12]}...")

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
    return context
