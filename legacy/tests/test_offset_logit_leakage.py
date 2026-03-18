import unittest

from scripts.train_offset_model import train_from_rows


class TestOffsetLogitLeakage(unittest.TestCase):
    def test_skips_rows_beyond_window(self) -> None:
        rows = [
            {
                "as_of_ts_ms": 2000,
                "window_end_ts_ms": 1000,
                "z_mom": 0.1,
                "z_rev": -0.1,
                "ewma_vol": 0.01,
                "p_market_exec_buy": 0.5,
                "label_up": 1,
            }
        ]
        with self.assertRaises(ValueError):
            train_from_rows(rows, l2_lambda=1.0, max_iter=10, tol=1e-6, seed=0)


if __name__ == "__main__":
    unittest.main()
