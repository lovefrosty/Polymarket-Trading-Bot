# Polymarket Read-Only Phase

Read-only wiring for market + user websockets, local L2 books, and deterministic event/decision tapes with executable (VWAP) prices. No order placement or signing.

## Run (read-only)

```bash
python3 -m scripts.run_readonly --markets config/markets.yaml
```

Optional trained model (falls back to baseline if missing or mismatched):

```bash
python3 -m scripts.run_readonly --markets config/markets.yaml --model artifacts/model_BTC.json
```

Environment overrides (optional):

```bash
LOG_DIR=./logs USER_WS_ENABLED=false MARKET_WS_ENABLED=true python3 -m scripts.run_readonly
```

## Discover Latest 15m Markets

```bash
python3 -m scripts.discover_markets --symbols BTC ETH
```

You can also auto-resolve missing IDs at runtime:

```bash
python3 -m scripts.run_readonly --markets config/markets.yaml --auto_discover
```

Enable reference feeds (spot polling) for reference events:

```bash
REFERENCE_ENABLED=true python3 -m scripts.run_readonly --auto_discover
```

Override reference source (polling or WS):

```bash
python3 -m scripts.run_readonly --reference_source poll_coinbase
```

Multiple sources (comma-separated) for fallback:

```bash
python3 -m scripts.run_readonly --reference_source ws_kraken,poll_coinbase
```

Reference fallback controls (partial reference confidence):

```bash
REFERENCE_ALLOW_PARTIAL=true
REFERENCE_PARTIAL_CONFIDENCE=0.6
```

Ingest existing reference tapes (offline):

```bash
python3 -m scripts.run_readonly --reference_tape ./logs/reference_20240101.jsonl
```

Collect reference ticks via Kraken WS into EventTape:

```bash
python3 -m scripts.reference_collect --symbols BTC,ETH --venue kraken --log_dir ./logs
```

## On-Chain Ingestion (Optional, Read-Only)

Enable Polygon on-chain ingestion (diagnostics only; no trading). Requires local ABI files:

- `abis/ctf_exchange.json`
- `abis/ctf.json`
- `abis/negrisk_ctf_exchange.json` (optional)

Set RPC endpoints:

```bash
export POLYGON_RPC_HTTP="https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY"
export POLYGON_RPC_WS="wss://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY"
```

Run with on-chain ingestion:

```bash
python3 -m scripts.run_readonly --markets config/markets.yaml --auto_discover --log-dir ./logs --onchain
```

Faster heartbeats and debug logging (optional):

```bash
ONCHAIN_HEARTBEAT_SECS=2.0 ONCHAIN_DEBUG=1 python3 -m scripts.run_readonly --onchain
```

Disable WS subscriptions (fallback to polling):

```bash
ONCHAIN_USE_WS=false python3 -m scripts.run_readonly --onchain
```

Advanced tuning (optional):

```bash
ONCHAIN_POLL_RECONCILE_SECS=30
ONCHAIN_WS_LOOP_SLEEP_SECS=0.2
ONCHAIN_DEDUPE_LRU_SIZE=5000
ONCHAIN_RECREATE_FILTER_AFTER_SECS=30
ONCHAIN_LOG_LEVEL=INFO
```

Why WS + reconciliation beats polling (SOP):

- Polling adds a latency floor (poll interval + RPC delay) and burns cycles when idle.
- WS filters surface events near block inclusion; reconciliation closes gaps on disconnects.
- Explicit IDs (tx_hash/log_index/block) make audits and dedupe deterministic.

Signals are logged in DecisionTape under `notes.onchain_signals` (non-gating):

- `imbalance`: float in [-1, 1]
- `buy_volume`, `sell_volume`, `window_secs`
- `whale_activity`: list of `{whale, direction, size, t_recv_mono_ns}`
- `capital_flow`: `{net_amount, signal, window_secs}`

On-chain events are logged to EventTape as `onchain_*.jsonl` with `source="rpc"`. Heartbeats are emitted when no events arrive for `ONCHAIN_HEARTBEAT_SECS` (default 2s, min 1s).

Configure whale addresses via `config/whales.json` (array of hex addresses).

## Replay from Tape

```bash
python3 -m scripts.replay_runner ./logs
```

Optional trained model for replay:

```bash
python3 -m scripts.replay_runner ./logs --model artifacts/model_BTC.json
```

Replay uses the latest `resolved_markets_*.json` artifact if present; you can also pass `--resolved-markets`.

Resolved markets artifact (canonical):
- `logs/<run_id>/resolved_markets.json` (schema `resolved_markets_v1`)

Replay consumes `market_*.jsonl`, `reference_*.jsonl`, and `onchain_*.jsonl` from a directory for deterministic reproduction.

Generate a walk-forward report after replay:

```bash
python3 -m scripts.replay_runner ./logs --walkforward --walkforward-out ./logs
```

## Audit Report (Offline)

```bash
python3 -m scripts.analyze_audit ./logs
```

This reads DecisionTape JSONL files and writes `audit_report.json` and `audit_report.md` to `LOG_DIR`.

## Repo Audit (Read-only)

```bash
python3 -m scripts.repo_audit --trigger "run audit"
```

The repo audit command runs read-only checks (git state, invariant scans, rollover/tradability checks,
mode-safety checks, replay parity evidence, and quality gate) and emits a structured A-I report with
ranked risks and minimal acceptance-criteria-driven recommendations.

## Replay Certification (Tier-1)

Compare two replay decision streams and fail on any Tier-1 mismatch.

```bash
python3 scripts/replay_certify.py --left ./logs/run_a --right ./logs/run_b
```

Tier-1 compares normalized decision contract fields:
- `decision_id`
- `as_of_ts_ms`
- `action`
- ordered `reasons`
- `outputs`
- `fsm_state` (when present in source record)

## Promotion Report (OBSERVE -> PAPER -> TRADE)

Generate a unified promotion verdict by combining integration health, promotion gates, replay certification,
and soak clocks.

```bash
python3 scripts/promotion_report.py \
  --db-path runtime.db \
  --current-mode PAPER \
  --replay-report ./tmp/replay_certify.json
```

`promotion_report` blocks promotion when any of the following fail:
- integration health
- promotion gates
- replay certification
- soak windows (`OBSERVE >= 48h`, `PAPER >= 48h`)

Soak windows are tracked in `.promotion_mode_history.json` and are reset when runtime fingerprint changes.

## Micro-Cap Live Profile

Pinned defaults for phase-1 constrained live operation are captured in:
- `config/constitution.yaml`
- `config/profiles/micro_cap_live.yaml`

Key values:
- `quote_interval_ms=2000`
- `max_orders_per_min=30`
- `max_daily_loss_usdc=50`
- `cap_gross_usd=200`
- `cap_total_gross_usd=400`
- `max_position_per_side=500`
- `book_stale_after_ms=30000`

## Secret Hygiene (Required)

- Load API credentials and private keys from environment variables only.
- Never log secrets to JSONL tapes, SQLite rows, or decision evidence payloads.
- Never commit secrets into repo files, fixtures, or promotion artifacts.

## Operator Runbook (Phase-1)

1. Run OBSERVE and accumulate at least 48h soak data.
2. Run PAPER and accumulate at least 48h soak data.
3. Run replay certification on comparable tapes (`scripts/replay_certify.py`).
4. Run promotion report (`scripts/promotion_report.py`) and require `status=PROMOTE`.
5. Enable TRADE only after all checks above are green.

## Build Training Datasets (Offline)

```bash
python3 -m scripts.build_datasets --logs ./logs --symbols BTC
```

Outputs JSONL datasets under `./artifacts`:
- `ref_window_BTC.jsonl` (reference-window features + labels)
- `micro_decisions.jsonl` (microstructure features aligned to labels)
- `dataset_manifest.json` (inputs, counts, schema_version)

Additional exports (CSV + Parquet) are written into partitioned directories under `./artifacts`:

- `ref_window_csv/` and `ref_window_parquet/`
- `micro_decisions_csv/` and `micro_decisions_parquet/`

Parquet export requires `pyarrow` (see `requirements.txt`).

## Walk-forward Report (Offline)

```bash
python3 -m scripts.walkforward_report --logs ./logs --out ./logs
```

## Train Ridge-Logistic Baseline (Offline)

```bash
python3 -m scripts.train_model --data artifacts/ref_window_BTC.jsonl --out artifacts/model_BTC.json
```

The model artifact includes `schema_version`, feature order, weights, metrics, and Platt calibration.

## Train Offset-Logit Model (Offline)

```bash
python3 -m scripts.train_offset_model --data artifacts/micro_decisions.jsonl --out artifacts/model_offset.json
```

Load the trained model in read-only or replay:

```bash
python3 -m scripts.run_readonly --model artifacts/model_offset.json
python3 -m scripts.replay_runner ./logs --model artifacts/model_offset.json
```

## Arb Half-Life (Offline)

```bash
python3 -m scripts.arb_half_life \
  --decision ./logs/decision_*.jsonl \
  --reference ./logs/reference_*.jsonl \
  --shock-horizon-sec 10 \
  --shock-quantile-q 0.01 \
  --shock-min-count 5 \
  --output-dir ./logs
```

## Experiment Safety Notes (Offline)

- `feature_asof_ts_ms` is enforced: experiments hard-fail if any feature uses data with `feature_asof_ts_ms >= t_decision_wall_ms` (`feature_from_future`).
- Confidence blocking: if `confidence_final < c_trade_min`, trade size is forced to 0 and `low_confidence` is logged.
- P* disagreement: `diff_bps <= soft` ⇒ no penalty; `soft < diff_bps <= hard` ⇒ exponential confidence decay; `diff_bps > hard` ⇒ freeze (`pstar_disagreement_extreme`).
- Arb half-life shocks are quantile-based and deterministic: threshold is computed from the experiment window with fallback q schedule.

## Scientific Method Backtests (Offline)

Each hypothesis lives under `backtests/scientific_method/Hxxx_*/` with a `spec.json`.

Run an experiment:

```bash
python3 -m scripts.run_experiment --spec backtests/scientific_method/H001_order_imbalance_persistence/spec.json
python3 -m scripts.run_experiment --spec backtests/scientific_method/H002_one_sided_pressure_pre_resolution/spec.json
python3 -m scripts.run_experiment --spec backtests/scientific_method/H003_time_of_day_effects/spec.json
```

Outputs are written to `backtests/scientific_method/Hxxx_*/outputs/` as:
- `results.json`
- `report.md`

To add a new hypothesis, copy `backtests/scientific_method/_template/spec.json` into a new folder and update the fields.

Note: 15m market slugs rotate every 15 minutes; discovery resolves the current contracts.
