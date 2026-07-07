import unittest

from core_mm.sizing import get_buy_sell_amount


class TestSizing(unittest.TestCase):
    def _plan(self, **overrides):
        params = {
            "position": 0.0,
            "max_size": 100.0,
            "trade_size": 50.0,
            "avg_price": 0.0,
            "reverse_position": 0.0,
            "net_position": None,
            "min_order_size": 0.0,
            "usdc_balance": None,
            "buy_price": None,
            "sell_price": None,
            "hard_position_cap": 250.0,
            "inventory_skew_factor": 0.0,
            "p_fair": None,
            "kelly_fraction": 0.0,
            "bankroll": None,
            "risk_per_trade_budget": None,
            "risk_based_share_sizing": True,
        }
        params.update(overrides)
        return get_buy_sell_amount(**params)

    def _assert_plan(self, plan, buy: float, sell: float) -> None:
        self.assertAlmostEqual(plan.buy_amount, buy)
        self.assertAlmostEqual(plan.sell_amount, sell)

    def test_buy_amount_respects_position_headroom(self) -> None:
        plan = self._plan()
        self._assert_plan(plan, 50.0, 0.0)
        self.assertEqual(plan.buy_limiter, "trade_size")
        self.assertEqual(plan.buy_limiters, "trade_size")
        self.assertEqual(plan.sell_limiter, "inventory")

    def test_buy_amount_caps_at_remaining_space(self) -> None:
        plan = self._plan(position=80)
        self.assertEqual(plan.buy_amount, 20.0)

    def test_buy_amount_stops_at_max_position(self) -> None:
        plan = self._plan(position=100)
        self.assertEqual(plan.buy_amount, 0.0)

    def test_net_short_allows_buy_despite_reverse(self) -> None:
        # position=0, reverse=25 → net=-25 → buying REDUCES risk → buy allowed
        plan = self._plan(reverse_position=25)
        self.assertGreater(plan.buy_amount, 0.0)

    def test_sell_amount_uses_position_and_avg_cost(self) -> None:
        plan = self._plan(position=40, avg_price=0.55)
        self.assertEqual(plan.sell_amount, 40.0)

    # ── Inventory skew tests ──────────────────────────────────────────────────

    def test_skew_reduces_buy_when_long(self) -> None:
        # position=50% of max → long_ratio=0.5 → buy_scale=0.5 → buy=25
        plan = self._plan(position=50, avg_price=0.55, inventory_skew_factor=1.0)
        self.assertAlmostEqual(plan.buy_amount, 25.0)

    def test_skew_zeros_buy_at_max_position(self) -> None:
        # position=max → long_ratio=1.0 → buy_scale=0.0 → buy=0
        plan = self._plan(position=100, avg_price=0.55, inventory_skew_factor=1.0)
        self.assertEqual(plan.buy_amount, 0.0)

    def test_skew_boosts_sell_when_long(self) -> None:
        # position=50% of max → long_ratio=0.5 → sell_scale=1.5 → sell=min(50, 50*1.5)=50
        # But position is 50, so sell is capped at 50
        plan = self._plan(position=50, avg_price=0.55, inventory_skew_factor=1.0)
        self.assertAlmostEqual(plan.sell_amount, 50.0)

    def test_skew_boosts_sell_below_position_cap(self) -> None:
        # position=30 → long_ratio=0.3 → sell_scale=1.3 → sell=min(30, 50*1.3)=min(30,65)=30
        plan = self._plan(position=30, trade_size=10, avg_price=0.55, inventory_skew_factor=1.0)
        # trade_size=10 → 10*1.3=13 but capped at position=30 → 13
        self.assertAlmostEqual(plan.sell_amount, 13.0)

    def test_skew_disabled_when_factor_zero(self) -> None:
        # skew_factor=0.0 → flat buy/sell (original behaviour)
        plan_skewed = self._plan(position=50, avg_price=0.55)
        self.assertAlmostEqual(plan_skewed.buy_amount, 50.0)
        self.assertAlmostEqual(plan_skewed.sell_amount, 50.0)

    # ── Net position tests ─────────────────────────────────────────────────

    def test_net_position_allows_buy_despite_reverse(self) -> None:
        # pos=50, reverse=30, net=20 → buy still allowed (headroom = 100 - 20 = 80)
        plan = self._plan(position=50, avg_price=0.55, reverse_position=30, net_position=20)
        self.assertGreater(plan.buy_amount, 0.0)

    def test_net_position_zero_full_buy(self) -> None:
        # pos=50, reverse=50, net=0 → full buying power
        plan = self._plan(position=50, avg_price=0.55, reverse_position=50, net_position=0)
        self.assertAlmostEqual(plan.buy_amount, 50.0)

    def test_net_position_at_max_blocks_buy(self) -> None:
        # pos=100, reverse=0, net=100 → no buy
        plan = self._plan(position=100, avg_price=0.55, net_position=100)
        self.assertEqual(plan.buy_amount, 0.0)

    def test_sell_uses_actual_position_not_net(self) -> None:
        # pos=50, reverse=30, net=20 → sell up to 50 (actual position)
        plan = self._plan(position=50, avg_price=0.55, reverse_position=30, net_position=20)
        self.assertAlmostEqual(plan.sell_amount, 50.0)

    def test_negative_net_position_opens_full_buy(self) -> None:
        # pos=0, reverse=40, net=-40 → effective_net=0 → full buy headroom
        plan = self._plan(reverse_position=40, net_position=-40)
        self.assertAlmostEqual(plan.buy_amount, 50.0)

    def test_risk_based_share_sizing_caps_buy_amount(self) -> None:
        plan = self._plan(buy_price=0.50, risk_per_trade_budget=4.0)
        self.assertAlmostEqual(plan.buy_amount, 8.0)
        self.assertEqual(plan.buy_limiter, "risk_budget")
        self.assertEqual(plan.buy_limiters, "risk_budget")

    def test_risk_budget_overrides_kelly_buy_size(self) -> None:
        plan = self._plan(
            buy_price=0.50,
            sell_price=0.50,
            p_fair=0.90,
            kelly_fraction=0.10,
            bankroll=10_000.0,
            risk_per_trade_budget=4.0,
        )
        self.assertAlmostEqual(plan.buy_amount, 8.0)

    def test_kelly_converts_notional_to_shares(self) -> None:
        plan = self._plan(
            buy_price=0.50,
            sell_price=0.50,
            p_fair=0.60,
            kelly_fraction=0.10,
            bankroll=1000.0,
            risk_based_share_sizing=False,
        )
        self.assertAlmostEqual(plan.buy_amount, 40.0)

    def test_kelly_cannot_increase_buy_above_trade_size(self) -> None:
        plan = self._plan(
            trade_size=25,
            buy_price=0.20,
            sell_price=0.20,
            p_fair=0.90,
            kelly_fraction=0.50,
            bankroll=10_000.0,
            risk_based_share_sizing=False,
        )
        self.assertAlmostEqual(plan.buy_amount, 25.0)

    def test_sell_uses_kelly_derived_sell_size(self) -> None:
        plan = self._plan(
            position=30,
            avg_price=0.55,
            net_position=30,
            sell_price=0.50,
            p_fair=0.495,
            kelly_fraction=0.10,
            bankroll=5000.0,
            inventory_skew_factor=0.5,
            risk_based_share_sizing=False,
        )
        self.assertAlmostEqual(plan.sell_amount, 11.5)

    def test_sell_is_not_blocked_by_buy_side_risk_budget(self) -> None:
        plan = self._plan(position=20, trade_size=10, avg_price=0.55, buy_price=0.50, risk_per_trade_budget=1.0)
        self.assertAlmostEqual(plan.sell_amount, 10.0)

    def test_negative_net_short_boosts_buy_reduction_path(self) -> None:
        plan = self._plan(trade_size=10, reverse_position=40, net_position=-40, inventory_skew_factor=1.0, risk_based_share_sizing=False)
        self.assertGreater(plan.buy_amount, 10.0)

    def test_affordability_caps_buy_amount(self) -> None:
        plan = self._plan(usdc_balance=3.0, buy_price=0.50)
        self.assertAlmostEqual(plan.buy_amount, 6.0)
        self.assertEqual(plan.buy_limiter, "affordability")
        self.assertEqual(plan.buy_limiters, "affordability")

    def test_min_order_size_rejects_small_buy(self) -> None:
        plan = self._plan(trade_size=10, buy_price=0.50, risk_per_trade_budget=2.0, min_order_size=5.0)
        self.assertEqual(plan.buy_amount, 0.0)

    def test_min_order_size_rejects_small_sell(self) -> None:
        plan = self._plan(position=3, trade_size=10, avg_price=0.55, min_order_size=5.0)
        self.assertEqual(plan.sell_amount, 0.0)

    def test_hard_position_cap_blocks_buy_even_if_max_size_is_larger(self) -> None:
        plan = self._plan(position=25, trade_size=10, avg_price=0.55, hard_position_cap=25.0)
        self.assertEqual(plan.buy_amount, 0.0)
        self.assertEqual(plan.buy_limiter, "hard_position_cap")
        self.assertEqual(plan.buy_limiters, "hard_position_cap")

    def test_sell_limiter_tracks_kelly_after_inventory_cap(self) -> None:
        plan = self._plan(
            position=30,
            avg_price=0.55,
            net_position=30,
            sell_price=0.50,
            p_fair=0.495,
            kelly_fraction=0.10,
            bankroll=5000.0,
            inventory_skew_factor=0.5,
            risk_based_share_sizing=False,
        )
        self.assertEqual(plan.sell_limiter, "kelly")
        self.assertEqual(plan.sell_limiters, "inventory,kelly")


if __name__ == "__main__":
    unittest.main()
