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
    if is_dev:
        st.dataframe(pstar, width="stretch", height=180)
    else:
        if pstar.empty:
            st.info("No recent price-feed freshness samples.")
        else:
            latest = pstar.iloc[0]
            valid = int(latest.get("valid") or 0) == 1
            age_spot = float(latest.get("age_spot_ms") or 0.0) / 1000.0
            age_perp = float(latest.get("age_perp_ms") or 0.0) / 1000.0
            disagree = latest.get("disagreement_bps")
            disagree_text = f"{float(disagree):.1f} bps" if disagree is not None and not pd.isna(disagree) else "N/A"
            state = "healthy" if valid else "invalid"
            st.markdown(f"Price feed is {state}. spot_age={age_spot:.1f}s perp_age={age_perp:.1f}s disagreement={disagree_text}.")
            cols = [col for col in ["ts", "symbol", "age_spot_ms", "age_perp_ms", "disagreement_bps", "valid"] if col in pstar.columns]
            st.dataframe(pstar[cols].head(30), width="stretch", height=140)

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
    if not is_dev:
        if label_token_fn is not None and not book.empty:
            book["Outcome"] = book["token_id"].apply(lambda token: label_token_fn(None, token).get("outcome_label", "Outcome"))
            book["Market"] = book["token_id"].apply(lambda token: label_token_fn(None, token).get("market_label", "Unknown market"))
        book = book.drop(columns=["token_id"], errors="ignore")
    if is_dev:
        st.dataframe(book, width="stretch", height=160)
    else:
        if book.empty:
            st.info("No recent book freshness samples.")
        else:
            latest_book = book.iloc[0]
            health = str(latest_book.get("book_health_state") or "UNKNOWN")
            age = latest_book.get("book_age_p95_ms")
            age_text = f"{float(age):.1f} ms" if age is not None and not pd.isna(age) else "N/A"
            st.markdown(f"Book health is {health}. p95 book age={age_text}.")
            cols = [col for col in ["ts", "Market", "Outcome", "book_health_state", "book_age_p95_ms"] if col in book.columns]
            st.dataframe(book[cols].head(30), width="stretch", height=140)

    if allow_widgets and is_dev:
        selected_metric = st.selectbox("Metric inspector", METRIC_KEYS, index=0)
    else:
        selected_metric = METRIC_KEYS[0]
        if not pstar.empty and int(pstar.iloc[0].get("valid") or 0) == 0:
            selected_metric = "PSTAR_AGE"
        elif not book.empty and str(book.iloc[0].get("book_health_state") or "").upper() == "DOWN":
            selected_metric = "BOOK_STALE"
        st.caption(f"Metric inspector: {selected_metric} (auto)")
    evidence = query_evidence_rows(
        start_ts_ms=start_ts,
        end_ts_ms=end_ts,
        market=filters.selected_market,
        token_id=filters.selected_token,
        limit=200,
    )
    st.subheader("Evidence rows")
    if not is_dev:
        evidence = evidence.drop(columns=["token_id", "decision_id", "order_id"], errors="ignore")
    if evidence.empty:
        st.info("No evidence rows for current filters.")
    else:
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
