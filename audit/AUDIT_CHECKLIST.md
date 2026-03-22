[x] Read-only: no signing, no order placement in the runtime path
[x] Discovery v2: CLOB enumeration → fee gate → optional Gamma enrichment
[x] Fee gate remains hard (fee_rate_bps > 0)
[x] Deterministic replay uses same handlers as live
[x] DecisionTape schema remains backward compatible
[x] ReferenceTape schema remains backward compatible
[x] Book updates are monotonic by recv_mono_ns
[x] Feature as-of guard enforced (feature_from_future hard-fail)
[x] Confidence gate blocks trades below threshold
[x] assets_ids uses token IDs only (no condition_id in public market WS)
[x] discovery_summary.json emitted for each run
