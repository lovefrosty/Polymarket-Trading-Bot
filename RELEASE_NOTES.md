# Release Notes

## v3.0.0-safety (2026-02-11)

### Guarantees
- Deterministic safety hardening for live/runtime control paths.
- Liveness monitoring and fail-closed freeze/quarantine behavior for WS starvation, gaps, and clock drift.
- Unknown startup-order quarantine in live modes (no silent auto-cancel at startup).
- Throttle and risk-budget gate enforcement for order/cancel cadence, daily loss, and notional limits.
- Reconciliation/promotion checks remain fail-closed with explicit reason-code diagnostics.
- Dashboard reliability health panel consumes pre-aggregated SQLite telemetry.
- Replay/chaos coverage validates deterministic fault handling and recovery logging.

### Explicitly Out of Scope
- No changes to strategy/EV math or signal generation policy.
- No expansion to adaptive quoting logic.
- No weakening of causality/time-leak guardrails.
