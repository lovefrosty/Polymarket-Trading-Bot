from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backtests.scientific_method.weight_calibration import run_calibration


class TestWeightCalibration(unittest.TestCase):
    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        payload = "\n".join(json.dumps(row, separators=(",", ":"), sort_keys=True) for row in rows)
        path.write_text(payload, encoding="utf-8")

    def test_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            decisions = tmp / "decision.jsonl"
            references = tmp / "reference.jsonl"

            decision_rows = [
                {
                    "t_decision_wall_ms": 1500,
                    "feature_asof_ts_ms": 1400,
                    "notes": {"resolved_market": {"reference_symbol": "BTC"}, "signals": {"z_mom": 1.0}},
                    "book": {"spread_bps": 12.0},
                    "p_fair": 0.6,
                    "p_market_exec_buy": 0.55,
                },
                {
                    "t_decision_wall_ms": 2500,
                    "feature_asof_ts_ms": 2400,
                    "notes": {"resolved_market": {"reference_symbol": "BTC"}, "signals": {"z_mom": -0.5}},
                    "book": {"spread_bps": 18.0},
                    "p_fair": 0.52,
                    "p_market_exec_buy": 0.51,
                },
            ]
            reference_rows = [
                {
                    "channel": "reference",
                    "t_recv_wall_ms": 1000,
                    "raw": {"symbol": "BTC", "value": 100.0},
                },
                {
                    "channel": "reference",
                    "t_recv_wall_ms": 2000,
                    "raw": {"symbol": "BTC", "value": 101.0},
                },
                {
                    "channel": "reference",
                    "t_recv_wall_ms": 3000,
                    "raw": {"symbol": "BTC", "value": 102.0},
                },
                {
                    "channel": "reference",
                    "t_recv_wall_ms": 4000,
                    "raw": {"symbol": "BTC", "value": 101.0},
                },
            ]
            self._write_jsonl(decisions, decision_rows)
            self._write_jsonl(references, reference_rows)

            spec = {
                "name": "Calib_unit",
                "inputs": {"decision_paths": [str(decisions)], "reference_paths": [str(references)]},
                "label": {"mode": "directional", "horizon_sec": 1},
                "features": [
                    {
                        "name": "edge_bps_buy",
                        "path": "calc.edge_bps_buy",
                        "class": "edge",
                        "units": "bps",
                        "missing": "drop",
                    },
                    {
                        "name": "z_mom",
                        "path": "notes.signals.z_mom",
                        "class": "price_momentum",
                        "units": "sigma",
                        "missing": "drop",
                    },
                    {
                        "name": "spread_bps",
                        "path": "book.spread_bps",
                        "class": "liquidity",
                        "units": "bps",
                        "missing": "drop",
                    },
                ],
                "model": {"type": "ridge_logistic", "l2_lambda": 1.0, "max_iter": 100, "tol": 1e-6, "seed": 0, "train_frac": 0.5},
                "orthogonalize": {"method": "residualize", "ridge_lambda": 1e-6},
                "standardize": {"method": "median_mad"},
                "stability": {"time_slices": 2, "min_slice_size": 1},
                "require_feature_asof": True,
                "random_seed": 0,
            }
            spec_path = tmp / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            output_dir = tmp / "out"
            first = run_calibration(spec_path, output_dir_override=output_dir)
            second = run_calibration(spec_path, output_dir_override=output_dir)

            weights_path = Path(first["weights_path"])
            self.assertTrue(weights_path.exists())
            self.assertEqual(weights_path.read_text(), Path(second["weights_path"]).read_text())

            report_path = Path(first["report_path"])
            self.assertTrue(report_path.exists())
            first_line = report_path.read_text().splitlines()[0]
            self.assertTrue(first_line.startswith("PASS") or first_line.startswith("FAIL"))

    def test_leakage_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            decisions = tmp / "decision.jsonl"
            references = tmp / "reference.jsonl"

            decision_rows = [
                {
                    "t_decision_wall_ms": 1500,
                    "feature_asof_ts_ms": 1600,
                    "notes": {"resolved_market": {"reference_symbol": "BTC"}, "signals": {"z_mom": 1.0}},
                    "book": {"spread_bps": 12.0},
                    "p_fair": 0.6,
                    "p_market_exec_buy": 0.55,
                }
            ]
            reference_rows = [
                {
                    "channel": "reference",
                    "t_recv_wall_ms": 1000,
                    "raw": {"symbol": "BTC", "value": 100.0},
                },
                {
                    "channel": "reference",
                    "t_recv_wall_ms": 2000,
                    "raw": {"symbol": "BTC", "value": 101.0},
                },
                {
                    "channel": "reference",
                    "t_recv_wall_ms": 3000,
                    "raw": {"symbol": "BTC", "value": 102.0},
                },
            ]
            self._write_jsonl(decisions, decision_rows)
            self._write_jsonl(references, reference_rows)

            spec = {
                "name": "Calib_leak",
                "inputs": {"decision_paths": [str(decisions)], "reference_paths": [str(references)]},
                "label": {"mode": "directional", "horizon_sec": 1},
                "features": [
                    {
                        "name": "z_mom",
                        "path": "notes.signals.z_mom",
                        "class": "price_momentum",
                        "units": "sigma",
                        "missing": "drop",
                    }
                ],
                "model": {"type": "ridge_logistic", "l2_lambda": 1.0, "max_iter": 50, "tol": 1e-6, "seed": 0, "train_frac": 0.5},
                "orthogonalize": {"method": "residualize", "ridge_lambda": 1e-6},
                "standardize": {"method": "median_mad"},
                "stability": {"time_slices": 2, "min_slice_size": 1},
                "require_feature_asof": True,
                "random_seed": 0,
            }
            spec_path = tmp / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                run_calibration(spec_path, output_dir_override=tmp / "out")
            self.assertIn("feature_from_future", str(ctx.exception))
