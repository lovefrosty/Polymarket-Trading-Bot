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


# ── Enhancement A: Market Expiry Countdown ───────────────────────────────────


def _parse_expiry_ms(market_id: Optional[str]) -> Optional[int]:
    """Parse Unix timestamp (seconds) from slug suffix, e.g. xrp-updown-15m-1773702900."""
    m = re.search(r"-(\d{9,10})$", market_id or "")
    return int(m.group(1)) * 1000 if m else None


def _expiry_badge(market_id: Optional[str]) -> str:
    """HTML badge showing time remaining until market close."""
    expiry_ms = _parse_expiry_ms(market_id)
    if not expiry_ms:
        return ""
    remaining_ms = expiry_ms - int(_time_mod.time() * 1000)
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

    degraded: List[str] = []
    if not connected:
        degraded.append("feed disconnected")
    if not has_books:
        degraded.append("no books")

    health_class = "warn" if degraded else "ok"
    health_label = "DEGRADED" if degraded else "HEALTHY"
    expiry_html = _expiry_badge(market_id)

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


def _render_symbol_grid(sys_payload: Dict[str, Any], db_path: Path) -> None:
    """4-column grid: BTC | ETH | SOL | XRP with fill/PnL/active status."""
    assert st is not None
    sym_df = da.get_per_symbol_summary(db_path=db_path)
    active_market = (sys_payload.get("runner") or {}).get("market_id") or ""
    symbols = ["BTC", "ETH", "SOL", "XRP"]
    cols = st.columns(4)
    for col, sym in zip(cols, symbols):
        row = None
        if not sym_df.empty and "symbol" in sym_df.columns:
            matching = sym_df[sym_df["symbol"] == sym]
            if not matching.empty:
                row = matching.iloc[0]

        is_active = sym.lower() in active_market.lower()
        fills = int(row["fills"]) if row is not None and pd.notna(row.get("fills")) else 0
        pnl = float(row["realized_net_pnl"]) if row is not None and pd.notna(row.get("realized_net_pnl")) else 0.0
        decisions = int(row["total_decisions"]) if row is not None and pd.notna(row.get("total_decisions")) else 0
        markets = int(row["markets"]) if row is not None and pd.notna(row.get("markets")) else 0

        pnl_color = _pnl_color(pnl)
        border_color = "#05ffa1" if is_active else ("#00f0ff" if fills > 0 else "rgba(0,240,255,0.12)")
        glow = "box-shadow:0 0 12px rgba(5,255,161,0.2);" if is_active else ""
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
    st.altair_chart(chart, use_container_width=True)


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
    st.altair_chart(chart, use_container_width=True)


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
    st.altair_chart(chart, use_container_width=True)


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
    st.altair_chart(chart, use_container_width=True)


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
    st.altair_chart(chart, use_container_width=True)


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

    # Avg cost line from inventory
    try:
        inv_df = da.get_per_token_inventory(db_path=db_path)
        if not inv_df.empty and "token_id" in inv_df.columns:
            my_inv = inv_df[inv_df["token_id"].astype(str) == str(token_id)]
            if not my_inv.empty:
                pos_size = float(my_inv.iloc[0].get("yes_qty") or 0.0)
                if pos_size > 0:
                    # Show position size as annotation near mid
                    pass  # Position shown in header, not duplicated on chart
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
        [c for c in ["ts", "side", "fill_price", "fill_qty", "realized_net_pnl_delta", "fee_usdc", "token_id"] if c in fills_df.columns]
    ].copy()

    if "ts" in display.columns:
        display["ts"] = display["ts"].dt.strftime("%H:%M:%S")
    if "fill_price" in display.columns:
        display["fill_price"] = display["fill_price"].map(lambda v: f"{v:.4f}" if pd.notna(v) else "")
    if "fill_qty" in display.columns:
        display["fill_qty"] = display["fill_qty"].map(lambda v: f"{v:.1f}" if pd.notna(v) else "")
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


# ── Main Panel ───────────────────────────────────────────────────────────────


def render_core_mm_panel(db_path: Path) -> None:
    assert st is not None
    st.subheader("Core MM — Live Run Monitor")

    summary = da.get_run_summary(db_path=db_path)
    pnl = da.get_paper_pnl_summary(db_path=db_path)
    sys_payload = da.get_latest_system_payload(db_path=db_path)
    curve = da.get_paper_pnl_curve(db_path=db_path)
    eq_df = da.get_execution_quality_df(db_path=db_path)

    if not summary and not pnl:
        st.markdown('<div class="warn">No core_mm run data found for the selected runtime.</div>', unsafe_allow_html=True)
        st.caption(f"db_path={db_path}")
        return

    # ── Health Banner (Enhancement A: expiry countdown inline) ─────────────
    _render_health_banner(summary, pnl, sys_payload)

    # ── Phase 0 badge ──────────────────────────────────────────────────────
    phase0 = summary.get("phase0_acceptance") or {}
    if phase0:
        _phase0_badge(phase0)

    # ── Multi-Symbol Grid (BTC / ETH / SOL / XRP) ─────────────────────────
    _render_symbol_grid(sys_payload, db_path)

    st.divider()

    # ── PnL Chart + Compact Metrics Sidebar (Enhancement D + G inside) ────
    updated_at = summary.get("updated_at_ms") or pnl.get("latest_ts_ms")
    col_chart, col_stats = st.columns([3, 1])
    with col_chart:
        _pnl_drawdown_chart(curve)
    with col_stats:
        _render_metrics_sidebar(pnl, summary, sys_payload, updated_at, curve, eq_df, db_path=db_path)

    st.divider()

    # ── Market History (multi-market, only shown when >1 market traded) ───
    _render_market_history(db_path)

    # ── Active Market YES/NO Detail (Enhancement B: depth bars) ───────────
    _render_active_market_detail(sys_payload, db_path)

    st.divider()

    # ── Risk Summary + Fill Breakdown ─────────────────────────────────────
    col_risk, col_fills = st.columns([1, 2])
    with col_risk:
        _render_risk_summary(sys_payload, db_path)
    with col_fills:
        _fill_breakdown_chart(db_path, sys_payload)

    st.divider()

    # ── Fills Table + Timeline Sparkline (Enhancement E) ──────────────────
    st.markdown("**Recent fills (last 20)**")
    _render_fills_table(db_path, sys_payload)
    _fill_timeline_sparkline(db_path, sys_payload)

    # ── Alert Feed (Enhancement F) ────────────────────────────────────────
    _render_alert_feed(sys_payload)

    st.divider()

    # ── Alpha Overlay Status ──────────────────────────────────────────────
    _render_alpha_overlay(db_path)

    # ── Memory Layer ───────────────────────────────────────────────────────
    _render_memory_layer(db_path)

    # ── Advanced Telemetry (collapsed) ────────────────────────────────────
    with st.expander("Advanced Telemetry", expanded=False):
        if not eq_df.empty:
            _markout_chart(eq_df)

        col_wr, col_inv = st.columns(2)
        with col_wr:
            _fill_rate_chart(curve)
        with col_inv:
            _render_inventory_section(db_path, sys_payload)

        per_token_qs = sys_payload.get("per_token_quote_stats") or {}
        if per_token_qs:
            st.markdown("**Per-token quote stats**")
            yn_map = _resolve_yes_no_tokens(sys_payload)
            rows_qs = []
            for tid, counts in per_token_qs.items():
                rows_qs.append({
                    "Token": yn_map.get(str(tid), _token_label(str(tid))),
                    "Buy Q": counts.get("buy_quotes", 0),
                    "Sell Q": counts.get("sell_quotes", 0),
                    "Skip": counts.get("skip_count", 0),
                    "Freeze": counts.get("freeze_count", 0),
                    "Emergency": counts.get("emergency_count", 0),
                })
            st.dataframe(pd.DataFrame(rows_qs), use_container_width=True, hide_index=True)

        feed = sys_payload.get("feed") or {}
        if feed:
            msgs = int(feed.get("received_messages") or 0)
            updates = int(feed.get("applied_book_updates") or 0)
            st.caption(f"Feed: {msgs:,} messages, {updates:,} book updates")
