import json
import unittest

from scripts.train_model import train_from_rows


class TestTrainDeterminism(unittest.TestCase):
    def test_train_deterministic(self) -> None:
        rows = []
        for i in range(20):
            vol = 0.01 + 0.0001 * i
            ret_60s = 0.001 * i
            ret_300s = 0.002 * i
            ret_900s = 0.003 * i
            z_mom = ret_60s / vol
            z_rev = -ret_300s / vol
            rows.append(
                {
                    "as_of_ts_ms": i * 60_000,
                    "features": {
                        "ret_60s": ret_60s,
                        "ret_300s": ret_300s,
                        "ret_900s": ret_900s,
                        "ewma_vol_300s": vol,
                        "z_mom": z_mom,
                        "z_rev": z_rev,
                    },
                    "label_up": 1 if i % 2 == 0 else 0,
                }
            )

        model_a = train_from_rows(rows, l2_lambda=1.0, max_iter=200, tol=1e-8, seed=0)
        model_b = train_from_rows(rows, l2_lambda=1.0, max_iter=200, tol=1e-8, seed=0)

        self.assertEqual(
            json.dumps(model_a, sort_keys=True),
            json.dumps(model_b, sort_keys=True),
        )


if __name__ == "__main__":
    unittest.main()
