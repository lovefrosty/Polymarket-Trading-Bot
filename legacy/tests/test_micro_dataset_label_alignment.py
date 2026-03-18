import unittest
from pathlib import Path

from core.dataset_builder import build_microstructure_dataset_from_decisions, build_reference_window_dataset


class TestMicroDatasetLabelAlignment(unittest.TestCase):
    def test_labels_align_to_reference_window(self) -> None:
        fixture_dir = Path(__file__).parent / "fixtures"
        reference_path = fixture_dir / "reference_tape_sample.jsonl"
        decision_path = fixture_dir / "decision_tape_sample.jsonl"

        reference_rows = build_reference_window_dataset([reference_path], symbol="BTC")
        self.assertTrue(reference_rows)

        label_index = {
            (row["symbol"], int(row["window_start_ts_ms"])): int(row["label_up"])
            for row in reference_rows
        }
        micro_rows = build_microstructure_dataset_from_decisions([decision_path], label_index)
        self.assertTrue(micro_rows)

        for row in micro_rows:
            key = (row["symbol"], int(row["window_start_ts_ms"]))
            self.assertIn(key, label_index)
            self.assertEqual(row["label_up"], label_index[key])


if __name__ == "__main__":
    unittest.main()
