# Portfolio Management Agents

This repo is a personal trading and portfolio-management system: Polymarket/Kalshi market making today, broker-aware portfolio analytics and guarded algorithmic rebalancing next.

## Stack And Commands

- Python 3.10+ project with `pytest`, `streamlit`, `pandas`, `httpx`, `websockets`, `fastapi`, and `py-clob-client`.
- Install: `pip install -r requirements.txt`
- Core tests: `python3 -m pytest tests/core_mm -q`
- Dashboard tests: `python3 -m pytest tests/dashboard -q`
- Run bot paper mode: `python3 scripts/run_core_mm.py --exchange kalshi --mode PAPER --runtime-root tmp/runs/kalshi-paper`
- Run dashboard: `python3 scripts/run_dashboard.py`

## Project Structure

- `core_mm/`: prediction-market execution, quoting, risk, broker adapters, telemetry.
- `core_mm/kalshi/`: Kalshi-specific client, market feed, fees, execution bridge.
- `dashboard/`: Streamlit operator console and panels.
- `scripts/`: runnable entry points, reports, soak/proof harnesses.
- `tests/`: focused pytest coverage for core, dashboard, and scripts.
- `docs/`: strategy, live-readiness, operator, and roadmap notes.
- `tmp/`, `secrets/`, `.env`: local-only runtime and credential surfaces.

## Coding Rules

- Work on one small task at a time.
- Prefer minimal diffs over refactors.
- Keep strategy, broker, dashboard, and research-sidecar logic in separate lanes.
- Add types/dataclasses for contracts that cross module boundaries.
- Log decisions with enough metadata to replay or audit them later.
- Any trading signal must include timestamp, source freshness, horizon, confidence, and expiration.
- Backtests must model fees, spread/slippage, delayed fills, and causality.

## Boundaries

- Never touch `.git/`, `secrets/`, `.env`, `*.pem`, runtime databases, raw tapes, or `tmp/core_mm_runs/` unless explicitly asked.
- Never add dependencies, widen live limits, or enable live order routing without explicit approval.
- Never let an agent, LLM, or research sidecar directly place trades.
- Never merge broker-listed assets and prediction markets into one strategy module.
- Never use future data, centered windows, survivor-only universes, or whole-sample normalization in research code.

## Agent Workflow

- If a task touches multiple files or lanes, propose the smallest plan first.
- Edit only files named by the user or required by the approved plan.
- Summarize exact files before editing and exact files after editing.
- Run the narrowest relevant tests after code changes.
- If new recurring guidance emerges, propose an `AGENTS.md` update instead of relying on chat memory.

## Promotion Gates

- Research idea -> written hypothesis -> clean data contract -> walk-forward backtest -> costs/slippage -> multiple-testing adjustment -> paper trading -> human review -> small live allocation.
- Use Deflated Sharpe Ratio or an equivalent multiple-testing control whenever parameter searches or many hypotheses are tried.
- Live capital is managed by human approval and hard risk limits, not by model confidence.
