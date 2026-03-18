import unittest

from core_mm.risk_manager import RiskConfig, RiskManager


class TestRiskManager(unittest.TestCase):
    def test_stop_loss_fires(self) -> None:
        manager = RiskManager(RiskConfig(stop_loss_threshold_pct=-5.0, stop_loss_max_spread_bps=300.0, sleep_hours=1.0))
        decision = manager.evaluate(
            market_id="m1",
            now_ms=1_000,
            position_size=100,
            avg_price=0.50,
            current_mid=0.46,
            best_bid=0.45,
            best_ask=0.47,
            spread_bps=200.0,
        )
        self.assertEqual(decision.action, "STOP_LOSS")
        self.assertFalse(decision.allow_buy)
        self.assertTrue(decision.allow_sell)
        self.assertEqual(decision.exit_price, 0.45)
        self.assertEqual(decision.exit_size, 100.0)

    def test_sleep_blocks_buys_until_expiry(self) -> None:
        manager = RiskManager(RiskConfig(stop_loss_threshold_pct=-5.0, stop_loss_max_spread_bps=300.0, sleep_hours=1.0))
        manager.evaluate(
            market_id="m1",
            now_ms=1_000,
            position_size=100,
            avg_price=0.50,
            current_mid=0.46,
            best_bid=0.45,
            best_ask=0.47,
            spread_bps=200.0,
        )
        sleeping = manager.evaluate(
            market_id="m1",
            now_ms=2_000,
            position_size=0,
            avg_price=0.0,
            current_mid=0.50,
            best_bid=0.49,
            best_ask=0.51,
            spread_bps=100.0,
        )
        self.assertFalse(sleeping.allow_buy)
        self.assertIn("sleep_active", sleeping.reasons)

        resumed = manager.evaluate(
            market_id="m1",
            now_ms=3_700_001,
            position_size=0,
            avg_price=0.0,
            current_mid=0.50,
            best_bid=0.49,
            best_ask=0.51,
            spread_bps=100.0,
        )
        self.assertTrue(resumed.allow_buy)
        self.assertNotIn("sleep_active", resumed.reasons)

    def test_take_profit_price_uses_max_of_best_ask_and_tp(self) -> None:
        manager = RiskManager(RiskConfig(take_profit_pct=10.0))
        decision = manager.evaluate(
            market_id="m1",
            now_ms=1_000,
            position_size=40,
            avg_price=0.50,
            current_mid=0.56,
            best_bid=0.55,
            best_ask=0.54,
            spread_bps=150.0,
        )
        self.assertEqual(decision.action, "TAKE_PROFIT")
        self.assertAlmostEqual(decision.exit_price or 0.0, 0.55)


    def test_aggregate_position_notional_config_defaults_disabled(self) -> None:
        config = RiskConfig()
        self.assertEqual(config.max_total_position_notional, 0.0)
        self.assertEqual(config.max_markets_with_position, 0)

    def test_aggregate_position_notional_config_custom(self) -> None:
        config = RiskConfig(max_total_position_notional=50.0, max_markets_with_position=3)
        self.assertEqual(config.max_total_position_notional, 50.0)
        self.assertEqual(config.max_markets_with_position, 3)


if __name__ == "__main__":
    unittest.main()
