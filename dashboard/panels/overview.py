from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard import data_access as da
from dashboard.contracts import DashboardFilters, ViewMode
from dashboard.ui_theme import pill


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _status_chip_class(status: str) -> str:
    value = str(status or "").upper()
    if value in {"ABORT", "ERROR", "REJECT", "CRITICAL", "NONE_FOUND"}:
        return "alert"
    if value in {"WARN", "WARNING", "TIMEOUT", "RETRY"}:
        return "warn"
    return "ok"


def _load_runtime_state_badges() -> Dict[str, str]:
    latest_sys = da.query_df(
        """
        SELECT mode, is_frozen
        FROM system_state
        ORDER BY as_of_ts DESC
        LIMIT 1
        """
    )
    latest_discovery = da.query_df(
        """
        SELECT status, reason_code
        FROM discovery_requests
        ORDER BY ts_ms DESC
        LIMIT 1
        """
    )
    latest_rollover = da.query_df(
        """
        SELECT event_type, readiness_ok
        FROM rollover_status
        ORDER BY ts_ms DESC
        LIMIT 1
        """
    )
    mode = "N/A"
    guard = "N/A"
    discovery = "N/A"
    rollover = "N/A"
    if not latest_sys.empty:
        row = latest_sys.iloc[0]
        mode = str(row.get("mode") or "N/A")
        guard = "HALT_QUOTING" if int(row.get("is_frozen") or 0) == 1 else "ALLOW_QUOTING"
    if not latest_discovery.empty:
        row = latest_discovery.iloc[0]
        status = str(row.get("status") or "UNKNOWN")
        reason = str(row.get("reason_code") or "")
        discovery = status if not reason else f"{status}:{reason}"
    if not latest_rollover.empty:
        row = latest_rollover.iloc[0]
        readiness = row.get("readiness_ok")
        readiness_text = "ready" if readiness == 1 else ("not_ready" if readiness == 0 else "n/a")
        rollover = f"{str(row.get('event_type') or 'UNKNOWN')}:{readiness_text}"
    return {
        "Mode": mode,
        "Quoting Guard": guard,
        "Discovery": discovery,
        "Rollover": rollover,
    }


def _load_latest_abort_reject_refs() -> List[str]:
    latest_abort = da.query_df(
        """
        SELECT event_type, payload_json
        FROM rollover_status
        WHERE event_type IN ('ABORT','HEALTH_FREEZE')
        ORDER BY ts_ms DESC
        LIMIT 1
        """
    )
    latest_reject = da.query_df(
        """
        SELECT status, reason, reason_code
        FROM orders
        WHERE LOWER(COALESCE(status,'')) LIKE '%reject%' OR LOWER(COALESCE(reason_code,'')) LIKE '%error%'
        ORDER BY ts_ms DESC
        LIMIT 1
        """
    )
    refs: List[str] = []
    if not latest_abort.empty:
        row = latest_abort.iloc[0]
        refs.append(f"rollover={row.get('event_type')}")
    if not latest_reject.empty:
        row = latest_reject.iloc[0]
        refs.append(f"reject={row.get('reason_code') or row.get('reason') or row.get('status')}")
    return refs


def _load_cancel_latency(window_start_ms: int) -> tuple[Any, Any]:
    cancel_latency = da.query_df(
        """
        SELECT
          AVG(CASE WHEN LOWER(COALESCE(status,'')) LIKE '%cancel%' THEN
              (json_extract(payload_json, '$.t_cancel_wall_ms') - json_extract(payload_json, '$.t_cancel_request_wall_ms'))
          END) AS cancel_latency_p50_ms,
          MAX(CASE WHEN LOWER(COALESCE(status,'')) LIKE '%cancel%' THEN
              (json_extract(payload_json, '$.t_cancel_wall_ms') - json_extract(payload_json, '$.t_cancel_request_wall_ms'))
          END) AS cancel_latency_p95_ms
        FROM orders
        WHERE ts_ms >= ?
        """,
        (window_start_ms,),
    )
    return (
        da.safe_first(cancel_latency, "cancel_latency_p50_ms", None),
        da.safe_first(cancel_latency, "cancel_latency_p95_ms", None),
    )


if st is not None and hasattr(st, "cache_data"):

    @st.cache_data(ttl=1, show_spinner=False)
    def _load_runtime_state_badges_cached() -> Dict[str, str]:
        # Short TTL reduces repeated "latest row" queries in auto-refresh loops.
        return _load_runtime_state_badges()

    @st.cache_data(ttl=1, show_spinner=False)
    def _load_latest_abort_reject_refs_cached() -> List[str]:
        # Keep reason snippets current while avoiding duplicated per-tick probes.
        return _load_latest_abort_reject_refs()

    @st.cache_data(ttl=5, show_spinner=False)
    def _load_cancel_latency_cached(window_start_ms: int) -> tuple[Any, Any]:
        # 1h aggregate is expensive compared with tick cadence; safe to cache briefly.
        return _load_cancel_latency(window_start_ms)

else:

    def _load_runtime_state_badges_cached() -> Dict[str, str]:
        return _load_runtime_state_badges()

    def _load_latest_abort_reject_refs_cached() -> List[str]:
        return _load_latest_abort_reject_refs()

    def _load_cancel_latency_cached(window_start_ms: int) -> tuple[Any, Any]:
        return _load_cancel_latency(window_start_ms)


def render_live_order_newswire(
    filters: DashboardFilters,
    view_mode: ViewMode,
    label_registry: Dict[str, Dict[str, Any]],
) -> None:
    assert st is not None
    _ = view_mode
    _ = label_registry
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ts = max(0, now_ms - int(filters.window_minutes) * 60_000)
    state = st.session_state.setdefault(
        "order_newswire_state",
        {"last_seen_ts_ms": 0, "last_seen_event_id": "", "rows": []},
    )
    known_markets = ["ALL"] + da.get_live_order_newswire_markets(end_ts_ms=now_ms)
    if "order_newswire_market" not in st.session_state:
        st.session_state["order_newswire_market"] = filters.selected_market if filters.selected_market != "ALL" else "ALL"
    if str(st.session_state["order_newswire_market"]) not in known_markets:
        st.session_state["order_newswire_market"] = "ALL"
    selected_market = st.radio(
        "Newswire market",
        known_markets,
        index=max(0, known_markets.index(str(st.session_state["order_newswire_market"]))),
        horizontal=True,
        key="order_newswire_market",
    )

    incremental = da.get_live_order_newswire(
        start_ts_ms=start_ts,
        end_ts_ms=now_ms,
        limit=max(100, int(filters.lookback_rows)),
        market_slug=selected_market,
        last_seen_ts_ms=int(state.get("last_seen_ts_ms") or 0) if state.get("rows") else None,
        last_seen_event_id=str(state.get("last_seen_event_id") or "") if state.get("rows") else None,
    )
    if not incremental.empty:
        new_rows = incremental.to_dict("records")
        all_rows = new_rows + list(state.get("rows") or [])
        deduped: List[Dict[str, Any]] = []
        seen_keys = set()
        for row in all_rows:
            key = (
                int(row.get("ts_ms") or 0),
                str(row.get("event_id") or ""),
                str(row.get("event_type") or ""),
                str(row.get("order_id") or ""),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(row)
        deduped.sort(key=lambda item: (int(item.get("ts_ms") or 0), str(item.get("event_id") or "")), reverse=True)
        state["rows"] = deduped[:500]
        head = state["rows"][0]
        state["last_seen_ts_ms"] = int(head.get("ts_ms") or 0)
        state["last_seen_event_id"] = str(head.get("event_id") or "")

    frame = pd.DataFrame(list(state.get("rows") or []))
    if frame.empty:
        frame = da.get_live_order_newswire(
            start_ts_ms=start_ts,
            end_ts_ms=now_ms,
            limit=max(100, int(filters.lookback_rows)),
            market_slug=selected_market,
        )
        state["rows"] = frame.to_dict("records")
        if not frame.empty:
            head = frame.iloc[0]
            state["last_seen_ts_ms"] = int(head.get("ts_ms") or 0)
            state["last_seen_event_id"] = str(head.get("event_id") or "")
    if not frame.empty:
        frame = frame.sort_values(["ts_ms", "event_id"], ascending=[False, False]).copy()
        frame["ts"] = frame["ts_ms"].apply(_iso)

    one_min_ago = now_ms - 60_000
    recent = frame[frame["ts_ms"] >= one_min_ago].copy() if not frame.empty else frame
    order_events_per_min = int(len(recent[recent["event_type"] == "order"])) if not recent.empty else 0
    fills_per_min = int(len(recent[recent["event_type"] == "fill"])) if not recent.empty else 0
    reject_count = int(
        len(
            recent[
                recent["status"].astype(str).str.contains("reject", case=False, na=False)
                | recent["reason_code"].astype(str).str.contains("reject|error", case=False, na=False)
            ]
        )
    ) if not recent.empty else 0
    p50_cancel, p95_cancel = _load_cancel_latency_cached(max(0, now_ms - 60 * 60 * 1000))

    st.subheader("Live Order Newswire")
    kpi_cols = st.columns(5, gap="small")
    kpi_cols[0].metric("Order events/min", order_events_per_min)
    kpi_cols[1].metric("Fills/min", fills_per_min)
    kpi_cols[2].metric("Rejects/min", reject_count)
    kpi_cols[3].metric("Cancel latency p50 (ms)", "N/A" if p50_cancel is None else f"{float(p50_cancel):.1f}")
    kpi_cols[4].metric("Cancel latency p95 (ms)", "N/A" if p95_cancel is None else f"{float(p95_cancel):.1f}")

    badges = _load_runtime_state_badges_cached()
    badge_markup = " ".join(pill(f"{k}: {v}", _status_chip_class(v)) for k, v in badges.items())
    st.markdown(badge_markup, unsafe_allow_html=True)

    refs = _load_latest_abort_reject_refs_cached()
    if refs:
        st.caption("Latest reasons: " + " | ".join(refs) + " | See Rollover / WS Subscribe tab for details.")

    if frame.empty:
        st.info("No order/fill events yet.")
        return
    display_cols = [
        "ts",
        "event_type",
        "market_slug",
        "condition_id",
        "token_id",
        "side",
        "price",
        "size",
        "order_id",
        "status",
        "mode",
        "reason_code",
    ]
    st.dataframe(frame[[col for col in display_cols if col in frame.columns]].head(max(100, int(filters.lookback_rows))), width="stretch", height=300)
