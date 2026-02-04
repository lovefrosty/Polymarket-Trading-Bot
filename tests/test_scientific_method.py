from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "scientific_method"


class TestScientificMethod(unittest.TestCase):
    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m"] + args,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_determinism_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            spec_path = Path(tmpdir) / "spec.json"
            spec = {
                "name": "Exp_test",
                "hypothesis_id": "Exp_test_unit",
                "description": "determinism test",
                "inputs": {
                    "decision_paths": [str(FIXTURES / "decision_tape_sample.jsonl")],
                    "reference_paths": [str(FIXTURES / "reference_tape_sample.jsonl")],
                },
                "market_selection": {"condition_ids": [], "token_ids": []},
                "sampling": {"start_ms": None, "end_ms": None, "cadence_ms": 1000},
                "features": {
                    "reference_returns_sec": [60]
                },
                "label": {"type": "window", "window_secs": 900},
                "model": {
                    "type": "logistic",
                    "l2_lambda": 1.0,
                    "max_iter": 100,
                    "tol": 1e-6,
                    "seed": 0,
                    "train_frac": 0.7
                },
                "regime": {
                    "enabled": True,
                    "hmm_path": "models/regimes/hmm_reference.json",
                    "obs_return_sec": 300,
                    "vol_half_life_sec": 1800
                },
                "execution": {"fee_bps": 25, "qty_for_features": 1.0},
                "sizing": {"kelly_fraction_max": 0.25, "initial_equity": 1000.0},
                "constraints": {"portfolio_path": "config/portfolio.yaml"},
                "pstar_diff_bps_soft": 10.0,
                "pstar_diff_bps_hard": 50.0,
                "pstar_diff_bps_decay_k": 2.0,
                "c_trade_min": 0.3,
                "depth_within_ticks_n": 5,
                "depth_at_notional_target": 10.0,
                "shock_horizon_sec": 10,
                "shock_quantile_q": 0.01,
                "shock_min_count": 5,
                "acceptance": {},
                "random_seed": 0,
            }
            spec_path.write_text(json.dumps(spec))

            results_path = ROOT / "backtests" / "scientific_method" / "results" / "Exp_test_unit.json"
            report_path = ROOT / "backtests" / "scientific_method" / "reports" / "Exp_test_unit.md"
            if results_path.exists():
                results_path.unlink()
            if report_path.exists():
                report_path.unlink()

            first = self._run(["scripts.run_experiment", "--spec", str(spec_path)])
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            self.assertTrue(results_path.exists())
            self.assertTrue(report_path.exists())

            first_contents = results_path.read_text()
            results = json.loads(first_contents)
            depth_stats = results.get("depth_telemetry") or {}
            self.assertIn("depth_within_ticks_bid", depth_stats)
            self.assertGreater(depth_stats.get("depth_within_ticks_bid", {}).get("count", 0), 0)
            second = self._run(["scripts.run_experiment", "--spec", str(spec_path)])
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            second_contents = results_path.read_text()
            self.assertEqual(first_contents, second_contents)

            first_line = report_path.read_text().splitlines()[0]
            self.assertTrue(first_line.startswith("PASS") or first_line.startswith("FAIL"))

    def test_leakage_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            spec_path = Path(tmpdir) / "spec.json"
            spec = {
                "name": "Leak test",
                "hypothesis_id": "Leak_test",
                "description": "leakage guard test",
                "inputs": {
                    "decision_paths": [str(FIXTURES / "decision_tape_leak.jsonl")],
                    "reference_paths": [str(FIXTURES / "reference_tape_sample.jsonl")],
                },
                "market_selection": {"condition_ids": [], "token_ids": []},
                "sampling": {"start_ms": None, "end_ms": None, "cadence_ms": 1000},
                "features": {"reference_returns_sec": [60]},
                "label": {"type": "window", "window_secs": 900},
                "model": {"type": "logistic"},
                "regime": {
                    "enabled": True,
                    "hmm_path": "models/regimes/hmm_reference.json",
                    "obs_return_sec": 300,
                    "vol_half_life_sec": 1800
                },
                "execution": {"fee_bps": 25, "qty_for_features": 1.0},
                "sizing": {"kelly_fraction_max": 0.25, "initial_equity": 1000.0},
                "constraints": {"portfolio_path": "config/portfolio.yaml"},
                "pstar_diff_bps_soft": 10.0,
                "pstar_diff_bps_hard": 50.0,
                "pstar_diff_bps_decay_k": 2.0,
                "c_trade_min": 0.3,
                "depth_within_ticks_n": 5,
                "depth_at_notional_target": 10.0,
                "shock_horizon_sec": 10,
                "shock_quantile_q": 0.01,
                "shock_min_count": 5,
                "acceptance": {},
                "random_seed": 0,
            }
            spec_path.write_text(json.dumps(spec))

            result = self._run(["scripts.run_experiment", "--spec", str(spec_path)])
            self.assertNotEqual(result.returncode, 0)
