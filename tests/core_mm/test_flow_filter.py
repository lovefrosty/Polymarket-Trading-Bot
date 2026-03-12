import math
import unittest

from core_mm.flow_filter import evaluate_volume_ratio


class TestFlowFilter(unittest.TestCase):
    def test_ratio_below_threshold_suppresses_buys(self) -> None:
        decision = evaluate_volume_ratio(50, 100)
        self.assertAlmostEqual(decision.volume_ratio, 0.5)
        self.assertFalse(decision.allow_buy)
        self.assertTrue(decision.allow_sell)

    def test_ratio_in_band_quotes_both_sides(self) -> None:
        decision = evaluate_volume_ratio(100, 100)
        self.assertAlmostEqual(decision.volume_ratio, 1.0)
        self.assertTrue(decision.allow_buy)
        self.assertTrue(decision.allow_sell)

    def test_ratio_above_threshold_suppresses_sells(self) -> None:
        decision = evaluate_volume_ratio(200, 100)
        self.assertAlmostEqual(decision.volume_ratio, 2.0)
        self.assertTrue(decision.allow_buy)
        self.assertFalse(decision.allow_sell)

    def test_zero_ask_volume_is_buy_pressure_only(self) -> None:
        decision = evaluate_volume_ratio(100, 0)
        self.assertTrue(math.isinf(decision.volume_ratio))
        self.assertTrue(decision.allow_buy)
        self.assertFalse(decision.allow_sell)


if __name__ == "__main__":
    unittest.main()
