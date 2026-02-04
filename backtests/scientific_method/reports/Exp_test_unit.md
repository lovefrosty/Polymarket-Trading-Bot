PASS: Exp_test_unit Exp_test

Summary
- pnl: 0.0
- trades: 0
- test_metrics: {"accuracy": 1.0, "brier": 1.0000000000575112e-12, "logloss": 1.000000500029089e-06}

Invariants
- feature_time_violations: 0
- feature_from_future: 0
- execution_not_vwap: 0
- fee_double_count: 0
- low_confidence_blocks: 1
- pstar_disagreement_extreme: 0

Feature As-Of Sources
- hybrid: 1

Confidence Buckets
- <0.3: 1

Trade Rate by Regime
- 1: 0.0

Depth Telemetry
- imbalance_corr: -1.0
- depth_within_ticks_bid: {'count': 2, 'min': 22.0, 'p50': 22.0, 'p90': 25.0, 'p95': 25.0}
- depth_within_ticks_ask: {'count': 2, 'min': 18.0, 'p50': 18.0, 'p90': 20.0, 'p95': 20.0}
- depth_at_notional_bid: {'count': 2, 'min': 10.0, 'p50': 10.0, 'p90': 10.0, 'p95': 10.0}
- depth_at_notional_ask: {'count': 2, 'min': 10.0, 'p50': 10.0, 'p90': 10.0, 'p95': 10.0}

P* Disagreement
- diff_bps: {'count': 2, 'min': 20.0, 'p50': 20.0, 'p90': 20.0, 'p95': 20.0}
- alpha_basis: {'count': 2, 'min': 0.6065306597126334, 'p50': 0.6065306597126334, 'p90': 0.6065306597126334, 'p95': 0.6065306597126334}
- extreme_count: 0