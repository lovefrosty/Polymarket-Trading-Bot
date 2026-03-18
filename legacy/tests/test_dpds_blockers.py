import unittest

from core.dpds import DpdsEstimator


class TestDpdsBlockers(unittest.TestCase):
    def test_insufficient_history(self) -> None:
        estimator = DpdsEstimator(min_samples=3)
        result = estimator.estimate_dpds("asset", p_market=0.5, spot_mid=100.0, now_ts_ms=0)
        self.assertIn("INSUFFICIENT_HISTORY", result.blockers)

    def test_var_too_small(self) -> None:
        estimator = DpdsEstimator(min_samples=1, var_floor=1e-6)
        estimator.estimate_dpds("asset", p_market=0.5, spot_mid=100.0, now_ts_ms=0)
        result = estimator.estimate_dpds("asset", p_market=0.5, spot_mid=100.0, now_ts_ms=1000)
        self.assertIn("VAR_TOO_SMALL", result.blockers)


if __name__ == "__main__":
    unittest.main()
