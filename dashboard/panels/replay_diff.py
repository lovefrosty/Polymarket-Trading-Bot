from __future__ import annotations

from time import perf_counter
from typing import Any, List

import pandas as pd

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, PanelDependency, ReplayMismatchRow
from dashboard.data_access import query_df, require_sources, safe_json

REPLAY_DIFF_DEP = PanelDependency(
    panel_id="replay_diff",
    required_sources=("decisions",),
    optional_sources=(),
)


def render_replay_diff_panel(filters: DashboardFilters, start_ts: int, p_exec_delta_bps: float = 5.0, panel_budget_ms: int = 300) -> None:
    assert st is not None
    t0 = perf_counter()
    ok, missing_required, _ = require_sources(REPLAY_DIFF_DEP.required_sources)
    if not ok:
        st.markdown(
            f"<div class='warn'><b>DEGRADED</b> missing required table(s): {', '.join(missing_required)}</div>",
            unsafe_allow_html=True,
        )
        return

    decisions = query_df(
        """
        SELECT ts_ms, decision_id, market, token_id, action, reason_codes, p_hat, policy_json
        FROM decisions
        WHERE ts_ms >= ?
        ORDER BY ts_ms DESC
        LIMIT ?
        """,
        (start_ts, max(filters.lookback_rows, 500)),
    )

    if decisions.empty:
        st.info("No decisions available for replay/live diff.")
        return

    mismatches = compute_replay_mismatches(decisions, p_exec_delta_bps)

    st.subheader("Replay vs live mismatch (v0)")
    if not mismatches:
        st.caption("DEGRADED replay payload fields not present; no v0 mismatch rows computed.")
        return

    frame = pd.DataFrame([m.__dict__ for m in mismatches])
    frame["reason_attribution_ts"] = frame["evidence_refs"].apply(
        lambda refs: ",".join([str(item).replace("ts:", "") for item in refs if str(item).startswith("ts:")])
    )
    st.dataframe(frame, width="stretch", height=240)

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")


def compute_replay_mismatches(decisions: pd.DataFrame, p_exec_delta_bps: float) -> List[ReplayMismatchRow]:
    mismatches: List[ReplayMismatchRow] = []
    for _, row in decisions.iterrows():
        payload = safe_json(row.get("policy_json"))
        replay_action = str(payload.get("replay_action") or payload.get("replay", {}).get("action") or "")
        replay_reasons = str(payload.get("replay_reason_codes") or payload.get("replay", {}).get("reason_codes") or "")
        replay_p_exec = payload.get("replay_p_exec")
        live_p_exec = payload.get("live_p_exec")
        if not replay_action and not replay_reasons and replay_p_exec is None:
            continue
        action_live = str(row.get("action") or "")
        reasons_live = str(row.get("reason_codes") or "")
        p_delta = 0.0
        if replay_p_exec is not None and live_p_exec is not None:
            try:
                p_delta = abs(float(replay_p_exec) - float(live_p_exec)) * 10_000.0
            except (TypeError, ValueError):
                p_delta = 0.0
        mismatch = (
            (replay_action and replay_action != action_live)
            or (replay_reasons and replay_reasons != reasons_live)
            or (p_delta > p_exec_delta_bps)
        )
        if mismatch:
            evidence_refs = [str(row.get("decision_id") or "")]
            evidence_refs.extend([f"ts:{ts}" for ts in _extract_reason_timestamps(payload)])
            mismatches.append(
                ReplayMismatchRow(
                    decision_id=str(row.get("decision_id") or ""),
                    action_live=action_live,
                    action_replay=replay_action,
                    reasons_live=reasons_live,
                    reasons_replay=replay_reasons,
                    p_exec_delta_bps=float(p_delta),
                    evidence_refs=evidence_refs,
                )
            )
    return mismatches


def _extract_reason_timestamps(payload: dict[str, Any]) -> List[int]:
    timestamps: List[int] = []
    direct = payload.get("reason_timestamps_ms")
    if isinstance(direct, list):
        for item in direct:
            try:
                timestamps.append(int(item))
            except (TypeError, ValueError):
                continue
    replay = payload.get("replay")
    if isinstance(replay, dict):
        for key, value in replay.items():
            if not str(key).endswith("_ts_ms"):
                continue
            try:
                timestamps.append(int(value))
            except (TypeError, ValueError):
                continue
    return sorted(set(timestamps))
