# Scientific Method Backtests

Workflow: hypothesis → run → report

1) Pick or create an experiment spec in `backtests/scientific_method/experiments/` (DecisionTape + ReferenceTape only).
2) Run the experiment:

```bash
python3 -m scripts.run_experiment --spec backtests/scientific_method/experiments/Exp01_reference_only.json
```

3) Outputs:
- Results JSON: `backtests/scientific_method/results/<experiment_id>.json`
- Decision log JSONL: `backtests/scientific_method/results/<experiment_id>_decisions.jsonl`
- Report: `backtests/scientific_method/reports/<experiment_id>.md`

To add a new hypothesis, copy an existing spec, update its metadata and feature flags, then re-run.

## Weight Calibration (Offline, Deterministic)

This produces defensible initial feature weights without mutating runtime logic.

```bash
python3 -m scripts.calibrate_weights --spec backtests/scientific_method/experiments/Calib_weights.json
```

Outputs:
- Weights JSON: `backtests/scientific_method/calibration/calibrated_weights.json`
- Summary CSV: `backtests/scientific_method/calibration/calibration_summary.csv`
- Report: `backtests/scientific_method/calibration/calibration_report.md`
