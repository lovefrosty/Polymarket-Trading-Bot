from __future__ import annotations

from time import perf_counter
from typing import Any, Dict, List

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, PanelDependency, ViewMode
from dashboard.data_access import (
    get_active_quote_summary,
    get_open_orders_latest,
    get_portfolio_risk_summary,
    get_positions_as_of,
    get_trade_blotter,
    require_sources,
)


PORTFOLIO_DEP = PanelDependency(
    panel_id="portfolio",
    required_sources=("inventory", "open_orders_snapshot", "fills", "decisions", "system_state"),
    optional_sources=("execution_quality", "paper_pnl", "pstar", "market_data_book"),
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


def _fmt_money(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    return f"${float(value):.2f}"


def _fmt_bps(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    return f"{float(value):.1f} bps"


def _fmt_ratio(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    return f"{float(value) * 100.0:.1f}%"


def _fmt_qty(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    return f"{float(value):.2f}"


def _fmt_edge(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "N/A"
    return f"{float(value):.3f}"


def render_portfolio_summary_panel(
    filters: DashboardFilters,
    end_ts_ms: int,
    view_mode: ViewMode,
    compact: bool = False,
) -> None:
    assert st is not None
    as_of_ts_ms = int(end_ts_ms)

    summary = get_portfolio_risk_summary(as_of_ts_ms=as_of_ts_ms)
    positions = _apply_filters(get_positions_as_of(as_of_ts_ms=as_of_ts_ms), filters)
    open_orders = _apply_filters(get_open_orders_latest(as_of_ts_ms=as_of_ts_ms), filters)
    quotes = _apply_filters(get_active_quote_summary(as_of_ts_ms=as_of_ts_ms), filters)
    active_positions = int((positions["net_shares"].fillna(0.0).abs() > 0).sum()) if not positions.empty and "net_shares" in positions.columns else 0
    gross_exposure = float(positions["gross_notional"].fillna(0.0).sum()) if not positions.empty and "gross_notional" in positions.columns else 0.0
    net_exposure = float(positions["net_notional"].fillna(0.0).sum()) if not positions.empty and "net_notional" in positions.columns else 0.0
    offered_spread = float(quotes["offered_spread_bps"].dropna().median()) if not quotes.empty and "offered_spread_bps" in quotes.columns and not quotes["offered_spread_bps"].dropna().empty else summary.get("offered_spread_bps")

    st.subheader("Portfolio Summary" if not compact else "Live Portfolio / Risk")
    cards = st.columns(6)
    cards[0].metric("Positions", active_positions)
    cards[1].metric("Active orders", int(len(open_orders)))
    cards[2].metric("Gross exposure", _fmt_money(gross_exposure))
    cards[3].metric("Net exposure", _fmt_money(net_exposure))
    cards[4].metric("Hedge", _fmt_ratio(summary.get("hedge_completeness")))
    cards[5].metric("Edge now", _fmt_edge(summary.get("current_edge")))

    more_cards = st.columns(6)
    more_cards[0].metric("Offered spread", _fmt_bps(offered_spread))
    more_cards[1].metric("Realized PnL", _fmt_money(summary.get("realized_net_pnl")))
    more_cards[2].metric("Unrealized PnL", _fmt_money(summary.get("unrealized_pnl")))
    more_cards[3].metric("Total PnL", _fmt_money(summary.get("total_pnl")))
    more_cards[4].metric("Largest drawdown", _fmt_money(summary.get("max_drawdown_abs")))
    more_cards[5].metric("Max risk / trade", _fmt_money(summary.get("max_risk_per_trade_usd")))

    if compact:
        st.caption(
            f"quote_state live={int(summary.get('live_quote_rows') or 0)} partial={int(summary.get('partial_quote_rows') or 0)} "
            f"| trade_size={_fmt_qty(summary.get('trade_size'))} | max_size={_fmt_qty(summary.get('max_size'))} "
            f"| configured_spread={_fmt_bps(summary.get('maker_half_spread_bps'))}"
        )
        return

    status_cols = st.columns(4)
    status_cols[0].metric("Per-market cap", _fmt_money(summary.get("per_market_cap_usd")), delta=_fmt_ratio(summary.get("per_market_cap_utilization")))
    status_cols[1].metric("Portfolio cap", _fmt_money(summary.get("portfolio_cap_usd")), delta=_fmt_ratio(summary.get("portfolio_cap_utilization")))
    status_cols[2].metric("Daily loss limit", _fmt_money(summary.get("daily_loss_limit_usdc")))
    status_cols[3].metric("Daily notional limit", _fmt_money(summary.get("daily_notional_limit_usdc")))
    st.caption(
        f"hedge_state={summary.get('hedge_state')} | quote_size={_fmt_qty(summary.get('maker_quote_size'))} "
        f"| trade_size={_fmt_qty(summary.get('trade_size'))} | max_size={_fmt_qty(summary.get('max_size'))} "
        f"| recent_realized_spread={_fmt_bps(summary.get('recent_realized_spread_bps'))} "
        f"| recent_net_edge={_fmt_bps(summary.get('recent_net_edge_bps'))}"
    )

    st.subheader("Quote Summary")
    if quotes.empty:
        st.caption("No live quotes for current filters.")
    else:
        display_quotes = quotes.copy()
        st.dataframe(
            display_quotes[
                [
                    col
                    for col in [
                        "market_slug",
                        "token_id",
                        "quote_state",
                        "bid_count",
                        "ask_count",
                        "bid_size",
                        "ask_size",
                        "avg_bid",
                        "avg_ask",
                        "mid",
                        "offered_spread_bps",
                        "oldest_age_s",
                    ]
                    if col in display_quotes.columns
                ]
            ],
            width="stretch",
            height=180,
        )

    if positions.empty:
        return

    position_preview = positions.copy()
    if "as_of_ts_ms" in position_preview.columns:
        position_preview["as_of_ts"] = pd.to_datetime(position_preview["as_of_ts_ms"], unit="ms", utc=True)
    st.subheader("Position Concentration")
    st.dataframe(
        position_preview[
            [
                col
                for col in [
                    "as_of_ts",
                    "market_slug",
                    "token_id",
                    "net_shares",
                    "gross_notional",
                    "net_notional",
                    "mark",
                    "unrealized_pnl",
                ]
                if col in position_preview.columns
            ]
        ],
        width="stretch",
        height=180,
    )


def render_portfolio_panel(
    filters: DashboardFilters,
    start_ts_ms: int,
    end_ts_ms: int,
    view_mode: ViewMode = "developer",
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

    render_portfolio_summary_panel(filters, end_ts_ms=as_of_ts_ms, view_mode=view_mode, compact=False)

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
                        "gross_notional",
                        "net_notional",
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
