from __future__ import annotations

from time import perf_counter
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, PanelDependency
from dashboard.data_access import (
    get_latest_runtime_risk_snapshot,
    get_open_orders_latest,
    get_paper_fill_telemetry,
    get_positions_as_of,
    get_trade_blotter,
    require_sources,
)


PORTFOLIO_DEP = PanelDependency(
    panel_id="portfolio",
    required_sources=("inventory", "open_orders_snapshot", "fills"),
    optional_sources=("execution_quality", "pstar", "market_data_book", "decisions", "system_state"),
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


def _is_developer_mode(view_mode: str) -> bool:
    return str(view_mode).strip().lower() == "developer"


def _fmt_dollars(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _fmt_ratio(value: Any) -> str:
    try:
        return f"{float(value) * 100.0:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _fmt_bps(value: Any) -> str:
    try:
        return f"{float(value):.1f} bps"
    except (TypeError, ValueError):
        return "0.0 bps"


def _token_market_label(
    label_token_fn: Optional[Callable[[Optional[str], Optional[str]], Dict[str, Any]]],
    token_id: str,
) -> str:
    if label_token_fn is None:
        return str(token_id)
    try:
        label = label_token_fn(None, token_id)
    except Exception:
        return str(token_id)
    if not isinstance(label, dict):
        return str(token_id)
    market_label = str(label.get("market_label") or "").strip()
    outcome_label = str(label.get("outcome_label") or "").strip()
    if market_label and outcome_label:
        return f"{market_label} / {outcome_label}"
    if market_label:
        return market_label
    return str(token_id)


def render_portfolio_panel(
    filters: DashboardFilters,
    start_ts_ms: int,
    end_ts_ms: int,
    panel_budget_ms: int = 450,
    view_mode: str = "developer",
    label_token_fn: Optional[Callable[[Optional[str], Optional[str]], Dict[str, Any]]] = None,
) -> None:
    assert st is not None
    t0 = perf_counter()
    developer_mode = _is_developer_mode(view_mode)

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

    st.subheader("Risk Limits & Utilization")
    risk_snapshot = get_latest_runtime_risk_snapshot()
    if not risk_snapshot.get("available"):
        st.markdown(
            "<div class='warn'><b>DEGRADED</b> paper trading limits unavailable in runtime evidence.</div>",
            unsafe_allow_html=True,
        )
    else:
        profile = dict(risk_snapshot.get("profile") or {})
        utilization = dict(risk_snapshot.get("utilization") or {})
        risk_cols = st.columns(4, gap="small")
        risk_cols[0].metric("Orders / min", f"{int(utilization.get('orders_per_min', 0))} / {int(profile.get('max_orders_per_min', 0))}")
        risk_cols[1].metric("Cancels / min", f"{int(utilization.get('cancels_per_min', 0))} / {int(profile.get('max_cancels_per_min', 0))}")
        risk_cols[2].metric("Daily notional", f"{_fmt_dollars(utilization.get('daily_notional_usdc', 0.0))} / {_fmt_dollars(profile.get('max_daily_notional_usdc', 0.0))}")
        risk_cols[3].metric("Daily loss", f"{_fmt_dollars(utilization.get('daily_loss_usdc', 0.0))} / {_fmt_dollars(profile.get('max_daily_loss_usdc', 0.0))}")

        risk_state_parts: List[str] = []
        if risk_snapshot.get("stale"):
            risk_state_parts.append("stale runtime snapshot")
        active_reasons = [str(code) for code in utilization.get("active_risk_reasons", []) if str(code).strip()]
        if active_reasons:
            risk_state_parts.append("active blocks: " + ", ".join(active_reasons))
        else:
            risk_state_parts.append("no active paper risk blocks")
        risk_state_parts.append(f"open quotes: {int(utilization.get('open_quote_count', 0))}")
        state_class = "warn" if risk_snapshot.get("stale") or active_reasons else "ok"
        st.markdown(
            f"<div class='{state_class}'><b>Paper limits:</b> {' | '.join(risk_state_parts)}</div>",
            unsafe_allow_html=True,
        )

        summary_rows = pd.DataFrame(
            [
                {"Metric": "Orders / min", "Current": int(utilization.get("orders_per_min", 0)), "Usage": _fmt_ratio(utilization.get("orders_per_min_ratio", 0.0))},
                {"Metric": "Cancels / min", "Current": int(utilization.get("cancels_per_min", 0)), "Usage": _fmt_ratio(utilization.get("cancels_per_min_ratio", 0.0))},
                {"Metric": "Daily notional", "Current": _fmt_dollars(utilization.get("daily_notional_usdc", 0.0)), "Usage": _fmt_ratio(utilization.get("daily_notional_ratio", 0.0))},
                {"Metric": "Daily loss", "Current": _fmt_dollars(utilization.get("daily_loss_usdc", 0.0)), "Usage": _fmt_ratio(utilization.get("daily_loss_ratio", 0.0))},
                {
                    "Metric": "Portfolio caps",
                    "Current": f"gross {_fmt_dollars((utilization.get('portfolio_cap_state') or {}).get('gross', 0.0))} | net {_fmt_dollars((utilization.get('portfolio_cap_state') or {}).get('net', 0.0))}",
                    "Usage": f"limits gross {_fmt_dollars((utilization.get('portfolio_cap_state') or {}).get('gross_limit', 0.0))} | net {_fmt_dollars((utilization.get('portfolio_cap_state') or {}).get('net_limit', 0.0))}",
                },
            ]
        )
        if developer_mode:
            st.dataframe(summary_rows, width="stretch", height=210)
            token_caps = utilization.get("cap_state_by_token") or {}
            cap_rows = []
            for token_id, cap in sorted(token_caps.items()):
                cap_rows.append(
                    {
                        "token_id": str(token_id),
                        "market_label": _token_market_label(label_token_fn, str(token_id)),
                        "yes_qty": float(cap.get("yes_qty", 0.0)),
                        "no_qty": float(cap.get("no_qty", 0.0)),
                        "token_notional": float(cap.get("token_notional", 0.0)),
                        "token_net_notional": float(cap.get("token_net_notional", 0.0)),
                        "hard_breach": bool(cap.get("hard_breach", False)),
                        "soft_breach": bool(cap.get("soft_breach", False)),
                        "reason_codes": ", ".join(str(code) for code in cap.get("reason_codes", [])),
                    }
                )
            if cap_rows:
                st.caption("Per-token cap state")
                st.dataframe(pd.DataFrame(cap_rows), width="stretch", height=220)
        else:
            st.dataframe(summary_rows[["Metric", "Current", "Usage"]], width="stretch", height=190)

    st.subheader("Paper Fill Telemetry")
    fill_telemetry = _apply_filters(
        get_paper_fill_telemetry(
            start_ts_ms=int(start_ts_ms),
            end_ts_ms=int(end_ts_ms),
            limit=max(int(filters.lookback_rows), 100),
            market_slug=filters.selected_market,
            token_id=filters.selected_token,
        ),
        filters,
    )
    if fill_telemetry.empty:
        st.caption("No paper fill telemetry available for selected time window.")
    else:
        telemetry = fill_telemetry.copy()
        if "ts_ms" in telemetry.columns:
            telemetry["ts"] = pd.to_datetime(telemetry["ts_ms"], unit="ms", utc=True)
        if not developer_mode:
            trader_rows = telemetry.copy()
            trader_rows["Execution"] = trader_rows["market_slug"].fillna(trader_rows["token_id"]).astype(str)
            trader_rows["Fees"] = trader_rows["fee_bps"].apply(_fmt_bps)
            trader_rows["Slippage"] = trader_rows["slippage_bps"].apply(_fmt_bps)
            trader_rows["Spread"] = trader_rows["spread_bps"].apply(_fmt_bps)
            st.dataframe(
                trader_rows[
                    [
                        col
                        for col in ["ts", "Execution", "side", "fill_price", "fill_qty", "Fees", "Slippage", "Spread", "latency_ms"]
                        if col in trader_rows.columns
                    ]
                ],
                width="stretch",
                height=220,
            )
        else:
            st.dataframe(
                telemetry[
                    [
                        col
                        for col in [
                            "ts",
                            "event_id",
                            "order_id",
                            "market_slug",
                            "token_id",
                            "side",
                            "fill_price",
                            "fill_qty",
                            "broker",
                            "mode",
                            "vwap_price",
                            "depth_at_qty",
                            "slippage_bps",
                            "spread_bps",
                            "book_age_ms",
                            "fee_bps",
                            "fee_mode",
                            "fill_model",
                            "latency_ms",
                        ]
                        if col in telemetry.columns
                    ]
                ],
                width="stretch",
                height=240,
            )

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
