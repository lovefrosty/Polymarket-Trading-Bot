from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from time import perf_counter
from typing import Optional

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover
    st = None  # type: ignore[assignment]

from dashboard.contracts import DashboardFilters, DrillthroughContext, PanelDependency
from dashboard.data_access import build_drillthrough_context, now_utc_iso

EXPORT_DEP = PanelDependency(
    panel_id="incident_export",
    required_sources=(),
    optional_sources=("logs", "decisions", "market_data_book", "system_state"),
)


def render_export_panel(
    filters: DashboardFilters,
    start_ts: int,
    end_ts: int,
    drillthrough_context: Optional[DrillthroughContext],
    panel_budget_ms: int = 250,
) -> DrillthroughContext:
    assert st is not None
    t0 = perf_counter()

    context = drillthrough_context or build_drillthrough_context(
        metric_key="MANUAL_EXPORT",
        start_ts_ms=start_ts,
        end_ts_ms=end_ts,
        market=filters.selected_market,
        token_id=filters.selected_token,
        reason_codes=[],
        evidence_refs=[],
        payload={"created_by": "dashboard", "created_at": now_utc_iso()},
    )

    st.subheader("Incident export bundle v0")
    st.caption(
        "Bundle includes: manifest.json, context.json, logs.jsonl, decisions.jsonl, book_events.jsonl, system_state.json, config_fingerprint.json"
    )
    st.code(
        "python3 -m scripts.export_runtime_jsonl --db-path runtime.db --out-dir logs/export "
        f"--incident-bundle --start-ts-ms {start_ts} --end-ts-ms {end_ts} "
        f"--market {filters.selected_market} --token-id {filters.selected_token}"
    )

    context_json = json.dumps(asdict(context), indent=2, sort_keys=True)
    st.download_button(
        label="Download context.json",
        data=context_json,
        file_name="context.json",
        mime="application/json",
    )

    out_dir = Path("logs/export")
    out_dir.mkdir(parents=True, exist_ok=True)
    context_path = out_dir / f"context_{context.context_id}.json"
    context_path.write_text(context_json, encoding="utf-8")
    st.caption(f"Wrote context snapshot: {context_path}")

    elapsed = (perf_counter() - t0) * 1000.0
    if elapsed > panel_budget_ms:
        st.caption(f"DEGRADED panel_over_budget_ms={elapsed:.1f} budget_ms={panel_budget_ms}")
    return context
