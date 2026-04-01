from __future__ import annotations

import re
import time as _time_mod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import altair as alt
except ModuleNotFoundError:  # pragma: no cover
    alt = None  # type: ignore[assignment]

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard import data_access as da


# ── Helpers ──────────────────────────────────────────────────────────────────


def _iso(ts_ms: Optional[int]) -> str:
    if ts_ms is None:
        return "N/A"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _short_time(ts_ms: Optional[int]) -> str:
    if ts_ms is None:
        return "N/A"
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%H:%M:%S")


def _fmt(value: Optional[float], prefix: str = "", suffix: str = "", decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{prefix}{value:.{decimals}f}{suffix}"


def _duration_str(ms: Optional[float]) -> str:
    if ms is None or ms <= 0:
        return "N/A"
    if ms < 1000:
        return f"{ms:.0f}ms"
    secs = ms / 1000.0
    if secs < 60:
        return f"{secs:.1f}s"
    mins = secs / 60.0
    if mins < 60:
        return f"{mins:.1f}m"
    return f"{mins / 60:.1f}h"


def _pnl_color(value: float) -> str:
    if value > 0:
        return "#05ffa1"
    if value < 0:
        return "#ff3b5c"
    return "#7a8599"


def _token_label(token_id: str) -> str:
    if isinstance(token_id, str) and len(token_id) > 12:
        return f"{token_id[:6]}..{token_id[-4:]}"
    return str(token_id or "")


def _hex_to_rgb(hex_color: str) -> str:
    """Convert #rrggbb to 'r,g,b' string."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"{r},{g},{b}"


def _resolve_yes_no_tokens(sys_payload: Dict[str, Any]) -> Dict[str, str]:
    """Returns {token_id: 'YES' or 'NO'} based on best_bid from book_diag."""
    runner = sys_payload.get("runner") or {}
    per_token = (runner.get("book_diag") or {}).get("per_token") or {}
    result: Dict[str, str] = {}
    for tid, diag in per_token.items():
        bid = float(diag.get("best_bid") or 0.0)
        result[str(tid)] = "YES" if bid > 0.5 else "NO"
    return result


def _active_token_ids(sys_payload: Dict[str, Any]) -> List[str]:
    runner = sys_payload.get("runner") or {}
    return [str(t) for t in (runner.get("token_ids") or [])]


def _is_dev_mode(view_mode: Optional[Any]) -> bool:
    return str(view_mode or "").lower() == "developer"


def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"${value:+,.2f}"


def _fmt_bps(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f} bps"


def _fmt_last_update(ts_ms: Optional[int]) -> str:
    if ts_ms is None:
        return "N/A"
    age_s = max(0.0, (_time_mod.time() * 1000.0 - float(ts_ms)) / 1000.0)
    return _duration_str(age_s * 1000.0) + " ago"


def _format_size_limiter(size_plan: Dict[str, Any], side: str) -> str:
    primary = str(size_plan.get(f"{side}_limiter") or "n/a")
    chain = str(size_plan.get(f"{side}_limiters") or "")
    human_primary = primary.replace("_", " ")
    if not chain:
        return human_primary
    human_chain = " -> ".join(part.replace("_", " ") for part in chain.split(",") if part)
    if not human_chain or human_chain == human_primary:
        return human_primary
    return f"{human_primary} [{human_chain}]"


def _status_value(snapshot: Dict[str, Any], key: str, default: Any = None) -> Any:
    value = snapshot.get(key)
    return default if value in (None, "") else value


def _extract_regime(snapshot: Dict[str, Any]) -> str:
    runner = snapshot.get("runner") or {}
    selection = snapshot.get("selection") or {}
    health = snapshot.get("active_market_health") or {}
    for candidate in (
        health.get("regime"),
        health.get("market_regime"),
        selection.get("regime"),
        selection.get("market_regime"),
        runner.get("regime"),
        runner.get("regime_label"),
        runner.get("phase"),
        snapshot.get("state"),
    ):
        if candidate not in (None, ""):
            return str(candidate)
    return "unknown"


def _humanize_reason_codes(raw: Any) -> str:
    reason_map = {
        "book_absent": "Order book is unavailable",
        "book_empty": "Order book is empty",
        "one_sided_book": "Book is one-sided",
        "price_out_of_range": "Market price is outside the safe range",
        "spread_too_wide": "Spread is too wide to quote safely",
        "insufficient_volume": "Recent volume is too low",
        "insufficient_open_interest": "Open interest is too low",
        "liquidity_score_too_low": "Liquidity score is below the safe threshold",
        "quoteable_book": "Book is healthy enough to quote",
        "stale_position": "Inventory is stale and should be reduced",
        "take_profit": "The bot is harvesting a favorable exit",
        "stop_loss": "The bot is limiting a losing position",
        "flow_blocks_buy": "Flow filter is blocking new buys",
        "flow_blocks_sell": "Flow filter is blocking new sells",
        "ask_improve": "The bot is improving the ask",
        "bid_improve": "The bot is improving the bid",
        "ask_risk_exit_stale_unwind_maker": "The bot is trying a maker exit for stale inventory",
        "ask_risk_exit_take_profit_maker": "The bot is exiting passively to lock in profit",
        "ask_fallback_top_ask": "The bot is leaning on the top ask",
        "bid_fallback_top_bid": "The bot is leaning on the top bid",
        "freeze": "Trading is frozen by a safety gate",
        "no_hedge_market": "No better hedge market was available",
        "hedge_not_better_than_inventory_market": "Hedge quality did not beat the inventory market",
        "gross_increase_ceiling_exhausted": "Temporary gross exposure would exceed the ceiling",
        "stale_inventory_required": "Stale inventory was required before hedging",
        "maker_exit_window_active": "Maker exit window was still active",
        "hedge_failed_cooldown": "A failed hedge cooldown was still in force",
        "hedge_failed_no_improvement": "Hedge attempts had not improved inventory enough",
        "stop_open_window": "Open-window guard blocked the hedge",
        "force_flat_window": "Force-flat window was active",
        "forced_reduction": "Forced reduction was already in progress",
        "paper_only": "Paper-only hedge telemetry",
    }
    bits = [str(bit).strip() for bit in str(raw or "").split(",") if str(bit).strip()]
    if not bits:
        return "No blocking reason recorded"
    humanized: List[str] = []
    for bit in bits[:3]:
        humanized.append(reason_map.get(bit.lower(), bit.replace("_", " ")))
    return "; ".join(humanized)


def _selection_market_label(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("ticker", "market", "market_id", "slug", "title", "label"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate)
        return "N/A"
    return str(value or "N/A")


def _freeze_reason_text(raw: Any) -> str:
    if isinstance(raw, list):
        if not raw:
            return "none"
        return _humanize_reason_codes(",".join(str(item) for item in raw if item not in (None, "")))
    return _humanize_reason_codes(raw) if raw not in (None, "") else "none"


def _control_state_text(raw: Any) -> str:
    if raw in (None, ""):
        return "N/A"
    return str(raw).replace("_", " ").title()


def _time_to_expiry_ms(*sources: Any) -> Optional[int]:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("time_to_expiry_ms", "ms_to_expiry", "remaining_ms"):
            value = source.get(key)
            try:
                if value is None:
                    continue
                out = int(value)
                if out >= 0:
                    return out
            except (TypeError, ValueError):
                continue
    return None


def _cluster_net_label(net_yes_exposure_notional: Optional[float]) -> str:
    net = float(net_yes_exposure_notional or 0.0)
    if abs(net) < 1e-9:
        return "Flat"
    side = "YES" if net > 0 else "NO"
    return f"{side} {_fmt(abs(net), prefix='$', decimals=2)}"


def _cluster_gap_questions(gaps: List[str]) -> List[str]:
    gap_map = {
        "cluster control_state": "Which cluster-level gate is binding right now?",
        "cluster hedge action label": "Did the runner want SKEW, HEDGE, or UNWIND here?",
        "cluster action reason": "Why did the runner choose that cluster action?",
        "cluster hedge ratio": "What hedge ratio was the runner targeting?",
        "hedge target market": "Which market or cluster was the intended hedge target?",
        "cluster_exposure payload missing": "Which event cluster is carrying the risk right now?",
    }
    return [gap_map.get(gap, gap) for gap in gaps]


def _selection_gap_questions(gaps: List[str]) -> List[str]:
    gap_map = {
        "selection candidate diagnostics missing": "Which markets were even considered on this cycle?",
        "blocking market id": "Which active market blocked this candidate?",
        "blocking cluster id": "Which event cluster suppressed this candidate?",
        "blocking reason": "What multi-market rule caused the suppression?",
    }
    return [gap_map.get(gap, gap) for gap in gaps]


def _hedge_action_label(raw: Any) -> str:
    if raw in (None, "", "NONE"):
        return "N/A"
    return str(raw).replace("_", " ").upper()


def _stale_inventory_label(state: Any, stale_market_count: Any = None) -> str:
    text = str(state or "").strip().lower()
    if text in {"stale", "true"}:
        count = int(stale_market_count or 0)
        return f"STALE ({count})" if count > 0 else "STALE"
    return "FRESH"


def _fmt_optional_ms(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "N/A"
    except Exception:
        if value is None:
            return "N/A"
    try:
        return _duration_str(float(value))
    except (TypeError, ValueError):
        return "N/A"


def _format_optional_ratio(value: Any) -> str:
    try:
        if value is None or pd.isna(value):
            return "N/A"
        return f"{float(value) * 100.0:.1f}%"
    except Exception:
        return "N/A"


def _format_distribution_text(distribution: Any) -> str:
    if distribution in (None, "", {}, []):
        return "N/A"
    if isinstance(distribution, dict):
        parts: List[str] = []
        for key in ("p50", "p90", "p95", "max"):
            if key not in distribution:
                continue
            value = distribution.get(key)
            parts.append(f"{key}={_fmt_optional_ms(value)}")
        if parts:
            return ", ".join(parts)
    return str(distribution)


def _build_operator_brief(runtime_snapshot: Dict[str, Any], explainer_rows: pd.DataFrame) -> Dict[str, str]:
    control = runtime_snapshot.get("control_state") if isinstance(runtime_snapshot.get("control_state"), dict) else {}
    state = str(runtime_snapshot.get("state") or runtime_snapshot.get("book_health") or "unknown")
    quoteable = bool(runtime_snapshot.get("quoteable"))
    selected_reason = str(runtime_snapshot.get("selected_reason") or "n/a")
    regime = _extract_regime(runtime_snapshot)
    pnl_total = float(runtime_snapshot.get("total_pnl") or 0.0)
    fills = int(runtime_snapshot.get("fills") or 0)

    if bool(control.get("kill_switch_enabled")):
        return {
            "headline": "Kill switch is active",
            "summary": "Trading is stopped until the kill switch is cleared.",
            "accent": "#ff3b5c",
            "detail": f"State: {state} | Regime: {regime}",
        }
    if bool(control.get("flatten_only_mode")):
        return {
            "headline": "Flattening inventory only",
            "summary": "New risk is blocked while the bot reduces existing exposure.",
            "accent": "#fcee0a",
            "detail": f"Selected reason: {_humanize_reason_codes(selected_reason)}",
        }
    if explainer_rows.empty and not quoteable and fills <= 0:
        return {
            "headline": "Waiting on market conditions",
            "summary": f"{_humanize_reason_codes(selected_reason)}. No fills recorded in this session yet.",
            "accent": "#fcee0a",
            "detail": f"State: {state} | Regime: {regime} | Session PnL: {_fmt_money(pnl_total)}",
        }

    if not explainer_rows.empty:
        latest = explainer_rows.iloc[0].to_dict()
        decision = str(latest.get("decision_summary") or latest.get("action") or "Waiting")
        plain = str(latest.get("plain_english") or "").strip()
        reasons = _humanize_reason_codes(latest.get("reason_codes"))
        if decision.lower() == "quote":
            headline = "Trading now"
            accent = "#05ffa1"
            summary = plain or "The market is quoteable and the bot is posting liquidity."
        elif decision.lower() == "freeze":
            headline = "Trading is frozen"
            accent = "#ff3b5c"
            summary = plain or reasons
        else:
            headline = "Waiting for a better setup"
            accent = "#fcee0a"
            summary = plain or reasons
        return {
            "headline": headline,
            "summary": summary,
            "accent": accent,
            "detail": f"Decision: {decision} | Reason: {reasons} | Regime: {regime}",
        }

    if quoteable:
        return {
            "headline": "Ready to trade",
            "summary": "The market is quoteable, but no recent decision explanation is available yet.",
            "accent": "#05ffa1",
            "detail": f"Selected reason: {_humanize_reason_codes(selected_reason)} | Regime: {regime}",
        }
    return {
        "headline": "Waiting on market conditions",
        "summary": _humanize_reason_codes(selected_reason),
        "accent": "#fcee0a",
        "detail": f"State: {state} | Regime: {regime}",
    }


def _render_glass_metric(label: str, value: str, accent: str = "#00f0ff") -> None:
    assert st is not None
    st.markdown(
        f"""
        <div style="padding:12px 14px;border:1px solid rgba(255,255,255,0.08);border-radius:16px;
                    background:rgba(255,255,255,0.05);backdrop-filter:blur(14px);
                    box-shadow:0 10px 30px rgba(0,0,0,0.20);min-height:88px;">
          <div style="font-size:0.74em;letter-spacing:0.12em;text-transform:uppercase;color:#9aa4b2;">{label}</div>
          <div style="margin-top:6px;font-size:1.15em;font-weight:700;color:{accent};font-family:Orbitron,monospace;">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_compact_panel(title: str, rows: List[Tuple[str, Any]], accent: str = "#00f0ff") -> None:
    assert st is not None
    body = "".join(
        f"""
        <div style="display:flex;justify-content:space-between;gap:12px;padding:8px 0;
                    border-top:1px solid rgba(255,255,255,0.06);">
          <div style="color:#7a8599;text-transform:uppercase;letter-spacing:0.10em;font-size:0.68em;">{label}</div>
          <div style="color:#e6edf3;font-size:0.94em;font-weight:600;text-align:right;max-width:65%;
                      overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{value if value not in (None, "") else "N/A"}</div>
        </div>
        """
        for label, value in rows
    )
    st.markdown(
        f"""
        <div style="padding:14px 16px;border:1px solid rgba(255,255,255,0.08);border-radius:18px;
                    background:rgba(255,255,255,0.04);backdrop-filter:blur(14px);
                    box-shadow:0 10px 28px rgba(0,0,0,0.22);">
          <div style="color:{accent};font-size:0.72em;font-family:Orbitron,monospace;
                      letter-spacing:0.14em;text-transform:uppercase;margin-bottom:2px;">{title}</div>
          {body}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _display_runtime_table(df: pd.DataFrame, columns: List[str]) -> None:
    assert st is not None
    if df.empty:
        st.caption("No rows yet.")
        return
    show = df.copy()
    for col in columns:
        if col not in show.columns:
            show[col] = None
    st.dataframe(show[columns], use_container_width=True, hide_index=True)


def _queue_command(db_path: Path, *, command_type: str, payload: Optional[Dict[str, Any]] = None, scope: str = "global") -> None:
    assert st is not None
    try:
        command_id = da.queue_control_command(
            command_type=command_type,
            payload=payload or {},
            scope=scope,
            requested_by="dashboard",
            db_path=db_path,
        )
    except Exception as exc:
        st.error(f"Failed to queue command: {exc}")
        return
    st.success(f"Queued {command_type} as {command_id}")


def _render_run_control(db_path: Path, runtime_snapshot: Dict[str, Any]) -> None:
    assert st is not None
    control = da.get_control_plane_snapshot(db_path=db_path)
    mode = str(runtime_snapshot.get("mode") or "UNKNOWN").upper()
    st.markdown("### Run control")
    cols = st.columns(4)
    with cols[0]:
        _render_glass_metric("Trading", "ENABLED" if control.get("trading_enabled", True) else "PAUSED", accent="#05ffa1" if control.get("trading_enabled", True) else "#fcee0a")
    with cols[1]:
        _render_glass_metric("Kill switch", "ON" if control.get("kill_switch_enabled") else "OFF", accent="#ff3b5c" if control.get("kill_switch_enabled") else "#05ffa1")
    with cols[2]:
        _render_glass_metric("Flatten-only", "ON" if control.get("flatten_only_mode") else "OFF", accent="#fcee0a" if control.get("flatten_only_mode") else "#05ffa1")
    with cols[3]:
        _render_glass_metric("Pending cmds", str(control.get("pending_count") or 0), accent="#fcee0a")

    info_cols = st.columns(4)
    with info_cols[0]:
        _render_glass_metric("Cycle", _fmt(control.get("cycle_secs"), suffix="s", decimals=1))
    with info_cols[1]:
        _render_glass_metric("Safe profile", str(control.get("safe_risk_profile") or "custom").upper())
    with info_cols[2]:
        _render_glass_metric("Allocated equity", _fmt(control.get("strategy_allocated_equity"), prefix="$", decimals=0))
    with info_cols[3]:
        _render_glass_metric("Risk warning", "ON" if control.get("risk_warning_triggered") else "OFF", accent="#fcee0a" if control.get("risk_warning_triggered") else "#05ffa1")

    btn_cols = st.columns(5 if mode == "PAPER" else 4)
    with btn_cols[0]:
        if st.button("Pause", key=f"pause_{db_path}", use_container_width=True):
            _queue_command(db_path, command_type="pause_trading")
    with btn_cols[1]:
        if st.button("Resume", key=f"resume_{db_path}", use_container_width=True):
            _queue_command(db_path, command_type="resume_trading")
    with btn_cols[2]:
        if st.button("Cancel Quotes", key=f"cancel_all_{db_path}", use_container_width=True):
            _queue_command(db_path, command_type="cancel_all_quotes")
    with btn_cols[3]:
        if st.button("Kill Switch", key=f"kill_on_{db_path}", use_container_width=True):
            _queue_command(db_path, command_type="kill_switch_on")
    if mode == "PAPER":
        with btn_cols[4]:
            if st.button("Safe Restart", key=f"safe_restart_{db_path}", use_container_width=True):
                _queue_command(db_path, command_type="restart_paper_run_safe_profile")
        if control.get("kill_switch_enabled"):
            if st.button("Clear Kill Switch", key=f"kill_off_{db_path}", use_container_width=True):
                _queue_command(db_path, command_type="kill_switch_off")


def _render_strategy_settings_controls(db_path: Path, runtime_snapshot: Dict[str, Any]) -> None:
    assert st is not None
    settings = da.get_strategy_settings_view(db_path=db_path)
    current = dict(settings.get("current") or {})
    pending_patch = dict(settings.get("pending_patch") or {})
    last_applied_patch = dict(settings.get("last_applied_patch") or {})
    mode = str(runtime_snapshot.get("mode") or "UNKNOWN").upper()

    st.markdown("### Strategy settings")
    if mode != "PAPER":
        st.caption("Live v1 settings remain read-only. Emergency controls only.")
    cols = st.columns(3)
    with cols[0]:
        _render_compact_panel("Current", [(k, current.get(k)) for k in ["safe_risk_profile", "strategy_allocated_equity", "trade_size", "max_size", "hard_position_cap", "quote_spread_multiplier"]], accent="#00f0ff")
    with cols[1]:
        _render_compact_panel("Pending", [(k, pending_patch.get(k)) for k in ["safe_risk_profile", "strategy_allocated_equity", "trade_size", "max_size", "hard_position_cap", "quote_spread_multiplier"]], accent="#fcee0a")
    with cols[2]:
        _render_compact_panel("Last applied", [(k, last_applied_patch.get(k)) for k in ["safe_risk_profile", "strategy_allocated_equity", "trade_size", "max_size", "hard_position_cap", "quote_spread_multiplier"]], accent="#05ffa1")

    if mode != "PAPER":
        return

    with st.form(f"settings_patch_{db_path}"):
        c1, c2, c3 = st.columns(3)
        with c1:
            safe_risk_profile = st.selectbox("Safe risk profile", options=["custom", "200", "500", "1000"], index=max(0, ["custom", "200", "500", "1000"].index(str(current.get("safe_risk_profile") or "custom")) if str(current.get("safe_risk_profile") or "custom") in ["custom", "200", "500", "1000"] else 0))
            strategy_allocated_equity = st.number_input("Allocated equity", min_value=0.0, value=float(current.get("strategy_allocated_equity") or 0.0), step=50.0)
            trade_size = st.number_input("Trade size", min_value=0.0, value=float(current.get("trade_size") or 0.0), step=1.0)
            max_size = st.number_input("Max size", min_value=0.0, value=float(current.get("max_size") or 0.0), step=1.0)
            min_size = st.number_input("Min size", min_value=0.0, value=float(current.get("min_size") or 0.0), step=1.0)
            fallback_size = st.number_input("Fallback size", min_value=0.0, value=float(current.get("fallback_size") or 0.0), step=1.0)
        with c2:
            hard_position_cap = st.number_input("Hard position cap", min_value=0.0, value=float(current.get("hard_position_cap") or 0.0), step=1.0)
            min_order_size = st.number_input("Min order size", min_value=0.0, value=float(current.get("min_order_size") or 0.0), step=1.0)
            within_pct = st.number_input("Within pct", min_value=0.0, value=float(current.get("within_pct") or 0.0), step=0.01, format="%.4f")
            cycle_secs = st.number_input("Cycle secs", min_value=0.1, value=float(current.get("cycle_secs") or 1.0), step=0.1, format="%.2f")
            refresh_market_secs = st.number_input("Refresh market secs", min_value=1.0, value=float(current.get("refresh_market_secs") or 60.0), step=5.0)
            quote_spread_multiplier = st.number_input("Spread multiplier", min_value=0.1, value=float(current.get("quote_spread_multiplier") or 1.0), step=0.1, format="%.2f")
        with c3:
            per_event_loss_pct = st.number_input("Per-event loss pct", min_value=0.0, value=float(current.get("per_event_loss_pct") or 0.0), step=0.01, format="%.4f")
            per_day_loss_pct = st.number_input("Per-day loss pct", min_value=0.0, value=float(current.get("per_day_loss_pct") or 0.0), step=0.01, format="%.4f")
            stale_duration_scale = st.number_input("Stale duration scale", min_value=0.001, value=float(current.get("stale_duration_scale") or 0.0), step=0.001, format="%.4f")
            maker_exit_grace_secs = st.number_input("Maker exit grace secs", min_value=0.0, value=float(current.get("maker_exit_grace_secs") or 0.0), step=1.0)
            cross_escalation_drawdown_pct = st.number_input("Cross escalation pct", min_value=0.0, value=float(current.get("cross_escalation_drawdown_pct") or 0.0), step=0.001, format="%.4f")
            market_dwell_secs = st.number_input("Market dwell secs", min_value=0.0, value=float(current.get("market_dwell_secs") or 0.0), step=5.0)
        submit = st.form_submit_button("Stage config patch", use_container_width=True)
        if submit:
            candidate_patch = {
                "safe_risk_profile": safe_risk_profile,
                "strategy_allocated_equity": strategy_allocated_equity,
                "trade_size": trade_size,
                "max_size": max_size,
                "min_size": min_size,
                "fallback_size": fallback_size,
                "hard_position_cap": hard_position_cap,
                "min_order_size": min_order_size,
                "within_pct": within_pct,
                "cycle_secs": cycle_secs,
                "refresh_market_secs": refresh_market_secs,
                "quote_spread_multiplier": quote_spread_multiplier,
                "per_event_loss_pct": per_event_loss_pct,
                "per_day_loss_pct": per_day_loss_pct,
                "stale_duration_scale": stale_duration_scale,
                "maker_exit_grace_secs": maker_exit_grace_secs,
                "cross_escalation_drawdown_pct": cross_escalation_drawdown_pct,
                "market_dwell_secs": market_dwell_secs,
            }
            patch = {}
            for key, value in candidate_patch.items():
                current_value = current.get(key)
                if isinstance(value, str):
                    if str(current_value or "") != value:
                        patch[key] = value
                    continue
                try:
                    current_float = float(current_value) if current_value is not None else None
                except (TypeError, ValueError):
                    current_float = None
                if current_float is None or abs(float(value) - current_float) > 1e-9:
                    patch[key] = float(value)
            if not patch:
                st.info("No setting changes to stage.")
            else:
                _queue_command(db_path, command_type="apply_config_patch", payload={"patch": patch})


def _render_operator_portfolio_controls(db_path: Path, runtime_snapshot: Dict[str, Any]) -> None:
    assert st is not None
    health = runtime_snapshot.get("active_market_health") if isinstance(runtime_snapshot.get("active_market_health"), dict) else {}
    portfolio_risk = health.get("portfolio_risk") if isinstance(health.get("portfolio_risk"), dict) else {}
    broker_stats = health.get("broker_stats") if isinstance(health.get("broker_stats"), dict) else {}
    market_id = str(runtime_snapshot.get("market") or "")
    event_id = str(health.get("event_id") or "")
    mode = str(runtime_snapshot.get("mode") or "UNKNOWN").upper()

    st.markdown("### Portfolio management")
    cols = st.columns(4)
    with cols[0]:
        _render_glass_metric("Gross exposure", _fmt_money(portfolio_risk.get("gross_exposure") or 0.0))
    with cols[1]:
        _render_glass_metric("Unrealized", _fmt_money(portfolio_risk.get("unrealized_pnl") or 0.0), accent="#05ffa1" if float(portfolio_risk.get("unrealized_pnl") or 0.0) >= 0 else "#ff3b5c")
    with cols[2]:
        _render_glass_metric("Realized", _fmt_money(broker_stats.get("realized_net_pnl") or 0.0), accent="#05ffa1" if float(broker_stats.get("realized_net_pnl") or 0.0) >= 0 else "#ff3b5c")
    with cols[3]:
        _render_glass_metric("Active positions", str(int(portfolio_risk.get("active_positions") or 0)))
    if mode == "PAPER":
        action_cols = st.columns(2)
        with action_cols[0]:
            if st.button("Flatten event", key=f"flatten_event_{db_path}", use_container_width=True, disabled=not bool(event_id)):
                _queue_command(db_path, command_type="flatten_event_inventory", payload={"event_id": event_id}, scope="event")
        with action_cols[1]:
            if st.button("Flatten market", key=f"flatten_market_{db_path}", use_container_width=True, disabled=not bool(market_id)):
                _queue_command(db_path, command_type="flatten_market_inventory", payload={"market_id": market_id}, scope="market")
    else:
        st.caption("Live v1 portfolio actions remain read-only except pause / kill-switch controls.")


def _render_overnight_supervision(db_path: Path) -> None:
    assert st is not None
    rows = da.get_overnight_supervision_rows(db_path=db_path)
    alerts = da.get_runtime_alert_feed(db_path=db_path)
    commands = da.get_recent_control_commands(db_path=db_path, limit=12)
    st.markdown("### Overnight supervision")
    if rows.empty:
        st.caption("No overnight supervision rows yet.")
    else:
        _display_runtime_table(rows, [col for col in ["workstream", "owner", "status", "evidence", "next_task"] if col in rows.columns])
    if not alerts.empty:
        st.markdown("**Alert feed**")
        _display_runtime_table(alerts, [col for col in ["severity", "owner", "alert_type", "summary", "next_action"] if col in alerts.columns])
    with st.expander("Control audit", expanded=False):
        if not commands.empty:
            _display_runtime_table(commands, [col for col in ["requested_at_ms", "command_type", "scope", "status", "requested_by"] if col in commands.columns])
        else:
            st.caption("No control commands yet.")


# ── Enhancement A: Market Expiry Countdown ───────────────────────────────────


def _parse_expiry_ms(market_id: Optional[str]) -> Optional[int]:
    """Parse Unix timestamp (seconds) from slug suffix, e.g. xrp-updown-15m-1773702900."""
    m = re.search(r"-(\d{9,10})$", market_id or "")
    return int(m.group(1)) * 1000 if m else None


def _expiry_badge(market_id: Optional[str], *, time_to_expiry_ms: Optional[int] = None) -> str:
    """HTML badge showing time remaining until market close."""
    remaining_ms = time_to_expiry_ms
    if remaining_ms is None:
        expiry_ms = _parse_expiry_ms(market_id)
        if expiry_ms:
            remaining_ms = expiry_ms - int(_time_mod.time() * 1000)
    if remaining_ms is None:
        return ""
    if remaining_ms <= 0:
        return '<span style="color:#f87171;font-weight:600;margin-left:12px;">EXPIRED</span>'
    color = "#f87171" if remaining_ms < 120_000 else ("#facc15" if remaining_ms < 300_000 else "#6ee7b7")
    return f'<span style="color:{color};font-weight:600;margin-left:12px;">⏱ {_duration_str(float(remaining_ms))} to close</span>'


# ── Phase 0 Badge ───────────────────────────────────────────────────────────


def _phase0_badge(phase0: Dict[str, Any]) -> None:
    assert st is not None
    result = str(phase0.get("result") or "unknown").lower()
    rationale = str(phase0.get("rationale") or "")
    if result == "pass":
        st.markdown(
            f'<div class="ok"><b>Phase 0: PASS</b> — {rationale}</div>',
            unsafe_allow_html=True,
        )
    elif result in {"tunable_loss", "needs_review"}:
        st.markdown(
            f'<div class="warn"><b>Phase 0: {result.upper()}</b> — {rationale}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="alert"><b>Phase 0: {result.upper()}</b> — {rationale}</div>',
            unsafe_allow_html=True,
        )


# ── Health Banner (with expiry countdown) ────────────────────────────────────


def _render_health_banner(summary: Dict[str, Any], pnl: Dict[str, Any], sys_payload: Dict[str, Any]) -> None:
    assert st is not None
    runner = sys_payload.get("runner") or {}
    feed = sys_payload.get("feed") or {}
    mode = str(runner.get("mode") or summary.get("mode") or "?").upper()
    market_id = runner.get("market_id") or "None"
    has_books = bool(runner.get("has_books"))
    connected = bool(feed.get("connected"))
    book_updates = int(feed.get("applied_book_updates") or 0)
    health = sys_payload.get("active_market_health") if isinstance(sys_payload.get("active_market_health"), dict) else {}
    selection = sys_payload.get("selection") if isinstance(sys_payload.get("selection"), dict) else {}

    degraded: List[str] = []
    if not connected:
        degraded.append("feed disconnected")
    if not has_books:
        degraded.append("no books")

    health_class = "warn" if degraded else "ok"
    health_label = "DEGRADED" if degraded else "HEALTHY"
    expiry_html = _expiry_badge(
        market_id,
        time_to_expiry_ms=_time_to_expiry_ms(health, selection, runner),
    )

    dot_class = "live-dot" if not degraded else "live-dot-alert"
    banner_html = f"""
    <div class="{health_class}" style="display:flex;justify-content:space-between;align-items:center;padding:10px 18px;margin-bottom:8px;">
      <div>
        <span class="{dot_class}"></span>
        <b style="font-size:1.05em;font-family:Orbitron,monospace;letter-spacing:0.08em;">{health_label}</b>
        <span style="margin-left:14px;opacity:0.7;font-size:0.85em;">Mode: <b>{mode}</b></span>
        <span style="margin-left:14px;opacity:0.7;font-size:0.85em;">Market: <b>{market_id}</b></span>
        {expiry_html}
      </div>
      <div style="text-align:right;font-size:0.85em;">
        <span style="opacity:0.7;">Book updates: <b>{book_updates:,}</b></span>
        {f'<span style="margin-left:12px;color:#fcee0a;">{", ".join(degraded)}</span>' if degraded else ''}
      </div>
    </div>
    """
    st.markdown(banner_html, unsafe_allow_html=True)


# ── Enhancement C: Global Status Bar ─────────────────────────────────────────


def render_global_status_bar(db_path: Path) -> None:
    """Global status bar shown above all tabs. Call from app.py above st.tabs()."""
    assert st is not None
    try:
        pnl = da.get_paper_pnl_summary(db_path=db_path)
        sys_payload = da.get_latest_system_payload(db_path=db_path)
        inv_df = da.get_per_token_inventory(db_path=db_path)
    except Exception:
        return

    runner = sys_payload.get("runner") or {}
    mode = str(runner.get("mode") or "?").upper()
    market_id = runner.get("market_id") or "—"
    total_pnl = float(pnl.get("total_pnl") or 0.0)
    fills = int(pnl.get("fills") or 0)
    now_str = _short_time(int(_time_mod.time() * 1000))
    pnl_color = _pnl_color(total_pnl)

    hedge_str = "N/A"
    if not inv_df.empty and len(inv_df) >= 2:
        pos_a = float(inv_df.iloc[0].get("yes_qty") or 0.0)
        pos_b = float(inv_df.iloc[1].get("yes_qty") or 0.0)
        if max(pos_a, pos_b) > 0:
            hedge_pct = min(pos_a, pos_b) / max(pos_a, pos_b) * 100.0
            hedge_str = f"{hedge_pct:.0f}%"

    mode_color = "#05ffa1" if mode == "PAPER" else ("#00f0ff" if mode == "OBSERVE" else "#ff2a6d")
    live_dot_class = "live-dot" if fills > 0 else "live-dot-warn"

    st.markdown(
        f'<div style="background:rgba(15,15,25,0.85);backdrop-filter:blur(12px);border-radius:8px;padding:8px 20px;'
        f'display:flex;justify-content:space-between;align-items:center;'
        f'font-size:0.82em;margin-bottom:8px;border:1px solid rgba(0,240,255,0.2);'
        f'box-shadow:0 0 12px rgba(0,240,255,0.08);">'
        f'<span><span class="{live_dot_class}"></span>'
        f'<span style="color:{mode_color};font-family:Orbitron,monospace;font-weight:700;letter-spacing:0.1em;">{mode}</span></span>'
        f'<span style="font-family:Orbitron,monospace;font-size:0.85em;color:#7a8599;">PNL</span>&nbsp;'
        f'<b style="color:{pnl_color};font-family:Orbitron,monospace;font-size:1.1em;">${total_pnl:+.2f}</b>'
        f'<span style="font-family:Orbitron,monospace;font-size:0.85em;color:#7a8599;">FILLS</span>&nbsp;'
        f'<b style="font-family:Orbitron,monospace;">{fills}</b>'
        f'<span style="font-family:Orbitron,monospace;font-size:0.85em;color:#7a8599;">HEDGE</span>&nbsp;'
        f'<b style="font-family:Orbitron,monospace;">{hedge_str}</b>'
        f'<span style="opacity:0.5;max-width:240px;overflow:hidden;text-overflow:ellipsis;font-size:0.85em;">{market_id}</span>'
        f'<span style="opacity:0.4;font-family:Orbitron,monospace;font-size:0.8em;">{now_str}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Compact Metrics Sidebar (with Enhancement D + G) ─────────────────────────


def _render_metrics_sidebar(
    pnl: Dict[str, Any],
    summary: Dict[str, Any],
    sys_payload: Dict[str, Any],
    updated_at: Optional[int],
    curve: pd.DataFrame,
    eq_df: pd.DataFrame,
    db_path: Optional[Path] = None,
) -> None:
    assert st is not None
    realized_net = float(pnl.get("realized_net_pnl") or 0.0)
    unrealized = float(pnl.get("unrealized_pnl") or 0.0)
    total_pnl = float(pnl.get("total_pnl") or 0.0)
    fills = int(pnl.get("fills") or 0)
    decisions = int(pnl.get("decisions") or 0)
    fees = float(pnl.get("cumulative_fees") or 0.0)
    turnover = float(pnl.get("turnover") or 0.0)
    placed = int(summary.get("placed_orders") or 0)
    canceled = int(summary.get("canceled_orders") or 0)
    fill_rate = float(summary.get("fill_rate") or 0.0)
    max_dd_abs = float(pnl.get("max_drawdown_abs") or 0.0)

    eq_summary = summary.get("execution_quality") or {}
    avg_net_edge = float(eq_summary.get("avg_net_edge_bps") or 0.0) if eq_summary.get("avg_net_edge_bps") is not None else None
    avg_spread = float(eq_summary.get("avg_realized_spread_bps") or 0.0) if eq_summary.get("avg_realized_spread_bps") is not None else None
    avg_m1s = float(eq_summary.get("avg_markout_1s_bps") or 0.0) if eq_summary.get("avg_markout_1s_bps") is not None else None

    w = int(pnl.get("win_count") or 0)
    l_count = int(pnl.get("loss_count") or 0)
    wr = (w / max(1, w + l_count)) * 100

    action_counts = da.get_decision_action_counts(db_path=db_path)
    quote_n = int(action_counts.get("QUOTE", 0))
    skip_n = int(action_counts.get("SKIP", 0))
    freeze_n = int(action_counts.get("FREEZE", 0))

    pnl_color = _pnl_color(total_pnl)
    unreal_color = _pnl_color(unrealized)

    rows: List[Tuple[str, str]] = [
        ("PnL", f'<span style="color:{pnl_color};font-weight:600">${total_pnl:+.2f}</span>'),
        ("Realized", f'<span style="color:{_pnl_color(realized_net)};font-weight:600">${realized_net:+.2f}</span>'),
        ("Unrealized", f'<span style="color:{unreal_color};font-weight:600">${unrealized:+.2f}</span>'),
        ("Max DD", f'<span style="color:#f87171;font-weight:600">${max_dd_abs:.2f}</span>'),
        ("Turnover", f"<b>${turnover:.0f}</b>"),
        ("Fees", f"<b>${fees:.3f}</b>"),
        ("Fills / Orders", f"<b>{fills} / {placed}</b>"),
        ("Fill Rate", f"<b>{fill_rate * 100:.1f}%</b>"),
        ("Canceled", f"<b>{canceled:,}</b>"),
        ("Win/Loss", f"<b>{w}/{l_count}</b> <span style='opacity:0.7'>({wr:.0f}%)</span>"),
        ("Decisions", f"<b>{decisions:,}</b>"),
        ("QUOTE/SKIP/FREEZE", f"<b>{quote_n}/{skip_n}/{freeze_n}</b>"),
    ]
    if avg_net_edge is not None:
        rows.append(("Net Edge", f"<b>{avg_net_edge:.1f} bps</b>"))
    if avg_spread is not None:
        rows.append(("Spread", f"<b>{avg_spread:.1f} bps</b>"))
    if avg_m1s is not None:
        rows.append(("Markout 1s", f"<b>{avg_m1s:.1f} bps</b>"))

    # Enhancement D: Cycles/sec gauge
    if (
        not curve.empty
        and "ts_ms" in curve.columns
        and decisions > 0
        and updated_at
    ):
        first_ts = int(curve["ts_ms"].iloc[0])
        elapsed_secs = max(1.0, (updated_at - first_ts) / 1000.0)
        cycles_per_sec = decisions / elapsed_secs
        cps_color = "#6ee7b7" if cycles_per_sec > 0.5 else "#facc15"
        rows.append(("Cycles/sec", f'<b style="color:{cps_color}">{cycles_per_sec:.2f}</b>'))

    # Enhancement G: Spread capture rate
    if not eq_df.empty and "net_edge_bps" in eq_df.columns:
        pos_edge = int((eq_df["net_edge_bps"] > 0).sum())
        total_eq = len(eq_df)
        capture_pct = pos_edge / total_eq * 100 if total_eq > 0 else 0.0
        rows.append(("Spread Capture", f"<b>{capture_pct:.0f}%</b> ({pos_edge}/{total_eq})"))

    items_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(0,240,255,0.06);">'
        f'<span style="color:#7a8599;font-size:0.78em;text-transform:uppercase;letter-spacing:0.06em;">{name}</span>'
        f'<span style="font-size:0.88em;font-family:Orbitron,Rajdhani,monospace;">{val}</span>'
        f"</div>"
        for name, val in rows
    )

    ts_line = (
        f'<div style="margin-top:8px;font-size:0.72em;opacity:0.4;font-family:Orbitron,monospace;">UPD {_short_time(updated_at)}</div>'
        if updated_at
        else ""
    )

    st.markdown(
        f'<div class="neon-card" style="padding:12px 14px;">'
        f'<div class="cyber-label" style="margin-bottom:8px;">RUN STATS</div>'
        f"{items_html}{ts_line}"
        f"</div>",
        unsafe_allow_html=True,
    )


# ── Multi-Symbol Grid ─────────────────────────────────────────────────────────


def _render_ticker_grid(sys_payload: Dict[str, Any], db_path: Path) -> None:
    """4-column ticker card grid with click-to-expand detail per symbol."""
    assert st is not None
    sym_df = da.get_per_symbol_summary(db_path=db_path)
    active_market = (sys_payload.get("runner") or {}).get("market_id") or ""
    symbols = ["BTC", "ETH", "SOL", "XRP"]

    if "mm_selected_symbol" not in st.session_state:
        st.session_state["mm_selected_symbol"] = None
    selected = st.session_state["mm_selected_symbol"]

    cols = st.columns(4)
    for col, sym in zip(cols, symbols):
        row = None
        if not sym_df.empty and "symbol" in sym_df.columns:
            matching = sym_df[sym_df["symbol"] == sym]
            if not matching.empty:
                row = matching.iloc[0]

        is_active = sym.lower() in active_market.lower()
        is_selected = (selected == sym)
        fills = int(row["fills"]) if row is not None and pd.notna(row.get("fills")) else 0
        pnl = float(row["realized_net_pnl"]) if row is not None and pd.notna(row.get("realized_net_pnl")) else 0.0
        decisions = int(row["total_decisions"]) if row is not None and pd.notna(row.get("total_decisions")) else 0
        markets = int(row["markets"]) if row is not None and pd.notna(row.get("markets")) else 0

        pnl_color = _pnl_color(pnl)
        if is_selected:
            border_color = "#05ffa1"
            glow = "box-shadow:0 0 18px rgba(5,255,161,0.35);"
        elif is_active:
            border_color = "#05ffa1"
            glow = "box-shadow:0 0 12px rgba(5,255,161,0.2);"
        elif fills > 0:
            border_color = "#00f0ff"
            glow = ""
        else:
            border_color = "rgba(0,240,255,0.12)"
            glow = ""

        active_badge = (
            '<div style="color:#05ffa1;font-size:0.7em;font-family:Orbitron,monospace;letter-spacing:0.12em;margin-bottom:4px;">'
            '<span class="live-dot"></span>ACTIVE</div>'
            if is_active
            else '<div style="font-size:0.7em;margin-bottom:4px;opacity:0;">&nbsp;</div>'
        )

        with col:
            st.markdown(
                f'<div class="neon-card" style="border-color:{border_color};text-align:center;{glow}">'
                f'<div style="font-size:1.15em;font-weight:700;font-family:Orbitron,monospace;letter-spacing:0.12em;'
                f'color:{"#05ffa1" if is_active else "#00f0ff"};">{sym}</div>'
                f"{active_badge}"
                f'<div class="cyber-label" style="margin-top:6px;">FILLS</div>'
                f'<div style="font-family:Orbitron,monospace;font-size:1.1em;font-weight:700;">{fills}</div>'
                f'<div class="cyber-label" style="margin-top:4px;">PNL</div>'
                f'<div style="color:{pnl_color};font-family:Orbitron,monospace;font-weight:700;">${pnl:+.2f}</div>'
                f'<div style="font-size:0.68em;opacity:0.4;margin-top:6px;">{markets} mkts | {decisions} decisions</div>'
                f"</div>",
                unsafe_allow_html=True,
            )
            btn_label = "▼ COLLAPSE" if is_selected else "▶ DETAILS"
            if st.button(btn_label, key=f"mm_card_{sym}", use_container_width=True):
                st.session_state["mm_selected_symbol"] = None if is_selected else sym
                st.rerun()

    # Expanded detail for selected symbol
    if selected:
        st.divider()
        is_active_sym = selected.lower() in active_market.lower()
        if is_active_sym:
            _render_active_market_detail(sys_payload, db_path)
        else:
            st.markdown(
                f'<div class="neon-card" style="padding:16px;">'
                f'<div style="font-family:Orbitron,monospace;font-size:1.1em;color:#00f0ff;margin-bottom:8px;">{selected}</div>'
                f'<div style="color:#7a8599;font-size:0.85em;">Not the active market — no live book data available. '
                f'Historical stats shown in Portfolio tab.</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ── Charts ───────────────────────────────────────────────────────────────────


def _pnl_drawdown_chart(curve: pd.DataFrame) -> None:
    assert st is not None
    if alt is None or curve.empty or "ts_ms" not in curve.columns:
        st.caption("PnL chart unavailable.")
        return

    plot = curve[["ts_ms"]].copy()
    plot["ts"] = pd.to_datetime(curve["ts_ms"], unit="ms", utc=True)
    for col in ["realized_net_pnl", "total_pnl"]:
        if col in curve.columns:
            plot[col] = curve[col]
    if "total_pnl" in plot.columns:
        plot["equity_peak"] = plot["total_pnl"].cummax()
        plot["drawdown"] = plot["total_pnl"] - plot["equity_peak"]
    else:
        plot["drawdown"] = 0.0

    pnl_cols = [c for c in ["realized_net_pnl", "total_pnl"] if c in plot.columns]
    if not pnl_cols:
        return

    melted = plot.melt(id_vars="ts", value_vars=pnl_cols, var_name="series", value_name="pnl_usd")
    melted["series"] = melted["series"].map(
        {"realized_net_pnl": "Realized Net", "total_pnl": "Total (incl. unreal.)"}
    )

    pnl_lines = (
        alt.Chart(melted)
        .mark_line(interpolate="step-after")
        .encode(
            x=alt.X("ts:T", title="Time (UTC)", axis=alt.Axis(format="%H:%M")),
            y=alt.Y("pnl_usd:Q", title="PnL (USD)"),
            color=alt.Color(
                "series:N",
                scale=alt.Scale(
                    domain=["Realized Net", "Total (incl. unreal.)"],
                    range=["#6ee7b7", "#facc15"],
                ),
                legend=alt.Legend(orient="bottom-right"),
            ),
            tooltip=[
                alt.Tooltip("ts:T", title="Time", format="%H:%M:%S"),
                alt.Tooltip("series:N", title="Series"),
                alt.Tooltip("pnl_usd:Q", title="PnL (USD)", format=".4f"),
            ],
        )
    )

    dd_area = (
        alt.Chart(plot[["ts", "drawdown"]].dropna())
        .mark_area(opacity=0.25, color="#f87171", interpolate="step-after")
        .encode(x=alt.X("ts:T"), y=alt.Y("drawdown:Q"))
    )

    zero_rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#4b5563", strokeDash=[2, 2]).encode(y="y:Q")

    chart = (
        alt.layer(dd_area, zero_rule, pnl_lines)
        .properties(height=240, title="Cumulative PnL & Drawdown")
        .configure(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(labelColor="#9aa4b2", titleColor="#9aa4b2", gridColor="rgba(230,237,243,0.08)")
        .configure_title(color="#e6edf3")
        .configure_legend(labelColor="#9aa4b2", titleColor="#9aa4b2")
    )
    st.altair_chart(chart, use_container_width=True, key="pnl_drawdown_chart")


def _fill_rate_chart(curve: pd.DataFrame) -> None:
    assert st is not None
    if alt is None or curve.empty:
        return
    if "win_count" not in curve.columns or "loss_count" not in curve.columns:
        return
    plot = curve[["ts_ms", "win_count", "loss_count"]].copy()
    plot["ts"] = pd.to_datetime(plot["ts_ms"], unit="ms", utc=True)
    total = (plot["win_count"] + plot["loss_count"]).clip(lower=1)
    plot["win_rate"] = plot["win_count"] / total * 100.0
    plot = plot.dropna(subset=["win_rate"])
    if plot.empty:
        return

    chart = (
        alt.Chart(plot)
        .mark_line(color="#6ee7b7", interpolate="step-after")
        .encode(
            x=alt.X("ts:T", title="Time (UTC)", axis=alt.Axis(format="%H:%M")),
            y=alt.Y("win_rate:Q", title="Win rate (%)", scale=alt.Scale(domain=[0, 100])),
            tooltip=[
                alt.Tooltip("ts:T", title="Time", format="%H:%M:%S"),
                alt.Tooltip("win_rate:Q", title="Win rate (%)", format=".1f"),
            ],
        )
        .properties(height=140, title="Fill Win Rate Over Time")
        .configure(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(labelColor="#9aa4b2", titleColor="#9aa4b2", gridColor="rgba(230,237,243,0.08)")
        .configure_title(color="#e6edf3")
    )
    st.altair_chart(chart, use_container_width=True, key="fill_rate_chart")


def _markout_chart(eq_df: pd.DataFrame) -> None:
    """Markout chart as line (not scatter)."""
    assert st is not None
    if alt is None or eq_df.empty:
        return
    cols = [c for c in ["markout_1s_bps", "markout_5s_bps"] if c in eq_df.columns]
    if not cols:
        return

    plot = eq_df[["ts"] + cols].copy() if "ts" in eq_df.columns else eq_df[cols].copy()
    if "ts" not in plot.columns:
        return
    melted = plot.melt(id_vars="ts", value_vars=cols, var_name="horizon", value_name="bps")
    melted["horizon"] = melted["horizon"].map({"markout_1s_bps": "1s markout", "markout_5s_bps": "5s markout"})
    melted = melted.dropna(subset=["bps"])
    if melted.empty:
        return

    rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#f87171", strokeDash=[4, 4]).encode(y="y:Q")
    lines = (
        alt.Chart(melted)
        .mark_line(point=True, interpolate="monotone")
        .encode(
            x=alt.X("ts:T", title="Time (UTC)", axis=alt.Axis(format="%H:%M")),
            y=alt.Y("bps:Q", title="Markout (bps)"),
            color=alt.Color(
                "horizon:N",
                scale=alt.Scale(domain=["1s markout", "5s markout"], range=["#6ee7b7", "#facc15"]),
                legend=alt.Legend(orient="bottom-right"),
            ),
            tooltip=[
                alt.Tooltip("ts:T", title="Time", format="%H:%M:%S"),
                alt.Tooltip("horizon:N"),
                alt.Tooltip("bps:Q", title="Markout (bps)", format=".1f"),
            ],
        )
    )
    chart = (
        (rule + lines)
        .properties(height=160, title="Fill Markout (bps) — positive = good")
        .configure(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(labelColor="#9aa4b2", titleColor="#9aa4b2", gridColor="rgba(230,237,243,0.08)")
        .configure_title(color="#e6edf3")
        .configure_legend(labelColor="#9aa4b2", titleColor="#9aa4b2")
    )
    st.altair_chart(chart, use_container_width=True, key="markout_chart")


def _fill_breakdown_chart(db_path: Path, sys_payload: Dict[str, Any]) -> None:
    """Fill count by YES/NO and side — only active market tokens."""
    assert st is not None
    if alt is None:
        return

    active_ids = _active_token_ids(sys_payload)
    yn_map = _resolve_yes_no_tokens(sys_payload)

    fill_counts = da.get_per_token_fill_counts(db_path=db_path)
    if fill_counts.empty:
        return

    if active_ids:
        fill_counts = fill_counts[fill_counts["token_id"].isin(active_ids)]

    if fill_counts.empty:
        return

    plot = fill_counts[["token_id", "side", "fill_count"]].copy()
    plot["token_label"] = plot["token_id"].map(lambda t: yn_map.get(str(t), _token_label(str(t))))

    chart = (
        alt.Chart(plot)
        .mark_bar()
        .encode(
            x=alt.X("token_label:N", title="Token", axis=alt.Axis(labelAngle=0)),
            y=alt.Y("fill_count:Q", title="Fill Count"),
            color=alt.Color(
                "side:N",
                scale=alt.Scale(domain=["buy", "sell"], range=["#6ee7b7", "#f87171"]),
                legend=alt.Legend(title="Side"),
            ),
            xOffset="side:N",
            tooltip=[
                alt.Tooltip("token_label:N", title="Token"),
                alt.Tooltip("side:N", title="Side"),
                alt.Tooltip("fill_count:Q", title="Fills"),
            ],
        )
        .properties(height=160, title="Fill Count (YES / NO)")
        .configure(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(labelColor="#9aa4b2", titleColor="#9aa4b2", gridColor="rgba(230,237,243,0.08)")
        .configure_title(color="#e6edf3")
        .configure_legend(labelColor="#9aa4b2", titleColor="#9aa4b2")
    )
    st.altair_chart(chart, use_container_width=True, key="fill_breakdown_chart")


# ── Enhancement E: Fill Timeline Sparkline ────────────────────────────────────


def _fill_timeline_sparkline(db_path: Path, sys_payload: Dict[str, Any]) -> None:
    """Tick mark timeline of recent fills, colored by side."""
    assert st is not None
    if alt is None:
        return
    fills_df = da.get_fills_recent(limit=50, db_path=db_path)
    if fills_df.empty or "ts" not in fills_df.columns:
        return
    yn_map = _resolve_yes_no_tokens(sys_payload)
    plot = fills_df[["ts", "side", "token_id"]].copy()
    plot["token"] = plot["token_id"].map(lambda t: yn_map.get(str(t), "?"))
    plot = plot.dropna(subset=["ts"])
    if plot.empty:
        return

    chart = (
        alt.Chart(plot)
        .mark_tick(thickness=2, size=16)
        .encode(
            x=alt.X("ts:T", title="", axis=alt.Axis(format="%H:%M")),
            y=alt.Y("token:N", title="", axis=alt.Axis(labelColor="#9aa4b2")),
            color=alt.Color(
                "side:N",
                scale=alt.Scale(domain=["buy", "sell"], range=["#6ee7b7", "#f87171"]),
                legend=alt.Legend(orient="right", title="Side"),
            ),
            tooltip=[
                alt.Tooltip("ts:T", title="Time", format="%H:%M:%S"),
                alt.Tooltip("side:N", title="Side"),
                alt.Tooltip("token:N", title="Token"),
            ],
        )
        .properties(height=80, title="Fill Timeline")
        .configure(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(labelColor="#9aa4b2", titleColor="#9aa4b2", gridColor="rgba(230,237,243,0.08)")
        .configure_title(color="#e6edf3")
        .configure_legend(labelColor="#9aa4b2", titleColor="#9aa4b2")
    )
    st.altair_chart(chart, use_container_width=True, key="fill_timeline_chart")


# ── Enhancement F: Alert Feed ─────────────────────────────────────────────────


def _render_alert_feed(sys_payload: Dict[str, Any]) -> None:
    """Compact alert chips for significant events."""
    assert st is not None
    alerts: List[Tuple[str, str, str]] = []  # (level, message, color)

    last_error = sys_payload.get("last_error")
    if last_error:
        alerts.append(("ERROR", str(last_error)[:80], "#f87171"))

    flow_stats = sys_payload.get("flow_stats") or {}
    ec_count = int(flow_stats.get("emergency_cancel_count") or 0)
    if ec_count > 0:
        alerts.append(("EMERGENCY", f"{ec_count} emergency cancel(s)", "#facc15"))

    merge_stats = sys_payload.get("merge_stats") or {}
    merge_count = int(merge_stats.get("merge_count") or 0)
    total_merged = float(merge_stats.get("total_merged_amount") or 0.0)
    if merge_count > 0:
        alerts.append(("MERGE", f"{merge_count} merge(s) · {total_merged:.0f} shares", "#60a5fa"))

    rush_count = int(flow_stats.get("rush_fill_count") or 0)
    if rush_count > 0:
        alerts.append(("RUSH", f"{rush_count} rush fill(s)", "#a78bfa"))

    if not alerts:
        return

    st.markdown("**Alerts**")
    chips_html = " ".join(
        f'<span style="background:rgba({_hex_to_rgb(color)},0.15);color:{color};'
        f'border:1px solid {color};border-radius:4px;padding:2px 10px;'
        f'font-size:0.8em;margin-right:4px;display:inline-block;">'
        f"<b>{level}</b>: {msg}"
        f"</span>"
        for level, msg, color in alerts
    )
    st.markdown(f'<div style="line-height:2.4;margin-bottom:8px;">{chips_html}</div>', unsafe_allow_html=True)


# ── Market History Table ──────────────────────────────────────────────────────


def _render_market_history(db_path: Path) -> None:
    assert st is not None
    hist = da.get_market_history_summary(db_path=db_path)
    if hist.empty or len(hist) <= 1:
        return

    st.markdown("**Market History**")
    display = hist.copy()

    col_map = {
        "market_slug": "Market",
        "fills": "Fills",
        "fill_volume": "Volume",
        "realized_net_pnl": "Realized PnL",
        "win_count": "Wins",
        "loss_count": "Losses",
        "avg_spread_bps": "Avg Spread",
        "avg_markout_1s": "Markout 1s",
        "avg_net_edge_bps": "Net Edge",
        "total_decisions": "Decisions",
    }
    display = display.rename(columns={k: v for k, v in col_map.items() if k in display.columns})

    if "Realized PnL" in display.columns:
        display["Realized PnL"] = display["Realized PnL"].map(lambda v: f"${v:+.3f}" if pd.notna(v) else "")
    if "Volume" in display.columns:
        display["Volume"] = display["Volume"].map(lambda v: f"{v:.0f}" if pd.notna(v) else "")
    if "Avg Spread" in display.columns:
        display["Avg Spread"] = display["Avg Spread"].map(lambda v: f"{v:.1f}" if pd.notna(v) else "")
    if "Markout 1s" in display.columns:
        display["Markout 1s"] = display["Markout 1s"].map(lambda v: f"{v:.1f}" if pd.notna(v) else "")
    if "Net Edge" in display.columns:
        display["Net Edge"] = display["Net Edge"].map(lambda v: f"{v:.1f}" if pd.notna(v) else "")

    keep = [v for v in col_map.values() if v in display.columns]
    st.dataframe(display[keep], use_container_width=True, hide_index=True)


# ── Order Book Depth Chart ──────────────────────────────────────────────────


def _depth_chart(token_id: str, db_path: Path, label: str = "") -> None:
    """Render an Altair depth chart for *token_id* with order/fill overlays."""
    assert st is not None
    if alt is None:
        st.caption("Altair not available for depth chart.")
        return

    book_df = da.get_latest_book_snapshot(token_id, db_path=db_path)
    if book_df.empty:
        st.caption("No book snapshot data yet.")
        return

    # --- Layer 1: Book depth areas ---
    bids = book_df[book_df["side"] == "bid"].copy()
    asks = book_df[book_df["side"] == "ask"].copy()

    if bids.empty and asks.empty:
        st.caption("Book snapshot empty.")
        return

    # Compute mid for axis centering
    best_bid = float(bids["price"].iloc[0]) if not bids.empty else 0.0
    best_ask = float(asks["price"].iloc[0]) if not asks.empty else 0.0
    mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else (best_bid or best_ask)

    # Price domain: ±5% around mid, at least ±0.02
    spread = max(0.02, mid * 0.05) if mid > 0 else 0.05
    price_lo = mid - spread
    price_hi = mid + spread

    bid_area = (
        alt.Chart(bids)
        .mark_area(interpolate="step-after", opacity=0.35, color="#6ee7b7")
        .encode(
            x=alt.X("price:Q", scale=alt.Scale(domain=[price_lo, price_hi]), title="Price"),
            y=alt.Y("cumulative_size:Q", title="Cumulative Size"),
            tooltip=[
                alt.Tooltip("price:Q", title="Price", format=".4f"),
                alt.Tooltip("size:Q", title="Level Size", format=".1f"),
                alt.Tooltip("cumulative_size:Q", title="Cum. Size", format=".1f"),
            ],
        )
    )

    ask_area = (
        alt.Chart(asks)
        .mark_area(interpolate="step-before", opacity=0.35, color="#f87171")
        .encode(
            x=alt.X("price:Q", scale=alt.Scale(domain=[price_lo, price_hi])),
            y=alt.Y("cumulative_size:Q"),
            tooltip=[
                alt.Tooltip("price:Q", title="Price", format=".4f"),
                alt.Tooltip("size:Q", title="Level Size", format=".1f"),
                alt.Tooltip("cumulative_size:Q", title="Cum. Size", format=".1f"),
            ],
        )
    )

    layers: list = [bid_area, ask_area]

    # --- Layer 2: Our resting limit orders ---
    try:
        now_ms = int(_time_mod.time() * 1000)
        orders_df = da.get_open_orders_latest(now_ms, db_path=db_path)
        if not orders_df.empty and "token_id" in orders_df.columns:
            my_orders = orders_df[orders_df["token_id"].astype(str) == str(token_id)].copy()
            if not my_orders.empty:
                my_orders["side_label"] = my_orders["side"].map({"buy": "BUY", "sell": "SELL"})
                my_orders["color_val"] = my_orders["side"].map({"buy": "buy", "sell": "sell"})

                order_marks = (
                    alt.Chart(my_orders)
                    .mark_point(size=120, filled=True, shape="diamond", strokeWidth=2, stroke="#fff")
                    .encode(
                        x=alt.X("price:Q"),
                        y=alt.value(0),
                        color=alt.Color(
                            "color_val:N",
                            scale=alt.Scale(domain=["buy", "sell"], range=["#6ee7b7", "#f87171"]),
                            legend=None,
                        ),
                        tooltip=[
                            alt.Tooltip("side_label:N", title="Side"),
                            alt.Tooltip("price:Q", title="Price", format=".4f"),
                            alt.Tooltip("size:Q", title="Size", format=".1f"),
                        ],
                    )
                )
                order_rules = (
                    alt.Chart(my_orders)
                    .mark_rule(strokeDash=[4, 2], opacity=0.7, strokeWidth=1.5)
                    .encode(
                        x=alt.X("price:Q"),
                        color=alt.Color(
                            "color_val:N",
                            scale=alt.Scale(domain=["buy", "sell"], range=["#6ee7b7", "#f87171"]),
                            legend=None,
                        ),
                    )
                )
                layers.extend([order_rules, order_marks])
    except Exception:
        pass

    # --- Layer 3: Recent fills ---
    try:
        fills_df = da.get_fills_recent(limit=30, db_path=db_path)
        if not fills_df.empty and "token_id" in fills_df.columns:
            my_fills = fills_df[fills_df["token_id"].astype(str) == str(token_id)].copy()
            if not my_fills.empty:
                my_fills = my_fills.rename(columns={"fill_price": "price", "fill_qty": "size"})
                my_fills["shape_val"] = my_fills["side"].map({"buy": "triangle-up", "sell": "triangle-down"})
                my_fills["color_val"] = my_fills["side"].map({"buy": "buy", "sell": "sell"})

                fill_marks = (
                    alt.Chart(my_fills)
                    .mark_point(size=90, filled=True, strokeWidth=1.5, stroke="#0a0a0f")
                    .encode(
                        x=alt.X("price:Q"),
                        y=alt.value(0),
                        shape=alt.Shape(
                            "shape_val:N",
                            scale=alt.Scale(
                                domain=["triangle-up", "triangle-down"],
                                range=["triangle-up", "triangle-down"],
                            ),
                            legend=None,
                        ),
                        color=alt.Color(
                            "color_val:N",
                            scale=alt.Scale(domain=["buy", "sell"], range=["#6ee7b7", "#f87171"]),
                            legend=None,
                        ),
                        tooltip=[
                            alt.Tooltip("side:N", title="Side"),
                            alt.Tooltip("price:Q", title="Fill Price", format=".4f"),
                            alt.Tooltip("size:Q", title="Size", format=".1f"),
                        ],
                    )
                )
                layers.append(fill_marks)
    except Exception:
        pass

    # --- Layer 4: Reference lines ---
    if mid > 0:
        mid_rule = (
            alt.Chart(pd.DataFrame({"x": [mid]}))
            .mark_rule(color="#e6edf3", strokeDash=[3, 3], opacity=0.6)
            .encode(x="x:Q")
        )
        layers.append(mid_rule)

    # --- Layer 5: Our spread zone + trade size from latest decision ---
    try:
        ts_df = da.get_quote_time_series(str(token_id), limit=1, db_path=db_path)
        if not ts_df.empty:
            latest = ts_df.iloc[-1]
            our_bid = latest.get("our_bid")
            our_ask = latest.get("our_ask")
            trade_size = latest.get("trade_size")

            # Shaded spread zone between our bid and ask
            if our_bid is not None and our_ask is not None and not pd.isna(our_bid) and not pd.isna(our_ask):
                spread_df = pd.DataFrame({"x": [float(our_bid)], "x2": [float(our_ask)]})
                layers.append(
                    alt.Chart(spread_df)
                    .mark_rect(color="#00f0ff", opacity=0.12)
                    .encode(
                        x=alt.X("x:Q"),
                        x2=alt.X2("x2:Q"),
                    )
                )

            # Trade size bars at our bid/ask prices
            if trade_size is not None and not pd.isna(trade_size) and float(trade_size) > 0:
                our_quotes = []
                if our_bid is not None and not pd.isna(our_bid):
                    our_quotes.append({"price": float(our_bid), "size": float(trade_size), "side": "buy", "label": f"BID {float(trade_size):.0f}"})
                if our_ask is not None and not pd.isna(our_ask):
                    our_quotes.append({"price": float(our_ask), "size": float(trade_size), "side": "sell", "label": f"ASK {float(trade_size):.0f}"})
                if our_quotes:
                    oq_df = pd.DataFrame(our_quotes)
                    layers.append(
                        alt.Chart(oq_df)
                        .mark_bar(opacity=0.6, width=4)
                        .encode(
                            x=alt.X("price:Q"),
                            y=alt.Y("size:Q"),
                            color=alt.Color(
                                "side:N",
                                scale=alt.Scale(domain=["buy", "sell"], range=["#6ee7b7", "#f87171"]),
                                legend=None,
                            ),
                            tooltip=[
                                alt.Tooltip("label:N", title="Quote"),
                                alt.Tooltip("price:Q", title="Price", format=".4f"),
                                alt.Tooltip("size:Q", title="Trade Size", format=".1f"),
                            ],
                        )
                    )
    except Exception:
        pass

    title = f"Book Depth — {label}" if label else "Book Depth"
    chart = (
        alt.layer(*layers)
        .properties(height=200, title=title)
        .configure(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(labelColor="#9aa4b2", titleColor="#9aa4b2", gridColor="rgba(230,237,243,0.08)")
        .configure_title(color="#e6edf3")
    )
    safe_label = re.sub(r"[^a-zA-Z0-9]", "_", label or token_id[:12])
    st.altair_chart(chart, use_container_width=True, key=f"depth_{safe_label}")


def _spread_band_chart(token_id: str, db_path: Path, label: str = "") -> None:
    """Time-series chart: mid price line with our bid/ask spread band and fills."""
    assert st is not None
    if alt is None:
        st.caption("Altair not available.")
        return

    ts_df = da.get_quote_time_series(str(token_id), limit=300, db_path=db_path)
    if ts_df.empty or ts_df["mid"].dropna().empty:
        st.caption("No quote time-series data yet.")
        return

    # Convert ts_ms to datetime for readable x-axis
    ts_df["time"] = pd.to_datetime(ts_df["ts_ms"], unit="ms", utc=True)

    # Rolling 30-second window: keep only the last 30s of data
    window_secs = 30
    latest_ts = ts_df["ts_ms"].max()
    cutoff_ts = latest_ts - (window_secs * 1000)
    ts_df = ts_df[ts_df["ts_ms"] >= cutoff_ts].copy()
    if ts_df.empty:
        st.caption("No recent data.")
        return

    # Compute tight Y-axis domain from bid/ask/mid range + small padding
    price_cols = ["our_bid", "our_ask", "mid", "best_bid", "best_ask"]
    all_prices = pd.concat([ts_df[c].dropna() for c in price_cols if c in ts_df.columns])
    if all_prices.empty:
        st.caption("No price data.")
        return
    y_min = float(all_prices.min())
    y_max = float(all_prices.max())
    y_pad = max(0.002, (y_max - y_min) * 0.15)
    y_domain = [y_min - y_pad, y_max + y_pad]

    # X-axis domain: fixed 30s window ending at latest
    x_min = pd.to_datetime(cutoff_ts, unit="ms", utc=True)
    x_max = pd.to_datetime(latest_ts, unit="ms", utc=True)

    layers: list = []

    # Spread band (shaded area between our_bid and our_ask)
    band_df = ts_df.dropna(subset=["our_bid", "our_ask"]).copy()
    if not band_df.empty:
        layers.append(
            alt.Chart(band_df)
            .mark_area(opacity=0.25, color="#00f0ff", interpolate="step-after")
            .encode(
                x=alt.X("time:T", title="Time", scale=alt.Scale(domain=[x_min, x_max])),
                y=alt.Y("our_bid:Q", title="Price", scale=alt.Scale(domain=y_domain)),
                y2=alt.Y2("our_ask:Q"),
            )
        )
        # Our bid line
        layers.append(
            alt.Chart(band_df)
            .mark_line(strokeWidth=1.5, color="#6ee7b7", interpolate="step-after")
            .encode(x="time:T", y="our_bid:Q")
        )
        # Our ask line
        layers.append(
            alt.Chart(band_df)
            .mark_line(strokeWidth=1.5, color="#f87171", interpolate="step-after")
            .encode(x="time:T", y="our_ask:Q")
        )

    # Mid price line
    mid_df = ts_df.dropna(subset=["mid"]).copy()
    if not mid_df.empty:
        layers.append(
            alt.Chart(mid_df)
            .mark_line(strokeWidth=2.5, color="#e6edf3", interpolate="step-after")
            .encode(
                x="time:T",
                y=alt.Y("mid:Q", title="Price", scale=alt.Scale(domain=y_domain)),
                tooltip=[
                    alt.Tooltip("time:T", title="Time"),
                    alt.Tooltip("mid:Q", title="Mid", format=".4f"),
                    alt.Tooltip("our_bid:Q", title="Our Bid", format=".4f"),
                    alt.Tooltip("our_ask:Q", title="Our Ask", format=".4f"),
                ],
            )
        )

    # BBO lines (faded)
    bbo_df = ts_df.dropna(subset=["best_bid", "best_ask"]).copy()
    if not bbo_df.empty:
        layers.append(
            alt.Chart(bbo_df)
            .mark_line(strokeWidth=0.8, color="#6ee7b7", opacity=0.35, interpolate="step-after")
            .encode(x="time:T", y="best_bid:Q")
        )
        layers.append(
            alt.Chart(bbo_df)
            .mark_line(strokeWidth=0.8, color="#f87171", opacity=0.35, interpolate="step-after")
            .encode(x="time:T", y="best_ask:Q")
        )

    # Fill markers (only within the 30s window)
    try:
        fills_df = da.get_fills_recent(limit=50, db_path=db_path)
        if not fills_df.empty and "token_id" in fills_df.columns:
            my_fills = fills_df[fills_df["token_id"].astype(str) == str(token_id)].copy()
            if not my_fills.empty:
                ts_col = "ts" if "ts" in my_fills.columns else "ts_ms"
                my_fills["time"] = pd.to_datetime(my_fills[ts_col], unit="ms", utc=True)
                my_fills = my_fills[my_fills["time"] >= x_min].copy()
                if not my_fills.empty:
                    my_fills["price"] = my_fills.get("fill_price", my_fills.get("price"))
                    my_fills["color_val"] = my_fills["side"].map({"buy": "buy", "sell": "sell"})
                    my_fills["shape_val"] = my_fills["side"].map({"buy": "triangle-up", "sell": "triangle-down"})
                    layers.append(
                        alt.Chart(my_fills)
                        .mark_point(size=100, filled=True, strokeWidth=1.5, stroke="#0a0a0f")
                        .encode(
                            x="time:T",
                            y="price:Q",
                            color=alt.Color(
                                "color_val:N",
                                scale=alt.Scale(domain=["buy", "sell"], range=["#6ee7b7", "#f87171"]),
                                legend=None,
                            ),
                            shape=alt.Shape(
                                "shape_val:N",
                                scale=alt.Scale(domain=["triangle-up", "triangle-down"], range=["triangle-up", "triangle-down"]),
                                legend=None,
                            ),
                            tooltip=[
                                alt.Tooltip("time:T", title="Time"),
                                alt.Tooltip("side:N", title="Side"),
                                alt.Tooltip("price:Q", title="Fill Price", format=".4f"),
                            ],
                        )
                    )
    except Exception:
        pass

    if not layers:
        st.caption("No data for spread chart.")
        return

    title = f"Quote Spread — {label}" if label else "Quote Spread"
    chart = (
        alt.layer(*layers)
        .properties(height=250, title=title)
        .configure(background="transparent")
        .configure_view(strokeOpacity=0)
        .configure_axis(labelColor="#9aa4b2", titleColor="#9aa4b2", gridColor="rgba(230,237,243,0.08)")
        .configure_title(color="#e6edf3")
    )
    st.altair_chart(chart, use_container_width=True)


# ── Active Market YES/NO Detail (with Enhancement B: book depth bar) ──────────


def _render_active_market_detail(sys_payload: Dict[str, Any], db_path: Path) -> None:
    assert st is not None
    runner = sys_payload.get("runner") or {}
    market_id = runner.get("market_id")
    if not market_id:
        return

    per_token = (runner.get("book_diag") or {}).get("per_token") or {}
    if not per_token:
        st.caption("No book data for active market.")
        return

    yn_map = _resolve_yes_no_tokens(sys_payload)

    token_fills: Dict[str, Dict[str, Any]] = {}
    try:
        tf_df = da.get_per_token_stats_for_market(market_id, db_path=db_path)
        if not tf_df.empty:
            for _, row in tf_df.iterrows():
                tid = str(row["token_id"])
                side = str(row.get("side") or "")
                if tid not in token_fills:
                    token_fills[tid] = {"buy_fills": 0, "sell_fills": 0, "buy_vol": 0.0, "sell_vol": 0.0}
                if side == "buy":
                    token_fills[tid]["buy_fills"] = int(row.get("fill_count") or 0)
                    token_fills[tid]["buy_vol"] = float(row.get("fill_volume") or 0.0)
                else:
                    token_fills[tid]["sell_fills"] = int(row.get("fill_count") or 0)
                    token_fills[tid]["sell_vol"] = float(row.get("fill_volume") or 0.0)
    except Exception:
        pass

    st.markdown(f"**Active Market: `{market_id}`**")

    sorted_tokens = sorted(per_token.keys(), key=lambda t: (0 if yn_map.get(str(t)) == "YES" else 1))
    cols = st.columns(len(sorted_tokens) or 1)

    for col, tid in zip(cols, sorted_tokens):
        diag = per_token[tid]
        label = yn_map.get(str(tid), _token_label(str(tid)))
        fills_data = token_fills.get(str(tid), {})
        buy_fills = int(fills_data.get("buy_fills") or 0)
        sell_fills = int(fills_data.get("sell_fills") or 0)
        buy_vol = float(fills_data.get("buy_vol") or 0.0)
        sell_vol = float(fills_data.get("sell_vol") or 0.0)
        best_bid = float(diag.get("best_bid") or 0.0)
        best_ask = float(diag.get("best_ask") or 0.0)
        bid_levels = int(diag.get("bid_levels") or 0)
        ask_levels = int(diag.get("ask_levels") or 0)
        best_bid_size = float(diag.get("best_bid_size") or 0.0)
        best_ask_size = float(diag.get("best_ask_size") or 0.0)
        state = str(diag.get("state") or "?")

        label_color = "#6ee7b7" if label == "YES" else "#60a5fa"
        state_color = "#6ee7b7" if state == "book_ok" else "#f87171"

        # Enhancement B: bid/ask imbalance bar
        total_bbo = best_bid_size + best_ask_size
        bid_pct = best_bid_size / total_bbo * 100 if total_bbo > 0 else 50.0
        ask_pct = 100.0 - bid_pct
        depth_bar_html = (
            f'<div style="display:flex;height:6px;border-radius:3px;overflow:hidden;margin:8px 0 4px;">'
            f'<div style="width:{bid_pct:.0f}%;background:#6ee7b7;"></div>'
            f'<div style="width:{ask_pct:.0f}%;background:#f87171;"></div>'
            f"</div>"
            f'<div style="display:flex;justify-content:space-between;font-size:0.72em;opacity:0.55;margin-bottom:4px;">'
            f"<span>BID {best_bid_size:.0f}</span><span>ASK {best_ask_size:.0f}</span>"
            f"</div>"
        )

        with col:
            st.markdown(
                f'<div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:12px;border-top:3px solid {label_color};">'
                f'<div style="font-size:1.1em;font-weight:700;color:{label_color};margin-bottom:8px;">{label}</div>'
                f'<div style="font-size:0.8em;opacity:0.5;margin-bottom:8px;">{_token_label(str(tid))}</div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                f'<span style="opacity:0.65;">Bid</span><b>{best_bid:.3f}</b></div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                f'<span style="opacity:0.65;">Ask</span><b>{best_ask:.3f}</b></div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                f'<span style="opacity:0.65;">Spread</span><b>{(best_ask - best_bid):.3f}</b></div>'
                f"{depth_bar_html}"
                f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                f'<span style="opacity:0.65;">Bid levels</span><b>{bid_levels}</b></div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                f'<span style="opacity:0.65;">Ask levels</span><b>{ask_levels}</b></div>'
                f'<div style="margin-top:8px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.08);">'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                f'<span style="opacity:0.65;">Buys</span><b style="color:#6ee7b7">{buy_fills} ({buy_vol:.0f})</b></div>'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                f'<span style="opacity:0.65;">Sells</span><b style="color:#f87171">{sell_fills} ({sell_vol:.0f})</b></div>'
                f'<div style="display:flex;justify-content:space-between;">'
                f'<span style="opacity:0.65;">State</span><b style="color:{state_color}">{state}</b></div>'
                f"</div></div>",
                unsafe_allow_html=True,
            )
            _spread_band_chart(str(tid), db_path, label=label)
            _depth_chart(str(tid), db_path, label=label)


# ── Risk Summary with Hedge Gauge ────────────────────────────────────────────


def _render_risk_summary(sys_payload: Dict[str, Any], db_path: Path) -> None:
    assert st is not None
    broker_stats = sys_payload.get("broker_stats") or {}
    merge_stats = sys_payload.get("merge_stats") or {}
    flow_stats = sys_payload.get("flow_stats") or {}

    inv_df = da.get_per_token_inventory(db_path=db_path)
    positions: Dict[str, float] = {}
    for _, row in inv_df.iterrows():
        positions[str(row["token_id"])] = float(row.get("yes_qty") or 0.0)

    token_ids = sorted(positions.keys())
    if len(token_ids) == 2:
        pos_a = positions.get(token_ids[0], 0.0)
        pos_b = positions.get(token_ids[1], 0.0)
        net_directional = abs(pos_a - pos_b)
        hedge_pct = (min(pos_a, pos_b) / max(pos_a, pos_b) * 100.0) if max(pos_a, pos_b) > 0 else 0.0
    else:
        net_directional = sum(positions.values())
        hedge_pct = 0.0

    avg_duration_ms = broker_stats.get("avg_duration_ms")
    merge_count = int(merge_stats.get("merge_count") or 0)
    total_merged = float(merge_stats.get("total_merged_amount") or 0.0)
    emergency_cancel = int(flow_stats.get("emergency_cancel_count") or 0)
    rush_fill = int(flow_stats.get("rush_fill_count") or 0)

    gauge_color = "#6ee7b7" if hedge_pct >= 60 else ("#facc15" if hedge_pct >= 30 else "#f87171")
    gauge_html = (
        f'<div style="margin-bottom:12px;">'
        f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
        f'<span style="opacity:0.65;font-size:0.85em;">Hedge Completeness</span>'
        f'<b style="color:{gauge_color}">{hedge_pct:.0f}%</b>'
        f"</div>"
        f'<div style="background:rgba(255,255,255,0.1);border-radius:3px;overflow:hidden;height:8px;">'
        f'<div style="width:{min(100.0, hedge_pct):.1f}%;background:{gauge_color};height:100%;transition:width 0.3s;"></div>'
        f"</div></div>"
    )

    rows_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.05);">'
        f'<span style="opacity:0.65;font-size:0.82em;">{name}</span>'
        f'<span style="font-size:0.88em;">{val}</span>'
        f"</div>"
        for name, val in [
            ("Net Directional", f"<b>{net_directional:.1f}</b>"),
            ("Avg Hold", f"<b>{_duration_str(avg_duration_ms)}</b>"),
            ("Merges", f"<b>{merge_count}</b> ({total_merged:.0f} shares)"),
            ("Emergency Cancels", f'<b style="color:#facc15">{emergency_cancel}</b>'),
            ("Rush Fills", f'<b style="color:#60a5fa">{rush_fill}</b>'),
        ]
    )

    st.markdown(
        f'<div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:12px;">'
        f'<div style="font-size:0.8em;font-weight:700;letter-spacing:0.08em;opacity:0.5;margin-bottom:8px;">RISK</div>'
        f"{gauge_html}{rows_html}</div>",
        unsafe_allow_html=True,
    )


# ── Fills Table ───────────────────────────────────────────────────────────────


def _render_fills_table(db_path: Path, sys_payload: Dict[str, Any]) -> None:
    assert st is not None
    yn_map = _resolve_yes_no_tokens(sys_payload)

    fills_df = da.get_fills_recent(limit=20, db_path=db_path)
    if fills_df.empty:
        st.caption("No fills recorded yet.")
        return

    display = fills_df[
        [
            c
            for c in [
                "ts",
                "side",
                "fill_price",
                "fill_qty",
                "risk_action",
                "risk_state",
                "stale_state",
                "exit_mode",
                "exit_escalation_reason",
                "event_id",
                "current_equity",
                "market_exposure_notional",
                "event_exposure_notional",
                "control_state",
                "hedge_action",
                "hedge_action_reason",
                "hedge_cluster_id",
                "hedge_market_id",
                "hedge_target_token_id",
                "hedge_target_side",
                "hedge_preferred_side",
                "hedge_ratio",
                "hedge_quality_score",
                "realized_net_pnl_delta",
                "fee_usdc",
                "token_id",
                "market_slug",
            ]
            if c in fills_df.columns
        ]
    ].copy()

    if "ts" in display.columns:
        display["ts"] = display["ts"].dt.strftime("%H:%M:%S")
    if "fill_price" in display.columns:
        display["fill_price"] = display["fill_price"].map(lambda v: f"{v:.4f}" if pd.notna(v) else "")
    if "fill_qty" in display.columns:
        display["fill_qty"] = display["fill_qty"].map(lambda v: f"{v:.1f}" if pd.notna(v) else "")
    for col in ["risk_action", "risk_state", "stale_state", "exit_mode"]:
        if col in display.columns:
            display[col] = display[col].fillna("").astype(str)
    for col in ["current_equity", "market_exposure_notional", "event_exposure_notional"]:
        if col in display.columns:
            display[col] = display[col].map(lambda v: f"${v:.2f}" if pd.notna(v) else "")
    if "realized_net_pnl_delta" in display.columns:
        display["realized_net_pnl_delta"] = display["realized_net_pnl_delta"].map(
            lambda v: f"${v:.4f}" if pd.notna(v) else ""
        )
    if "fee_usdc" in display.columns:
        display["fee_usdc"] = display["fee_usdc"].map(lambda v: f"${v:.4f}" if pd.notna(v) else "")
    if "token_id" in display.columns:
        display["token_id"] = display["token_id"].map(
            lambda t: yn_map.get(str(t), _token_label(str(t))) if pd.notna(t) else ""
        )

    display.columns = [c.replace("_", " ").title() for c in display.columns]
    display = display.rename(columns={"Token Id": "Token"})

    st.dataframe(display, use_container_width=True, hide_index=True)


def _render_fill_risk_timeline(db_path: Path) -> None:
    assert st is not None
    timeline = da.get_fill_risk_timeline(limit=40, db_path=db_path)
    if timeline.empty:
        st.caption("No fill/risk timeline events yet.")
        return
    display = timeline[
        [
            c
            for c in [
                "ts",
                "event_kind",
                "market_slug",
                "token_id",
                "summary",
                "control_state",
                "hedge_action",
                "hedge_action_reason",
                "hedge_cluster_id",
                "hedge_market_id",
                "hedge_target_token_id",
                "hedge_target_side",
                "hedge_preferred_side",
                "risk_action",
                "exit_mode",
            ]
            if c in timeline.columns
        ]
    ].copy()
    if "ts" in display.columns:
        display["ts"] = display["ts"].dt.strftime("%H:%M:%S")
    for col in ["event_kind", "risk_action", "exit_mode", "market_slug", "token_id", "summary"]:
        if col in display.columns:
            display[col] = display[col].fillna("").astype(str)
    display.columns = [c.replace("_", " ").title() for c in display.columns]
    st.dataframe(display, use_container_width=True, hide_index=True)


# ── Inventory Section (used in advanced telemetry) ────────────────────────────


def _render_inventory_section(db_path: Path, sys_payload: Dict[str, Any]) -> None:
    assert st is not None
    inv_df = da.get_per_token_inventory(db_path=db_path)
    if inv_df.empty:
        st.caption("No inventory data yet.")
        return

    yn_map = _resolve_yes_no_tokens(sys_payload)
    positions: Dict[str, float] = {}
    for _, row in inv_df.iterrows():
        positions[str(row["token_id"])] = float(row.get("yes_qty") or 0.0)

    fill_counts = da.get_per_token_fill_counts(db_path=db_path)
    rows: List[Dict[str, Any]] = []
    for tid in sorted(positions.keys()):
        pos = positions.get(tid, 0.0)
        buy_fills = sell_fills = 0
        buy_vol = sell_vol = 0.0
        if not fill_counts.empty:
            for _, fc_row in fill_counts[fill_counts["token_id"] == tid].iterrows():
                if str(fc_row.get("side")).lower() == "buy":
                    buy_fills = int(fc_row.get("fill_count") or 0)
                    buy_vol = float(fc_row.get("fill_volume") or 0.0)
                else:
                    sell_fills = int(fc_row.get("fill_count") or 0)
                    sell_vol = float(fc_row.get("fill_volume") or 0.0)
        rows.append({
            "Token": yn_map.get(tid, _token_label(tid)),
            "Position": f"{pos:.1f}",
            "Buy Fills": f"{buy_fills} ({buy_vol:.0f})",
            "Sell Fills": f"{sell_fills} ({sell_vol:.0f})",
            "Net Flow": f"{buy_vol - sell_vol:+.0f}",
        })

    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ── Alpha Overlay Section ─────────────────────────────────────────────────


def _render_alpha_overlay(db_path: Path) -> None:
    """Show alpha overlay diagnostics: vol regime, fill adversity, skew."""
    assert st is not None
    alpha = da.get_alpha_overlay_stats(db_path=db_path)
    if not alpha or not alpha.get("samples"):
        return

    samples = int(alpha.get("samples", 0))
    regime_counts = alpha.get("vol_regime_counts") or {}
    avg_adv = alpha.get("avg_adversity_ratio")
    avg_skew = alpha.get("avg_extra_skew")
    avg_mult = alpha.get("avg_spread_mult")
    max_mult = alpha.get("max_spread_mult")
    skew_pct = alpha.get("skew_nonzero_pct")

    # Regime bar
    total_r = max(1, sum(regime_counts.values()))
    low_pct = regime_counts.get("low", 0) / total_r * 100
    normal_pct = regime_counts.get("normal", 0) / total_r * 100
    high_pct = regime_counts.get("high", 0) / total_r * 100

    regime_bar = (
        f'<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;margin:6px 0;">'
        f'<div style="width:{low_pct:.0f}%;background:#00f0ff;" title="Low vol"></div>'
        f'<div style="width:{normal_pct:.0f}%;background:#7a8599;" title="Normal vol"></div>'
        f'<div style="width:{high_pct:.0f}%;background:#ff2a6d;" title="High vol"></div>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;font-size:0.68em;opacity:0.5;">'
        f'<span style="color:#00f0ff;">LOW {low_pct:.0f}%</span>'
        f'<span style="color:#7a8599;">NORMAL {normal_pct:.0f}%</span>'
        f'<span style="color:#ff2a6d;">HIGH {high_pct:.0f}%</span>'
        f'</div>'
    )

    adv_color = "#05ffa1" if avg_adv is not None and avg_adv < 0.3 else (
        "#fcee0a" if avg_adv is not None and avg_adv < 0.6 else "#ff3b5c"
    )
    mult_color = "#05ffa1" if avg_mult is not None and avg_mult < 1.2 else (
        "#fcee0a" if avg_mult is not None and avg_mult < 1.5 else "#ff3b5c"
    )

    rows_html = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(0,240,255,0.06);">'
        f'<span style="color:#7a8599;font-size:0.78em;text-transform:uppercase;letter-spacing:0.06em;">{name}</span>'
        f'<span style="font-size:0.88em;font-family:Orbitron,Rajdhani,monospace;">{val}</span>'
        f'</div>'
        for name, val in [
            ("Fill Adversity", f'<b style="color:{adv_color}">{avg_adv:.1%}</b>' if avg_adv is not None else "N/A"),
            ("Spread Mult", f'<b style="color:{mult_color}">{avg_mult:.2f}x</b> (max {max_mult:.2f}x)' if avg_mult is not None and max_mult is not None else "N/A"),
            ("Avg Skew Ticks", f'<b>{avg_skew:+.1f}</b>' if avg_skew is not None else "N/A"),
            ("Skew Active", f'<b>{skew_pct:.0f}%</b> of quotes' if skew_pct is not None else "N/A"),
            ("Samples", f'<b>{samples}</b>'),
        ]
    )

    # Complement arbitrage and depth change signals
    avg_comp = alpha.get("avg_complement_bps")
    max_comp = alpha.get("max_complement_bps")
    comp_active = alpha.get("complement_active_pct")
    avg_depth = alpha.get("avg_depth_change")
    depth_active = alpha.get("depth_change_active_pct")

    comp_rows = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid rgba(0,240,255,0.06);">'
        f'<span style="color:#7a8599;font-size:0.78em;text-transform:uppercase;letter-spacing:0.06em;">{name}</span>'
        f'<span style="font-size:0.88em;font-family:Orbitron,Rajdhani,monospace;">{val}</span>'
        f'</div>'
        for name, val in [
            ("Complement Arb", f'<b style="color:#a78bfa;">{avg_comp:+.0f} bps</b> (max {max_comp:.0f})' if avg_comp is not None and max_comp is not None else "N/A"),
            ("Complement Active", f'<b>{comp_active:.0f}%</b>' if comp_active is not None else "N/A"),
            ("Depth Change Avg", f'<b style="color:#60a5fa;">{avg_depth:+.3f}</b>' if avg_depth is not None else "N/A"),
            ("Depth Active", f'<b>{depth_active:.0f}%</b>' if depth_active is not None else "N/A"),
        ]
    )

    st.markdown(
        f'<div class="neon-card" style="padding:12px 14px;">'
        f'<div class="cyber-label" style="margin-bottom:8px;">ALPHA OVERLAY</div>'
        f'<div style="font-size:0.72em;color:#7a8599;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.06em;">Vol Regime Distribution</div>'
        f'{regime_bar}'
        f'<div style="margin-top:8px;">{rows_html}</div>'
        f'<div style="margin-top:8px;border-top:1px solid rgba(0,240,255,0.1);padding-top:8px;">'
        f'<div style="font-size:0.72em;color:#a78bfa;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.06em;">Prediction Market Signals</div>'
        f'{comp_rows}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Memory Layer Section ──────────────────────────────────────────────────


def _render_memory_layer(db_path: Path) -> None:
    """Show memory layer: per-symbol learnings and session history."""
    assert st is not None
    # Memory DB lives one level up from the run directory
    memory_db = db_path.parent.parent / "memory.db"
    mem_data = da.get_memory_layer_stats(memory_db_path=memory_db)
    if not mem_data or not mem_data.get("memories"):
        return

    memories = mem_data.get("memories") or []
    sessions = mem_data.get("sessions") or []

    # Build symbol cards
    cards_html = ""
    for mem in memories:
        symbol = mem.get("symbol", "?")
        total_pnl = float(mem.get("total_pnl", 0))
        total_fills = int(mem.get("total_fills", 0))
        total_sessions = int(mem.get("total_sessions", 0))
        win_rate = float(mem.get("win_sessions", 0)) / max(1, total_sessions)
        adversity = float(mem.get("adverse_fills", 0)) / max(1, total_fills) if total_fills >= 5 else 0
        avg_spread = float(mem.get("avg_spread_bps", 0))
        avg_vol = float(mem.get("avg_realized_vol_bps", 0))
        max_pos = float(mem.get("max_position_seen", 0))

        pnl_color = "#05ffa1" if total_pnl > 0 else "#ff3b5c" if total_pnl < 0 else "#7a8599"
        wr_color = "#05ffa1" if win_rate > 0.6 else "#fcee0a" if win_rate > 0.4 else "#ff3b5c"
        adv_color = "#05ffa1" if adversity < 0.3 else "#fcee0a" if adversity < 0.6 else "#ff3b5c"

        cards_html += (
            f'<div style="border:1px solid rgba(0,240,255,0.15);border-radius:8px;padding:10px 12px;'
            f'background:rgba(0,240,255,0.03);min-width:160px;">'
            f'<div style="font-family:Orbitron,Rajdhani,monospace;font-size:1.1em;color:#00f0ff;'
            f'font-weight:700;margin-bottom:6px;">{symbol}</div>'
            f'<div style="font-size:0.78em;line-height:1.8;">'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:#7a8599;">Cumulative PnL</span>'
            f'<span style="color:{pnl_color};font-family:Orbitron,monospace;">${total_pnl:+.2f}</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:#7a8599;">Sessions</span>'
            f'<span style="font-family:Orbitron,monospace;">{total_sessions}</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:#7a8599;">Win Rate</span>'
            f'<span style="color:{wr_color};font-family:Orbitron,monospace;">{win_rate:.0%}</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:#7a8599;">Total Fills</span>'
            f'<span style="font-family:Orbitron,monospace;">{total_fills}</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:#7a8599;">Adversity</span>'
            f'<span style="color:{adv_color};font-family:Orbitron,monospace;">{adversity:.0%}</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:#7a8599;">Avg Spread</span>'
            f'<span style="font-family:Orbitron,monospace;">{avg_spread:.0f} bps</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:#7a8599;">Avg Vol</span>'
            f'<span style="font-family:Orbitron,monospace;">{avg_vol:.0f} bps</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="color:#7a8599;">Max Position</span>'
            f'<span style="font-family:Orbitron,monospace;">{max_pos:.0f}</span></div>'
            f'</div></div>'
        )

    # Session history timeline (last 10)
    session_rows = ""
    for sess in sessions[:10]:
        s_pnl = float(sess.get("realized_pnl", 0))
        s_fills = int(sess.get("total_fills", 0))
        s_symbol = sess.get("symbol", "?")
        s_color = "#05ffa1" if s_pnl > 0 else "#ff3b5c" if s_pnl < 0 else "#7a8599"
        s_run = sess.get("run_id", "")[:24]
        session_rows += (
            f'<div style="display:flex;align-items:center;gap:10px;padding:3px 0;'
            f'border-bottom:1px solid rgba(0,240,255,0.06);font-size:0.76em;">'
            f'<span style="color:#00f0ff;font-family:Orbitron,monospace;width:40px;text-align:center;">{s_symbol}</span>'
            f'<span style="color:{s_color};font-family:Orbitron,monospace;width:60px;text-align:right;">${s_pnl:+.2f}</span>'
            f'<span style="color:#7a8599;width:40px;text-align:right;">{s_fills}f</span>'
            f'<span style="color:#4a5568;font-size:0.85em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{s_run}</span>'
            f'</div>'
        )

    st.markdown(
        f'<div class="neon-card" style="padding:12px 14px;">'
        f'<div class="cyber-label" style="margin-bottom:8px;">MEMORY LAYER</div>'
        f'<div style="font-size:0.72em;color:#7a8599;margin-bottom:10px;text-transform:uppercase;letter-spacing:0.06em;">'
        f'Per-Symbol Learnings ({len(memories)} symbols, {len(sessions)} sessions)</div>'
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;">{cards_html}</div>'
        + (
            f'<div style="margin-top:8px;border-top:1px solid rgba(0,240,255,0.1);padding-top:8px;">'
            f'<div style="font-size:0.72em;color:#a78bfa;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.06em;">Session History</div>'
            f'{session_rows}'
            f'</div>'
            if session_rows else ""
        )
        + f'</div>',
        unsafe_allow_html=True,
    )


# ── Cockpit Components ───────────────────────────────────────────────────────


def _render_bot_thinking(sys_payload: Dict[str, Any], db_path: Path) -> None:
    """Per-token decision cards showing the bot's current reasoning."""
    assert st is not None

    decisions = da.get_latest_decisions_per_token(db_path=db_path)
    if not decisions:
        st.caption("No decisions recorded yet.")
        return

    yn_map = _resolve_yes_no_tokens(sys_payload)
    inv_df = da.get_per_token_inventory(db_path=db_path)
    positions: Dict[str, float] = {}
    for _, row in inv_df.iterrows():
        positions[str(row["token_id"])] = float(row.get("yes_qty") or 0.0)

    # Sort YES first
    sorted_tids = sorted(decisions.keys(), key=lambda t: (0 if yn_map.get(str(t)) == "YES" else 1))
    cols = st.columns(len(sorted_tids) or 1)

    for col, tid in zip(cols, sorted_tids):
        d = decisions[tid]
        label = yn_map.get(str(tid), _token_label(str(tid)))
        action = d["action"]
        reasons = d["reason_codes"]
        quote = d["quote_plan"]
        size = d["size_plan"]
        flow = d["flow_filter"]
        risk = d["risk_decision"]
        metrics = d["metrics"]

        bid_price = quote.get("bid_price")
        ask_price = quote.get("ask_price")
        bid_mode = str(quote.get("bid_mode") or "—")
        ask_mode = str(quote.get("ask_mode") or "—")
        buy_amt = size.get("buy_amount") or 0
        sell_amt = size.get("sell_amount") or 0
        buy_limiter = _format_size_limiter(size, "buy")
        sell_limiter = _format_size_limiter(size, "sell")
        flow_reason = str(flow.get("reason") or "—")
        allow_buy = flow.get("allow_buy", True)
        allow_sell = flow.get("allow_sell", True)
        imbalance = flow.get("imbalance_bps")
        risk_action = str(risk.get("action") or "NORMAL")
        position = positions.get(str(tid), 0.0)

        # Colors
        label_color = "#6ee7b7" if label == "YES" else "#60a5fa"
        action_color = "#05ffa1" if action == "QUOTE" else ("#facc15" if action == "SKIP" else "#f87171")
        flow_icon = "✓" if (allow_buy and allow_sell) else "⚠"
        flow_color = "#6ee7b7" if (allow_buy and allow_sell) else "#facc15"
        risk_color = "#6ee7b7" if risk_action == "NORMAL" else "#f87171"
        imb_str = f"{imbalance:+.0f} bps" if imbalance is not None else "—"

        def _row(name: str, val: str) -> str:
            return (
                f'<div style="display:flex;justify-content:space-between;padding:3px 0;'
                f'border-bottom:1px solid rgba(255,255,255,0.05);">'
                f'<span style="opacity:0.6;font-size:0.8em;">{name}</span>'
                f'<span style="font-family:Orbitron,Rajdhani,monospace;font-size:0.88em;">{val}</span>'
                f'</div>'
            )

        rows_html = "".join([
            _row("Action", f'<b style="color:{action_color}">{action}</b>'),
            _row("Bid", f'{bid_price:.3f}' if bid_price else "—") + _row("Ask", f'{ask_price:.3f}' if ask_price else "—"),
            _row("Bid Mode", f'<span style="font-size:0.78em;">{bid_mode}</span>'),
            _row("Ask Mode", f'<span style="font-size:0.78em;">{ask_mode}</span>'),
            _row("Flow", f'<span style="color:{flow_color}">{flow_icon} {flow_reason}</span>'),
            _row("Risk", f'<span style="color:{risk_color}">{risk_action}</span>'),
            _row("Size", f'buy {buy_amt:.0f} / sell {sell_amt:.0f}'),
            _row("Limits", f'<span style="font-size:0.78em;">buy {buy_limiter}; sell {sell_limiter}</span>'),
            _row("Position", f'<b>{position:.1f}</b>'),
            _row("Imbalance", imb_str),
        ])

        if reasons and reasons != "None":
            rows_html += (
                f'<div style="margin-top:6px;font-size:0.72em;color:#7a8599;word-break:break-all;">'
                f'{reasons}</div>'
            )

        with col:
            st.markdown(
                f'<div style="background:rgba(255,255,255,0.04);border-radius:6px;padding:12px;'
                f'border-top:3px solid {label_color};">'
                f'<div style="font-size:1.1em;font-weight:700;color:{label_color};margin-bottom:8px;">{label}</div>'
                f'{rows_html}'
                f'</div>',
                unsafe_allow_html=True,
            )


def _render_performance_risk(
    pnl: Dict[str, Any],
    summary: Dict[str, Any],
    sys_payload: Dict[str, Any],
    curve: pd.DataFrame,
    eq_df: pd.DataFrame,
    db_path: Path,
) -> None:
    """PnL chart (left) + risk/adverse selection stats (right)."""
    assert st is not None

    col_chart, col_risk = st.columns([7, 3])

    with col_chart:
        _pnl_drawdown_chart(curve)

    with col_risk:
        # Gather risk stats
        inv_df = da.get_per_token_inventory(db_path=db_path)
        token_ids = sorted(str(r["token_id"]) for _, r in inv_df.iterrows()) if not inv_df.empty else []
        if len(token_ids) >= 2:
            pos_a = float(inv_df.iloc[0].get("yes_qty") or 0.0)
            pos_b = float(inv_df.iloc[1].get("yes_qty") or 0.0)
            hedge_pct = (min(pos_a, pos_b) / max(pos_a, pos_b) * 100.0) if max(pos_a, pos_b) > 0 else 0.0
        else:
            hedge_pct = 0.0

        max_dd = float(pnl.get("max_drawdown_abs") or 0.0)
        fill_rate = float(summary.get("fill_rate") or 0.0) * 100
        eq_summary = summary.get("execution_quality") or {}
        avg_net_edge = eq_summary.get("avg_net_edge_bps")
        avg_m1s = eq_summary.get("avg_markout_1s_bps")
        avg_spread = eq_summary.get("avg_realized_spread_bps")

        # Adversity from execution quality
        adversity_pct = None
        if not eq_df.empty and "markout_1s_bps" in eq_df.columns:
            valid = eq_df["markout_1s_bps"].dropna()
            if len(valid) > 0:
                adversity_pct = (valid < 0).sum() / len(valid) * 100

        gauge_color = "#6ee7b7" if hedge_pct >= 60 else ("#facc15" if hedge_pct >= 30 else "#f87171")

        def _stat(name: str, val: str) -> str:
            return (
                f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
                f'border-bottom:1px solid rgba(0,240,255,0.06);">'
                f'<span style="color:#7a8599;font-size:0.8em;text-transform:uppercase;letter-spacing:0.04em;">{name}</span>'
                f'<span style="font-family:Orbitron,Rajdhani,monospace;font-size:0.9em;">{val}</span>'
                f'</div>'
            )

        stats_html = "".join([
            # Hedge gauge
            f'<div style="margin-bottom:8px;">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:3px;">'
            f'<span style="color:#7a8599;font-size:0.8em;">HEDGE</span>'
            f'<b style="color:{gauge_color};font-family:Orbitron,monospace;">{hedge_pct:.0f}%</b></div>'
            f'<div style="background:rgba(255,255,255,0.1);border-radius:3px;height:6px;overflow:hidden;">'
            f'<div style="width:{min(100, hedge_pct):.0f}%;background:{gauge_color};height:100%;"></div>'
            f'</div></div>',
            _stat("Max DD", f'<b style="color:#f87171">${max_dd:.2f}</b>'),
            _stat("Fill Rate", f'<b>{fill_rate:.1f}%</b>'),
            _stat("Net Edge", f'<b>{avg_net_edge:.1f} bps</b>' if avg_net_edge is not None else "—"),
            _stat("Markout 1s", f'<b>{avg_m1s:.1f} bps</b>' if avg_m1s is not None else "—"),
            _stat("Spread", f'<b>{avg_spread:.1f} bps</b>' if avg_spread is not None else "—"),
            _stat("Adversity", f'<b style="color:{"#f87171" if (adversity_pct or 0) > 30 else "#6ee7b7"}">{adversity_pct:.0f}%</b>' if adversity_pct is not None else "—"),
        ])

        st.markdown(
            f'<div class="neon-card" style="padding:12px 14px;">'
            f'<div class="cyber-label" style="margin-bottom:8px;">RISK & EXECUTION</div>'
            f'{stats_html}'
            f'</div>',
            unsafe_allow_html=True,
        )


def _render_inventory_strip(sys_payload: Dict[str, Any], db_path: Path) -> None:
    """Compact inventory display: per-token positions + hedge status."""
    assert st is not None

    inv_df = da.get_per_token_inventory(db_path=db_path)
    if inv_df.empty:
        st.caption("No inventory data.")
        return

    yn_map = _resolve_yes_no_tokens(sys_payload)
    cells: List[str] = []
    total_pos = 0.0

    for _, row in inv_df.iterrows():
        tid = str(row["token_id"])
        label = yn_map.get(tid, _token_label(tid))
        pos = float(row.get("yes_qty") or 0.0)
        total_pos += abs(pos)
        label_color = "#6ee7b7" if label == "YES" else "#60a5fa"
        cells.append(
            f'<div style="flex:1;min-width:120px;padding:8px 12px;'
            f'background:rgba(255,255,255,0.03);border-radius:4px;border-left:3px solid {label_color};">'
            f'<div style="font-family:Orbitron,monospace;font-weight:700;color:{label_color};font-size:0.9em;">{label}</div>'
            f'<div style="font-family:Orbitron,monospace;font-size:1.1em;font-weight:700;margin-top:4px;">{pos:.1f}</div>'
            f'<div style="font-size:0.72em;opacity:0.5;">shares</div>'
            f'</div>'
        )

    # Net directional
    positions = [float(r.get("yes_qty") or 0.0) for _, r in inv_df.iterrows()]
    if len(positions) >= 2:
        net_dir = abs(positions[0] - positions[1])
        hedge_pct = (min(positions) / max(positions) * 100) if max(positions) > 0 else 0
    else:
        net_dir = sum(abs(p) for p in positions)
        hedge_pct = 0

    cells.append(
        f'<div style="flex:1;min-width:120px;padding:8px 12px;'
        f'background:rgba(0,240,255,0.04);border-radius:4px;border-left:3px solid #00f0ff;">'
        f'<div style="font-size:0.78em;color:#7a8599;text-transform:uppercase;">Net Directional</div>'
        f'<div style="font-family:Orbitron,monospace;font-size:1.1em;font-weight:700;margin-top:4px;">{net_dir:.1f}</div>'
        f'<div style="font-size:0.72em;opacity:0.5;">hedge {hedge_pct:.0f}%</div>'
        f'</div>'
    )

    st.markdown(
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin:4px 0;">'
        f'{"".join(cells)}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_operator_hero(
    *,
    runtime_snapshot: Dict[str, Any],
    explainer_rows: pd.DataFrame,
    pnl: Dict[str, Any],
    curve: pd.DataFrame,
    db_path: Path,
) -> None:
    assert st is not None
    brief = _build_operator_brief(runtime_snapshot, explainer_rows)
    control = da.get_control_plane_snapshot(db_path=db_path)
    health = runtime_snapshot.get("active_market_health") if isinstance(runtime_snapshot.get("active_market_health"), dict) else {}
    portfolio_risk = health.get("portfolio_risk") if isinstance(health.get("portfolio_risk"), dict) else {}
    summary_cards = st.columns(4)
    with summary_cards[0]:
        _render_glass_metric("Quoteable", "YES" if bool(runtime_snapshot.get("quoteable")) else "NO", accent="#05ffa1" if bool(runtime_snapshot.get("quoteable")) else "#ff3b5c")
    with summary_cards[1]:
        _render_glass_metric("Risk mode", "FLATTEN" if control.get("flatten_only_mode") else ("PAUSED" if not control.get("trading_enabled", True) else "ACTIVE"), accent="#fcee0a" if control.get("flatten_only_mode") else ("#ff3b5c" if not control.get("trading_enabled", True) else "#05ffa1"))
    with summary_cards[2]:
        _render_glass_metric("Regime", _extract_regime(runtime_snapshot).upper(), accent="#00f0ff")
    with summary_cards[3]:
        _render_glass_metric("Exposure", _fmt_money(portfolio_risk.get("gross_exposure") or 0.0), accent="#fcee0a")

    chart_col, brief_col = st.columns([7, 5])
    with chart_col:
        _pnl_drawdown_chart(curve)
    with brief_col:
        st.markdown(
            f"""
            <div style="padding:16px 18px;border:1px solid rgba(255,255,255,0.08);border-radius:18px;
                        background:rgba(255,255,255,0.05);backdrop-filter:blur(14px);
                        box-shadow:0 12px 30px rgba(0,0,0,0.22);min-height:340px;">
              <div style="font-size:0.72em;color:#7a8599;text-transform:uppercase;letter-spacing:0.14em;">Operator brief</div>
              <div style="margin-top:8px;font-family:Orbitron,monospace;font-size:1.35em;font-weight:700;color:{brief['accent']};">
                {brief['headline']}
              </div>
              <div style="margin-top:10px;color:#e6edf3;font-size:0.98em;line-height:1.45;">
                {brief['summary']}
              </div>
              <div style="margin-top:14px;padding:10px 12px;border-radius:12px;background:rgba(0,0,0,0.18);
                          color:#aab4c3;font-size:0.85em;line-height:1.45;">
                {brief['detail']}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _render_compact_panel(
            "Current supervision",
            [
                ("selected market", runtime_snapshot.get("market") or "N/A"),
                ("state", runtime_snapshot.get("state") or runtime_snapshot.get("book_health") or "unknown"),
                ("selected reason", _humanize_reason_codes(runtime_snapshot.get("selected_reason"))),
                ("last update", _fmt_last_update(runtime_snapshot.get("updated_at_ms"))),
                ("pnl", _fmt_money(runtime_snapshot.get("total_pnl") or pnl.get("total_pnl") or 0.0)),
            ],
            accent="#fcee0a",
        )


def _render_cluster_exposure_panel(db_path: Path, runtime_snapshot: Dict[str, Any]) -> None:
    assert st is not None
    cluster_snapshot = da.get_cluster_exposure_snapshot(runtime_snapshot=runtime_snapshot, db_path=db_path)
    cluster_rows = da.get_cluster_exposure_rows(runtime_snapshot=runtime_snapshot, db_path=db_path)
    market_rows = da.get_cluster_market_rows(runtime_snapshot=runtime_snapshot, db_path=db_path)
    calibration_gaps = da.get_cluster_calibration_gaps(runtime_snapshot=runtime_snapshot, db_path=db_path)

    st.markdown("### Event cluster exposure")
    if cluster_rows.empty:
        st.caption("Cluster exposure is not available for this runtime yet.")
        return

    active_cluster = cluster_rows.sort_values(
        by=["active_market_count", "gross_exposure"],
        ascending=[False, False],
    ).iloc[0].to_dict()

    top = st.columns(4)
    with top[0]:
        _render_glass_metric("Clusters", str(int(cluster_snapshot.get("cluster_count") or len(cluster_rows))))
    with top[1]:
        _render_glass_metric("Active clusters", str(int(cluster_snapshot.get("active_cluster_count") or 0)), accent="#05ffa1")
    with top[2]:
        _render_glass_metric("Cluster gross", _fmt_money(cluster_snapshot.get("gross_exposure") or 0.0), accent="#fcee0a")
    with top[3]:
        _render_glass_metric("Cluster unrealized", _fmt_money(cluster_snapshot.get("unrealized_pnl") or 0.0), accent="#05ffa1" if float(cluster_snapshot.get("unrealized_pnl") or 0.0) >= 0 else "#ff3b5c")

    st.markdown("#### Cluster cards")
    cluster_cards = st.columns(min(3, max(1, len(cluster_rows))))
    for idx, (_, cluster_row) in enumerate(cluster_rows.head(6).iterrows()):
        with cluster_cards[idx % len(cluster_cards)]:
            _render_compact_panel(
                str(cluster_row.get("cluster_id") or f"Cluster {idx + 1}"),
                [
                    ("markets", f"{int(cluster_row.get('active_market_count') or 0)}/{int(cluster_row.get('market_count') or 0)} active"),
                    ("net", _cluster_net_label(cluster_row.get("net_yes_exposure_notional"))),
                    ("gross", _fmt_money(cluster_row.get("gross_exposure") or 0.0)),
                    ("control", _control_state_text(cluster_row.get("control_state"))),
                    ("hedge", _hedge_action_label(cluster_row.get("hedge_action"))),
                    ("stale", _stale_inventory_label(cluster_row.get("stale_inventory_state"), cluster_row.get("stale_market_count"))),
                    ("expiry", _duration_str(cluster_row.get("time_to_expiry_ms"))),
                ],
                accent="#05ffa1" if int(cluster_row.get("active_market_count") or 0) > 0 else "#00f0ff",
            )

    calibration_cols = st.columns(5)
    with calibration_cols[0]:
        _render_glass_metric("Net exposure", _cluster_net_label(active_cluster.get("net_yes_exposure_notional")), accent="#00f0ff")
    with calibration_cols[1]:
        _render_glass_metric("Gross exposure", _fmt_money(active_cluster.get("gross_exposure") or 0.0), accent="#fcee0a")
    with calibration_cols[2]:
        _render_glass_metric("Active markets", str(int(active_cluster.get("active_market_count") or 0)), accent="#05ffa1")
    with calibration_cols[3]:
        _render_glass_metric("Time to expiry", _duration_str(active_cluster.get("time_to_expiry_ms")), accent="#05ffa1")
    with calibration_cols[4]:
        _render_glass_metric("Hedge label", _control_state_text(active_cluster.get("hedge_action")), accent="#fcee0a")

    detail_cols = st.columns(2)
    with detail_cols[0]:
        _render_compact_panel(
            "Active cluster",
            [
                ("cluster", active_cluster.get("cluster_id")),
                ("net exposure", _cluster_net_label(active_cluster.get("net_yes_exposure_notional"))),
                ("gross exposure", _fmt_money(active_cluster.get("gross_exposure"))),
                ("markets", f"{int(active_cluster.get('active_market_count') or 0)}/{int(active_cluster.get('market_count') or 0)} active"),
                ("time to expiry", _duration_str(active_cluster.get("time_to_expiry_ms"))),
            ],
            accent="#00f0ff",
        )
    with detail_cols[1]:
        _render_compact_panel(
            "Cluster controls",
            [
                ("event cap", _fmt_money(active_cluster.get("max_event_exposure_notional"))),
                ("cap remaining", _fmt_money(active_cluster.get("remaining_event_exposure_notional"))),
                ("control state", _control_state_text(active_cluster.get("control_state"))),
                ("hedge state", _control_state_text(active_cluster.get("hedge_action"))),
                ("reason", active_cluster.get("hedge_action_reason") or "N/A"),
                ("hedge ratio", _fmt(active_cluster.get("hedge_ratio"), decimals=2)),
                ("target market", active_cluster.get("hedge_target_market") or "N/A"),
                ("target token/side", " / ".join([part for part in [str(active_cluster.get("hedge_target_token") or "").strip(), str(active_cluster.get("hedge_target_side") or "").strip()] if part]) or "N/A"),
            ],
            accent="#fcee0a",
        )

    question_cols = st.columns(2)
    with question_cols[0]:
        _render_compact_panel(
            "Calibration questions answered",
            [
                ("bias", _cluster_net_label(active_cluster.get("net_yes_exposure_notional"))),
                ("gross risk", _fmt_money(active_cluster.get("gross_exposure"))),
                ("concurrency", f"{int(active_cluster.get('active_market_count') or 0)}/{int(active_cluster.get('market_count') or 0)} active"),
                ("expiry pressure", _duration_str(active_cluster.get("time_to_expiry_ms"))),
                ("event headroom", _fmt_money(active_cluster.get("remaining_event_exposure_notional"))),
            ],
            accent="#05ffa1",
        )
    with question_cols[1]:
        unanswered = _cluster_gap_questions(calibration_gaps)
        _render_compact_panel(
            "Still unknown from runtime",
            [
                ("question 1", unanswered[0] if len(unanswered) > 0 else "None"),
                ("question 2", unanswered[1] if len(unanswered) > 1 else "None"),
                ("question 3", unanswered[2] if len(unanswered) > 2 else "None"),
                ("question 4", unanswered[3] if len(unanswered) > 3 else "None"),
                ("question 5", unanswered[4] if len(unanswered) > 4 else "None"),
            ],
            accent="#ff3b5c",
        )

    if calibration_gaps:
        st.caption("Calibration visibility gaps: " + ", ".join(calibration_gaps))

    with st.expander("Cluster calibration detail", expanded=False):
        show_clusters = cluster_rows.copy()
        show_clusters["time_to_expiry"] = show_clusters["time_to_expiry_ms"].apply(_duration_str)
        cluster_cols = [
            col
            for col in [
                "cluster_id",
                "market_count",
                "active_market_count",
                "yes_exposure_notional",
                "no_exposure_notional",
                "net_yes_exposure_notional",
                "gross_exposure",
                "unrealized_pnl",
                "time_to_expiry",
                "max_event_exposure_notional",
                "remaining_event_exposure_notional",
                "control_state",
                "hedge_action",
                "hedge_action_reason",
                "hedge_ratio",
                "hedge_target_market",
                "hedge_target_token",
                "hedge_target_side",
            ]
            if col in show_clusters.columns and (col not in {"control_state", "hedge_action", "hedge_action_reason", "hedge_ratio", "hedge_target_market", "hedge_target_token", "hedge_target_side"} or show_clusters[col].notna().any())
        ]
        _display_runtime_table(show_clusters, cluster_cols)

        if not market_rows.empty:
            st.caption("Markets inside the selected event clusters")
            show_markets = market_rows.copy()
            show_markets["time_to_expiry"] = show_markets["time_to_expiry_ms"].apply(_duration_str)
            market_cols = [
                col
                for col in [
                    "cluster_id",
                    "market_id",
                    "active",
                    "yes_exposure_notional",
                    "no_exposure_notional",
                    "market_position_notional",
                    "market_unrealized_pnl",
                    "time_to_expiry",
                    "hedge_action",
                    "hedge_action_reason",
                    "hedge_ratio",
                    "hedge_target_market",
                    "hedge_target_token",
                    "hedge_target_side",
                ]
                if col in show_markets.columns and (col not in {"hedge_action", "hedge_action_reason", "hedge_ratio", "hedge_target_market", "hedge_target_token", "hedge_target_side"} or show_markets[col].notna().any())
            ]
            _display_runtime_table(show_markets, market_cols)


def _render_active_books_portfolio_panel(db_path: Path, runtime_snapshot: Dict[str, Any]) -> None:
    assert st is not None
    active_rows = da.get_active_market_rows(runtime_snapshot=runtime_snapshot, db_path=db_path)
    st.markdown("### Active books across portfolio")
    if active_rows.empty:
        st.caption("Active multi-market book detail is not available for this runtime yet.")
        return

    stale_count = int((active_rows.get("stale_inventory_state", pd.Series(dtype=object)).astype(str).str.lower() == "stale").sum()) if "stale_inventory_state" in active_rows.columns else 0
    cluster_count = int(active_rows.get("cluster_id", pd.Series(dtype=object)).fillna("unmapped").nunique())
    hedge_action_count = int(active_rows.get("hedge_action", pd.Series(dtype=object)).fillna("").astype(str).replace("NONE", "").replace("N/A", "").ne("").sum()) if "hedge_action" in active_rows.columns else 0

    top = st.columns(4)
    with top[0]:
        _render_glass_metric("Active books", str(len(active_rows)), accent="#05ffa1")
    with top[1]:
        _render_glass_metric("Clusters touched", str(cluster_count), accent="#00f0ff")
    with top[2]:
        _render_glass_metric("Stale books", str(stale_count), accent="#ff3b5c" if stale_count > 0 else "#05ffa1")
    with top[3]:
        _render_glass_metric("Books under cluster action", str(hedge_action_count), accent="#fcee0a" if hedge_action_count > 0 else "#05ffa1")

    cols = st.columns(min(3, max(1, len(active_rows))))
    for idx, (_, market_row) in enumerate(active_rows.head(6).iterrows()):
        with cols[idx % len(cols)]:
            _render_compact_panel(
                str(market_row.get("market_id") or f"Market {idx + 1}"),
                [
                    ("cluster", market_row.get("cluster_id") or "N/A"),
                    ("exposure", _fmt_money(market_row.get("market_position_notional"))),
                    ("unrealized", _fmt_money(market_row.get("market_unrealized_pnl"))),
                    ("stale", _stale_inventory_label(market_row.get("stale_inventory_state"))),
                    ("control", _control_state_text(market_row.get("control_state"))),
                    ("hedge", _hedge_action_label(market_row.get("hedge_action"))),
                    ("expiry", _duration_str(market_row.get("time_to_expiry_ms"))),
                ],
                accent="#ff3b5c" if str(market_row.get("stale_inventory_state") or "").lower() == "stale" else "#00f0ff",
            )

    with st.expander("Active book detail", expanded=False):
        detail = active_rows.copy()
        if "time_to_expiry_ms" in detail.columns:
            detail["time_to_expiry"] = detail["time_to_expiry_ms"].apply(_duration_str)
        cols = [
            col
            for col in [
                "cluster_id",
                "market_id",
                "market_position_notional",
                "market_unrealized_pnl",
                "stale_inventory_state",
                "control_state",
                "hedge_action",
                "hedge_action_reason",
                "affected_by_cluster_action",
                "time_to_expiry",
            ]
            if col in detail.columns
        ]
        _display_runtime_table(detail, cols)


def _render_selection_diagnostics_panel(db_path: Path, runtime_snapshot: Dict[str, Any]) -> None:
    assert st is not None
    diag_rows = da.get_selection_diagnostic_rows(runtime_snapshot=runtime_snapshot, db_path=db_path)
    gaps = da.get_selection_diagnostic_gaps(runtime_snapshot=runtime_snapshot, db_path=db_path)
    st.markdown("### Selection diagnostics")
    if diag_rows.empty:
        st.caption("Selection candidate diagnostics are not available for this runtime yet.")
        return

    accepted_rows = diag_rows[diag_rows["accepted"].astype(bool)].copy()
    rejected_rows = diag_rows[~diag_rows["accepted"].astype(bool)].copy()
    top_reject_reason = "N/A"
    if not rejected_rows.empty:
        top_reject_reason = _humanize_reason_codes(rejected_rows["reason"].fillna("").astype(str).mode().iloc[0] if not rejected_rows["reason"].dropna().empty else "N/A")
    suppressed_rows = diag_rows[
        diag_rows.get("blocking_market_id", pd.Series(dtype=object)).notna()
        | diag_rows.get("blocking_cluster_id", pd.Series(dtype=object)).notna()
        | diag_rows.get("blocking_reason", pd.Series(dtype=object)).notna()
    ].copy()

    top = st.columns(4)
    with top[0]:
        _render_glass_metric("Accepted", str(len(accepted_rows)), accent="#05ffa1")
    with top[1]:
        _render_glass_metric("Rejected", str(len(rejected_rows)), accent="#ff3b5c" if len(rejected_rows) > 0 else "#05ffa1")
    with top[2]:
        _render_glass_metric("Suppression visible", str(len(suppressed_rows)), accent="#fcee0a" if len(suppressed_rows) > 0 else "#00f0ff")
    with top[3]:
        _render_glass_metric("Top reject reason", top_reject_reason, accent="#fcee0a")

    panels = st.columns(2)
    with panels[0]:
        best_accept = accepted_rows.iloc[0].to_dict() if not accepted_rows.empty else {}
        _render_compact_panel(
            "Accepted queue",
            [
                ("top market", best_accept.get("ticker") or "N/A"),
                ("score", _fmt(best_accept.get("score"), decimals=3)),
                ("liquidity", _fmt(best_accept.get("liquidity_score"), decimals=3)),
                ("reason", _humanize_reason_codes(best_accept.get("reason"))),
                ("quoteability", best_accept.get("quoteability_state") or "N/A"),
            ],
            accent="#05ffa1",
        )
    with panels[1]:
        best_reject = rejected_rows.iloc[0].to_dict() if not rejected_rows.empty else {}
        _render_compact_panel(
            "Rejected queue",
            [
                ("top reject", best_reject.get("ticker") or "N/A"),
                ("reason", _humanize_reason_codes(best_reject.get("reason"))),
                ("blocking market", best_reject.get("blocking_market_id") or "N/A"),
                ("blocking cluster", best_reject.get("blocking_cluster_id") or "N/A"),
                ("blocking rule", best_reject.get("blocking_reason") or "N/A"),
            ],
            accent="#ff3b5c",
        )

    if gaps:
        unanswered = _selection_gap_questions(gaps)
        st.caption("Multi-market visibility gaps: " + ", ".join(gaps))
        _render_compact_panel(
            "Still unknown from runtime",
            [(f"question {idx + 1}", question) for idx, question in enumerate(unanswered[:3])],
            accent="#ff3b5c",
        )

    with st.expander("Selection diagnostic detail", expanded=False):
        show = diag_rows.copy()
        show["reason"] = show["reason"].apply(_humanize_reason_codes)
        cols = [
            col
            for col in [
                "status",
                "ticker",
                "reason",
                "quoteability_state",
                "score",
                "liquidity_score",
                "transition_risk",
                "proximity_score",
                "volume",
                "blocking_market_id",
                "blocking_cluster_id",
                "blocking_reason",
            ]
            if col in show.columns and (col not in {"blocking_market_id", "blocking_cluster_id", "blocking_reason"} or show[col].notna().any())
        ]
        _display_runtime_table(show, cols)


def _render_hedge_readout_panel(db_path: Path, runtime_snapshot: Dict[str, Any], view_mode: Optional[Any] = None) -> None:
    assert st is not None
    hedge_summary = da.get_hedge_readout_summary(runtime_snapshot=runtime_snapshot, db_path=db_path)
    st.markdown("### Hedge readout")

    candidate_count = int(hedge_summary.get("candidate_count") or 0)
    accepted_count = int(hedge_summary.get("accepted_count") or 0)
    rejected_count = int(hedge_summary.get("rejected_count") or 0)
    if candidate_count <= 0:
        st.caption("Hedge candidate diagnostics are not available for this runtime yet.")

    top = st.columns(5)
    with top[0]:
        _render_glass_metric("Candidates", str(candidate_count), accent="#00f0ff")
    with top[1]:
        accepted_rate = hedge_summary.get("accepted_rate")
        accepted_text = f"{accepted_count}/{candidate_count}" if candidate_count > 0 else "N/A"
        if accepted_rate is not None:
            accepted_text = f"{accepted_text} ({_format_optional_ratio(accepted_rate)})"
        _render_glass_metric("Accepted", accepted_text, accent="#05ffa1" if accepted_count > 0 else "#fcee0a")
    with top[2]:
        _render_glass_metric("Rejected", str(rejected_count), accent="#ff3b5c" if rejected_count > 0 else "#05ffa1")
    with top[3]:
        quality_gap = hedge_summary.get("quality_gap")
        quality_text = _fmt(quality_gap, decimals=3) if quality_gap is not None else "N/A"
        _render_glass_metric("Quality gap", quality_text, accent="#fcee0a")
    with top[4]:
        hold_tail = hedge_summary.get("hold_tail") if isinstance(hedge_summary.get("hold_tail"), dict) else {}
        hold_tail_text = _fmt_optional_ms(hold_tail.get("p95_ms") if isinstance(hold_tail, dict) else None)
        _render_glass_metric("Hold tail p95", hold_tail_text, accent="#a78bfa")

    selected_reason = hedge_summary.get("selection_reason") or runtime_snapshot.get("selected_reason") or "N/A"
    top_rejection_reason = hedge_summary.get("top_rejection_reason")
    top_rejection_count = int(hedge_summary.get("top_rejection_reason_count") or 0)
    hold_tail = hedge_summary.get("hold_tail") if isinstance(hedge_summary.get("hold_tail"), dict) else {}
    hold_tail_summary = _format_distribution_text(hold_tail.get("distribution"))
    hold_tail_source = str(hold_tail.get("source") or "N/A").replace("_", " ")
    forced_flat_markets = hedge_summary.get("forced_flat_markets") or []
    forced_flat_events = hedge_summary.get("forced_flat_events") or []
    flatten_only_cycles = int(hedge_summary.get("flatten_only_cycles") or 0)

    status_bits = [
        f"Selected reason: {_humanize_reason_codes(selected_reason)}",
        f"Top rejection: {_humanize_reason_codes(top_rejection_reason)} ({top_rejection_count})" if top_rejection_reason else "Top rejection: N/A",
        f"Hold tail source: {hold_tail_source}",
        f"Forced-flat markets: {len(forced_flat_markets)}",
        f"Forced-flat events: {len(forced_flat_events)}",
        f"Flatten-only cycles: {flatten_only_cycles}",
    ]
    st.caption(" | ".join(status_bits))

    if hedge_summary.get("stale_unwind_observed") or hedge_summary.get("force_flat_observed") or hedge_summary.get("day_loss_observed"):
        observed_bits = []
        if hedge_summary.get("stale_unwind_observed"):
            observed_bits.append("stale unwind observed")
        if hedge_summary.get("force_flat_observed"):
            observed_bits.append("force-flat observed")
        if hedge_summary.get("day_loss_observed"):
            observed_bits.append("flatten-only observed")
        st.caption("Flattening diagnostics: " + ", ".join(observed_bits))

    if not _is_dev_mode(view_mode):
        if top_rejection_reason:
            st.caption(f"Top hedge rejection reason: {_humanize_reason_codes(top_rejection_reason)}")
        if hold_tail_summary != "N/A":
            st.caption(f"Hold-tail distribution: {hold_tail_summary}")

    with st.expander("Hedge candidate detail", expanded=False):
        detail = da.get_selection_diagnostic_rows(runtime_snapshot=runtime_snapshot, db_path=db_path).copy()
        if detail.empty:
            st.caption("No hedge candidate detail available.")
        else:
            detail["reason"] = detail["reason"].apply(_humanize_reason_codes)
            detail_cols = [
                col
                for col in [
                    "status",
                    "ticker",
                    "reason",
                    "quoteability_state",
                    "score",
                    "liquidity_score",
                    "transition_risk",
                    "proximity_score",
                    "mid",
                    "spread",
                    "volume",
                    "touch_depth",
                    "blocking_market_id",
                    "blocking_cluster_id",
                    "blocking_reason",
                ]
                if col in detail.columns and (col not in {"blocking_market_id", "blocking_cluster_id", "blocking_reason"} or detail[col].notna().any())
            ]
            if _is_dev_mode(view_mode):
                detail["accepted_label"] = detail["accepted"].map(lambda value: "accepted" if bool(value) else "rejected")
                dev_cols = [
                    col
                    for col in [
                        "accepted_label",
                        "ticker",
                        "title",
                        "reason",
                        "quoteability_state",
                        "score",
                        "liquidity_score",
                        "transition_risk",
                        "proximity_score",
                        "mid",
                        "spread",
                        "volume",
                        "touch_depth",
                        "blocking_market_id",
                        "blocking_cluster_id",
                        "blocking_reason",
                    ]
                    if col in detail.columns
                ]
                _display_runtime_table(detail, dev_cols)
            else:
                _display_runtime_table(detail, detail_cols)

    with st.expander("Hedge tail detail", expanded=False):
        tail = hedge_summary.get("hold_tail") if isinstance(hedge_summary.get("hold_tail"), dict) else {}
        if not tail:
            st.caption("Hold-tail metrics are not present in this runtime yet.")
        else:
            tail_rows = pd.DataFrame(
                [
                    {"metric": "sample_count", "value": tail.get("sample_count")},
                    {"metric": "p50_ms", "value": _fmt_optional_ms(tail.get("p50_ms"))},
                    {"metric": "p90_ms", "value": _fmt_optional_ms(tail.get("p90_ms"))},
                    {"metric": "p95_ms", "value": _fmt_optional_ms(tail.get("p95_ms"))},
                    {"metric": "max_ms", "value": _fmt_optional_ms(tail.get("max_ms"))},
                    {"metric": "distribution", "value": _format_distribution_text(tail.get("distribution"))},
                ]
            )
            _display_runtime_table(tail_rows, ["metric", "value"])

            if hedge_summary.get("forced_flat_markets") or hedge_summary.get("forced_flat_events"):
                forced_rows = pd.DataFrame(
                    [
                        {
                            "metric": "forced_flat_markets",
                            "value": ", ".join(str(item) for item in hedge_summary.get("forced_flat_markets") or []) or "N/A",
                        },
                        {
                            "metric": "forced_flat_events",
                            "value": ", ".join(str(item) for item in hedge_summary.get("forced_flat_events") or []) or "N/A",
                        },
                    ]
                )
                _display_runtime_table(forced_rows, ["metric", "value"])


# ── Tab Entry Points ─────────────────────────────────────────────────────────


def _load_core_data(db_path: Path) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """Shared data loader for all tabs."""
    summary = da.get_run_summary(db_path=db_path)
    pnl = da.get_paper_pnl_summary(db_path=db_path)
    sys_payload = da.get_latest_system_payload(db_path=db_path)
    curve = da.get_paper_pnl_curve(db_path=db_path)
    eq_df = da.get_execution_quality_df(db_path=db_path)
    return summary, pnl, sys_payload, curve, eq_df


def _render_markets_grid(db_path: Path, sys_payload: Dict[str, Any]) -> None:
    """Render a card for every market the bot has traded this session."""
    assert st is not None

    markets = da.get_all_markets_summary(db_path=db_path)
    if not markets:
        st.caption("No markets traded yet.")
        return

    active_market = (sys_payload.get("runner") or {}).get("market_id") or ""

    # Render cards in a responsive grid (2 per row)
    for i in range(0, len(markets), 2):
        cols = st.columns(2)
        for j, col in enumerate(cols):
            idx = i + j
            if idx >= len(markets):
                break
            mkt = markets[idx]
            slug = mkt["market"]
            is_active = (slug == active_market)
            pnl_val = mkt["realized_net_pnl"]
            pnl_color = _pnl_color(pnl_val)

            # Extract symbol from slug (e.g., "btc-updown-15m-..." → "BTC")
            symbol = slug.split("-")[0].upper() if slug else "?"
            expiry_html = _expiry_badge(slug)
            duration_ms = mkt["last_ts"] - mkt["first_ts"]

            border_color = "#05ffa1" if is_active else "rgba(0,240,255,0.2)"
            glow = "box-shadow:0 0 14px rgba(5,255,161,0.25);" if is_active else ""
            active_dot = '<span class="live-dot"></span>' if is_active else ""

            with col:
                st.markdown(
                    f'<div class="neon-card" style="border-color:{border_color};{glow}padding:12px 16px;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
                    f'<div>'
                    f'{active_dot}'
                    f'<span style="font-family:Orbitron,monospace;font-size:1.1em;font-weight:700;'
                    f'color:{"#05ffa1" if is_active else "#00f0ff"};letter-spacing:0.1em;">{symbol}</span>'
                    f'<span style="font-size:0.75em;opacity:0.4;margin-left:8px;">{slug}</span>'
                    f'</div>'
                    f'<div>{expiry_html}</div>'
                    f'</div>'
                    f'<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:0.85em;">'
                    f'<div><span style="opacity:0.5;">PnL</span> <b style="color:{pnl_color};font-family:Orbitron,monospace;">${pnl_val:+.2f}</b></div>'
                    f'<div><span style="opacity:0.5;">Fills</span> <b style="font-family:Orbitron,monospace;">{mkt["fills"]}</b></div>'
                    f'<div><span style="opacity:0.5;">Decisions</span> <b style="font-family:Orbitron,monospace;">{mkt["decisions"]}</b></div>'
                    f'<div><span style="opacity:0.5;">Turnover</span> <b style="font-family:Orbitron,monospace;">${mkt["turnover"]:.0f}</b></div>'
                    f'<div><span style="opacity:0.5;">Duration</span> <b style="font-family:Orbitron,monospace;">{_duration_str(float(duration_ms))}</b></div>'
                    f'<div><span style="opacity:0.5;">Q/S/F</span> <b style="font-family:Orbitron,monospace;">{mkt["quotes"]}/{mkt["skips"]}/{mkt["freezes"]}</b></div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


def render_market_making_tab(db_path: Path, view_mode: Optional[Any] = None) -> None:
    """Strategy drilldown: current market, selection rationale, market table, operations, fills."""
    assert st is not None

    summary, pnl, sys_payload, curve, eq_df = _load_core_data(db_path)
    runtime_snapshot = da.get_runtime_status_snapshot(db_path=db_path)
    market_summary = da.get_strategy_market_summary(db_path=db_path)
    operations = da.get_strategy_operation_rows(db_path=db_path, limit=40)
    explainer = da.get_decision_explainer_rows(db_path=db_path, limit=12)

    if not summary and not pnl and not runtime_snapshot.get("status"):
        st.markdown(
            '<div class="warn">No run data found — start <code>scripts/run_core_mm.py</code> to begin.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div style="font-family:Orbitron,monospace;font-size:1.15em;color:#00f0ff;'
        'letter-spacing:0.08em;margin-bottom:10px;">STRATEGY DRILLDOWN</div>',
        unsafe_allow_html=True,
    )

    _render_health_banner(summary, pnl, sys_payload)
    cols = st.columns(4)
    with cols[0]:
        _render_glass_metric("Strategy", str(runtime_snapshot.get("strategy_name") or summary.get("run_name") or "Unknown"))
    with cols[1]:
        _render_glass_metric("Market", str(runtime_snapshot.get("market") or sys_payload.get("runner", {}).get("market_id") or "N/A"))
    with cols[2]:
        _render_glass_metric("State", str(runtime_snapshot.get("state") or runtime_snapshot.get("book_health") or "unknown"), accent="#05ffa1" if runtime_snapshot.get("quoteable") else "#fcee0a")
    with cols[3]:
        _render_glass_metric("PnL", _fmt_money(runtime_snapshot.get("total_pnl") or pnl.get("total_pnl") or 0.0), accent="#05ffa1" if float(runtime_snapshot.get("total_pnl") or 0.0) >= 0 else "#ff3b5c")
    explainer_rows = explainer.copy()
    _render_operator_hero(
        runtime_snapshot=runtime_snapshot,
        explainer_rows=explainer_rows,
        pnl=pnl,
        curve=curve,
        db_path=db_path,
    )

    st.markdown("### Why it is trading or waiting")
    if not explainer_rows.empty:
        latest_rows = explainer_rows.head(5).copy()
        keep_cols = [
            col
            for col in [
                "ts",
                "market_label",
                "decision_summary",
                "plain_english",
                "ev",
                "reason_codes",
                "control_state",
                "hedge_action",
                "hedge_action_reason",
                "hedge_cluster_id",
                "hedge_market_id",
                "hedge_target_token_id",
                "hedge_target_side",
                "hedge_preferred_side",
                "hedge_ratio",
            ]
            if col in latest_rows.columns
        ]
        if not _is_dev_mode(view_mode) and "reason_codes" in keep_cols:
            keep_cols.remove("reason_codes")
        _display_runtime_table(latest_rows, keep_cols)
    else:
        st.caption("No decision history yet.")

    st.markdown("### Selection and market health")
    selection = runtime_snapshot.get("selection") if isinstance(runtime_snapshot.get("selection"), dict) else {}
    health = runtime_snapshot.get("active_market_health") if isinstance(runtime_snapshot.get("active_market_health"), dict) else {}
    a, b, c, d = st.columns(4)
    with a:
        _render_glass_metric("Selected reason", _humanize_reason_codes(runtime_snapshot.get("selected_reason") or selection.get("selected_reason") or "N/A"), accent="#fcee0a")
    with b:
        _render_glass_metric("Quoteable", "YES" if bool(runtime_snapshot.get("quoteable")) else "NO", accent="#05ffa1" if runtime_snapshot.get("quoteable") else "#ff3b5c")
    with c:
        _render_glass_metric("Spread", _fmt_bps(runtime_snapshot.get("spread_bps")))
    with d:
        _render_glass_metric("Book health", str(runtime_snapshot.get("book_health") or "unknown").upper(), accent="#05ffa1" if str(runtime_snapshot.get("book_health") or "").lower() == "healthy" else "#fcee0a")

    if health or selection:
        detail_cols = st.columns(2)
        with detail_cols[0]:
            _render_compact_panel(
                "Selection",
                [
                    ("market", _selection_market_label(selection.get("selected_market") or selection.get("market") or runtime_snapshot.get("market"))),
                    ("score", selection.get("selected_score")),
                    ("reason", _humanize_reason_codes(selection.get("selected_reason") or runtime_snapshot.get("selected_reason"))),
                    ("freeze", _freeze_reason_text(selection.get("freeze_reasons") or runtime_snapshot.get("freeze_reasons"))),
                ],
                accent="#fcee0a",
            )
        with detail_cols[1]:
            _render_compact_panel(
                "Book health",
                [
                    ("state", health.get("state") or runtime_snapshot.get("state")),
                    ("quoteable", "YES" if health.get("quoteable", runtime_snapshot.get("quoteable")) else "NO"),
                    ("both sides", "YES" if health.get("book_valid_both_sides", runtime_snapshot.get("book_valid_both_sides")) else "NO"),
                    ("spread", _fmt_bps(health.get("spread_bps") or runtime_snapshot.get("spread_bps"))),
                    ("health", health.get("book_health") or runtime_snapshot.get("book_health")),
                ],
                accent="#05ffa1",
            )

    _render_active_books_portfolio_panel(db_path, runtime_snapshot)
    _render_cluster_exposure_panel(db_path, runtime_snapshot)
    _render_selection_diagnostics_panel(db_path, runtime_snapshot)
    _render_hedge_readout_panel(db_path, runtime_snapshot, view_mode=view_mode)

    with st.expander("Operator controls", expanded=False):
        _render_run_control(db_path, runtime_snapshot)
        _render_operator_portfolio_controls(db_path, runtime_snapshot)

    with st.expander("Strategy settings and overnight supervision", expanded=False):
        _render_strategy_settings_controls(db_path, runtime_snapshot)
        _render_overnight_supervision(db_path)

    with st.expander("Tracked markets", expanded=False):
        if market_summary.empty:
            st.caption("No tracked markets yet.")
        else:
            market_rows = market_summary.copy()
            market_rows["last_update"] = market_rows.get("last_update_ms", pd.Series(dtype="float64")).apply(lambda ts: _short_time(int(ts)) if pd.notna(ts) and float(ts) > 0 else "N/A")
            market_rows["spread"] = market_rows.get("current_spread_bps")
            market_rows["profitability"] = market_rows.get("profitability_usd")
            cols_to_show = [
                col
                for col in [
                    "market_slug",
                    "symbol",
                    "market",
                    "state",
                    "decisions",
                    "active_orders",
                    "fills",
                    "spread",
                    "profitability",
                    "last_update",
                ]
                if col in market_rows.columns or col in {"spread", "profitability", "last_update"}
            ]
            if not _is_dev_mode(view_mode):
                cols_to_show = [col for col in cols_to_show if col != "market_slug"]
            _display_runtime_table(market_rows, cols_to_show)

    with st.expander("Live operations", expanded=False):
        ops = operations.copy()
        if not ops.empty:
            ops["ts"] = pd.to_datetime(ops["ts_ms"], unit="ms", utc=True, errors="coerce")
            ops["when"] = ops["ts"].dt.strftime("%H:%M:%S")
            op_cols = [
                col
                for col in [
                    "when",
                    "row_type",
                    "market_label",
                    "action",
                    "why",
                    "price",
                    "size",
                    "status",
                    "control_state",
                    "hedge_action",
                    "hedge_action_reason",
                    "hedge_cluster_id",
                    "hedge_market_id",
                    "hedge_target_token_id",
                    "hedge_target_side",
                    "hedge_preferred_side",
                    "hedge_ratio",
                ]
                if col in ops.columns
            ]
            if _is_dev_mode(view_mode):
                op_cols = [
                    col
                    for col in [
                        "when",
                        "row_type",
                        "market_slug",
                        "token_id",
                        "action",
                        "why",
                        "control_state",
                        "hedge_action",
                        "hedge_action_reason",
                        "hedge_cluster_id",
                        "hedge_market_id",
                        "hedge_target_token_id",
                        "hedge_target_side",
                        "hedge_preferred_side",
                        "hedge_ratio",
                        "price",
                        "size",
                        "status",
                    ]
                    if col in ops.columns
                ]
            _display_runtime_table(ops, op_cols)
        else:
            st.caption("No recent operations.")

    st.divider()
    if runtime_snapshot.get("stage") == "running":
        st.markdown("**Below the fold: fills, exposure, and charts**")
    _render_inventory_strip(sys_payload, db_path)
    st.markdown("**Live fills**")
    _render_fills_table(db_path, sys_payload)
    st.markdown("**Fill + Risk Timeline**")
    _render_fill_risk_timeline(db_path)
    _fill_timeline_sparkline(db_path, sys_payload)
    with st.expander("Performance and risk detail", expanded=False):
        _render_performance_risk(pnl, summary, sys_payload, curve, eq_df, db_path)


def render_alpha_overlay_tab(db_path: Path) -> None:
    """Tab 2: Alpha overlay diagnostics + memory layer."""
    assert st is not None

    st.markdown(
        '<div style="font-family:Orbitron,monospace;font-size:1.15em;color:#a78bfa;'
        'letter-spacing:0.08em;margin-bottom:12px;">ALPHA OVERLAY</div>',
        unsafe_allow_html=True,
    )
    _render_alpha_overlay(db_path)

    st.divider()

    _render_memory_layer(db_path)


def render_portfolio_tab(db_path: Path, view_mode: Optional[Any] = None) -> None:
    """Portfolio-first landing built from runtime discovery."""
    assert st is not None

    summary, pnl, sys_payload, curve, eq_df = _load_core_data(db_path)
    runtimes = da.discover_core_mm_runtimes()
    if runtimes.empty:
        runtimes = pd.DataFrame([da.get_runtime_status_snapshot(db_path=db_path)])
    portfolio_curve = da.get_portfolio_curve_from_runtimes(runtimes=runtimes)
    current_snapshot = da.get_runtime_status_snapshot(db_path=db_path)

    if runtimes.empty and not summary and not pnl:
        st.markdown(
            '<div class="warn">No portfolio data — start a paper run first.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div style="font-family:Orbitron,monospace;font-size:1.15em;color:#05ffa1;'
        'letter-spacing:0.08em;margin-bottom:10px;">PORTFOLIO</div>',
        unsafe_allow_html=True,
    )

    top_metrics = st.columns(5)
    portfolio_total = float(portfolio_curve["total_pnl"].iloc[-1]) if not portfolio_curve.empty and "total_pnl" in portfolio_curve.columns else float(pnl.get("total_pnl") or 0.0)
    active_runs = int((runtimes["stage"].astype(str) == "running").sum()) if "stage" in runtimes.columns else 0
    quoteable_runs = int((runtimes["quoteable"].fillna(False)).sum()) if "quoteable" in runtimes.columns else 0
    fills_total = int(pd.to_numeric(runtimes.get("fills", pd.Series(dtype="float64")), errors="coerce").fillna(0).sum()) if "fills" in runtimes.columns else int(summary.get("fills") or 0)
    selected_market = str(current_snapshot.get("market") or summary.get("market") or "N/A")
    with top_metrics[0]:
        _render_glass_metric("Portfolio PnL", _fmt_money(portfolio_total), accent="#05ffa1" if portfolio_total >= 0 else "#ff3b5c")
    with top_metrics[1]:
        _render_glass_metric("Runtimes", str(len(runtimes)))
    with top_metrics[2]:
        _render_glass_metric("Active", str(active_runs), accent="#05ffa1")
    with top_metrics[3]:
        _render_glass_metric("Quoteable", str(quoteable_runs))
    with top_metrics[4]:
        _render_glass_metric("Selected", selected_market[:28])

    chart_col, summary_col = st.columns([7, 3])
    with chart_col:
        if portfolio_curve.empty:
            st.info("No portfolio curve available yet.")
        elif alt is None:
            st.info("Portfolio curve available, but Altair is not installed.")
        else:
            curve_plot = portfolio_curve.copy()
            curve_plot["ts"] = pd.to_datetime(curve_plot["ts_ms"], unit="ms", utc=True, errors="coerce")
            chart = alt.Chart(curve_plot).mark_line(color="#05ffa1").encode(
                x=alt.X("ts:T", title="Time"),
                y=alt.Y("total_pnl:Q", title="Portfolio PnL"),
                tooltip=["ts:T", "total_pnl:Q", "realized_net_pnl:Q", "unrealized_pnl:Q"],
            ).properties(height=340)
            st.altair_chart(chart, use_container_width=True)
    with summary_col:
        selected_row = runtimes.iloc[0].to_dict() if not runtimes.empty else {}
        if not runtimes.empty and "db_path" in runtimes.columns:
            match = runtimes[runtimes["db_path"].astype(str) == str(db_path.resolve())]
            if not match.empty:
                selected_row = match.iloc[0].to_dict()
        _render_compact_panel(
            "Selected strategy",
            [
                ("strategy", selected_row.get("strategy_name")),
                ("exchange", selected_row.get("exchange")),
                ("mode", selected_row.get("mode")),
                ("state", selected_row.get("stage")),
                ("market", selected_row.get("market_label") or selected_row.get("market")),
                ("health", selected_row.get("book_health")),
                ("quoteable", "YES" if selected_row.get("quoteable") else "NO"),
                ("pnl", _fmt_money(selected_row.get("total_pnl"))),
                ("fills", selected_row.get("fills")),
            ],
            accent="#05ffa1",
        )

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        _render_glass_metric("Selected market", selected_market)
    with col_b:
        _render_glass_metric("Current state", str(current_snapshot.get("state") or current_snapshot.get("book_health") or "unknown"))
    with col_c:
        _render_glass_metric("Reason", str(current_snapshot.get("selected_reason") or "N/A"), accent="#fcee0a")

    with st.expander("Strategy registry", expanded=False):
        runtime_rows = runtimes.copy()
        if not runtime_rows.empty:
            runtime_rows["selected"] = runtime_rows["db_path"].astype(str) == str(db_path.resolve()) if "db_path" in runtime_rows.columns else False
            runtime_rows["last_update"] = runtime_rows.apply(
                lambda row: _fmt_last_update(
                    int(
                        ((row.get("snapshot") or {}).get("updated_at_ms") if isinstance(row.get("snapshot"), dict) else row.get("updated_at_ms"))
                        or 0
                    )
                ),
                axis=1,
            )
            runtime_rows["selected_reason"] = runtime_rows.get("selected_reason", pd.Series(dtype=object)).fillna("")
            show_cols = [
                col for col in [
                    "selected",
                    "label",
                    "exchange",
                    "mode",
                    "stage",
                    "market_label",
                    "decisions",
                    "fills",
                    "total_pnl",
                    "quoteable",
                    "book_health",
                    "spread_bps",
                    "selected_reason",
                    "last_update",
                ]
                if col in runtime_rows.columns
            ]
            if _is_dev_mode(view_mode):
                show_cols = [col for col in ["selected", "label", "runtime_root", "db_path", "exchange", "mode", "stage", "market", "market_label", "decisions", "fills", "total_pnl", "quoteable", "book_health", "spread_bps", "selected_reason", "last_update"] if col in runtime_rows.columns]
            _display_runtime_table(runtime_rows, show_cols)
        else:
            st.caption("No discovered runtimes yet.")

    if _is_dev_mode(view_mode):
        with st.expander("Advanced telemetry", expanded=False):
            if not eq_df.empty:
                _markout_chart(eq_df)
            _fill_rate_chart(curve)
            _render_inventory_section(db_path, sys_payload)
            _render_market_history(db_path)
            _render_risk_summary(sys_payload, db_path)
            _fill_breakdown_chart(db_path, sys_payload)
            _render_alert_feed(sys_payload)


# Backward-compatible alias
def render_core_mm_panel(db_path: Path) -> None:
    render_market_making_tab(db_path)
