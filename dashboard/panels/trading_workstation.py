from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, Mapping, Optional, Sequence

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from brokers.ibkr.analytics import (
    aggregate_greeks,
    asset_class_exposure,
    historical_portfolio_risk,
    margin_metrics,
    portfolio_metrics,
    sector_exposure,
    symbol_exposure,
)
from brokers.ibkr.models import PortfolioSnapshot
from brokers.ibkr.portfolio_sync import PortfolioStore
from portfolio_agents.advisor import advice_from_payload


def _money(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):,.2f}"


def _percent(value: Any, digits: int = 1) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.{digits}%}"


def _number(value: Any, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):,.{digits}f}"


def quote_direction(previous: Any, current: Any) -> str:
    if previous is None or current is None:
        return "flat"
    try:
        previous_value = float(previous)
        current_value = float(current)
    except (TypeError, ValueError):
        return "flat"
    if current_value > previous_value:
        return "up"
    if current_value < previous_value:
        return "down"
    return "flat"


def quote_tiles_html(items: Sequence[Mapping[str, Any]], directions: Mapping[str, str]) -> str:
    tiles = []
    for item in items:
        label = str(item.get("label") or "")
        direction = directions.get(label, "flat")
        detail = str(item.get("detail") or "")
        tiles.append(
            f'<div class="terminal-quote tick-{html.escape(direction)}">'
            f'<span class="terminal-quote-label">{html.escape(label)}</span>'
            f'<strong class="terminal-quote-value">{html.escape(str(item.get("value") or "N/A"))}</strong>'
            f'<span class="terminal-quote-detail">{html.escape(detail)}</span>'
            "</div>"
        )
    return f'<div class="terminal-quote-grid">{"".join(tiles)}</div>'


def _quote_tiles(namespace: str, items: Sequence[Mapping[str, Any]]) -> None:
    assert st is not None
    state_key = f"terminal_quote_values_{namespace}"
    previous = st.session_state.get(state_key, {})
    directions = {
        str(item.get("label") or ""): quote_direction(previous.get(str(item.get("label") or "")), item.get("numeric"))
        for item in items
    }
    st.markdown(quote_tiles_html(items, directions), unsafe_allow_html=True)
    st.session_state[state_key] = {
        str(item.get("label") or ""): item.get("numeric")
        for item in items
        if item.get("numeric") is not None
    }


def _utc(ts_ms: int) -> str:
    if not ts_ms:
        return "N/A"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%b %d %H:%M:%S UTC")


def _age_text(ts_ms: int, now_ms: Optional[int] = None) -> str:
    if not ts_ms:
        return "N/A"
    age = max(int((now_ms or time.time() * 1000) - ts_ms) // 1000, 0)
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    if age < 86_400:
        return f"{age // 3600}h"
    return f"{age // 86_400}d"


def _display_as_of(value: Any) -> str:
    if value in (None, ""):
        return "N/A"
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return str(value)
    return _utc(timestamp) if timestamp > 10_000_000_000 else str(value)


def _section(title: str, suffix: str = "") -> None:
    assert st is not None
    text = html.escape(title)
    detail = f" <span style='float:right;color:#6f91a6'>{html.escape(suffix)}</span>" if suffix else ""
    st.markdown(f'<div class="terminal-section">{text}{detail}</div>', unsafe_allow_html=True)


def _empty(message: str) -> None:
    assert st is not None
    st.markdown(f'<div class="terminal-empty">{html.escape(message)}</div>', unsafe_allow_html=True)


def _json_path(env_name: str, default: str) -> Path:
    return Path(os.getenv(env_name, default)).expanduser()


def load_json_contract(path: Path) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not path.exists():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"invalid: {type(exc).__name__}"
    if not isinstance(payload, dict):
        return None, "invalid: root must be an object"
    return payload, None


def _payload_timestamp(payload: Optional[Mapping[str, Any]], path: Path) -> int:
    if payload:
        for key in ("as_of_ts_ms", "generated_at_ms", "fetched_at_ms", "updated_at_ms", "now_ts_ms"):
            try:
                value = int(payload.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return 0


def _store() -> PortfolioStore:
    return PortfolioStore(_json_path("IBKR_PORTFOLIO_DB_PATH", "tmp/ibkr/portfolio.db"))


def load_portfolio_state() -> tuple[Optional[PortfolioSnapshot], list[PortfolioSnapshot]]:
    store = _store()
    return store.latest(), store.history(limit=5000)


def _header(snapshot: Optional[PortfolioSnapshot]) -> None:
    assert st is not None
    now_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if snapshot is None:
        broker = '<span class="bad-text">IBKR OFFLINE</span>'
        age = "NO SNAPSHOT"
    else:
        health_class = "live" if snapshot.health.status == "healthy" else "bad-text"
        broker = f'<span class="{health_class}">IBKR {html.escape(snapshot.health.status.upper())}</span>'
        age = _age_text(snapshot.fetched_at_ms)
    st.markdown(
        '<div class="terminal-header">'
        '<span>PORTFOLIO: <strong>ALPHA CORE</strong> &nbsp;|&nbsp; MODE: <strong>HUMAN REVIEW</strong></span>'
        f'<span>{html.escape(now_text)}</span>'
        f'<span><i class="terminal-live-dot"></i>BROKER: {broker} &nbsp;|&nbsp; DATA AGE: <strong>{html.escape(age)}</strong></span>'
        '</div>',
        unsafe_allow_html=True,
    )


def _snapshot_status(snapshot: Optional[PortfolioSnapshot]) -> bool:
    assert st is not None
    if snapshot is None:
        st.markdown(
            '<div class="terminal-critical">IBKR SNAPSHOT UNAVAILABLE. Start the read-only sync before relying on portfolio values.</div>',
            unsafe_allow_html=True,
        )
        st.code("python3 scripts/run_ibkr_sync.py --interval-seconds 30")
        return False
    age_seconds = max((time.time() * 1000 - snapshot.fetched_at_ms) / 1000.0, 0.0)
    stale_after = float(os.getenv("IBKR_STALE_AFTER_SECONDS", "90"))
    if snapshot.health.status != "healthy":
        st.markdown(
            f'<div class="terminal-critical">LAST SYNC FAILED: {html.escape(snapshot.health.message)}. Displayed positions are the last stored state.</div>',
            unsafe_allow_html=True,
        )
    elif age_seconds > stale_after:
        st.markdown(
            f'<div class="terminal-warning">STALE BROKER DATA: snapshot age {age_seconds:.0f}s exceeds {stale_after:.0f}s.</div>',
            unsafe_allow_html=True,
        )
    return True


def position_rows(snapshot: PortfolioSnapshot) -> list[Dict[str, Any]]:
    nlv = sum(account.net_liquidation for account in snapshot.accounts)
    rows = []
    for position in sorted(snapshot.positions, key=lambda item: abs(item.market_value), reverse=True):
        rows.append(
            {
                "Symbol": position.symbol,
                "Asset": position.asset_class,
                "Quantity": position.quantity,
                "Market Price": position.market_price,
                "Market Value": position.market_value,
                "Weight": abs(position.market_value) / nlv if nlv else None,
                "Unrealized PnL": position.unrealized_pnl,
                "Realized PnL": position.realized_pnl,
                "Currency": position.currency,
            }
        )
    return rows


def render_portfolio_view() -> None:
    assert st is not None
    snapshot, history = load_portfolio_state()
    _header(snapshot)
    if not _snapshot_status(snapshot):
        return
    assert snapshot is not None

    metrics = portfolio_metrics(snapshot)
    margin = margin_metrics(snapshot)
    risk = historical_portfolio_risk(history)
    _quote_tiles(
        "portfolio",
        [
            {"label": "Net Liquidation", "value": _money(metrics["net_liquidation"]), "numeric": metrics["net_liquidation"], "detail": "live account value"},
            {"label": "Cash", "value": _money(metrics["cash"]), "numeric": metrics["cash"], "detail": _percent(metrics["cash"] / metrics["net_liquidation"] if metrics["net_liquidation"] else None)},
            {"label": "Margin Used", "value": _money(margin["maintenance_margin"]), "numeric": margin["maintenance_margin"], "detail": _percent(margin["margin_utilization"])},
            {"label": "Total PnL", "value": _money(metrics["total_pnl"]), "numeric": metrics["total_pnl"], "detail": "realized + unrealized"},
            {"label": "Gross Exposure", "value": _money(metrics["gross_exposure"]), "numeric": metrics["gross_exposure"], "detail": f"{metrics['gross_leverage']:.2f}x leverage"},
            {"label": "Net Exposure", "value": _money(metrics["net_exposure"]), "numeric": metrics["net_exposure"], "detail": "long less short"},
            {"label": "VaR 95%", "value": _money(risk["var_value"]), "numeric": risk["var_value"], "detail": _percent(risk["var_fraction"])},
        ],
    )

    chart_col, concentration_col = st.columns([2.25, 1])
    with chart_col:
        _section("Portfolio Value History", f"{int(risk['observation_days'])} daily observations")
        series = pd.DataFrame(risk["nlv_series"])
        if series.empty:
            _empty("Portfolio history will appear after the broker sync has stored snapshots across multiple days.")
        else:
            series["date"] = pd.to_datetime(series["date"], utc=True)
            st.line_chart(series.set_index("date")[["net_liquidation"]], height=300)
    with concentration_col:
        _section("Top Concentrations")
        concentration = pd.DataFrame(symbol_exposure(snapshot)).head(10)
        if concentration.empty:
            _empty("No open position exposure.")
        else:
            chart = concentration.set_index("symbol")[["concentration"]]
            st.bar_chart(chart, horizontal=True, height=300)

    _section("Positions", f"{len(snapshot.positions)} open")
    positions = pd.DataFrame(position_rows(snapshot))
    if positions.empty:
        _empty("No positions returned by IBKR.")
    else:
        st.dataframe(
            positions,
            width="stretch",
            height=360,
            hide_index=True,
            column_config={
                "Market Price": st.column_config.NumberColumn(format="$%.2f"),
                "Market Value": st.column_config.NumberColumn(format="$%.2f"),
                "Weight": st.column_config.ProgressColumn(format="%.1%%", min_value=0.0, max_value=1.0),
                "Unrealized PnL": st.column_config.NumberColumn(format="$%.2f"),
                "Realized PnL": st.column_config.NumberColumn(format="$%.2f"),
            },
        )
    _render_freshness(snapshot)


def risk_warnings(snapshot: PortfolioSnapshot, risk: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    concentrations = symbol_exposure(snapshot)
    if concentrations and float(concentrations[0]["concentration"]) > 0.25:
        warnings.append(f"{concentrations[0]['symbol']} exceeds 25% of gross position exposure")
    if risk.get("max_drawdown") is not None and float(risk["max_drawdown"]) < -0.10:
        warnings.append("historical portfolio drawdown exceeds 10%")
    if int(risk.get("return_observations") or 0) < 20:
        warnings.append("fewer than 20 daily returns; volatility and tail-risk estimates are not decision grade")
    return warnings


def risk_decision_rows(snapshot: PortfolioSnapshot, risk: Mapping[str, Any], margin: Mapping[str, Any]) -> list[Dict[str, Any]]:
    concentrations = symbol_exposure(snapshot)
    top = concentrations[0] if concentrations else None
    observations = int(risk.get("return_observations") or 0)
    return [
        {
            "Decision": "Position sizing",
            "Evidence": f"{top['symbol']} {_percent(top['concentration'])}" if top else "No open exposure",
            "Review Trigger": "Top symbol > 25% of gross exposure",
            "Status": "REVIEW" if top and float(top["concentration"]) > 0.25 else "OK",
        },
        {
            "Decision": "Portfolio loss budget",
            "Evidence": f"VaR {_money(risk.get('var_value'))} / CVaR {_money(risk.get('cvar_value'))}",
            "Review Trigger": "Tail loss exceeds approved budget",
            "Status": "LOW SAMPLE" if observations < 20 else "MONITOR",
        },
        {
            "Decision": "Leverage and liquidity",
            "Evidence": f"Margin utilization {_percent(margin.get('margin_utilization'))}",
            "Review Trigger": "Margin headroom narrows",
            "Status": "MONITOR",
        },
        {
            "Decision": "Regime sensitivity",
            "Evidence": "Beta and factor history unavailable",
            "Review Trigger": "Benchmark/factor data missing",
            "Status": "BLOCKED",
        },
    ]


def render_risk_view() -> None:
    assert st is not None
    snapshot, history = load_portfolio_state()
    _header(snapshot)
    if not _snapshot_status(snapshot):
        return
    assert snapshot is not None
    metrics = portfolio_metrics(snapshot)
    margin = margin_metrics(snapshot)
    risk = historical_portfolio_risk(history)
    greeks = aggregate_greeks(snapshot)

    _quote_tiles(
        "risk",
        [
            {"label": "Portfolio Beta", "value": "N/A", "numeric": None, "detail": "benchmark history required"},
            {"label": "Annualized Vol", "value": _percent(risk["annualized_volatility"]), "numeric": risk["annualized_volatility"], "detail": f"{int(risk['return_observations'])} returns"},
            {"label": "Max Drawdown", "value": _percent(risk["max_drawdown"]), "numeric": risk["max_drawdown"], "detail": "peak to trough"},
            {"label": "VaR 95%", "value": _money(risk["var_value"]), "numeric": risk["var_value"], "detail": _percent(risk["var_fraction"])},
            {"label": "CVaR 95%", "value": _money(risk["cvar_value"]), "numeric": risk["cvar_value"], "detail": _percent(risk["cvar_fraction"])},
            {"label": "Gross Leverage", "value": f"{metrics['gross_leverage']:.2f}x", "numeric": metrics["gross_leverage"], "detail": "gross exposure / NLV"},
            {"label": "Margin Utilization", "value": _percent(margin["margin_utilization"]), "numeric": margin["margin_utilization"], "detail": "maintenance / NLV"},
        ],
    )

    for warning in risk_warnings(snapshot, risk):
        st.markdown(f'<div class="terminal-warning">{html.escape(warning.upper())}</div>', unsafe_allow_html=True)

    _section("Risk Decision Board", "evidence before action")
    st.dataframe(pd.DataFrame(risk_decision_rows(snapshot, risk, margin)), width="stretch", hide_index=True)

    drawdown_col, mix_col = st.columns([1.6, 1])
    with drawdown_col:
        _section("Drawdown History")
        drawdown = pd.DataFrame(risk["drawdown_series"])
        if drawdown.empty:
            _empty("Drawdown requires portfolio snapshots from at least two valuation dates.")
        else:
            drawdown["date"] = pd.to_datetime(drawdown["date"], utc=True)
            st.line_chart(drawdown.set_index("date")[["drawdown"]], height=280)
    with mix_col:
        _section("Asset-Class Exposure")
        classes = pd.DataFrame(asset_class_exposure(snapshot))
        if classes.empty:
            _empty("No asset-class exposure.")
        else:
            st.bar_chart(classes.set_index("asset_class")[["market_value"]], horizontal=True, height=280)

    sector_col, factor_col, greek_col = st.columns(3)
    with sector_col:
        _section("Sector Exposure")
        sectors = pd.DataFrame(sector_exposure(snapshot))
        if sectors.empty:
            _empty("Sector classifications are unavailable in the current broker snapshot.")
        else:
            st.dataframe(sectors, width="stretch", hide_index=True, height=230)
    with factor_col:
        _section("Factor Exposure")
        _empty("Factor loadings require benchmark and factor return history. Missing values are intentionally not zero-filled.")
    with greek_col:
        _section("Options Greeks", f"coverage {greeks['covered_positions']}/{greeks['option_positions']}")
        greek_frame = pd.DataFrame(
            [{"Greek": name.upper(), "Portfolio Value": greeks[name]} for name in ("delta", "gamma", "theta", "vega")]
        )
        if not greeks["option_positions"]:
            _empty("No option positions in the latest snapshot.")
        elif not greeks["covered_positions"]:
            _empty("Option positions exist, but the broker snapshot does not include Greeks.")
        else:
            st.dataframe(greek_frame, width="stretch", hide_index=True)


def _macro_items(payload: Mapping[str, Any]) -> list[Dict[str, Any]]:
    aliases = [
        ("Policy Stance", "policy_stance"),
        ("Expected Rate Direction", "expected_policy_direction"),
        ("Yield Curve", "yield_curve_regime"),
        ("Liquidity", "liquidity_direction"),
        ("Inflation", "inflation_regime"),
        ("Volatility", "volatility_regime"),
        ("Credit Stress", "credit_stress"),
    ]
    features = payload.get("features") if isinstance(payload.get("features"), dict) else payload
    rows = []
    for label, key in aliases:
        value = features.get(key) if isinstance(features, Mapping) else None
        if isinstance(value, Mapping):
            rows.append({"Metric": label, "State": value.get("value", value.get("label", "N/A")), "Confidence": value.get("confidence"), "As Of": _display_as_of(value.get("as_of", value.get("as_of_ts_ms")))})
        else:
            rows.append({"Metric": label, "State": value if value not in (None, "") else "N/A", "Confidence": None, "As Of": None})
    return rows


def macro_decision_rows(payload: Mapping[str, Any]) -> list[Dict[str, Any]]:
    states = {row["Metric"]: row["State"] for row in _macro_items(payload)}
    return [
        {"Question": "Policy path", "Evidence": f"Stance: {states['Policy Stance']} | Direction: {states['Expected Rate Direction']}", "Portfolio Decision": "Duration, financing cost, and rate-sensitive exposure"},
        {"Question": "Curve and inflation", "Evidence": f"Curve: {states['Yield Curve']} | Inflation: {states['Inflation']}", "Portfolio Decision": "Nominal versus real exposure and cyclical sensitivity"},
        {"Question": "Liquidity impulse", "Evidence": str(states["Liquidity"]), "Portfolio Decision": "Gross exposure, liquidity premium, and cash buffer"},
        {"Question": "Stress transmission", "Evidence": f"Vol: {states['Volatility']} | Credit: {states['Credit Stress']}", "Portfolio Decision": "Distinguish market noise from systemic de-risking"},
    ]


def energy_rows(payload: Mapping[str, Any]) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for category in (
        "markets",
        "fundamentals",
        "weather",
        "positioning",
        "corporate",
        "geopolitical",
        "hypotheses",
        "thresholds",
    ):
        block = payload.get(category)
        if not isinstance(block, Mapping):
            continue
        for name, item in block.items():
            if isinstance(item, Mapping):
                value = item.get("value", item.get("state", item.get("label", "N/A")))
                change = item.get("change", item.get("delta"))
                rows.append(
                    {
                        "Category": category.upper(),
                        "Indicator": str(name),
                        "Value": "N/A" if value is None else str(value),
                        "Change": "N/A" if change is None else str(change),
                        "Source": item.get("source", "N/A"),
                        "As Of": _display_as_of(item.get("as_of", item.get("as_of_ts_ms"))),
                    }
                )
            else:
                rows.append({"Category": category.upper(), "Indicator": str(name), "Value": str(item), "Change": "N/A", "Source": "N/A", "As Of": "N/A"})
    return rows


def _contract_rows(block: Any) -> list[Dict[str, Any]]:
    if isinstance(block, Sequence) and not isinstance(block, (str, bytes)):
        return [dict(item) for item in block if isinstance(item, Mapping)]
    if not isinstance(block, Mapping):
        return []
    for key in ("rows", "table_rows", "contracts", "events"):
        nested = block.get(key)
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            return [dict(item) for item in nested if isinstance(item, Mapping)]
    rows = []
    for key, value in block.items():
        if isinstance(value, Mapping):
            row = {"Name": key}
            row.update(value)
            rows.append(row)
        elif not isinstance(value, (list, tuple, dict)):
            rows.append({"Field": key, "Value": value})
    return rows


def _render_contract_block(title: str, block: Any, empty_message: str) -> None:
    assert st is not None
    _section(title)
    rows = _contract_rows(block)
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        _empty(empty_message)


def render_macro_view() -> None:
    assert st is not None
    snapshot, _ = load_portfolio_state()
    _header(snapshot)
    path = _json_path("MACRO_FEATURES_PATH", "tmp/macro_features.json")
    payload, error = load_json_contract(path)
    _section("Central Bank and Macro Regime", path.as_posix())
    if payload is None:
        _empty(
            "Macro feature export unavailable. The central-bank dashboard should publish macro_features_v1 JSON here; this workstation will consume it without importing its strategy logic."
        )
        st.caption(f"Source status: {error}. Set MACRO_FEATURES_PATH to the exporter output when available.")
        return

    schema = str(payload.get("schema_version") or "UNVERSIONED")
    as_of = _payload_timestamp(payload, path)
    _quote_tiles(
        "macro",
        [
            {"label": "Contract", "value": schema, "numeric": None, "detail": "atomic macro export"},
            {"label": "Macro Data Age", "value": _age_text(as_of), "numeric": None, "detail": _utc(as_of)},
            {"label": "Source Health", "value": str(payload.get("status") or "UNKNOWN").upper(), "numeric": None, "detail": "missing data stays explicit"},
            {"label": "Feature Count", "value": str(len(_macro_items(payload))), "numeric": len(_macro_items(payload)), "detail": "resolved state fields"},
        ],
    )

    state_tab, banks_tab, curves_tab, stress_tab, calendar_tab, energy_tab = st.tabs(
        ["STATE", "CENTRAL BANKS", "CURVES", "LIQUIDITY + STRESS", "CALENDAR", "ENERGY DESK"]
    )
    with state_tab:
        _section("Macro State", "facts -> mechanics -> evidence -> interpretation")
        st.dataframe(pd.DataFrame(_macro_items(payload)), width="stretch", hide_index=True)
        _section("Decision Map", "human interpretation required")
        st.dataframe(pd.DataFrame(macro_decision_rows(payload)), width="stretch", hide_index=True)
    with banks_tab:
        _render_contract_block(
            "Central-Bank Policy and Implied Path",
            payload.get("central_banks") or payload.get("policy") or payload.get("policy_curve"),
            "No central-bank detail block was supplied. Export policy rate, last decision, next meeting, guidance, and market-implied path.",
        )
        _render_contract_block(
            "Policy Disagreements",
            payload.get("disagreements"),
            "No policy-versus-market disagreements were supplied.",
        )
    with curves_tab:
        _render_contract_block(
            "Yield Curves",
            payload.get("yield_curves") or payload.get("yield_curve"),
            "No tenor-level yield-curve block was supplied.",
        )
        _render_contract_block(
            "Real Yields and Breakevens",
            payload.get("inflation_real_rates"),
            "No real-rate or breakeven decomposition was supplied.",
        )
    with stress_tab:
        stress_rows = [row for row in _macro_items(payload) if row["Metric"] in {"Liquidity", "Volatility", "Credit Stress"}]
        st.dataframe(pd.DataFrame(stress_rows), width="stretch", hide_index=True)
        _render_contract_block(
            "Cross-Asset Stress Evidence",
            payload.get("volatility") or payload.get("credit") or payload.get("stress"),
            "No cross-asset volatility or credit detail was supplied.",
        )
    with calendar_tab:
        calendar = payload.get("economic_calendar") or payload.get("calendar") or []
        _section("Economic Calendar")
        if isinstance(calendar, Sequence) and not isinstance(calendar, (str, bytes)) and calendar:
            st.dataframe(pd.DataFrame(list(calendar)), width="stretch", hide_index=True)
        else:
            _empty("No economic-calendar events were supplied by the macro exporter.")
    with energy_tab:
        energy_path = _json_path("ENERGY_FEATURES_PATH", "tmp/energy_features.json")
        energy_payload, energy_error = load_json_contract(energy_path)
        _section("Energy Research Desk", energy_path.as_posix())
        if energy_payload is None:
            _empty("Energy intelligence is not connected yet. Publish energy_features_v1 with market, fundamental, weather, positioning, corporate, and geopolitical evidence blocks.")
            st.caption(f"Source status: {energy_error}. This panel is research-only and cannot submit orders.")
        else:
            rows = energy_rows(energy_payload)
            if rows:
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                _empty("The energy feature contract is present but contains no supported evidence blocks.")


def idea_rows(payload: Mapping[str, Any], now_ms: Optional[int] = None) -> list[Dict[str, Any]]:
    effective_now = int(now_ms or time.time() * 1000)
    rows = []
    for signal in payload.get("signals", []):
        if not isinstance(signal, Mapping):
            continue
        as_of = int(signal.get("as_of_ts_ms") or 0)
        expiry = int(signal.get("expires_ts_ms") or 0)
        rows.append(
            {
                "Status": "ACTIVE" if as_of <= effective_now < expiry else "EXPIRED",
                "As Of": _utc(as_of),
                "Age": _age_text(as_of, effective_now),
                "Agent": signal.get("agent_id", "unknown"),
                "Symbol": signal.get("symbol", "UNKNOWN"),
                "Score": signal.get("score"),
                "Confidence": signal.get("confidence"),
                "Horizon": signal.get("horizon", "N/A"),
                "Expires": _utc(expiry),
                "Thesis": signal.get("thesis", ""),
                "Evidence": ", ".join(str(item) for item in signal.get("evidence_links", [])),
                "Model History": signal.get("model_history", "N/A"),
            }
        )
    return rows


def _advice_payload() -> tuple[Optional[Dict[str, Any]], Path, Optional[str]]:
    path = _json_path("PORTFOLIO_ADVICE_PATH", "tmp/portfolio_advice/latest.json")
    payload, error = load_json_contract(path)
    return payload, path, error


def render_idea_inbox_view() -> None:
    assert st is not None
    snapshot, _ = load_portfolio_state()
    _header(snapshot)
    payload, path, error = _advice_payload()
    _section("Agent Idea Inbox", path.as_posix())
    if payload is None:
        _empty("No agent signal contract is available. Agents may write proposals here, but they cannot place orders.")
        st.caption(f"Source status: {error}. Set PORTFOLIO_ADVICE_PATH to an agent_signal_v1-compatible payload.")
        return
    ideas = pd.DataFrame(idea_rows(payload))
    if ideas.empty:
        _empty("The latest agent payload contains no proposals.")
        return
    active = int((ideas["Status"] == "ACTIVE").sum())
    cards = st.columns(4)
    cards[0].metric("Active Ideas", active)
    cards[1].metric("Expired", len(ideas) - active)
    cards[2].metric("Agents", ideas["Agent"].nunique())
    cards[3].metric("Symbols", ideas["Symbol"].nunique())
    st.dataframe(
        ideas,
        width="stretch",
        height=480,
        hide_index=True,
        column_config={
            "Score": st.column_config.NumberColumn(format="%.3f"),
            "Confidence": st.column_config.ProgressColumn(format="%.0%%", min_value=0.0, max_value=1.0),
        },
    )


def render_rebalance_view() -> None:
    assert st is not None
    snapshot, history = load_portfolio_state()
    _header(snapshot)
    payload, path, error = _advice_payload()
    _section("Human-Reviewed Rebalance", path.as_posix())
    if payload is None:
        _empty("No rebalance input is available. A proposal requires current holdings, active agent signals, and explicit risk policy.")
        st.caption(f"Source status: {error}")
        return
    try:
        advice = advice_from_payload(payload, now_ts_ms=int(time.time() * 1000))
    except (TypeError, ValueError, KeyError) as exc:
        st.markdown(f'<div class="terminal-critical">REBALANCE CONTRACT REJECTED: {html.escape(str(exc))}</div>', unsafe_allow_html=True)
        return

    proposals = pd.DataFrame(advice.get("proposals", []))
    current_risk = historical_portfolio_risk(history)
    cards = st.columns(5)
    cards[0].metric("Portfolio Value", _money(advice.get("portfolio_value")))
    cards[1].metric("Active Signals", int(advice.get("active_signal_count", 0)))
    cards[2].metric("Trades Proposed", int((proposals.get("action", pd.Series(dtype=str)) != "HOLD").sum()))
    cards[3].metric("Current VaR 95%", _money(current_risk.get("var_value")))
    cards[4].metric("Post-Trade Risk", "N/A", "price/covariance inputs required")
    st.markdown('<div class="terminal-warning">HUMAN APPROVAL REQUIRED. THIS VIEW DOES NOT ROUTE ORDERS.</div>', unsafe_allow_html=True)
    if proposals.empty:
        _empty("No proposal rows were generated.")
    else:
        visible = proposals.rename(
            columns={
                "symbol": "Symbol",
                "action": "Action",
                "current_weight": "Current Weight",
                "target_weight": "Target Weight",
                "delta_weight": "Change",
                "estimated_trade_value": "Estimated Trade",
                "agent_count": "Agents",
                "consensus_score": "Consensus",
                "disagreement": "Disagreement",
                "reasons": "Reasons",
            }
        )
        if "Reasons" in visible:
            visible["Reasons"] = visible["Reasons"].map(lambda value: ", ".join(value) if isinstance(value, list) else value)
        st.dataframe(visible, width="stretch", height=390, hide_index=True)
    _section("Before / After Risk")
    _empty("Before-risk metrics use stored NLV history. After-risk estimates remain blocked until market history, covariance, liquidity, and transaction-cost inputs are present.")


def _source_cell(name: str, state: str, age: str) -> str:
    return (
        '<div class="terminal-status-cell">'
        f'<span>{html.escape(name)}</span><strong>{html.escape(state)} &nbsp; {html.escape(age)}</strong>'
        '</div>'
    )


def _render_freshness(snapshot: PortfolioSnapshot) -> None:
    assert st is not None
    macro_path = _json_path("MACRO_FEATURES_PATH", "tmp/macro_features.json")
    advice_path = _json_path("PORTFOLIO_ADVICE_PATH", "tmp/portfolio_advice/latest.json")
    macro_payload, macro_error = load_json_contract(macro_path)
    advice_payload, advice_error = load_json_contract(advice_path)
    cells = [
        _source_cell("Interactive Brokers", snapshot.health.status.upper(), _age_text(snapshot.fetched_at_ms)),
        _source_cell("Macro Features", "READY" if macro_payload else str(macro_error).upper(), _age_text(_payload_timestamp(macro_payload, macro_path))),
        _source_cell("Agent Signals", "READY" if advice_payload else str(advice_error).upper(), _age_text(_payload_timestamp(advice_payload, advice_path))),
        _source_cell("Benchmark Returns", "MISSING", "N/A"),
        _source_cell("Factor Returns", "MISSING", "N/A"),
    ]
    _section("Data Source Freshness")
    st.markdown(f'<div class="terminal-status-grid">{"".join(cells)}</div>', unsafe_allow_html=True)
