from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backtests.scientific_method.weight_calibration import run_calibration


class TestCalibrationContracts(unittest.TestCase):
    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        payload = "\n".join(json.dumps(row, separators=(",", ":"), sort_keys=True) for row in rows)
        path.write_text(payload, encoding="utf-8")

    def test_contract_enforcement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            decisions = tmp / "decision.jsonl"
            references = tmp / "reference.jsonl"

            self._write_jsonl(
                decisions,
                [
                    {
                        "t_decision_wall_ms": 1500,
                        "feature_asof_ts_ms": 1400,
                        "notes": {"resolved_market": {"reference_symbol": "BTC"}},
                    }
                ],
            )
            self._write_jsonl(
                references,
                [
                    {"channel": "reference", "t_recv_wall_ms": 1000, "raw": {"symbol": "BTC", "value": 100.0}},
                    {"channel": "reference", "t_recv_wall_ms": 2000, "raw": {"symbol": "BTC", "value": 101.0}},
                ],
            )

            feature_contract = {
                "schema_version": "feature_contract_v1",
                "required_coverage": 0.8,
                "features": [
                    {"name": "missing_feature", "path": "notes.signals.missing", "required": True, "missing_policy": "keep"}
                ],
            }
            label_contract = {"schema_version": "label_contract_v1", "required_coverage": 0.0}

            feature_contract_path = tmp / "feature_contract.json"
            label_contract_path = tmp / "label_contract.json"
            feature_contract_path.write_text(json.dumps(feature_contract), encoding="utf-8")
            label_contract_path.write_text(json.dumps(label_contract), encoding="utf-8")

            spec = {
                "name": "Calib_contract",
                "inputs": {"decision_paths": [str(decisions)], "reference_paths": [str(references)]},
                "label": {"mode": "directional", "horizon_sec": 1},
                "features": [
                    {"name": "missing_feature", "path": "notes.signals.missing", "class": "edge", "units": "unitless"}
                ],
                "model": {"type": "ridge_linear", "l2_lambda": 1.0, "max_iter": 50, "tol": 1e-6, "train_frac": 0.5},
                "orthogonalize": {"method": "residualize", "ridge_lambda": 1e-6},
                "standardize": {"method": "median_mad"},
                "stability": {"time_slices": 2, "min_slice_size": 1},
                "require_feature_asof": True,
                "feature_contract_path": str(feature_contract_path),
                "label_contract_path": str(label_contract_path),
            }
            spec_path = tmp / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            out_dir = tmp / "out"
            with self.assertRaises(ValueError) as ctx:
                run_calibration(spec_path, output_dir_override=out_dir)
            self.assertIn("calibration_contract_failed", str(ctx.exception))
            report = out_dir / "calibration_drop_report.json"
            self.assertTrue(report.exists())
