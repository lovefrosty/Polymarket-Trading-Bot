from __future__ import annotations

from time import perf_counter
from typing import List

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, PanelDependency
from dashboard.data_access import (
    get_open_orders_latest,
    get_positions_as_of,
    get_trade_blotter,
    require_sources,
)


PORTFOLIO_DEP = PanelDependency(
    panel_id="portfolio",
    required_sources=("inventory", "open_orders_snapshot", "fills"),
    optional_sources=("execution_quality", "pstar", "market_data_book", "decisions"),
)


def _apply_filters(df: pd.DataFrame, filters: DashboardFilters) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if filters.selected_market != "ALL" and "market_slug" in out.columns:
        out = out[out["market_slug"] == filters.selected_market]
    if filters.selected_token != "ALL" and "token_id" in out.columns:
        out = out[out["token_id"] == filters.selected_token]
    return out


def _missing_hint(table_names: List[str]) -> str:
    if not table_names:
        return ""
    return f"missing table(s): {', '.join(sorted(table_names))}"


def render_portfolio_panel(
    filters: DashboardFilters,
    start_ts_ms: int,
    end_ts_ms: int,
    panel_budget_ms: int = 450,
) -> None:
    assert st is not None
    t0 = perf_counter()

    ok, missing_required, missing_optional = require_sources(
        PORTFOLIO_DEP.required_sources,
        PORTFOLIO_DEP.optional_sources,
    )
    if not ok:
        st.markdown(
            f"<div class='warn'><b>DEGRADED</b> portfolio source gap: {_missing_hint(missing_required)}</div>",
            unsafe_allow_html=True,
        )
    if missing_optional:
        st.caption(f"DEGRADED optional missing: {', '.join(missing_optional)}")

    as_of_ts_ms = int(end_ts_ms)

    st.subheader("Current Positions")
    positions = _apply_filters(get_positions_as_of(as_of_ts_ms=as_of_ts_ms), filters)
    if positions.empty:
        st.caption("No positions available for current filters.")
    else:
        positions = positions.copy()
        if "as_of_ts_ms" in positions.columns:
            positions["as_of_ts"] = pd.to_datetime(positions["as_of_ts_ms"], unit="ms", utc=True)
        st.dataframe(
            positions[
                [
                    col
                    for col in [
                        "as_of_ts",
                        "token_id",
                        "market_slug",
                        "symbol",
                        "yes_qty",
                        "no_qty",
                        "net_shares",
                        "avg_entry",
                        "mark_source",
                        "mark",
                        "unrealized_pnl",
                    ]
                    if col in positions.columns
                ]
            ],
            width="stretch",
            height=240,
        )

    st.subheader("Open Orders")
    open_orders = _apply_filters(get_open_orders_latest(as_of_ts_ms=as_of_ts_ms), filters)
    if open_orders.empty:
        st.caption("No open orders available for current filters.")
    else:
        open_orders = open_orders.copy()
        if "ts_ms" in open_orders.columns:
            open_orders["as_of_ts"] = pd.to_datetime(open_orders["ts_ms"], unit="ms", utc=True)
        st.dataframe(
            open_orders[
                [
                    col
                    for col in [
                        "as_of_ts",
                        "order_id",
                        "token_id",
                        "market_slug",
                        "side",
                        "price",
                        "size",
                        "status",
                        "client_order_id",
                        "quote_group_id",
                    ]
                    if col in open_orders.columns
                ]
            ],
            width="stretch",
            height=220,
        )

    st.subheader("Trade Blotter")
    blotter = _apply_filters(
        get_trade_blotter(
            start_ts_ms=int(start_ts_ms),
            end_ts_ms=int(end_ts_ms),
            limit=max(int(filters.lookback_rows), 200),
        ),
        filters,
    )
    if blotter.empty:
        st.caption("No fills available for selected time window.")
    else:
        blotter = blotter.copy()
        if "ts_ms" in blotter.columns:
            blotter["ts"] = pd.to_datetime(blotter["ts_ms"], unit="ms", utc=True)
        st.dataframe(
            blotter[
                [
                    col
                    for col in [
                        "ts",
                        "order_id",
                        "token_id",
                        "market_slug",
                        "side",
                        "fill_price",
                        "fill_qty",
                        "realized_spread_bps",
                        "markout_5s_bps",
                        "net_edge_bps",
                    ]
                    if col in blotter.columns
                ]
            ],
            width="stretch",
            height=240,
        )

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
