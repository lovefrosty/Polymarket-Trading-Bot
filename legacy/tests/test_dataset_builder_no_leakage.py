import json
import tempfile
import unittest
from pathlib import Path

from core.dataset_builder import build_reference_window_dataset


class TestDatasetBuilderNoLeakage(unittest.TestCase):
    def test_no_leakage_features_and_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reference.jsonl"
            events = [
                _ref_event(0, 100.0),
                _ref_event(61_000, 101.0),
                _ref_event(90_000, 102.0),
                _ref_event(119_000, 103.0),
                _ref_event(120_000, 104.0),
                _ref_event(180_000, 105.0),
            ]
            path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
            rows = build_reference_window_dataset(
                [path],
                symbol="BTC",
                window_secs=60,
                horizon_secs=60,
                lookbacks={"ret_60s": 60},
                ewma_halflife_secs=30,
            )
            target = next(row for row in rows if row["as_of_ts_ms"] == 120_000)
            meta = target["meta"]
            self.assertEqual(meta["n_ref_events_used"], 3)
            self.assertEqual(meta["label_price_t0_ts_ms"], 120_000)
            self.assertEqual(meta["label_price_t1_ts_ms"], 180_000)
            self.assertTrue(meta["label_price_t1_ts_ms"] >= target["window_end_ts_ms"])


def _ref_event(t_event_ms: int, value: float) -> dict:
    return {
        "run_id": "run",
        "channel": "reference",
        "event_type": "reference_update",
        "market": "BTC",
        "asset_id": None,
        "t_event_ms": t_event_ms,
        "t_recv_wall_iso": "2024-01-01T00:00:00.000Z",
        "t_recv_mono_ns": t_event_ms,
        "raw": {"source": "spot", "symbol": "BTC", "value": value, "t_event_ms": t_event_ms},
        "parse_warnings": [],
        "out_of_order": False,
    }


if __name__ == "__main__":
    unittest.main()
