# Polymarket Trading Bot — Project Tracker

Last updated: 2026-02-04

## Status Summary

Goal: single execution-safe system for 15-minute crypto Up/Down markets with strict reference validation, causal features, and simulation-true backtests.

Current focus:
- Reference WS integration + multi-source fallback
- Walk-forward report with expanded metrics
- Dataset export upgrades (CSV + Parquet)

## Roadmap (12 Implementations)

Status legend: `Done`, `Partial`, `Missing`, `In Progress`

1. WS On-chain Ingestion (Primary) — Done  
Goal: WS persistent filters for on-chain logs  
Acceptance: events <1s post-block, reconnects ok, EventTape includes `tx_hash` + `log_index`

2. HTTP Reconciliation (Fallback) — Done  
Goal: gap-fill missed logs  
Acceptance: WS off still yields continuity; no double counting

3. LRU Deduper — Done  
Goal: dedupe on reconnect/provider quirks  
Acceptance: duplicate `(tx_hash, log_index)` processed once

4. Heartbeat + Health Telemetry — Partial  
Goal: regular system-alive + lag counters  
Acceptance: heartbeat every 2s idle; includes lag/counters

5. Event Normalization Schema — Partial  
Goal: canonical event record with stable keys  
Acceptance: all events have required keys; tests pass

6. As-of Timestamp Guardrails — Partial  
Goal: enforce causality in features  
Acceptance: assert `max_event_ts_used <= as_of_ts`

7. Exporter (Tapes → Dataset) — Partial  
Goal: Parquet/CSV rows with as-of join  
Acceptance: re-run exporter → identical dataset; stable schema

8. Labeler (15m target) — Partial  
Goal: honest labels (no leakage), log-odds optional  
Acceptance: labels use only future data

9. Broker Interface — Done  
Goal: strategy talks to Broker interface only  
Acceptance: strategy runs unchanged on Sim vs Live

10. SimBroker + Fill/Cost Model — Partial  
Goal: realistic backtest (fees/slip/latency)  
Acceptance: PnL sensibly worsens with slippage/latency

11. Walk-forward Report — Partial  
Goal: OOS evaluation + stress tests  
Acceptance: stable results across windows; outputs PnL/DD/turnover

12. Dry-run Execution Loop — Partial  
Goal: full loop w/ SimBroker live feed  
Acceptance: 6h run; orders → fills → inventory logged

## Audit Considerations

Priority is highest-first.

- Reference P Pipeline — In Progress  
Switch to WS for Kraken; multi-source fallback; degrade confidence vs freeze.

- DecisionTape Emission / Signal Cadence — Done  
DecisionTape every 1s (heartbeat) + event-triggered decisions.

- L2 Order Book Maintenance — Partial  
Configurable staleness; multi-level imbalance feature still needed.

- Replay & Calibration Harness — Partial  
Fast-replay flag + walk-forward CV pending.

- Fee Model Accuracy — Partial  
Configurable fee override; ability to query fee curve pending.

## Open Questions

- How strict should the partial reference confidence gating be in live/paper?
- Do we want a dedicated trade PnL ledger (TradeTape → PnL) beyond label-based PnL?

## Acceptance Tests (Must-Haves)

- Failure A (Reference): stale or mismatched reference ⇒ no trade
- Failure B (Time leakage): max feature ts < decision ts
- Failure C (Book lies): depth/slippage gates enforced
- Failure D (One-leg risk): hedge deadline enforced
- Failure E (Latency): reject on excessive signal age / ack latency

## Next Milestones

1. Finish reference WS integration + fallback confidence policy
2. Deliver walk-forward report with expanded metrics and sensitivity
3. Export datasets to CSV + Parquet with daily partitions
