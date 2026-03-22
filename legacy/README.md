# Legacy Code Archive

This directory contains code from the original Polymarket Bot research platform,
quarantined on 2026-03-16 when the system was narrowed to a focused standalone
market maker (`core_mm/`).

## Contents

| Directory | Description |
|-----------|-------------|
| `core/` | Original reference feed stack, broker adapter, on-chain signals, model pipeline, and decision engine (~11,100 lines) |
| `data/` | WebSocket clients used by `run_system.py` (`polymarket_ws.py`, `reference_feed.py`) |
| `scripts/` | Legacy runner, training scripts, analysis tools, and promotion gate checkers (~22 files) |
| `tests/` | ~138 test files covering the legacy `core/` system |

## Why kept, not deleted

- `core/` reference feed and broker code is a useful reference for the LIVE wiring work (Epic 1 blocker).
- The legacy tests document expected behaviour of the old system and may be useful for comparison.
- `run_system.py` contains the old paper-soak gate logic worth consulting when implementing the promotion gate.

## Nothing here is imported by the active codebase

`core_mm/`, `dashboard/`, and `scripts/run_core_mm.py` have zero imports from this directory.
Tests are run from `tests/core_mm/` only.
