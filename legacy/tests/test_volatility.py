import math
import unittest

from core.volatility import ewma_variance_update, percentile_rank


class TestVolatility(unittest.TestCase):
    def test_ewma_variance_update(self) -> None:
        prev = 0.0
        r = 0.1
        dt_sec = 1.0
        half_life = 2.0
        alpha = 1.0 - math.exp(-math.log(2.0) * dt_sec / half_life)
        expected = (1.0 - alpha) * prev + alpha * (r * r)
        got = ewma_variance_update(prev, r, dt_sec, half_life)
        self.assertAlmostEqual(got, expected, places=12)

    def test_percentile_rank(self) -> None:
        history = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(percentile_rank(3.0, history), 0.75)
        self.assertAlmostEqual(percentile_rank(0.5, history), 0.0)
        self.assertAlmostEqual(percentile_rank(4.0, history), 1.0)


if __name__ == "__main__":
    unittest.main()
