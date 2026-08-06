import unittest

from core_mm.risk_manager import RiskConfig, RiskManager


class TestRiskManager(unittest.TestCase):
    def test_stop_loss_fires_maker_first(self) -> None:
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
        self.assertEqual(decision.exit_price, 0.47)
        self.assertEqual(decision.exit_size, 100.0)
        self.assertEqual(decision.exit_mode, "maker")

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

    def test_stale_position_enters_maker_unwind(self) -> None:
        manager = RiskManager()
        manager.record_fill(token_id="t1", side="buy", ts_ms=0)
        decision = manager.evaluate(
            market_id="m1",
            token_id="t1",
            now_ms=31_000,
            position_size=10,
            avg_price=0.50,
            current_mid=0.49,
            best_bid=0.48,
            best_ask=0.50,
            spread_bps=400.0,
            market_duration_ms=3_600_000,
        )
        self.assertEqual(decision.action, "STALE_UNWIND")
        self.assertEqual(decision.exit_mode, "maker")
        self.assertEqual(decision.exit_price, 0.50)
        self.assertEqual(decision.stale_state, "stale")

    def test_stale_position_crosses_after_grace_and_worsening(self) -> None:
        manager = RiskManager(RiskConfig(maker_exit_grace_secs=10.0, cross_escalation_drawdown_pct=0.01))
        manager.record_fill(token_id="t1", side="buy", ts_ms=0)
        first = manager.evaluate(
            market_id="m1",
            token_id="t1",
            now_ms=31_000,
            position_size=10,
            avg_price=0.50,
            current_mid=0.49,
            best_bid=0.48,
            best_ask=0.50,
            spread_bps=400.0,
            current_equity=1_000.0,
            reference_equity=1_000.0,
            market_unrealized_pnl=-1.0,
            market_duration_ms=3_600_000,
        )
        self.assertEqual(first.exit_mode, "maker")
        second = manager.evaluate(
            market_id="m1",
            token_id="t1",
            now_ms=42_000,
            position_size=10,
            avg_price=0.50,
            current_mid=0.30,
            best_bid=0.29,
            best_ask=0.31,
            spread_bps=400.0,
            current_equity=1_000.0,
            reference_equity=1_000.0,
            market_unrealized_pnl=-20.0,
            market_duration_ms=3_600_000,
        )
        self.assertEqual(second.action, "STOP_LOSS")
        self.assertEqual(second.exit_mode, "cross")
        self.assertEqual(second.exit_price, 0.29)
        self.assertTrue(second.cross_armed)

    def test_expiry_windows_scale_and_force_flat(self) -> None:
        manager = RiskManager()
        decision = manager.evaluate(
            market_id="m1",
            token_id="t1",
            now_ms=1_000,
            position_size=12,
            avg_price=0.50,
            current_mid=0.49,
            best_bid=0.48,
            best_ask=0.50,
            spread_bps=200.0,
            market_duration_ms=300_000,
            time_to_expiry_ms=7_000,
        )
        self.assertEqual(decision.action, "FORCE_FLAT")
        self.assertTrue(decision.force_flat_triggered)
        self.assertFalse(decision.allow_buy)

    def test_max_buy_size_respects_equity_caps(self) -> None:
        manager = RiskManager()
        decision = manager.evaluate(
            market_id="m1",
            token_id="t1",
            now_ms=1_000,
            position_size=0,
            avg_price=0.0,
            current_mid=0.50,
            best_bid=0.49,
            best_ask=0.51,
            spread_bps=200.0,
            current_equity=1_000.0,
            reference_equity=1_000.0,
            planned_buy_price=0.50,
            market_position_notional=20.0,
            event_position_notional=30.0,
        )
        self.assertIsNotNone(decision.max_buy_size)
        self.assertAlmostEqual(decision.max_buy_size or 0.0, 10.0)

    def test_max_buy_size_uses_per_trade_risk_budget(self) -> None:
        manager = RiskManager(
            RiskConfig(
                per_trade_loss_pct=0.02,
                max_order_notional_pct=0.0,
                max_market_exposure_pct=0.0,
                max_event_exposure_pct=0.0,
                risk_based_share_sizing=True,
            )
        )
        decision = manager.evaluate(
            market_id="m1",
            token_id="t1",
            now_ms=1_000,
            position_size=0,
            avg_price=0.0,
            current_mid=0.50,
            best_bid=0.49,
            best_ask=0.51,
            spread_bps=200.0,
            current_equity=200.0,
            reference_equity=200.0,
            planned_buy_price=0.50,
            market_position_notional=0.0,
            event_position_notional=0.0,
        )
        self.assertAlmostEqual(decision.max_buy_size or 0.0, 8.0)

    def test_day_loss_cap_forces_risk_off(self) -> None:
        manager = RiskManager()
        decision = manager.evaluate(
            market_id="m1",
            token_id="t1",
            now_ms=1_000,
            position_size=10,
            avg_price=0.50,
            current_mid=0.40,
            best_bid=0.39,
            best_ask=0.41,
            spread_bps=300.0,
            current_equity=900.0,
            reference_equity=1_000.0,
            market_unrealized_pnl=-1.0,
            event_unrealized_pnl=-1.0,
            portfolio_total_pnl=-150.0,
        )
        self.assertEqual(decision.action, "DAY_LOSS_CAP")
        self.assertFalse(decision.allow_buy)
        self.assertEqual(decision.exit_mode, "maker")
        self.assertTrue(decision.flatten_only_triggered)


if __name__ == "__main__":
    unittest.main()
