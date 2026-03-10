from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backtests.scientific_method.engine import _load_reference_events
from scripts.adapt_reference_tapes import adapt_reference_file, adapt_reference_paths


class TestReferenceTapeAdapter(unittest.TestCase):
    def test_adapts_spot_and_perp_ticks_into_enriched_reference_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "reference_raw.jsonl"
            output_dir = Path(tmpdir) / "out"
            rows = [
                {
                    "run_id": "run",
                    "channel": "reference",
                    "event_type": "reference_tick",
                    "market": "BTC",
                    "t_event_ms": None,
                    "t_recv_wall_ms": 1700000000000,
                    "t_recv_mono_ns": 1,
                    "raw": {"symbol": "BTC", "source": "spot", "value": 100.0},
                },
                {
                    "run_id": "run",
                    "channel": "reference",
                    "event_type": "reference_tick",
                    "market": "BTC",
                    "t_event_ms": None,
                    "t_recv_wall_ms": 1700000000200,
                    "t_recv_mono_ns": 2,
                    "raw": {"symbol": "BTC", "source": "binance_perp", "value": 100.2},
                },
            ]
            input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            manifest = adapt_reference_paths([input_path], output_dir)
            self.assertEqual(manifest["compatible_count"], 1)

            output_path = output_dir / "reference_enriched_raw.jsonl"
            self.assertTrue(output_path.exists())

            adapted_rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(len(adapted_rows), 1)
            raw = adapted_rows[0]["raw"]
            self.assertEqual(raw["sources"], ["spot", "perp"])
            self.assertAlmostEqual(raw["spot_value"], 100.0)
            self.assertAlmostEqual(raw["perp_value"], 100.2)
            self.assertGreater(raw["diff_bps"], 0.0)

            events = _load_reference_events([str(output_path)])
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["symbol"], "BTC")

    def test_marks_spot_only_file_incompatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "reference_spot_only.jsonl"
            input_path.write_text(
                json.dumps(
                    {
                        "run_id": "run",
                        "channel": "reference",
                        "event_type": "reference_tick",
                        "market": "BTC",
                        "t_event_ms": None,
                        "t_recv_wall_ms": 1700000000000,
                        "t_recv_mono_ns": 1,
                        "raw": {"symbol": "BTC", "source": "spot", "value": 100.0},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            rows, stats = adapt_reference_file(input_path)
            self.assertEqual(rows, [])
            self.assertFalse(stats["compatible"])
            self.assertEqual(stats["skip_reason"], "missing_spot_or_perp_pair")


if __name__ == "__main__":
    unittest.main()
