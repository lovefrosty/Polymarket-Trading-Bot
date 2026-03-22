import unittest

from core_mm.sizing import get_buy_sell_amount


class TestSizing(unittest.TestCase):
    def test_buy_amount_respects_position_headroom(self) -> None:
        plan = get_buy_sell_amount(position=0, max_size=100, trade_size=50, avg_price=0.0, inventory_skew_factor=0.0)
        self.assertEqual(plan.buy_amount, 50.0)
        self.assertEqual(plan.sell_amount, 0.0)

    def test_buy_amount_caps_at_remaining_space(self) -> None:
        plan = get_buy_sell_amount(position=80, max_size=100, trade_size=50, avg_price=0.0, inventory_skew_factor=0.0)
        self.assertEqual(plan.buy_amount, 20.0)

    def test_buy_amount_stops_at_max_position(self) -> None:
        plan = get_buy_sell_amount(position=100, max_size=100, trade_size=50, avg_price=0.0, inventory_skew_factor=0.0)
        self.assertEqual(plan.buy_amount, 0.0)

    def test_net_short_allows_buy_despite_reverse(self) -> None:
        # position=0, reverse=25 → net=-25 → buying REDUCES risk → buy allowed
        plan = get_buy_sell_amount(
            position=0,
            max_size=100,
            trade_size=50,
            avg_price=0.0,
            reverse_position=25,
            reverse_position_min_size=20,
            inventory_skew_factor=0.0,
        )
        self.assertGreater(plan.buy_amount, 0.0)

    def test_sell_amount_uses_position_and_avg_cost(self) -> None:
        plan = get_buy_sell_amount(
            position=40, max_size=100, trade_size=50, avg_price=0.55, inventory_skew_factor=0.0
        )
        self.assertEqual(plan.sell_amount, 40.0)

    # ── Inventory skew tests ──────────────────────────────────────────────────

    def test_skew_reduces_buy_when_long(self) -> None:
        # position=50% of max → long_ratio=0.5 → buy_scale=0.5 → buy=25
        plan = get_buy_sell_amount(
            position=50, max_size=100, trade_size=50, avg_price=0.55, inventory_skew_factor=1.0
        )
        self.assertAlmostEqual(plan.buy_amount, 25.0)

    def test_skew_zeros_buy_at_max_position(self) -> None:
        # position=max → long_ratio=1.0 → buy_scale=0.0 → buy=0
        plan = get_buy_sell_amount(
            position=100, max_size=100, trade_size=50, avg_price=0.55, inventory_skew_factor=1.0
        )
        self.assertEqual(plan.buy_amount, 0.0)

    def test_skew_boosts_sell_when_long(self) -> None:
        # position=50% of max → long_ratio=0.5 → sell_scale=1.5 → sell=min(50, 50*1.5)=50
        # But position is 50, so sell is capped at 50
        plan = get_buy_sell_amount(
            position=50, max_size=100, trade_size=50, avg_price=0.55, inventory_skew_factor=1.0
        )
        self.assertAlmostEqual(plan.sell_amount, 50.0)

    def test_skew_boosts_sell_below_position_cap(self) -> None:
        # position=30 → long_ratio=0.3 → sell_scale=1.3 → sell=min(30, 50*1.3)=min(30,65)=30
        plan = get_buy_sell_amount(
            position=30, max_size=100, trade_size=10, avg_price=0.55, inventory_skew_factor=1.0
        )
        # trade_size=10 → 10*1.3=13 but capped at position=30 → 13
        self.assertAlmostEqual(plan.sell_amount, 13.0)

    def test_skew_disabled_when_factor_zero(self) -> None:
        # skew_factor=0.0 → flat buy/sell (original behaviour)
        plan_skewed = get_buy_sell_amount(
            position=50, max_size=100, trade_size=50, avg_price=0.55, inventory_skew_factor=0.0
        )
        self.assertAlmostEqual(plan_skewed.buy_amount, 50.0)
        self.assertAlmostEqual(plan_skewed.sell_amount, 50.0)

    # ── Net position tests ─────────────────────────────────────────────────

    def test_net_position_allows_buy_despite_reverse(self) -> None:
        # pos=50, reverse=30, net=20 → buy still allowed (headroom = 100 - 20 = 80)
        plan = get_buy_sell_amount(
            position=50, max_size=100, trade_size=50, avg_price=0.55,
            reverse_position=30, net_position=20, inventory_skew_factor=0.0,
        )
        self.assertGreater(plan.buy_amount, 0.0)

    def test_net_position_zero_full_buy(self) -> None:
        # pos=50, reverse=50, net=0 → full buying power
        plan = get_buy_sell_amount(
            position=50, max_size=100, trade_size=50, avg_price=0.55,
            reverse_position=50, net_position=0, inventory_skew_factor=0.0,
        )
        self.assertAlmostEqual(plan.buy_amount, 50.0)

    def test_net_position_at_max_blocks_buy(self) -> None:
        # pos=100, reverse=0, net=100 → no buy
        plan = get_buy_sell_amount(
            position=100, max_size=100, trade_size=50, avg_price=0.55,
            reverse_position=0, net_position=100, inventory_skew_factor=0.0,
        )
        self.assertEqual(plan.buy_amount, 0.0)

    def test_sell_uses_actual_position_not_net(self) -> None:
        # pos=50, reverse=30, net=20 → sell up to 50 (actual position)
        plan = get_buy_sell_amount(
            position=50, max_size=100, trade_size=50, avg_price=0.55,
            reverse_position=30, net_position=20, inventory_skew_factor=0.0,
        )
        self.assertAlmostEqual(plan.sell_amount, 50.0)

    def test_negative_net_position_opens_full_buy(self) -> None:
        # pos=0, reverse=40, net=-40 → effective_net=0 → full buy headroom
        plan = get_buy_sell_amount(
            position=0, max_size=100, trade_size=50, avg_price=0.0,
            reverse_position=40, net_position=-40, inventory_skew_factor=0.0,
        )
        self.assertAlmostEqual(plan.buy_amount, 50.0)


if __name__ == "__main__":
    unittest.main()
