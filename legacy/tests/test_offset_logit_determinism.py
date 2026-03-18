import json
import unittest

from scripts.train_offset_model import train_from_rows


class TestOffsetLogitDeterminism(unittest.TestCase):
    def test_deterministic_training(self) -> None:
        rows = []
        for i in range(20):
            rows.append(
                {
                    "as_of_ts_ms": i * 1000,
                    "window_end_ts_ms": (i + 1) * 1000,
                    "z_mom": 0.1 * i,
                    "z_rev": -0.05 * i,
                    "ewma_vol": 0.01 + 0.0001 * i,
                    "p_market_exec_buy": 0.4 + 0.001 * i,
                    "label_up": 1 if i % 2 == 0 else 0,
                }
            )
        model_a = train_from_rows(rows, l2_lambda=1.0, max_iter=200, tol=1e-8, seed=0)
        model_b = train_from_rows(rows, l2_lambda=1.0, max_iter=200, tol=1e-8, seed=0)
        self.assertEqual(json.dumps(model_a, sort_keys=True), json.dumps(model_b, sort_keys=True))

    def test_filters_invalid_prefix_before_time_split(self) -> None:
        rows = [
            {
                "as_of_ts_ms": 0,
                "window_end_ts_ms": 1000,
                "z_mom": None,
                "z_rev": None,
                "ewma_vol": None,
                "p_market_exec_buy": 0.5,
                "label_up": 1,
            }
            for _ in range(5)
        ]
        rows.extend(
            {
                "as_of_ts_ms": (i + 10) * 1000,
                "window_end_ts_ms": (i + 11) * 1000,
                "z_mom": 0.1 * i,
                "z_rev": -0.05 * i,
                "ewma_vol": 0.01 + 0.0001 * i,
                "p_market_exec_buy": 0.4 + 0.001 * i,
                "label_up": 1 if i % 2 == 0 else 0,
            }
            for i in range(20)
        )

        model = train_from_rows(rows, l2_lambda=1.0, max_iter=200, tol=1e-8, seed=0)
        self.assertIsNotNone(model["training_time_range"]["start_ms"])


if __name__ == "__main__":
    unittest.main()
