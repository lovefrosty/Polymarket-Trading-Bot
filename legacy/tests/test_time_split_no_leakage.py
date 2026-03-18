import unittest

from scripts.train_model import _time_split


class TestTimeSplitNoLeakage(unittest.TestCase):
    def test_time_split_order(self) -> None:
        rows = [
            {
                "as_of_ts_ms": i * 60_000,
                "features": {"ret_60s": 0.0, "ret_300s": 0.0, "ret_900s": 0.0, "ewma_vol_300s": 0.01, "z_mom": 0.0, "z_rev": 0.0},
                "label_up": 1,
            }
            for i in range(10)
        ]
        train_rows, val_rows = _time_split(rows, train_frac=0.7)
        self.assertTrue(train_rows)
        self.assertTrue(val_rows)
        self.assertLessEqual(train_rows[-1]["as_of_ts_ms"], val_rows[0]["as_of_ts_ms"])


if __name__ == "__main__":
    unittest.main()
