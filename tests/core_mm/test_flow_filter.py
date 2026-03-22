import math
import unittest

from core_mm.flow_filter import FlowFilter, evaluate_volume_ratio


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

    # ── New tests ─────────────────────────────────────────────────────────────

    def test_imbalance_bps_positive_when_bid_heavy(self) -> None:
        # 150 bid, 100 ask → (150-100)/250 * 10000 = 2000 bps
        decision = evaluate_volume_ratio(150, 100)
        self.assertAlmostEqual(decision.imbalance_bps, 2000.0)

    def test_imbalance_bps_negative_when_ask_heavy(self) -> None:
        # 50 bid, 100 ask → (50-100)/150 * 10000 ≈ -3333 bps
        decision = evaluate_volume_ratio(50, 100)
        self.assertAlmostEqual(decision.imbalance_bps, (50 - 100) / 150 * 10_000, places=1)

    def test_imbalance_bps_zero_when_balanced(self) -> None:
        decision = evaluate_volume_ratio(100, 100)
        self.assertAlmostEqual(decision.imbalance_bps, 0.0)

    def test_symmetric_threshold_exact_boundary(self) -> None:
        # With threshold=0.5, upper bound = 1/0.5 = 2.0
        # ratio exactly at lower bound: suppress buys
        d_low = evaluate_volume_ratio(40, 100, imbalance_threshold=0.5)  # ratio=0.4 < 0.5
        self.assertFalse(d_low.allow_buy)
        self.assertTrue(d_low.allow_sell)
        # ratio exactly at upper bound: suppress sells
        d_high = evaluate_volume_ratio(250, 100, imbalance_threshold=0.5)  # ratio=2.5 > 2.0
        self.assertTrue(d_high.allow_buy)
        self.assertFalse(d_high.allow_sell)

    def test_upper_bound_is_reciprocal_of_lower_bound(self) -> None:
        # threshold=0.8 → upper=1/0.8=1.25
        d_in = evaluate_volume_ratio(110, 100, imbalance_threshold=0.8)  # ratio=1.1 < 1.25
        self.assertTrue(d_in.allow_buy)
        self.assertTrue(d_in.allow_sell)
        d_out = evaluate_volume_ratio(130, 100, imbalance_threshold=0.8)  # ratio=1.3 > 1.25
        self.assertTrue(d_out.allow_buy)
        self.assertFalse(d_out.allow_sell)

    def test_zero_both_volumes(self) -> None:
        decision = evaluate_volume_ratio(0, 0)
        self.assertFalse(decision.allow_buy)
        self.assertFalse(decision.allow_sell)
        self.assertAlmostEqual(decision.imbalance_bps, 0.0)

    def test_zero_bid_volume_is_sell_pressure_only(self) -> None:
        decision = evaluate_volume_ratio(0, 100)
        self.assertFalse(decision.allow_buy)
        self.assertTrue(decision.allow_sell)
        self.assertAlmostEqual(decision.imbalance_bps, -10_000.0)

    def test_stateless_evaluate_returns_ewma_zero(self) -> None:
        # evaluate_volume_ratio is stateless → ewma_imbalance_bps defaults to 0.0
        decision = evaluate_volume_ratio(100, 100)
        self.assertAlmostEqual(decision.ewma_imbalance_bps, 0.0)


class TestFlowFilterEWMA(unittest.TestCase):
    def test_one_extreme_after_balanced_doesnt_suppress(self) -> None:
        # 20 balanced updates build up EWMA near 0; 1 extreme should barely move it
        ff = FlowFilter(imbalance_threshold=0.7, ewma_span=10)
        for _ in range(20):
            ff.update(100, 100)
        # One extreme: bid=20, ask=100 → ratio=0.2, well below 0.7
        dec = ff.update(20, 100)
        # EWMA should still be close to 0 → not suppressed
        self.assertTrue(dec.allow_buy)

    def test_consecutive_extreme_updates_suppresses_eventually(self) -> None:
        # 15 consecutive extreme updates should push EWMA below threshold
        ff = FlowFilter(imbalance_threshold=0.7, ewma_span=10)
        for _ in range(15):
            dec = ff.update(20, 100)
        self.assertFalse(dec.allow_buy)

    def test_first_call_initialises_to_raw_value(self) -> None:
        ff = FlowFilter(imbalance_threshold=0.7, ewma_span=10)
        # First call: balanced (imbalance_bps=0)
        dec = ff.update(100, 100)
        self.assertAlmostEqual(dec.ewma_imbalance_bps, 0.0)

    def test_ewma_imbalance_bps_field_present(self) -> None:
        ff = FlowFilter(imbalance_threshold=0.7, ewma_span=10)
        dec = ff.update(150, 100)
        # ewma_imbalance_bps should be a float
        self.assertIsInstance(dec.ewma_imbalance_bps, float)

    def test_no_depth_uses_raw_result(self) -> None:
        # Build up positive EWMA, then pass zero depth → raw "no_near_mid_depth" should win
        ff = FlowFilter(imbalance_threshold=0.7, ewma_span=10)
        for _ in range(5):
            ff.update(200, 100)  # positive EWMA
        dec = ff.update(0, 0)
        self.assertFalse(dec.allow_buy)
        self.assertFalse(dec.allow_sell)
        self.assertEqual(dec.reason, "no_near_mid_depth")


class TestFlowFilterEmergency(unittest.TestCase):
    def test_cooldown_decrements_each_update(self) -> None:
        ff = FlowFilter(ewma_span=10)
        ff._emergency_cooldown = 3
        ff.update(100, 100)
        self.assertEqual(ff._emergency_cooldown, 2)
        ff.update(100, 100)
        self.assertEqual(ff._emergency_cooldown, 1)
        ff.update(100, 100)
        self.assertEqual(ff._emergency_cooldown, 0)
        self.assertFalse(ff.in_emergency_cooldown)

    def test_no_reversal_no_emergency(self) -> None:
        ff = FlowFilter(ewma_span=10)
        for _ in range(10):
            ff.update(100, 100)
        self.assertFalse(ff.check_reversal(threshold_bps=2000.0))
        self.assertFalse(ff.in_emergency_cooldown)

    def test_check_reversal_returns_false_on_first_call(self) -> None:
        ff = FlowFilter(ewma_span=10)
        ff.update(100, 100)
        # No previous EWMA stored yet (first update sets it to None)
        self.assertFalse(ff.check_reversal())

    def test_in_emergency_cooldown_property(self) -> None:
        ff = FlowFilter(ewma_span=10)
        self.assertFalse(ff.in_emergency_cooldown)
        ff._emergency_cooldown = 2
        self.assertTrue(ff.in_emergency_cooldown)

    def test_large_reversal_triggers_cooldown(self) -> None:
        ff = FlowFilter(ewma_span=2)
        # Build up strong positive EWMA
        for _ in range(10):
            ff.update(500, 100)
        # Sudden reversal to strong negative
        ff.update(100, 500)
        triggered = ff.check_reversal(threshold_bps=500.0, cooldown_cycles=3)
        if triggered:
            self.assertTrue(ff.in_emergency_cooldown)
            self.assertEqual(ff._emergency_cooldown, 3)

    def test_magnitude_gate_blocks_trigger_on_reversion_to_neutral(self) -> None:
        """Reverting to neutral (delta large, but |ewma| small) should NOT trigger."""
        ff = FlowFilter(ewma_span=2)
        # Build up moderate positive EWMA (~+5000 bps)
        for _ in range(15):
            ff.update(300, 100)  # 2/3 bid heavy → imbalance ≈ +5000 bps
        # One balanced update brings EWMA back toward neutral
        ff.update(100, 100)
        # delta may be large, but magnitude is now low — should not cancel
        triggered = ff.check_reversal(
            threshold_bps=2000.0, cooldown_cycles=4, min_magnitude_bps=4000.0
        )
        self.assertFalse(triggered)

    def test_magnitude_gate_allows_trigger_on_escalating_extreme_flow(self) -> None:
        """Escalating to an extreme regime (delta large AND |ewma| >= min_magnitude) SHOULD trigger."""
        ff = FlowFilter(ewma_span=2)  # alpha = 2/3
        # Build moderate EWMA: (200-100)/300 * 10000 = +3333 bps, converges fully after 15
        for _ in range(15):
            ff.update(200, 100)
        # One extreme update: raw = (900-100)/1000*10000 = +8000 bps
        # New EWMA = 2/3*8000 + 1/3*3333 ≈ +6444; delta ≈ 3111 bps, magnitude ≈ 6444
        ff.update(900, 100)
        triggered = ff.check_reversal(
            threshold_bps=1000.0, cooldown_cycles=2, min_magnitude_bps=3000.0
        )
        self.assertTrue(triggered)
        self.assertEqual(ff._emergency_cooldown, 2)


if __name__ == "__main__":
    unittest.main()
