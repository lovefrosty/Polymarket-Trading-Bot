# G1 - On-Chain + Discovery Hardening

Date: 2026-02-11 (UTC)
Status: Complete

## Changed Files
- `core/market_discovery.py`
- `scripts/discover_markets.py`
- `scripts/run_system.py`
- `tests/test_market_discovery_slug_events_fallback.py`

## What Changed
- Added events-endpoint fallback when slug-window discovery returns zero candidates.
- Added prefix fallback selection path when slug results are empty for a symbol.
- Extended no-market error with explicit `slug_hits` and `events_hits` diagnostics.
- Added startup logging path `startup_discovery_no_markets` to persist discovery diagnostics when startup cannot resolve a market.
- Added CLI discovery script diagnostics payload output on `ValueError` and `NoActiveMarketError`.

## Acceptance Evidence
- Discovery fallback test: `tests/test_market_discovery_slug_events_fallback.py`.
- Existing latest-active determinism and hard-fail tests remain passing.

Command executed:

```bash
.venv/bin/python -m pytest -q \
  tests/test_market_discovery_slug_events_fallback.py \
  tests/test_resolve_markets_latest_active_mode.py \
  tests/test_run_system_autodiscover_btc_15m_hardfail.py
```

Result: passing.

## Risk Notes
- Fallback to events endpoint increases network calls when slug queries fail; bounded by existing cache TTL.

## Rollback
- Revert `core/market_discovery.py`, `scripts/discover_markets.py`, and `scripts/run_system.py`.
