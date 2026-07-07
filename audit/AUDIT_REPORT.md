# AUDIT_REPORT

## Executive Summary
FAIL — runtime probe did not execute the pipeline (module import failure), contract validator failed, and security scan found multiple pattern hits. Kill‑or‑Justify verdict: **FAIL** until these are resolved.

## The Fat (Unused Code)
Orphaned files (not referenced by any other module per static scan):
- data/gamma_client.py
- src/features/orthogonalization.py
- src/model/fair_probability.py
- src/replay/replay_harness.py
- src/data/gamma_api.py

## The Safety Check
Security scan hits (pattern‑based, requires manual triage):
- mnemonic in core/model_fit.py
- mnemonic in core/model_fit_offset.py
- private_key in logs/cache_gamma_markets.json
- mnemonic in scripts/train_model.py
- mnemonic in scripts/train_offset_model.py
- mnemonic in .venv/lib/python3.13/site-packages/pip/_vendor/pygments/lexers/python.py
- private_key in logs/0ac0b46d746b409489a3b35bd5e1ae52/resolved_markets.json
- private_key in logs/1748c0ed0715452c918b0fb902d0d8a2/resolved_markets.json
- private_key in logs/211e65a2a4334b22861b99c8effaff87/resolved_markets.json
- private_key in logs/live/cache_gamma_markets.json
- private_key in logs/4e66383863f04d9e8b807d5a576eb186/resolved_markets.json
- private_key in logs/74cf3e5860ea4cfc9feed2ae487e7de2/resolved_markets.json
- private_key in logs/204f9550159d44c6af39a150e5d71ed7/resolved_markets.json
- mnemonic in backtests/scientific_method/engine.py

Forbidden activity (runtime probe):
- none detected

Contract validation:
- FAIL — Missing book_stale (x2), No discovery_summary found

## Performance
Runtime probe recorded no HTTP/WS activity. This is consistent with the module import failure (`scripts` not importable), so latency statistics are empty and not representative of runtime behavior.

## Trim Plan (Ranked by ROI)
1. PR1 — Remove orphaned modules listed above (or explicitly wire them in if needed). This is the largest, safest deletion set.
2. PR2 — Remove or quarantine legacy Gamma clients (`data/gamma_client.py`, `src/data/gamma_api.py`) if CLOB‑first discovery is now authoritative.
3. PR3 — Enforce a single runtime entry point importable by `audit_runtime_probe.py` (add `scripts/__init__.py` or move to `main.py`) so runtime auditing can actually execute the pipeline.
