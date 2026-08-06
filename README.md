# Prediction-Market Market-Making Research System

A Python research and operations project for studying automated quoting on Polymarket and Kalshi-style binary markets. It combines live public order-book ingestion, deterministic paper execution, inventory-aware quoting, risk controls, experiment telemetry, and local operator workstations.

This is a portfolio project and research system—not a claim of a profitable trading product. The strongest committed result is from simulated paper execution, not live capital. The historical live-order path was built for Polymarket CLOB V1 and is **not production-compatible** with the CLOB V2 exchange introduced on April 28, 2026.

## What this project demonstrates

- Event-driven ingestion of public CLOB market data into local L2 books.
- Market discovery and rotation for short-horizon BTC, ETH, SOL, and XRP markets.
- Two-sided quote generation with inventory skew, flow filtering, volatility and fill-adversity overlays, and position-aware exits.
- Deterministic paper fills with queue-wait, visible-depth, staleness, fee, markout, turnover, and PnL accounting assumptions.
- Binary complement and multi-outcome NegRisk arbitrage scanners.
- Liquidity-reward scoring and quote-tightening analysis.
- Per-order, per-fill, and per-cycle telemetry written to reproducible run artifacts and SQLite.
- A Streamlit operator dashboard for market state, positions, PnL, execution quality, risk, and reliability.
- A Kalshi-oriented React/Tauri Operator Workstation backed by a local FastAPI control plane for starting paper runs, inspecting decisions and fills, and issuing validated stop/kill commands.
- Kalshi market discovery, fee modeling, execution bridging, rotation reporting, risk harnesses, and hedge-calibration tooling.
- A fail-closed safety design with order, position, and loss limits plus cancel-on-shutdown behavior in the historical live adapter.

## Current status

| Area | Status | Evidence / limitation |
| --- | --- | --- |
| Core implementation | Working locally | `419` tests pass on August 6, 2026; the Operator Workstation production build also succeeds. |
| Public-data observation | Verified August 6, 2026 | A 15-second read-only check discovered the current BTC 15-minute market, received 1,220 WebSocket messages, applied 2,432 book updates, and completed without a runtime error. |
| Paper execution | Implemented | Uses a deterministic simulator, not exchange-confirmed fills. |
| Recorded economics | Promising but insufficient | Best committed run recorded $23.93 realized net PnL and $17.11 total PnL on $1,331.10 turnover with 188 simulated fills. Of the other six committed summaries, four had zero fills and two had only 12 or 23 fills. |
| Kalshi integration | Implemented, not promoted | The venue adapter, dynamic fee model, one-active-market launch profile, risk harness, and Operator Workstation are present. No committed Kalshi run set yet establishes profitability or live readiness. |
| Consistent income | Not established | No statistically adequate out-of-sample record, live fill record, or capital-scaled drawdown study exists. |
| Live Polymarket execution | Blocked | The code pins the retired `py-clob-client==0.20.0`. Production now requires CLOB V2, pUSD collateral, updated signing, and end-to-end revalidation. |

The recorded paper run is useful evidence that the accounting and reporting path works under its assumptions. It is not evidence that the same fills, latency, rebates, adverse selection, or returns would occur live.

## Architecture

```text
Polymarket or Kalshi public APIs / WebSockets
                 |
                 v
  market selector + L2 book manager
                 |
                 v
 quote engine + alpha overlays + risk manager
                 |
          +------+------+
          |             |
       OBSERVE        PAPER
     decisions only   simulated broker
          |             |
          +------+------+
                 v
     JSONL tapes + SQLite telemetry
                 |
                 v
 reports + Streamlit dashboard + Operator Workstation
```

The active implementation is under `core_mm/`. The earlier research platform is preserved under `legacy/` for provenance and reference; it is not the primary runtime.

## Quick start

Python 3.11+ is recommended.

```bash
git clone https://github.com/lovefrosty/Polymarket-Trading-Bot.git
cd Polymarket-Trading-Bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

Observe a current BTC market without submitting or simulating orders:

```bash
python scripts/run_core_mm.py \
  --mode OBSERVE \
  --runtime-root tmp/observe-demo \
  --duration-secs 120 \
  --symbol BTC
```

Run the paper broker against live public book data:

```bash
python scripts/run_core_mm.py \
  --mode PAPER \
  --runtime-root tmp/paper-demo \
  --run-name "Local paper demo" \
  --duration-secs 300 \
  --symbol BTC \
  --trade-size 5 \
  --max-size 25
```

For the current narrow research lane, run one Kalshi BTC market at a time:

```bash
python scripts/run_core_mm.py \
  --exchange kalshi \
  --mode PAPER \
  --runtime-root tmp/core_mm_runs/kalshi-btc-paper \
  --run-name "Kalshi BTC single-market paper" \
  --duration-secs 900 \
  --symbol BTC \
  --max-active-markets 1 \
  --safe-risk-profile 200
```

Build a deterministic summary and open the dashboard:

```bash
python scripts/report_core_mm_run.py --runtime-root tmp/paper-demo
python scripts/run_dashboard.py --db-path tmp/paper-demo/runtime.db
```

The dashboard is then available at `http://localhost:8501` by default.

## Kalshi Operator Workstation

The repository now includes a local operator surface in `operator_app/`. It is
designed around the Kalshi-first, one-active-market research plan: inspect why
a market was selected, see after-fee decisions and fills, follow risk-state
transitions, and operate paper runtimes without treating the UI as permission
to trade live.

Run the local control service and frontend in separate terminals:

```bash
python scripts/run_operator_service.py

cd operator_app
npm ci
npm run dev
```

The service binds to `127.0.0.1:8765` by default and deliberately launches
managed runtimes in `PAPER` mode. The workstation is an operator and reporting
layer; it does not turn the current evidence into a live-trading approval. See
the [owner-understanding checklist](docs/OWNER_UNDERSTANDING_CHECKLIST.md) and
[Kalshi go-live criteria](docs/BTC_KALSHI_GO_LIVE_CRITERIA.md).

## Paper-trading evidence

The strongest committed simulator run made money, but the path was highly
concentrated and ended with open-inventory losses. Maximum drawdown was $6.53,
and 92% of fills plus 95% of realized net PnL came from the first of three BTC
15-minute contracts.

![Historical paper-run equity and drawdown](docs/assets/paper_equity_drawdown.png)

![Paper-run market concentration](docs/assets/paper_market_concentration.png)

The reproducible [paper-trading EDA](docs/analysis/PAPER_TRADING_EDA.md) explains
why neither Sharpe nor risk of ruin can be estimated defensibly from the saved
sample, and documents the next one-market testing gate.

## Paper-model assumptions

The simulator includes explicit approximations, but it does not reproduce a real matching engine. In particular:

- Resting orders must wait at least 200 ms before an assumed fill.
- The model assumes an average queue position behind 50% of displayed depth.
- Fills are capped by the remaining visible depth at the modeled price.
- Books older than five seconds are rejected.
- Maker/taker fees are configured locally and may not match the current per-market fee schedule.
- Network latency, cancel races, hidden liquidity, exchange rejection, settlement, and reward competition are not fully modeled.

Any profitability study should vary these assumptions and report sensitivity, not select a single favorable configuration.

## Safety and live-trading boundary

Do **not** use `--mode LIVE` against production in the current version. The legacy V1 SDK and order-signing path are no longer accepted by Polymarket production. The archived [go-live guide](docs/GO_LIVE_GUIDE.md) records the old implementation context and the migration work still required.

Before live execution could be reconsidered, the project would need to:

1. Follow Polymarket's [official CLOB V2 migration guide](https://docs.polymarket.com/v2-migration), replacing `py-clob-client` with `py-clob-client-v2` and adding pUSD collateral handling.
2. Query current fee schedules per market rather than rely on a static fee assumption.
3. Revalidate discovery, signing, post/cancel, user-feed fills, reconciliation, and settlement end to end.
4. Run a fresh observe period and a conservative paper study with delayed fills and adverse-selection stress tests.
5. Use a separately approved, low-notional canary with a human-operated kill switch.
6. Confirm the operator is legally permitted to trade in their physical jurisdiction.

Never commit a private key, API secret, passphrase, wallet seed, or funded `.env` file.

## Repository map

| Path | Purpose |
| --- | --- |
| `core_mm/` | Active market-making, paper-broker, risk, strategy, and telemetry code. |
| `scripts/run_core_mm.py` | Main OBSERVE/PAPER runtime; contains the historical LIVE wiring. |
| `scripts/report_core_mm_run.py` | Deterministic run/economics summary. |
| `dashboard/` | Streamlit operator and research views. |
| `operator_app/` | React/Tauri Kalshi Operator Workstation. |
| `core_mm/operator_service.py` | Local paper-runtime control and inspection API. |
| `scripts/analyze_paper_runs.py` | Reproducible saved-run EDA and chart generation. |
| `docs/analysis/` | Evidence reports and statistical limitations. |
| `tests/core_mm/` | Focused tests for quoting, execution, risk, telemetry, and strategy modules. |
| `tmp/core_mm_runs/` | Selected committed run evidence; most runtime output is intentionally ignored. |
| `legacy/` | Archived first-generation runtime and research code. |
| `audit/` | Earlier implementation, safety, and milestone records. |

## Contributing

Issues, experiments, and pull requests are welcome. Good contributions make one bounded claim and include the evidence needed to evaluate it.

```bash
git checkout -b feature/short-description
python -m pytest -q
git push -u origin feature/short-description
```

Then open a pull request describing the change, the assumptions it introduces, the tests run, and whether its evidence comes from fixtures, historical data, live public data, paper execution, or live fills. See [CONTRIBUTING.md](CONTRIBUTING.md) for the full checklist.

Useful contribution areas include CLOB V2 migration, paper-model calibration, point-in-time datasets, fee/rebate accounting, latency and queue-position sensitivity, reproducible evaluation, and dashboard reliability.

## Public-release checklist

This repository is currently private. Before using it as a public portfolio link:

- Choose and add an explicit open-source license. Without one, GitHub visitors may read the code but do not receive permission to reuse it.
- Review the full Git history for secrets and remove any sensitive material safely.
- PR #1 is closed as stale. PR #2 is superseded by a curated integration that excludes its unrelated legacy-tooling commit.
- Keep the CLOB V2/live-execution blocker prominent until it is actually resolved and independently verified.

## Disclaimer

This software is for research and educational use. Prediction-market trading can lose the full amount at risk, simulated performance can differ materially from live performance, and access depends on jurisdiction and platform rules. Nothing in this repository is financial, legal, or investment advice.
